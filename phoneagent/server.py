"""Task daemon for the chat UI.

    # host, driving a phone over adb
    python3 -m phoneagent.server --serial e3fa9e60 --port 8765

    # inside the APK, driving its own virtual display (see ondevice.serve)
    ondevice.serve(8765, api_key=..., ...)

    POST /task          {"text": "打开蓝牙设置"}  -> {"id": "..."}
    GET  /task/<id>     -> {"state", "steps", "answer", "frame"}

The device is whatever the caller injects, so the same HTTP surface works both
ways: a host shell over ``adb``, or the app's own adbd connection. Tasks are
serialised because the virtual display is a single shared surface -- two
agents tapping at once would fight over the same screen.
"""

from __future__ import annotations

import argparse
import base64
import faulthandler
import hmac
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from .core.agent import AgentConfig, PhoneAgent
from .core.app_map import AppMap
from .core.memory import MemoryStore

TASKS: dict[str, dict] = {}
#: Per-task stop flags, so a running task can be cancelled from the UI.
CANCEL: dict[str, threading.Event] = {}
#: Per-task handover slot: the question the agent asked and the reply it waits for.
PENDING: dict[str, dict] = {}
LOCK = threading.Lock()
#: One agent at a time: they would otherwise share the virtual display.
RUN_LOCK = threading.Lock()

#: Per-task mode: True when the caller put this task in autonomous mode.
AUTONOMOUS: dict[str, bool] = {}
#: Per-task mode: True when this task may also run shell commands.
SHELL_MODE: dict[str, bool] = {}
#: Per-task mode: True when the map/macro layer ("CLI the app") is allowed.
MAPS_MODE: dict[str, bool] = {}
#: Crawls running inside the app. Same shape as TASKS: what the caller polls.
CRAWLS: dict[str, dict] = {}
#: The crawler prints this when it gives up on an app (see build_map).
_REFUSED_LINE = re.compile(r"cannot start (\S+) here")
#: Replay benchmarks running against the app's own display.
BENCHES: dict[str, dict] = {}
#: The last picture the app published, whoever took it. The capsule and the
#: live view read this instead of asking for a capture, because a second reader
#: on the adb transport corrupts it.
LAST_FRAME: dict = {"data": "", "t": 0.0}


def _publish_frame(png: bytes) -> None:
    try:
        LAST_FRAME["data"] = base64.b64encode(png).decode()
        LAST_FRAME["t"] = time.time()
    except Exception:  # noqa: BLE001 - a picture must never fail a task
        pass


@dataclass
class ServerConfig:
    """How to build the two things a task needs."""


    device_factory: Callable[[], object]
    llm_factory: Callable[[], object]
    #: Called when the device connection is dead, before a task starts. The app
    #: passes a function that reconnects and rebuilds the display.
    recover: Optional[Callable[[], str]] = None
    start_app: str = ""
    #: Leave the display running after the task; the app owns one device for
    #: its whole lifetime rather than creating one per task.
    keep_device: bool = False
    #: Accessibility labels are worth prompting with when the service is on.
    include_ui_text: bool = True
    #: Steps per task. 25 was tight for anything that has to read content off
    #: the results (a search plus a recommendation took 27-29 steps), and the
    #: partial outcome means running long no longer risks a false "done".
    max_rounds: int = 35
    #: How long the agent waits for a reply to ask_user before giving up.
    ask_timeout: float = 180.0
    #: Where memories live; empty disables memory entirely.
    memory_path: str = ""
    #: Where token usage is appended; the app puts it next to the memories.
    usage_path: str = ""
    #: Ask the model what is worth remembering after a successful task.
    memory_extract: bool = True
    #: App maps that provide the operation catalog; without them the agent is
    #: VLM-only, one model call per step.
    map_paths: list = field(default_factory=list)
    #: Directories scanned before every task, later ones winning on a name
    #: clash. A crawl writes into one of these, so the map it produced is in
    #: effect for the very next task instead of the next app restart.
    map_dirs: list = field(default_factory=list)
    #: Where the pinned map per package is remembered. Empty disables pinning.
    pins_path: str = ""
    #: Apps that answered with a captcha are driven by screen, not replayed.
    risk_path: str = ""
    #: When each app was last crawled, so one app is not hammered.
    pacing_path: str = ""
    #: Optional factory that returns a device driving the phone's own screen,
    #: for apps that cannot run on a virtual display at all.
    hands_off_factory: Optional[Callable[[], object]] = None
    #: Where a crawl writes its map; empty disables the crawl endpoint.
    crawl_dir: str = ""
    #: App name -> package, so the launch tool can resolve "日历" to a package
    #: even when that app has no map.
    known_apps: dict = field(default_factory=dict)
    #: Credentials for the crawl's classify/enrich calls. The crawl runs the
    #: same pipeline as the host, so without these it can only produce a bare
    #: map -- screens and elements, no descriptions and no aliases.
    llm_api_key: str = ""
    llm_api_base: str = "https://openrouter.ai/api/v1"
    llm_model: str = "qwen/qwen3.8-flash"
    #: A task that produces no new step for this long is treated as stuck:
    #: its traceback is dumped and the task is failed instead of sitting in
    #: "running" until the app is killed.
    #: How long a task may go without a new step before it is treated as stuck.
    #: Must exceed two model calls (the client's timeout is 60 s), or a single
    #: retry is indistinguishable from a freeze.
    stall_timeout: float = 240.0
    #: Shared secret every request must present. Empty means "no check", which
    #: is how the host CLI runs; the app always sets one, because on Android any
    #: installed app can reach 127.0.0.1 and would otherwise be able to drive the
    #: agent or read /dump.
    token: str = ""
    #: Packages that must be driven on the phone's own display (see
    #: AgentConfig.user_display_packages).
    user_display_packages: tuple = ()
    extra: dict = field(default_factory=dict)


def _step_dict(step) -> dict:
    return {
        "round": step.round,
        "kind": step.kind,
        "action": step.action,
        "detail": step.detail,
    }


#: How many finished tasks keep their record. Each holds steps, the answer and
#: -- while it runs -- a base64 screenshot of about 200 KB, so an app left open
#: for a day would otherwise grow without bound.
KEEP_TASKS = 30
KEEP_CRAWLS = 10
KEEP_BENCHES = 10


def _prune(store: dict, keep: int) -> None:
    """Drop the oldest entries, keeping insertion order."""
    if len(store) <= keep:
        return
    for key in list(store)[: len(store) - keep]:
        store.pop(key, None)


def _update(task_id: str, **fields) -> None:
    with LOCK:
        record = TASKS.setdefault(task_id, {})
        record.update(fields)
        if fields.get("state") in ("done", "failed", "cancelled"):
            # The picture is the expensive part and LAST_FRAME already keeps the
            # latest one for the capsule and the viewer.
            record["frame"] = ""
            _prune(TASKS, KEEP_TASKS)


#: "停在小红书那个页面" -- the user wants the task to end with that app in front.
STAY_RE = re.compile(
    r"(停在|留在|停到|留在|保持|不要关|别关|别退出|先别退|keep\s+it\s+open|"
    r"leave\s+it\s+open|stay\s+(in|on))",
    re.IGNORECASE,
)


def _wants_to_stay(text: str) -> bool:
    """Did the user ask to be left on that screen?

    The default is to come back to the chat: a task that ends with someone
    else's app in front leaves the user staring at 小红书 instead of the answer
    they asked for.
    """
    return bool(STAY_RE.search(text or ""))


#: Anything the agent could act on: a non-empty text or content-description.
LABEL_RE = re.compile(r'(?:text|content-desc)="([^"]+)"')


def _has_labels(tree: str) -> bool:
    return any(match.group(1).strip() for match in LABEL_RE.finditer(tree or ""))


def _ensure_content(device, app: str) -> bool:
    """Bring something onto the display if the system emptied it.

    A virtual display outlives the app that was on it: minutes later the task
    may be gone (memory pressure, the app finishing) and the accessibility
    tree is then empty. The agent cannot work from that, and it is not a
    device failure, so relaunch something instead of reporting no_ui.
    """
    try:
        if getattr(device, "current_app", lambda: "unknown")() != "unknown":
            return True
    except Exception:  # noqa: BLE001 - treat as empty and try to recover
        pass
    try:
        if getattr(device, "display_id", None) is None:
            device.start(initial_app=app)
            return True
        print(f"[server] display is empty; launching {app}")
        device.launch(app)
        deadline = time.time() + 8.0
        while time.time() < deadline:
            try:
                if getattr(device, "current_app", lambda: "unknown")() != "unknown":
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001 - surfaced in the task result
        print(f"[server] could not reopen the display: {exc}")
    return False


def remember(llm, memory, task: str, answer: str):
    """Extract memories from a finished task and store what survives.

    Returns the steps to show, so a remembered fact and a procedure awaiting
    approval are both visible to the user rather than silent.
    """
    from .core.memory import extract
    from .core.agent import Step

    steps = []
    for candidate in extract(llm, task, answer):
        item = memory.add(candidate["kind"], candidate["content"], candidate.get("quote", ""))
        if item is None:
            steps.append(Step(0, "memory", "refused", candidate["content"][:40]))
            continue
        if item.status == "pending":
            steps.append(Step(0, "memory", "pending", item.content[:60]))
        else:
            steps.append(Step(0, "memory", "remembered", item.content[:60]))
    return steps


def _ask(task_id: str, question: str, timeout: float) -> Optional[str]:
    """Hand the task to the user: publish the question and block for a reply."""
    slot = PENDING.get(task_id)
    if slot is None:
        return None
    slot["answer"] = None
    slot["event"].clear()
    _update(task_id, state="waiting", question=question)
    print(f"[server] asking the user: {question[:120]}")
    answered = slot["event"].wait(timeout)
    _update(task_id, state="running", question="")
    if not answered:
        print("[server] no reply before the timeout")
        return None
    return slot["answer"]


def _better_map(path, candidate, previous_path, previous) -> bool:
    """Which map should represent a package.

    More screens wins: a three-minute crawl that only got one screen should not
    replace a map that knows the app. Newest breaks a tie, so a real re-crawl
    still takes over.
    """
    if len(candidate.screens) != len(previous.screens):
        return len(candidate.screens) > len(previous.screens)
    return os.path.getmtime(path) >= os.path.getmtime(previous_path)


def _scan_maps(config: ServerConfig) -> list:
    """Every map on disk, newest directory last.

    A map is a file the app can write at any time -- a crawl just finished, or
    the same app was crawled again -- so the list is rebuilt per task rather
    than fixed at server start.
    """
    import glob

    found: dict = {}
    for directory in config.map_dirs:
        if not directory:
            continue
        for path in sorted(glob.glob(os.path.join(directory, "*.yaml"))):
            name = os.path.basename(path)
            # `.public.yaml` is the redacted copy a map is shared with, not a
            # map to drive a phone with
            if name.endswith(".public.yaml") or name.endswith(".yaml.partial"):
                continue
            previous = found.get(name)
            if previous is None or os.path.getmtime(path) >= os.path.getmtime(previous):
                found[name] = path
    return list(found.values())


def _run(task_id: str, text: str, config: ServerConfig,
         hands_off: bool = False) -> None:
    from . import usage as usage_mod

    # Everything this task spends is attributed to it, so the usage page can
    # answer "what did that request cost" instead of only "what did today cost".
    usage_mod.set_context(task_id, text)
    cancel_event = CANCEL[task_id]
    with RUN_LOCK:
        device = config.device_factory()
        if hands_off and config.hands_off_factory is not None:
            # The user handed the phone over: drive their screen directly.
            device = config.hands_off_factory()
        # adbd drops the connection now and then (measured: SSLEOFError in the
        # middle of a task, then every later task fails the same way). A cheap
        # shell command tells us before the task starts, and the app rebuilds
        # the display instead of asking the user to restart anything.
        if config.recover is not None:
            try:
                device.shell("echo ok")
            except Exception as exc:  # noqa: BLE001 - any failure means dead
                print(f"[server] device connection is down ({exc}); rebuilding")
                recovery = None
                try:
                    recovery = config.recover()
                except Exception as exc2:  # noqa: BLE001 - reported below
                    print(f"[server] recovery raised: {exc2}")
                message = str(recovery or "").strip()
                print(f"[server] recovery: {message}")
                device = config.device_factory()
                if device is None:
                    # The recovery knows why it failed (wireless debugging off,
                    # a moved port); passing that on is the difference between
                    # an actionable error and "reopen the app", which would not
                    # have helped.
                    _update(task_id, state="failed",
                            answer=message or "adb 连接已断开，重建失败。")
                    return
        try:
            if config.start_app and not getattr(device, "display_id", None):
                device.start(initial_app=config.start_app)
            _ensure_content(device, config.start_app or "com.android.settings")
            spent = {"calls": 0, "prompt": 0, "completion": 0}

            def count_usage(prompt: int, completion: int) -> None:
                spent["calls"] += 1
                spent["prompt"] += prompt
                spent["completion"] += completion
                _update(task_id, usage=dict(spent))

            try:
                llm = config.llm_factory(usage_sink=count_usage)
            except TypeError:
                # a factory that does not take the sink (the mock, older code)
                llm = config.llm_factory()
            memory = MemoryStore(config.memory_path) if config.memory_path else None
            # GUI-only means no map at all: the agent then works from what it
            # sees, screenshot by screenshot, which is the other way to drive a
            # phone and the one this project started from.
            maps = []
            usable_paths = (
                _scan_maps(config) if config.map_dirs else config.map_paths
            ) if MAPS_MODE.get(task_id, True) else []
            # One map per package. Two crawls of the same app are alternatives,
            # not a bigger map -- routing across both is how a task lands on a
            # screen from a map that no longer describes this phone.
            loaded = []
            for path in usable_paths:
                try:
                    loaded.append((path, AppMap(path)))
                except Exception as exc:  # noqa: BLE001 - a bad map must not stop the agent
                    print(f"[server] map skipped: {path}: {exc}")
            from .map_pins import choose, load_pins

            shipped = tuple(config.map_dirs[:1])
            chosen = choose(loaded, load_pins(config.pins_path), shipped)
            maps = [entry[1] for entry in chosen]
            if maps:
                print("[server] maps: " + ", ".join(
                    f"{m.app_name}({os.path.basename(path)})"
                    for path, m in chosen
                ))

            original = device.screenshot
            capture_lock = threading.Lock()

            def screenshot(path=None):
                with capture_lock:
                    png = original(path)
                _update(task_id, frame=base64.b64encode(png).decode())
                _publish_frame(png)
                return png

            device.screenshot = screenshot  # type: ignore[assignment]

            def publish_frame(why: str = "") -> None:
                """One picture, on this thread, so the app has something to show.

                A macro task takes no screenshots of its own, so the live view
                stayed empty through the whole run. Capturing from a background
                thread is not the answer -- the adb transport is one channel and
                two writers corrupt it (measured) -- so this happens before the
                agent starts and once more when it is done.
                """
                try:
                    png = original()
                except Exception:  # noqa: BLE001 - a picture is optional
                    print(f"[server] no {why or 'startup'} frame")
                    return
                try:
                    _update(task_id, frame=base64.b64encode(png).decode())
                    _publish_frame(png)
                except Exception:  # noqa: BLE001
                    pass

            # The live view needs at least a first and a last picture: a macro
            # task takes no screenshots of its own, so the card stayed empty
            # through the whole run. A background thread is not the answer --
            # the adb transport is one channel and two writers corrupt it
            # (measured) -- so this runs on the task's own thread.
            publish_frame("startup")

            # If the tree is still empty, this task runs from pixels rather
            # than failing the preflight with no_ui.
            labels = config.include_ui_text
            if labels:
                try:
                    if not _has_labels(device.dump_ui()):
                        labels = False
                        print("[server] no labels on the display; pixels for this task")
                except Exception:  # noqa: BLE001 - fall back to pixels
                    labels = False

            agent = PhoneAgent(
                device,
                maps,
                llm,
                cancel=cancel_event.is_set,
                ask=lambda q: _ask(task_id, q, config.ask_timeout),
                memory=memory,
                known_apps=config.known_apps,
                log=lambda *parts: print("[agent]", *parts),
                config=AgentConfig(
                    macro_enabled=bool(maps),
                    include_ui_text=labels,
                    max_rounds=config.max_rounds,
                    # Ask is the default: a step that spends money waits for the
                    # user unless this task was explicitly put in autonomous mode.
                    autonomous=AUTONOMOUS.get(task_id, False),
                    # GUI-only unless the caller asked for the hybrid mode.
                    shell_enabled=SHELL_MODE.get(task_id, False),
                    # apps that answered us with a captcha are driven by screen
                    # from then on, not replayed
                    risk_path=config.risk_path,
                    user_display_packages=config.user_display_packages,
                    # "停在小红书" is the one instruction that overrides the
                    # end-of-task restore.
                    stay_on_user_display=_wants_to_stay(text),
                ),
                sleep=time.sleep,
            )
            _update(task_id, state="running")
            # Mirror the agent's steps into the task dict as they appear, so
            # the chat shows progress instead of the finished list.
            stop_watch = threading.Event()

            def watch() -> None:
                seen = -1
                progressed = time.time()
                dumped = False
                while not stop_watch.is_set():
                    snapshot = [_step_dict(step) for step in list(agent.steps)]
                    if len(snapshot) != seen:
                        seen = len(snapshot)
                        progressed = time.time()
                        _update(task_id, steps=snapshot)
                    elif (config.stall_timeout
                          and time.time() - progressed > config.stall_timeout):
                        # One reproducible case: a calendar task stopped right
                        # after settling, the HTTP server's listening socket
                        # disappeared, and the process stayed alive -- so the
                        # agent could not even be asked what it was doing. The
                        # only way to see the stack of a stuck thread from the
                        # outside is to take it from the inside.
                        if not dumped:
                            dumped = True
                            print(f"[server] task {task_id} stalled for "
                                  f"{config.stall_timeout:.0f}s; dumping stacks")
                            try:
                                faulthandler.dump_traceback()
                            except Exception:  # noqa: BLE001 - a debug aid
                                pass
                        cancel_event.set()
                        _update(task_id, state="failed",
                                answer="这一步卡住了（"
                                       f"{config.stall_timeout:.0f} 秒没有新进展），已停下。"
                                       "\n常见原因是网络慢导致模型调用超时——再发一次通常就能继续；"
                                       "如果反复出现，告诉我，我换个做法。")
                    stop_watch.wait(0.4)

            watcher = threading.Thread(target=watch, daemon=True)
            watcher.start()
            try:
                result = agent.run(text)
            finally:
                stop_watch.set()
                watcher.join(timeout=2.0)
                publish_frame("closing")

            # What should be remembered is decided after the task, by the model;
            # a refused credential or a pending procedure never reaches a prompt.
            if memory is not None and config.memory_extract and result.ok:
                # TaskResult.steps is a copy of the agent's list, so append to
                # the result or the memory lines never reach the client
                for step in remember(llm, memory, text, result.answer):
                    result.steps.append(step)

            cancelled = result.reason == "cancelled"
            # partial: something was achieved but the task did not finish. The
            # app renders it as 部分完成 rather than 失败, because the two are
            # different things to read.
            if result.ok:
                outcome_state = "done"
            elif result.reason == "partial":
                outcome_state = "partial"
            else:
                outcome_state = "failed"
            # Computed here, not in the finally block: a client that polls the
            # task the moment it turns terminal would otherwise see the flags
            # missing and never bring the chat back.
            try:
                # "Followed" counts as well: the agent leaves the app on the
                # phone's screen during the task and moves it back at the end,
                # so display_id alone would say "not on your screen" and the
                # chat would never be brought forward again.
                stayed = bool(getattr(device, "display_id", None) == 0
                              or getattr(agent, "_followed", False))
            except Exception:  # noqa: BLE001 - housekeeping
                stayed = False
            _update(
                task_id,
                on_user_display=stayed,
                leave_app=_wants_to_stay(text),
                state="cancelled" if cancelled else outcome_state,
                answer="已取消" if cancelled else (result.answer or result.reason),
                steps=[_step_dict(step) for step in result.steps],
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the app
            # The answer only carries the message; without the stack a failure
            # like "UnicodeDecodeError: ... utf-16-le ..." is untraceable from
            # the phone.
            import traceback

            traceback.print_exc()
            _update(task_id, state="failed", answer=f"{type(exc).__name__}: {exc}")
        finally:
            # A task may have followed its app onto the user's display; the next
            # one should start on the agent's own screen again.
            try:
                back = getattr(device, "return_to_own_display", None)
                if back is not None:
                    back()
            except Exception:  # noqa: BLE001 - housekeeping
                pass
            # And the user gets their screen back with the chat on it. Without
            # this a task that followed an app (携程's search page, a singleTask
            # activity) ended with that app still in front, and the answer was
            # sitting behind it.
            try:
                _hand_back_the_screen(device)
            except Exception as exc:  # noqa: BLE001 - never fail a finished task
                print(f"[server] could not hand the screen back: {exc}")
            usage_mod.clear_context()
            CANCEL.pop(task_id, None)
            PENDING.pop(task_id, None)
            AUTONOMOUS.pop(task_id, None)
            SHELL_MODE.pop(task_id, None)
            MAPS_MODE.pop(task_id, None)
            if not config.keep_device:
                try:
                    device.stop()
                except Exception:
                    pass


#: The app this server runs in; it must end up in front.
AGENT_PACKAGE = "com.phoneagent.app"
#: Foreground owners that are already "the user's own screen".
_HOME_PACKAGES = ("com.miui.home", "com.android.launcher3", "com.android.systemui")


def _hand_back_the_screen(device) -> None:
    """Move the driven app off the phone's display and bring the chat forward.

    Called when a task ends. Movies the app back rather than stopping it, so the
    page the agent was reading is still there when the user asks for it.
    """
    package = ""
    try:
        package = device.current_app() or ""
    except Exception:  # noqa: BLE001
        package = ""
    if package and package not in _HOME_PACKAGES + (AGENT_PACKAGE,):
        pull = getattr(device, "pull_back", None)
        if pull is not None:
            try:
                if pull(package):
                    print(f"[server] moved {package} back to the agent's display")
            except Exception:  # noqa: BLE001
                pass
    try:
        if package == AGENT_PACKAGE:
            # a full-screen viewer over the chat is closed with back
            device.press_back()
        # --activity-single-top: the manifest says singleTop, and without this
        # flag every finished task still opened a second copy of the chat
        # screen -- measured 15 live instances -- and the copy that came up
        # before the transcript was flushed rendered an empty conversation.
        device.shell(f"am start --activity-single-top -n {AGENT_PACKAGE}/.MainActivity")
    except Exception:  # noqa: BLE001
        pass


def _crawl_run(crawl_id: str, argv: list[str], device, crawl: dict, recover=None,
               _device_factory=None, config=None, event=None) -> None:
    """Crawl in-process, on the display the app already owns.

    The crawler is the same one the host runs; the only difference is where its
    device comes from. Nothing here touches display 0, so the phone stays usable
    while a map is being built.
    """
    import contextlib
    import io
    import sys as _sys

    from . import build_map as crawler

    # The crawler works from accessibility trees and never takes a screenshot,
    # so the live view stays empty unless it is given a way to publish one. The
    # hook runs once per screen, on the crawl thread -- never a second reader on
    # the adb transport.
    def publish_crawl_frame() -> None:
        try:
            _publish_frame(device.screenshot())
        except Exception as exc:  # noqa: BLE001 - a picture is optional
            print(f"[crawl] no frame: {type(exc).__name__}: {exc}")

    # Same repair as a task gets: a crawl that starts with a dead connection
    # (or a display that died with it) launches apps onto the user's screen.
    try:
        device.shell("echo ok")
        if hasattr(device, "display_alive") and not device.display_alive():
            raise OSError("display is gone")
    except Exception as exc:  # noqa: BLE001
        print(f"[crawl] device connection is down ({exc}); rebuilding")
        if recover is None:
            crawl["error"] = f"device is not usable: {exc}"
            crawl["state"] = "failed"
            return
        try:
            print(f"[crawl] {recover()}")
        except Exception as exc2:  # noqa: BLE001
            crawl["error"] = f"recovery failed: {exc2}"
            crawl["state"] = "failed"
            return
        device = _device_factory() if _device_factory is not None else device

    real_stdout = _sys.stdout

    class _Tee(io.TextIOBase):
        """Collect output for the caller and keep the app log readable."""

        def write(self, chunk: str) -> int:  # noqa: D102 - io protocol
            for line in str(chunk).splitlines():
                if not line.strip():
                    continue
                # the HTTP server logs every poll into the same stdout; those
                # lines are the caller's own requests, not crawl progress
                if 'HTTP/1.1"' in line or line.startswith('[beat]'):
                    continue
                # the access log prints its tag and its message on separate
                # lines, so the tag survives the filter above on its own
                if line.strip() == "[server]":
                    continue
                with LOCK:
                    crawl["lines"].append(line)
                    del crawl["lines"][:-40]
                # write to the stream captured before the redirect, or this
                # writes into itself until the recursion limit
                real_stdout.write(line + "\n")
            return len(chunk)

    crawl["state"] = "running"
    try:
        with contextlib.redirect_stdout(_Tee()):
            code = crawler.main(argv, device=device,
                                on_screen=publish_crawl_frame,
                                should_stop=event.is_set if event else None)
        crawl["state"] = "done" if code == 0 else "failed"
        crawl["code"] = code
        if code == 0:
            # A pin silently wins over a fresh crawl: say so, or the new map
            # looks like it did nothing.
            # A fresh map does not automatically become the one in use: a pin
            # wins, and so does a map with more screens. Both are silent by
            # design, so say when the map just built is not the one that will
            # be loaded -- otherwise a re-crawl looks like it did nothing.
            try:
                from .core.app_map import AppMap
                from .map_pins import choose, load_pins

                fresh_path = crawl.get("output", "")
                fresh = AppMap(fresh_path)
                loaded = []
                for path in _scan_maps(config):
                    try:
                        loaded.append((path, AppMap(path)))
                    except Exception:  # noqa: BLE001 - one bad map must not stop the note
                        continue
                chosen = choose(loaded, load_pins(config.pins_path),
                                tuple(config.map_dirs[:1]))
                mine = next(
                    (entry for entry in chosen
                     if os.path.basename(entry[0]) == os.path.basename(fresh_path)),
                    None,
                )
                if mine is None:
                    winner = next(
                        (entry for entry in chosen
                         if entry[1].package == fresh.package), None
                    )
                    if winner is not None:
                        with LOCK:
                            crawl["lines"].append(
                                f"注意：新地图 {len(fresh.screens)} 屏，"
                                f"当前实际使用的是 {os.path.basename(winner[0])}"
                                f"（{len(winner[1].screens)} 屏）——"
                                "要换成新的，到「已装地图」里点它「固定使用这份」"
                            )
            except Exception:  # noqa: BLE001 - a note is optional
                pass
            from .apps import forgive_refusal

            base = os.path.dirname(crawl.get("output", "")) or ""
            forgive_refusal(os.path.join(os.path.dirname(base), "refused-apps.txt"),
                            crawl.get("package", ""))
        for line in list(crawl["lines"]):
            match = _REFUSED_LINE.search(line)
            if match:
                from .apps import record_refusal

                base = os.path.dirname(crawl.get("output", "")) or ""
                record_refusal(os.path.join(os.path.dirname(base), "refused-apps.txt"),
                               match.group(1))
                break
    except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
        crawl["state"] = "failed"
        crawl["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        crawl["finished"] = time.time()
        with LOCK:
            _prune(CRAWLS, KEEP_CRAWLS)


def _write_bench_header(map_path: str, summary: dict) -> None:
    """Put a fresh bench result into the map's header, in place."""
    import time as _time

    import yaml as _yaml

    try:
        with open(map_path, encoding="utf-8") as fh:
            data = _yaml.safe_load(fh) or {}
    except OSError:
        return
    data["bench"] = {
        "ops": summary.get("ops", 0),
        "exact": summary.get("exact", 0),
        "same_app": summary.get("same_app", 0),
        "at": _time.strftime("%Y-%m-%dT%H:%M"),
    }
    tmp = f"{map_path}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            _yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
        os.replace(tmp, map_path)
    except OSError:
        pass


def _bench_run(bench_id: str, map_path: str, limit: int, device, result: dict) -> None:
    """Replay sampled operations against the display the map was built on.

    A map measured on the host is measured on display 0, and a map crawled
    inside the app was measured on the virtual display. Those are different
    screens: the same map scored 2/10 on the phone's display and verified on
    every operation on the virtual one. The number only means something next to
    the screen it was taken on.
    """
    from . import bench as bench_mod
    from .core.app_map import AppMap

    try:
        app = AppMap(map_path)
        ops = bench_mod._sample_nav_ops(app, limit, 0)
        rows = []
        for op in ops:
            row = bench_mod.run_one(device, app, op, settle=1.5)
            rows.append(row)
            with LOCK:
                result["rows"] = [
                    {"op": r.op_id, "expected": r.expected, "landed": r.landed,
                     "confidence": r.confidence, "ok": r.replay_ok,
                     "seconds": round(r.seconds, 1), "error": r.error[:60]}
                    for r in rows
                ]
        exact = sum(1 for r in rows if r.exact)
        result["summary"] = {
            "ops": len(rows),
            "exact": exact,
            "same_app": sum(1 for r in rows if r.same_app),
            "replay_ok": sum(1 for r in rows if r.replay_ok),
        }
        result["state"] = "done"
        # Write the score into the map's own header. Without this the card in
        # the app still shows the number from the day it was crawled, and a
        # re-measure looks like it did nothing.
        _write_bench_header(map_path, result["summary"])
    except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
        result["state"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        with LOCK:
            _prune(BENCHES, KEEP_BENCHES)


class Handler(BaseHTTPRequestHandler):
    server_version = "phoneagent/0.1"

    def log_message(self, fmt, *args):  # quieter than the default
        print("[server]", fmt % args)

    def _start_crawl(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        if not config.crawl_dir:
            return self._send(503, {"error": "crawling is disabled"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        package = str(body.get("package", "")).strip()
        if not package:
            return self._send(400, {"error": "package is required"})
        app_name = str(body.get("app", "")).strip() or package
        max_screens = int(body.get("max_screens", 4) or 4)
        max_minutes = float(body.get("max_minutes", 10) or 10)
        # BFS depth: how many taps from the home screen. 3 is the crawler's own
        # default; it was simply never passed through, so the field in the app
        # changed nothing until now.
        max_depth = int(body.get("max_depth", 3) or 3)
        from . import pacing as pacing_mod

        left = pacing_mod.remaining(config.pacing_path, package)
        if left > 0:
            commercial = pacing_mod.is_commercial(package)
            reason = ("几分钟内连着爬几十次，正是风控要抓的行为"
                      if commercial else "刚爬过，稍等一下再来")
            return self._send(429, {
                "error": f"这个 App {reason}，还要等 {int(left // 60) + 1} 分钟。",
                "retry_after": int(left),
            })
        os.makedirs(config.crawl_dir, exist_ok=True)
        pacing_mod.mark(config.pacing_path, package)
        crawl_id = uuid.uuid4().hex[:8]
        output = os.path.join(config.crawl_dir, f"{app_name}.yaml")
        argv = [
            "--package", package,
            "--app", app_name,
            "--max-screens", str(max_screens),
            "--max-minutes", str(max_minutes),
            "--max-depth", str(max_depth),
            "--bench-ops", "4",     # a crawl ends with its own score
            "--no-keep-awake",
            "--output", output,
        ]
        enrich = bool(config.llm_api_key) and not body.get("no_enrich")
        if enrich:
            argv += [
                "--llm-api-key", config.llm_api_key,
                "--llm-api-base", config.llm_api_base,
                "--llm-model", config.llm_model,
            ]
        else:
            argv += ["--no-classify", "--no-enrich"]
        crawl = {"state": "queued", "package": package, "app": app_name,
                 "output": output, "lines": [], "started": time.time(),
                 # set by POST /crawl/<id>/cancel; the loop checks it between
                 # branches and keeps what it has
                 "cancel": threading.Event(),
                 }
        with LOCK:
            CRAWLS[crawl_id] = crawl
        hands_off = bool(body.get("display") == 0) and config.hands_off_factory is not None
        crawl_device = config.hands_off_factory() if hands_off else config.device_factory()
        threading.Thread(
            target=_crawl_run,
            args=(crawl_id, argv, crawl_device, crawl, config.recover,
                  config.device_factory, config, crawl.get("cancel")),
            daemon=True,
        ).start()
        print(f"[server] crawl {crawl_id}: {package}")
        self._send(202, {"id": crawl_id, "output": output})

    def _test_llm(self) -> None:
        """One tiny call, so "did I type the key right" is answerable before a task.

        Switching provider used to be trial and error: the first feedback was a
        whole task failing with HTTP 401 somewhere in the log.
        """
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        config = self.server.config  # type: ignore[attr-defined]
        api_key = str(body.get("api_key") or "") or getattr(config, "llm_api_key", "")
        api_base = str(body.get("api_base") or "") or getattr(config, "llm_api_base", "")
        model = str(body.get("model") or "") or getattr(config, "llm_model", "")
        vision = str(body.get("vision_model") or "") or model
        if not api_key:
            return self._send(200, {"ok": False, "detail": "没有填 API Key"})
        try:
            # make_llm, not OpenAICompatClient directly: the protocol depends on
            # the base URL, and a connection test that speaks the wrong one
            # reports a 404 from a perfectly good endpoint.
            client = make_llm(api_key, api_base, model, vision)
            reply = client.text("", "请只回复两个字：可用")
        except Exception as exc:  # noqa: BLE001 - reported to the settings dialog
            return self._send(200, {"ok": False, "detail": f"{type(exc).__name__}: {exc}"})
        return self._send(200, {"ok": True, "detail": str(reply)[:80] or "(空回复)"})

    def _drain_body(self) -> None:
        """Read and discard a request body so the connection stays usable."""
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)

    def _warmup(self) -> None:
        """Launch the apps that have a map, so the first task is not a cold start.

        Measured: the first 小红书 task after the app starts took nine steps and
        57k tokens, the same task warm took four steps and 13k. Nothing was
        wrong with it; the app simply had to be started and drawn while the
        model watched. Doing that in the background, on the agent's own display
        where nobody sees it, makes the first task cost what the later ones do.
        """
        # Never warm up while work is running: the warm-up launches apps on the
        # same display the crawl or task is driving. Measured today, it started
        # the clock and Settings underneath a 小红书 crawl, the crawl's taps then
        # landed in those apps, and the app was killed as a misbehaving process.
        for crawl in list(CRAWLS.values()):
            if crawl.get("state") in ("queued", "running"):
                return self._send(409, {"error": "busy", "warmed": []})
        for task in list(TASKS.values()):
            if task.get("state") in ("queued", "running"):
                return self._send(409, {"error": "busy", "warmed": []})

        from .core.app_map import AppMap
        from .map_pins import choose, load_pins

        config = self.server.config  # type: ignore[attr-defined]
        packages: list = []
        try:
            pins = load_pins(config.pins_path)
            paths = (_scan_maps(config) if config.map_dirs else config.map_paths)
            loaded = []
            for path in paths:
                try:
                    loaded.append((path, AppMap(path)))
                except Exception:  # noqa: BLE001 - skip a broken map
                    continue
            skip = set(getattr(config, "user_display_packages", ()) or ())
            for _path, app_map in choose(loaded, pins, tuple(config.map_dirs[:1])):
                # An app that only runs on the user's own display cannot be
                # warmed without opening it in front of them, so leave it out:
                # measured, warming XHS on the agent's display changed nothing
                # about its first task (still ten steps) because the task runs
                # it on display 0.
                if app_map.package and app_map.package not in packages \
                        and app_map.package not in skip:
                    packages.append(app_map.package)
        except Exception as exc:  # noqa: BLE001 - warming up is best effort
            print(f"[warmup] could not list maps: {exc}")
            packages = []

        if not packages:
            return self._send(200, {"warmed": []})

        def run() -> None:
            device = None
            for package in packages[:3]:
                try:
                    device = config.device_factory()
                    device.launch(package)
                except Exception as exc:  # noqa: BLE001 - keep going
                    print(f"[warmup] {package}: {exc}")
            # Leave the display clean. What makes the first task faster is the
            # process being loaded, not the app standing in front: the app left
            # in front was inherited by whatever ran next. A crawl started a
            # minute after a warm-up spent its first minutes unwinding the
            # calculator and Settings.
            try:
                if device is not None:
                    device.press_home()
                    print("[warmup] display returned home; the processes stay warm")
            except Exception as exc:  # noqa: BLE001 - housekeeping
                print(f"[warmup] could not go home: {exc}")
            print(f"[warmup] done: {packages[:3]}")

        threading.Thread(target=run, daemon=True).start()
        return self._send(202, {"warmed": packages[:3]})

    def _debug_type(self) -> None:
        """POST /type {"text": "...", "display": 0} -- exercise the input path."""
        config = self.server.config  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        text = str(body.get("text", ""))
        target = body.get("display", None)
        try:
            device = (config.hands_off_factory()
                      if target == 0 and config.hands_off_factory is not None
                      else config.device_factory())
            ok = bool(device.input_text(text))
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        return self._send(200, {"ok": ok, "text": text})

    def _start_bench(self) -> None:
        config = self.server.config  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        map_path = str(body.get("map", "")).strip()
        if not map_path or not os.path.exists(map_path):
            return self._send(400, {"error": "map not found"})
        limit = int(body.get("limit", 8) or 8)
        bench_id = uuid.uuid4().hex[:8]
        result = {"state": "running", "map": map_path, "rows": [], "summary": {}}
        with LOCK:
            BENCHES[bench_id] = result
        threading.Thread(
            target=_bench_run,
            args=(bench_id, map_path, limit, config.device_factory(), result),
            daemon=True,
        ).start()
        print(f"[server] bench {bench_id}: {os.path.basename(map_path)}")
        self._send(202, {"id": bench_id})

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        self._guarded(self._post)

    def do_GET(self):  # noqa: N802
        self._guarded(self._get)

    def _guarded(self, handler) -> None:
        """Answer with the error, not with a closed socket.

        A typo in a field name used to raise inside the handler, which closes the
        connection: the caller saw "RemoteDisconnected" and had no way to learn
        what went wrong. Every failure now comes back as a 500 with its message.
        """
        server = getattr(self, "server", None)
        config = getattr(server, "config", None) if server is not None else None
        token = getattr(config, "token", "") if config is not None else ""
        if token and not self._authorized(token):
            return self._send(401, {"error": "unauthorized: missing or wrong token"})
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            return                       # the client went away; nothing to say
        except Exception as exc:          # noqa: BLE001 - that is the point
            import traceback

            traceback.print_exc()
            try:
                self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
            except Exception:             # noqa: BLE001 - headers may be out already
                pass

    def _authorized(self, token: str) -> bool:
        """Header (the app) or query parameter (curl during development)."""
        supplied = self.headers.get("X-Agent-Token", "")
        if not supplied:
            from urllib.parse import parse_qs, urlparse

            supplied = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
        return hmac.compare_digest(str(supplied), token)

    def _post(self):
        parts = self.path.strip("/").split("/")
        if parts == ["debug", "spill"]:
            # Test hook, not a feature: move a package's task from the agent's
            # display to the phone's, which is what some apps do to themselves
            # (a NEW_TASK page the system hosts on the default display). It is
            # the only way to exercise the pull-back path on demand instead of
            # waiting for an app to escape during a task. Same token as every
            # other endpoint, and it only moves a task between displays.
            config = self.server.config  # type: ignore[attr-defined]
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            package = str(body.get("package", "")).strip()
            if not package:
                return self._send(400, {"error": "package is required"})
            try:
                device = config.device_factory()
            except Exception as exc:  # noqa: BLE001
                return self._send(503, {"error": f"{type(exc).__name__}: {exc}"})
            if device is None:
                # The factory answers None while the display is being rebuilt
                # (a reconnect right after a move does exactly that).
                return self._send(503, {"error": "no device yet; retry"})
            own = getattr(device, "display_id", None)
            task = device.task_on(0, package)
            if task:
                return self._send(200, {"package": package, "task": task,
                                        "moved": False,
                                        "note": "already on the phone's display"})
            task = device.task_on(own, package) if own is not None else 0
            if not task:
                return self._send(404, {"error": "no task for that package",
                                        "display": own})
            try:
                device.shell(f"am display move-stack {task} 0")
            except Exception as exc:  # noqa: BLE001
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
            time.sleep(0.6)
            return self._send(200, {"package": package, "task": task,
                                    "from_display": own, "moved": True,
                                    "on_phone": bool(device.task_on(0, package))})

        if parts == ["risk"]:
            from . import risk as risk_mod

            config = self.server.config  # type: ignore[attr-defined]
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            package = str(body.get("package", "")).strip()
            if not package:
                return self._send(400, {"error": "package is required"})
            if body.get("clear"):
                data = risk_mod.load(config.risk_path)
                data.pop(package, None)
                risk_mod.save(config.risk_path, data)
                return self._send(200, {"risks": data})
            entry = risk_mod.mark(config.risk_path, package,
                                  str(body.get("note", "") or "manual mark"))
            return self._send(200, {"package": package, "entry": entry})

        if parts == ["maps", "pin"] or parts == ["maps", "unpin"]:
            from .map_pins import load_pins, save_pins

            config = self.server.config  # type: ignore[attr-defined]
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            package = str(body.get("package", "")).strip()
            if not package:
                return self._send(400, {"error": "package is required"})
            pins = load_pins(config.pins_path)
            if parts == ["maps", "unpin"]:
                pins.pop(package, None)
            else:
                pins[package] = str(body.get("file", "")).strip()
            save_pins(config.pins_path, pins)
            return self._send(200, {"pins": pins})

        if parts == ["maps", "delete"]:
            config = self.server.config  # type: ignore[attr-defined]
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            name = os.path.basename(str(body.get("file", "")).strip())
            crawl_dir = os.path.join(os.path.dirname(config.memory_path), "crawls") \
                if config.memory_path else ""
            path = os.path.join(crawl_dir, name)
            if not crawl_dir or not os.path.exists(path):
                return self._send(404, {"error": "no such crawled map"})
            try:
                os.remove(path)
            except OSError as exc:
                return self._send(500, {"error": str(exc)})
            # a pin pointing at a file that is gone would silently do nothing
            from .map_pins import load_pins, save_pins

            pins = load_pins(config.pins_path)
            for package, wanted in list(pins.items()):
                if wanted == name:
                    pins.pop(package)
            save_pins(config.pins_path, pins)
            return self._send(200, {"deleted": name})

        if parts == ["crawl"]:
            return self._start_crawl()
        if parts == ["test-llm"]:
            return self._test_llm()
        if parts == ["warmup"]:
            self._drain_body()
            return self._warmup()
        if len(parts) == 3 and parts[0] == "crawl" and parts[2] == "cancel":
            with LOCK:
                crawl = CRAWLS.get(parts[1])
                if crawl is None:
                    return self._send(404, {"error": "unknown crawl"})
                event = crawl.get("cancel")
                if event is None:
                    return self._send(409, {"error": "this crawl cannot be cancelled"})
                event.set()
            return self._send(200, {"cancelled": True})
        if parts == ["type"]:
            return self._debug_type()
        if parts == ["bench"]:
            return self._start_bench()
        if len(parts) == 3 and parts[0] == "task" and parts[2] == "cancel":
            flag = CANCEL.get(parts[1])
            if flag is None:
                return self._send(404, {"error": "unknown or finished task"})
            flag.set()
            _update(parts[1], state="cancelling")
            return self._send(200, {"ok": True})
        if len(parts) == 3 and parts[0] == "task" and parts[2] == "answer":
            slot = PENDING.get(parts[1])
            if slot is None:
                return self._send(404, {"error": "unknown or finished task"})
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            slot["answer"] = str(body.get("text", "")).strip()
            slot["event"].set()
            return self._send(200, {"ok": True})
        if not self.path.startswith("/task"):
            return self._send(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        text = str(body.get("text", "")).strip()
        if not text:
            return self._send(400, {"error": "empty task"})

        task_id = uuid.uuid4().hex[:8]
        AUTONOMOUS[task_id] = bool(body.get("autonomous", False))
        # The shell is available by default: it is how an agent opens an app or
        # reads a setting without tapping its way there. Callers can still turn
        # it off for a task with "shell": false.
        MAPS_MODE[task_id] = bool(body.get("maps", True))
        # Pure-GUI tasks get no shell. A model that loses the screen reaches for
        # `am start`/`dumpsys` as a fallback, and those commands run outside the
        # accessibility layer the mode is supposed to be about: measured on
        # 小红书, half the steps of a GUI run were shell, one of them launching
        # the app onto the user's own display and starting a pull-back cycle.
        SHELL_MODE[task_id] = bool(body.get("shell", True)) and MAPS_MODE[task_id]
        CANCEL[task_id] = threading.Event()
        PENDING[task_id] = {"question": "", "answer": None, "event": threading.Event()}
        print(f"[server] task {task_id}: {text[:60]}")
        _update(task_id, state="queued", steps=[], answer="", frame="")
        config = self.server.config  # type: ignore[attr-defined]
        hands_off = bool(body.get("display") == 0) if "display" in body else False
        if hands_off and config.hands_off_factory is None:
            return self._send(400, {"error": "托管模式不可用（设备不支持）"})
        _update(task_id, hands_off=hands_off)
        threading.Thread(target=_run, args=(task_id, text, config, hands_off),
                         daemon=True).start()
        self._send(200, {"id": task_id})

    def _get(self):
        path, _, query = self.path.partition("?")
        parts = path.strip("/").split("/")
        if parts == ["web"]:
            # Debug surface for the page the app hosts: text, links, title.
            from .web import WebPage

            what = query or "text"
            page = WebPage()
            import urllib.parse as _up

            if what.startswith("open="):
                return self._send(200, {"opened": page.open(_up.unquote(what[5:]))})
            if what == "blocked":
                return self._send(200, {"blocked": page.blocked()})
            if what == "network":
                return self._send(200, {"network": page.network()})
            if what == "install":
                return self._send(200, {"installed": page.install_capture()})
            if what == "links":
                return self._send(200, {"links": page.links()})
            if what == "title":
                return self._send(200, {"title": page.title()})
            if what.startswith("js="):
                import urllib.parse as _up

                return self._send(200, {"result": page.eval(_up.unquote(what[3:]))})
            if what.startswith("click="):
                return self._send(200, {"clicked": page.click_text(_up.unquote(what[6:]))})
            return self._send(200, {"url": page.url(), "text": page.text()})
        if parts == ["type"]:
            # Debugging hook for the input path: typing has no visible result in
            # a screenshot until a field takes the text, and Chinese goes through
            # a different route than ASCII.
            config = self.server.config  # type: ignore[attr-defined]
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            text = str(body.get("text", ""))
            target = body.get("display", None)
            try:
                device = config.hands_off_factory() if target == 0 and \
                    config.hands_off_factory is not None else config.device_factory()
                ok = bool(device.input_text(text))
            except Exception as exc:  # noqa: BLE001 - reported to the caller
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
            return self._send(200, {"ok": ok, "text": text})
        if parts == ["dump"]:
            # ?display=0 reads the phone's own screen; the default reads the
            # agent's display. Which one is worth debugging depends on the mode
            # the task ran in.
            if query == "display=0":
                config = self.server.config  # type: ignore[attr-defined]
                if config.hands_off_factory is not None:
                    try:
                        return self._send(200, {"xml": config.hands_off_factory().dump_ui()})
                    except Exception as exc:  # noqa: BLE001
                        return self._send(500, {"error": str(exc)})
                return self._send(400, {"error": "hands-off not available"})
            # The accessibility tree the agent sees, as text. Debugging a task
            # without this means guessing from screenshots.
            device = self.server.config.device_factory()  # type: ignore[attr-defined]
            try:
                xml = device.dump_ui()
            except Exception as exc:  # noqa: BLE001
                return self._send(503, {"error": f"{type(exc).__name__}: {exc}"})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(xml.encode())))
            self.end_headers()
            self.wfile.write(xml.encode())
            return
        if parts == ["usage"]:
            from .usage import format_report, summary

            days = 7
            if query.startswith("days="):
                try:
                    days = max(1, min(90, int(query[5:])))
                except ValueError:
                    days = 7
            data = summary(self.server.config.usage_path, days=days)  # type: ignore[attr-defined]
            data["report"] = format_report(data)
            return self._send(200, data)
        if parts == ["frame"]:
            # The last picture any driver published -- a task round or a crawl
            # screen. Never a fresh capture: the adb transport is one channel
            # owned by whoever is driving, and a viewer polling for pictures
            # breaks it (measured: torn PNGs, then SSLEOFError).
            latest = LAST_FRAME.get("data") or ""
            if not latest:
                for task in TASKS.values():
                    frame = task.get("frame") or ""
                    if len(frame) > len(latest):
                        latest = frame
            if not latest:
                return self._send(503, {"error": "no frame yet"})
            png = base64.b64decode(latest)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
            return
        if parts == ["dump"]:
            # The accessibility tree the agent sees, as text. Debugging a task
            # without this means guessing from screenshots.
            device = self.server.config.device_factory()  # type: ignore[attr-defined]
            try:
                xml = device.dump_ui()
            except Exception as exc:  # noqa: BLE001
                return self._send(503, {"error": f"{type(exc).__name__}: {exc}"})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(xml.encode())))
            self.end_headers()
            self.wfile.write(xml.encode())
            return
        if parts == ["maps"]:
            # What the agent would load right now, why, and where each file
            # came from -- the difference between "I crawled it" and "it is in
            # use" is a pin, so say which is which.
            from .core.app_map import AppMap
            from .map_pins import choose, load_pins

            config = self.server.config  # type: ignore[attr-defined]
            pins = load_pins(config.pins_path)
            paths = (_scan_maps(config) if config.map_dirs else config.map_paths)
            loaded, broken = [], []
            for path in paths:
                try:
                    loaded.append((path, AppMap(path)))
                except Exception as exc:  # noqa: BLE001 - a bad map is worth seeing
                    broken.append({"file": os.path.basename(path),
                                   "error": str(exc)[:120]})
            crawl_dir = os.path.join(os.path.dirname(config.memory_path), "crawls") \
                if config.memory_path else ""
            # How many *crawled* maps exist per package, so the app can say
            # "还有 N 张": choose() shows one and the rest would otherwise be
            # invisible. Shipped maps are excluded -- they come back on every
            # start and are not something the user can act on.
            crawl_root = os.path.join(os.path.dirname(config.memory_path), "crawls") \
                if config.memory_path else ""
            per_package: dict = {}
            for _path, _app_map in loaded:
                if crawl_root and os.path.dirname(_path) == crawl_root:
                    per_package[_app_map.package] = per_package.get(_app_map.package, 0) + 1

            rows = []
            shipped = tuple(config.map_dirs[:1])
            for path, app_map in choose(loaded, pins, shipped):
                rows.append({
                    "file": os.path.basename(path),
                    "dir": os.path.dirname(path),
                    "app": app_map.app_name,
                    "package": app_map.package,
                    "screens": len(app_map.screens),
                    "operations": len(app_map.build_operations()),
                    "bytes": os.path.getsize(path),
                    "crawled_at": getattr(app_map, "crawled_at", ""),
                    "bench": getattr(app_map, "bench", {}),
                    "alternatives": max(
                        0,
                        per_package.get(app_map.package, 0)
                        - (1 if crawl_root and os.path.dirname(path) == crawl_root else 0),
                    ),
                    "coverage": getattr(app_map, "coverage", {}),
                    "modified": int(os.path.getmtime(path)),
                    "pinned": pins.get(app_map.package) == os.path.basename(path),
                    "deletable": bool(crawl_dir) and os.path.dirname(path) == crawl_dir,
                    # a shipped map is re-extracted on every start, so its
                    # timestamp says "just now" and means nothing
                    "source": "crawl" if crawl_dir and os.path.dirname(path) == crawl_dir
                              else "asset",
                })
            return self._send(200, {"maps": rows + broken,
                                    "count": len(choose(loaded, pins))})
        if parts == ["crawl", "apps"]:
            # Ask before crawling: a singleTask launcher activity can never be
            # placed on this display, and the app list is where that is visible.
            from .apps import catalog

            config = self.server.config  # type: ignore[attr-defined]
            # "has a map" means either a shipped one or a crawl: the crawl
            # output lives in its own directory, and passing only the parent
            # made every app look uncrawled right after a crawl finished.
            dirs = [d for d in [config.crawl_dir, *list(config.map_dirs)] if d]
            base = os.path.dirname(config.crawl_dir) if config.crawl_dir else ""
            return self._send(200, {
                "apps": catalog(dirs, os.path.join(base, "refused-apps.txt") if base else ""),
            })
        if len(parts) == 2 and parts[0] == "bench":
            with LOCK:
                bench = BENCHES.get(parts[1])
                if bench is None:
                    return self._send(404, {"error": "unknown bench"})
            return self._send(200, bench)
        if len(parts) == 2 and parts[0] == "crawl":
            with LOCK:
                crawl = CRAWLS.get(parts[1])
                if crawl is None:
                    return self._send(404, {"error": "unknown crawl"})
                # "cancel" is a threading.Event: not JSON, and the client has
                # no use for it
                payload = {k: v for k, v in crawl.items()
                           if k not in ("lines", "cancel")}
                payload["lines"] = list(crawl["lines"])[-12:]
            return self._send(200, payload)
        if parts == ["tasks"]:
            with LOCK:
                summary = {
                    task_id: {
                        "state": task.get("state", ""),
                        "question": task.get("question", ""),
                        "answer": task.get("answer", ""),
                    }
                    for task_id, task in TASKS.items()
                }
            return self._send(200, {"tasks": summary})
        if len(parts) != 2 or parts[0] != "task":
            return self._send(404, {"error": "not found"})
        with LOCK:
            task = TASKS.get(parts[1])
        if task is None:
            return self._send(404, {"error": "unknown task"})
        self._send(200, task)


def start_server(port: int, config: ServerConfig) -> ThreadingHTTPServer:
    """Bind the port and serve on a daemon thread. Returns the server."""
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.config = config  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True, name="pa-http").start()
    return server


def make_llm(
    api_key: str,
    api_base: str = "https://openrouter.ai/api/v1",
    model: str = "qwen/qwen3.8-flash",
    vision_model: str = "",
    mock: str = "",
    usage_path: str = "",
    usage_sink=None,
):
    """A real LLM, or a scripted one for plumbing tests.

    ``mock`` is a ``;``-separated list of vision replies, so the whole loop can
    be exercised without network access or a key.
    """
    if mock:
        from .core.llm import MockLLM

        replies = [part.strip() for part in mock.split(";") if part.strip()]
        # the router wants a text reply first; anything that is not a known
        # OP:/FINISH: line falls through to the vision loop
        return MockLLM(text_responses=["keep going"], vision_responses=replies)
    # DeepSeek serves an Anthropic-protocol endpoint next to its OpenAI one;
    # the base URL is the only thing that distinguishes them.
    if "/anthropic" in api_base.rstrip("/") or api_base.rstrip("/").endswith("/messages"):
        from .core.llm import AnthropicClient

        return AnthropicClient(
            api_key=api_key, api_base=api_base, model=model,
            vision_model=vision_model or model,
            usage_path=usage_path, usage_sink=usage_sink,
        )

    from .core.llm import OpenAICompatClient

    return OpenAICompatClient(
        api_key=api_key, api_base=api_base, model=model,
        vision_model=vision_model or model,
        # The reasoning switch is OpenRouter's; the client only sends it there.
        disable_reasoning=True,
        usage_path=usage_path, usage_sink=usage_sink,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY", ""))
    parser.add_argument("--api-base", default="https://openrouter.ai/api/v1")
    parser.add_argument("--model", default="qwen/qwen3.8-flash")
    parser.add_argument("--start-app", default="com.android.settings",
                        help="app to put on the fresh virtual display so it has a frame")
    args = parser.parse_args(argv)
    if not args.api_key:
        parser.error("pass --api-key or set OPENROUTER_API_KEY")

    from .devices.virtual import VirtualDisplayDevice

    config = ServerConfig(
        device_factory=lambda: VirtualDisplayDevice(serial=args.serial),
        llm_factory=lambda usage_sink=None: make_llm(
            args.api_key, args.api_base, args.model, usage_sink=usage_sink),
        start_app=args.start_app,
        keep_device=False,
        # the host has no accessibility service, so there is no tree to prompt
        # with; uiautomator cannot see a virtual display either
        include_ui_text=False,
    )
    server = start_server(args.port, config)
    print(f"listening on http://127.0.0.1:{args.port} (adb reverse tcp:{args.port} tcp:{args.port})")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
