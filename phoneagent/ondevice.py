"""On-device entry points called from Kotlin through Chaquopy.

After pairing, the RSA identity lives in the app's private storage. This module
uses it to reach adbd over loopback TLS and, from there, run the shell-side
helper that creates the virtual display. The phone drives itself; no computer
is in the loop.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass
from typing import Optional
import os
import threading
import time

CERT_FILE = "pair-cert.pem"
KEY_FILE = "pair-key.pem"

#: The running virtual display, kept alive between calls from Kotlin.
_device = None
_client = None
#: A device that drives the phone's own screen, for apps that cannot run on a
#: virtual display (京东, 淘宝, 备忘录…). Only used when a task asks for it.
_hands_off_device = None


def save_identity(directory: str, cert_pem: bytes, key_pem: bytes) -> str:
    """Persist the paired identity so later runs can reuse it."""
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, CERT_FILE), "wb") as fh:
        fh.write(cert_pem)
    with open(os.path.join(directory, KEY_FILE), "wb") as fh:
        fh.write(key_pem)
    return directory


def has_identity(directory: str) -> bool:
    return os.path.exists(os.path.join(directory, CERT_FILE)) and os.path.exists(
        os.path.join(directory, KEY_FILE)
    )


def _connect(directory: str, port: int, host: str = "127.0.0.1"):
    """Open (or reuse) the adbd connection that authenticates with our key."""
    global _client
    if _client is not None:
        return _client
    from .devices.adbclient import AdbClient

    with open(os.path.join(directory, CERT_FILE), "rb") as fh:
        cert = fh.read()
    with open(os.path.join(directory, KEY_FILE), "rb") as fh:
        key = fh.read()
    client = AdbClient(cert, key)
    client.connect(host, port)
    _client = client
    return client


def shell(directory: str, port: int, command: str = "id", host: str = "127.0.0.1") -> str:
    """Connect to adbd with the paired identity and run one command."""
    client = _connect(directory, port, host)
    output = client.shell(command)
    return f"{output.strip()}\n--\n{client.banner}"


def probe(directory: str, port: int) -> str:
    """Diagnostic entry point: paired? reachable? shell works?"""
    if not has_identity(directory):
        return "no paired identity stored yet"
    try:
        return shell(directory, port)
    except Exception as exc:  # noqa: BLE001 - surfaced to logcat
        return f"{type(exc).__name__}: {exc}"


# -- virtual display ---------------------------------------------------------


#: Everything start_display needs, kept so the display can be rebuilt after the
#: adb connection dies (adbd closes it, and nothing in the app used to notice).
_display_args: dict = {}


def start_display(
    directory: str,
    port: int,
    helper_jar: str,
    width: int = 1280,
    height: int = 2772,
    dpi: int = 520,
    app: str = "",
    host: str = "127.0.0.1",
) -> str:
    """Push the helper, create the virtual display and optionally launch an app.

    ``helper_jar`` is the extracted asset; adbd copies it to /data/local/tmp
    because only the shell user may run ``app_process`` with a hostable display.
    """
    global _device
    _display_args.update(
        directory=directory, port=port, helper_jar=helper_jar,
        width=width, height=height, dpi=dpi, app=app, host=host,
    )
    if _device is not None and _device.is_ready():
        return f"already running display={_device.display_id}"

    from .devices.transports import SelfAdbTransport
    from .devices.virtual import HELPER_JAR, VirtualDisplayDevice

    device = _device
    if device is not None:
        # Rebuild in place, on the same object. Two reasons:
        #  - the display the helper owns must be released, or it stays in
        #    dumpsys forever (300+ were, which is what made the phone crawl);
        #  - a running task holds this object. Swapping it for a new one left
        #    that task with a closed transport ("AdbError: connection closed")
        #    the moment anything rebuilt the display under it.
        try:
            device.stop()
            device.start()
        except Exception:  # noqa: BLE001 - its transport is dead too
            device = None

    if device is None:
        client = _connect(directory, port, host)
        transport = SelfAdbTransport(client)
        with open(helper_jar, "rb") as fh:
            transport.write_file(fh.read(), HELPER_JAR)
        device = VirtualDisplayDevice(
            transport=transport, width=width, height=height, dpi=dpi, name="phoneagent"
        )
        device.start()
    display_id = int(device.display_id or 0)
    # A display with nothing on it is useless: the frame reader either stays
    # empty or hands back the mirrored phone screen, and current_app() is
    # unknown. Note the launcher is not an option -- MIUI's refuses to start on
    # a secondary display, `am start --display N` simply creates no task.
    target = app or device.home_package()
    for candidate in (target, FALLBACK_APP):
        if not candidate:
            continue
        try:
            device.launch(candidate)
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
        if _wait_for_content(device):
            break
    _device = device
    return (
        f"display={display_id} size={width}x{height} dpi={dpi}"
        f" app={device.current_app()}"
    )


def _hands_off():
    """The phone's own screen as a device, created once and reused.

    Apps whose launcher hands over to a singleTask activity (京东's
    MainFrameActivity, 淘宝's SearchDoorActivity) can never be placed on a
    virtual display: the system puts that activity on display 0 no matter what
    we ask for. The only way to drive them is to ask the user for the phone and
    work where they can see it.
    """
    global _hands_off_device
    if _device is None:
        raise RuntimeError("the display has not started yet")
    if _hands_off_device is None:
        from .devices.virtual import VirtualDisplayDevice

        _hands_off_device = VirtualDisplayDevice(transport=_device.transport,
                                                 hands_off=True)
        _hands_off_device.start()
    return _hands_off_device


def probe_display(directory: str, port: int, helper_jar: str, host: str = "127.0.0.1") -> str:
    """Can this Android build host a virtual display at all?

    Creates a 2x2 display and reports the verdict. Cheap enough to run before
    the real one, and it turns "the helper did not report a display" into a
    reason: an Android 13 image refuses a PUBLIC display without
    CAPTURE_VIDEO_OUTPUT or a MediaProjection token, and that is worth saying
    out loud instead of retrying.
    """
    from .devices.transports import SelfAdbTransport
    from .devices.virtual import HELPER_JAR

    client = _connect(directory, port, host)
    transport = SelfAdbTransport(client)
    with open(helper_jar, "rb") as fh:
        transport.write_file(fh.read(), HELPER_JAR)
    from .devices.virtual import HELPER_CLASS

    command = (
        f"CLASSPATH={HELPER_JAR} app_process /system/bin "
        f"--nice-name=pa-probe {HELPER_CLASS} 2 2 1 probe"
    )
    proc = transport.spawn(command)
    verdict = ""
    try:
        for _ in range(40):
            line = proc.read_line(25)
            if not line:
                break
            if line.startswith(("PROBE ok", "PROBE error")):
                verdict = line
                break
    finally:
        try:
            proc.close() if hasattr(proc, "close") else None
        except Exception:  # noqa: BLE001
            pass
    return verdict or "PROBE error: the helper produced no verdict"


#: Proved to accept a secondary display; used when the requested app does not.
FALLBACK_APP = "com.android.settings"


def _wait_for_content(device, timeout: float = 4.0) -> bool:
    """True once something is actually resumed on the display."""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if device.current_app() != "unknown":
            return True
        time.sleep(0.4)
    return False


def display_status() -> str:
    if _device is None:
        return "not running"
    if not _device.is_ready():
        return "helper exited"
    return f"display={_device.display_id} app={_device.current_app()}"


def stop_display() -> str:
    global _device, _hands_off_device
    if _hands_off_device is not None:
        try:
            _hands_off_device.stop()
        except Exception:  # noqa: BLE001
            pass
        _hands_off_device = None
    if _device is None:
        return "not running"
    _device.stop()
    _device = None
    return "stopped"


def display_shot(path: str) -> str:
    """Write one PNG frame to ``path`` and return its size."""
    if _device is None or not _device.is_ready():
        return "not running"
    data = _device.screenshot(path)
    return f"{path} {len(data)} bytes"


def display_tap(x: int, y: int) -> str:
    if _device is None or not _device.is_ready():
        return "not running"
    _device.tap(int(x), int(y))
    return f"tap {x},{y} on display={_device.display_id}"


def display_launch(package: str) -> str:
    if _device is None or not _device.is_ready():
        return "not running"
    _device.launch(package)
    return f"launched {package} on display={_device.display_id}"


def display_dump() -> str:
    """Accessibility tree of the virtual display."""
    if _device is None or not _device.is_ready():
        return "<hierarchy error=\"not running\"></hierarchy>"
    return _device.dump_ui()


def display_scroll(direction: str = "down") -> str:
    """Scroll the virtual display (accessibility action, swipe as fallback)."""
    if _device is None or not _device.is_ready():
        return "not running"
    try:
        ok = _device.scroll(direction)
    except Exception as exc:  # noqa: BLE001 - surfaced to logcat
        return f"scroll failed: {exc}"
    return f"scrolled {direction}" if ok else f"could not scroll {direction}"


def display_input_text(text: str) -> str:
    """Type into the focused (or first editable) field on the display."""
    if _device is None or not _device.is_ready():
        return "not running"
    try:
        ok = _device.input_text(text)
    except Exception as exc:  # noqa: BLE001 - surfaced to logcat
        return f"input failed: {exc}"
    return f"typed {text!r}" if ok else "no editable field"


def display_tap_text(text: str) -> str:
    """Click the control labelled ``text``.

    Prefers the accessibility action; falls back to tapping its coordinates.
    """
    if _device is None or not _device.is_ready():
        return "not running"
    if _device.click_text(text):
        return f"clicked {text!r} via accessibility"
    bounds = _device.find_text(text)
    if bounds is None:
        return f"no control labelled {text!r}"
    left, top, right, bottom = bounds
    _device.tap((left + right) // 2, (top + bottom) // 2)
    return f"tapped {text!r} at {(left + right) // 2},{(top + bottom) // 2}"


# -- in-process task server --------------------------------------------------

_server = None
#: The arguments serve() was called with, so a rebuild can bring the server back.
_serve_args: dict = {}
#: The heartbeat thread is started once per process; a rebuild must not add one.
_heartbeat_started = False


def _known_apps() -> dict:
    """Every launchable app, name -> package, for the agent's launch tool."""
    from .apps import known_apps

    try:
        mapping = known_apps()
    except Exception:  # noqa: BLE001 - a missing list must not stop the server
        return {}
    if mapping:
        names = ", ".join(sorted(mapping)[:14])
        print(f"[apps] {len(mapping)} apps known by name: {names}")
    return mapping


#: Written by the app before the agent starts; both sides read the same file.
TOKEN_FILE = "agent-token"

#: Apps that must be driven on the phone's own display.
#:
#: 小红书 used to be here: its search page is a NEW_TASK activity, so the
#: system hosts it on the default display even when the app runs on a virtual
#: one (measured: tapping 搜索 at (1192,224) on display 644 moved the whole
#: 小红书 task to display 0). What changed is that the task can be moved back:
#: `am display move-stack <task> <display>` returns it, and the search page
#: then reads and types on the virtual display (its EditText,
#: id=mSearchToolBarEt, is a real accessibility node there -- the earlier
#: "no editable node" reading was taken from a page that had already moved).
#: Driving 小红书 on the phone's screen hides PhoneGo behind it, which is
#: exactly what the app is for, so the list is empty and the agent pulls an
#: escaped task back instead (VirtualDisplayDevice.pull_back).
USER_DISPLAY_PACKAGES = ()


def _read_token_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _start_heartbeat() -> None:
    """Proof that the interpreter is still running.

    A frozen interpreter and a frozen request handler look identical from
    outside: the socket stays open, nothing answers. A line every two seconds
    is enough to tell them apart, and it is the only channel that survives
    when the task server itself is the thing that stopped.
    """
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "phoneagent-beat.txt")

    def beat() -> None:
        count = 0
        while True:
            time.sleep(2.0)
            count += 1
            # The file is the reliable channel: if stdout stops but the file
            # keeps growing, the interpreter is fine and only the print path is
            # stuck. If both stop, every thread is frozen. Once every 30 s is
            # enough resolution and is cheap enough to leave running.
            if count % 15 == 0:
                try:
                    with open(path, "w") as fh:
                        fh.write(f"{count * 2}\n")
                except Exception:  # noqa: BLE001 - a heartbeat must never fail
                    pass
                print(f"[beat] {count * 2}s", flush=True)

    global _heartbeat_started
    if _heartbeat_started:
        return
    _heartbeat_started = True
    threading.Thread(target=beat, daemon=True).start()


def serve(
    port: int = 8765,
    api_key: str = "",
    api_base: str = "https://openrouter.ai/api/v1",
    model: str = "qwen/qwen3.8-flash",
    vision_model: str = "",
    mock: str = "",
    maps_dir: str = "",
    memory_path: str = "",
    token: str = "",
    max_rounds: int = 0,
) -> str:
    """Run the task server inside the app, on the display it already owns.

    MainActivity then talks plain HTTP to 127.0.0.1 with no ``adb reverse``,
    no host process and no cable.
    """
    global _server
    if _server is not None:
        return f"already listening on 127.0.0.1:{port}"
    if _device is None or not _device.is_ready():
        return "virtual display is not running; call start_display first"
    if not api_key and not mock:
        return "no api key and no mock script"

    # Python's default umask leaves files 0666; several of these carry the
    # user's data (risks, pacing, maps, sessions). Owner-only from here on.
    try:
        os.umask(0o077)
    except OSError:  # noqa: BLE001 - not fatal
        pass

    from .server import ServerConfig, make_llm, start_server

    usage_path = os.path.join(os.path.dirname(memory_path), "usage.jsonl") if memory_path else ""

    # Without the accessibility service there is no tree, and a prompt full of
    # labels the agent cannot resolve is worse than none: fall back to the
    # screenshot-only behaviour PhoneCLI started from.
    map_paths, skipped = _usable_maps(
        sorted(glob.glob(os.path.join(maps_dir, "*.yaml"))) if maps_dir else [], _device
    )
    for path, why in skipped:
        print(f"[maps] skipped {os.path.basename(path)}: {why}")
    try:
        print(f"[agent] {ensure_accessibility()}")
    except Exception as exc:  # noqa: BLE001 - a nicety, not a requirement
        print(f"[agent] could not check accessibility: {exc}")
    labels = accessibility_ready()
    config = ServerConfig(
        device_factory=lambda: _device,
        hands_off_factory=_hands_off,
        llm_factory=lambda usage_sink=None: make_llm(
            api_key, api_base, model, vision_model or model, mock,
            usage_path=usage_path, usage_sink=usage_sink
        ),
        # the display outlives individual tasks: it is the app's screen
        keep_device=True,
        include_ui_text=labels,
        map_paths=map_paths,
        # assets first, then whatever was crawled on this phone: the crawl
        # result wins on a name clash, because it describes this device
        map_dirs=[maps_dir, os.path.join(os.path.dirname(memory_path), "crawls")]
        if maps_dir and memory_path else [],
        risk_path=os.path.join(os.path.dirname(memory_path), "app-risks.json"),
        pacing_path=os.path.join(os.path.dirname(memory_path), "app-pacing.json"),
        pins_path=os.path.join(os.path.dirname(memory_path), "map-pins.json")
        if memory_path else "",
        memory_path=memory_path,
        usage_path=usage_path,
        # A crawl writes next to the memories; without a directory the app
        # simply does not offer the endpoint.
        crawl_dir=os.path.join(os.path.dirname(memory_path), "crawls")
        if memory_path else "",
        # The app rebuilds the display when the adb connection dies.
        recover=recover,
        # XHS opens its search page on the phone's own display even when
        # launched elsewhere (measured), so it is driven there from the start.
        user_display_packages=USER_DISPLAY_PACKAGES,
        known_apps=_known_apps(),
        # 0 means "whatever the default is"; the app sends the user's choice.
        max_rounds=max_rounds or 35,
        llm_api_key=api_key,
        llm_api_base=api_base,
        llm_model=model,
    )
    # The app writes this file before starting the service; both sides read the
    # same one, so a restart of either still agrees. Empty means no check, which
    # is only how the host CLI runs.
    global _serve_args
    _serve_args = {
        "port": port, "api_key": api_key, "api_base": api_base, "model": model,
        "vision_model": vision_model or model,
        "mock": mock, "maps_dir": maps_dir, "memory_path": memory_path,
        "token": token, "max_rounds": max_rounds,
    }
    if not token and memory_path:
        token = _read_token_file(os.path.join(os.path.dirname(memory_path), TOKEN_FILE))
    config.token = token
    if token:
        print("[agent] local API requires a token")
    _server = start_server(port, config)
    _start_heartbeat()
    mode = "labels" if labels else "pixels only"
    macro = f", {len(map_paths)} map(s)" if map_paths else ", no maps"
    if skipped:
        macro += f", {len(skipped)} skipped"
    return f"listening on 127.0.0.1:{port}" + (" (mock)" if mock else "") + f" [{mode}{macro}]"


def _usable_maps(paths, device):
    """Drop maps compiled for a different screen.

    A map records the screen it was crawled on. Replaying it on a different
    size is not fatal -- coordinates are normalized -- but the layout is what
    actually differs, so routing would miss. Skipping it out loud beats
    degrading to pure vision without a word.
    """
    import yaml

    kept, skipped = [], []
    width = int(getattr(device, "width", 0) or 0)
    height = int(getattr(device, "height", 0) or 0)
    for path in paths:
        try:
            with open(path, encoding="utf-8") as fh:
                header = yaml.safe_load(fh) or {}
        except Exception as exc:  # noqa: BLE001 - a broken map must not stop the agent
            skipped.append((path, f"unreadable: {exc}"))
            continue
        map_w = int(header.get("screen_w") or 0)
        map_h = int(header.get("screen_h") or 0)
        if width and height and map_w and map_h:
            if abs(map_w - width) > width * 0.02 or abs(map_h - height) > height * 0.02:
                skipped.append((path, f"built for {map_w}x{map_h}, display is {width}x{height}"))
                continue
        kept.append(path)
    return kept, skipped


#: The service this app reads the screen through.
A11Y_SERVICE = "com.phoneagent.app/com.phoneagent.app.PhoneAgentAccessibilityService"


def ensure_accessibility(device=None) -> str:
    """Turn this app's accessibility service back on, without the user.

    MIUI forgets it after a reboot (measured: a task that had been running
    fell back to "pixels only"), and without the service the agent loses labels
    and click-by-text. The shell we already have runs as uid 2000, which holds
    WRITE_SECURE_SETTINGS, so the app can restore its own service instead of
    asking someone to walk to Settings and back.

    Other services are preserved: the value is a colon-separated list.
    """
    device = device if device is not None else _device
    if device is None:
        return "accessibility: no device"
    current = str(device.shell("settings get secure enabled_accessibility_services")).strip()
    if current == "null":
        current = ""
    already = A11Y_SERVICE in current
    if not already:
        merged = f"{current}:{A11Y_SERVICE}".strip(":") if current else A11Y_SERVICE
        device.shell(
            "settings put secure enabled_accessibility_services " + "'" + merged + "'"
        )
    enabled = str(device.shell("settings get secure accessibility_enabled")).strip()
    if enabled != "1":
        device.shell("settings put secure accessibility_enabled 1")
    if already:
        return "accessibility already enabled"
    return f"accessibility re-enabled (was {current or 'empty'})"


def accessibility_ready(timeout: float = 6.0) -> bool:
    """Is the accessibility service bound and returning a real tree?

    The binding can lag a second or two behind the process starting, and the
    answer is baked into the server config, so a single early probe would
    downgrade the whole session to pixels for no reason. Poll briefly instead.
    """
    import time

    if _device is None:
        return False
    deadline = time.time() + timeout
    while True:
        tree = _device.dump_ui()
        if "<hierarchy" in tree and " error=" not in tree:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.4)


def healthy() -> bool:
    """Is the adb connection still usable?

    adbd drops the connection (measured: SSLEOFError in the middle of a task,
    "could not reopen the display: EOF"), and after that every task fails until
    the app is restarted. A shell command is the cheapest way to find out.
    """
    if _client is None or _device is None:
        return False
    try:
        _client.shell("echo ok")
    except Exception:  # noqa: BLE001 - any failure means "not usable"
        return False
    # A live socket is not enough: the helper (and with it the display) dies
    # when the shell session that started it goes away.
    try:
        if not _device.display_alive():
            return False
    except Exception:  # noqa: BLE001
        return False
    # And a live display is not enough either: the HTTP server is the other half,
    # and when its thread dies every request answers "connection closed" while
    # the screen looks perfectly healthy.
    return _server_listening()


def _wireless_debugging_on() -> Optional[bool]:
    """Is wireless debugging switched on? None when we cannot tell."""
    try:
        from java import jclass  # Chaquopy only
        from com.chaquo.python import Python

        context = Python.getPlatform().getApplication()
        health = jclass("com.phoneagent.app.AgentHealth")
        return bool(health.wirelessDebugging(context))
    except Exception:  # noqa: BLE001 - outside the app there is no setting to read
        return None


def _server_port() -> int:
    server = _server
    if server is None:
        return 0
    try:
        return int(server.server_address[1])
    except Exception:  # noqa: BLE001 - any weirdness means "not listening"
        return 0


def _server_listening() -> bool:
    """Can a client still open a connection to the agent's own HTTP server?"""
    import socket as _socket

    port = _server_port()
    if not port:
        return False
    try:
        with _socket.create_connection(("127.0.0.1", port), timeout=0.6):
            return True
    except OSError:
        return False


def _rediscover_port() -> int:
    """Ask the app's own scanner for adbd's port, if it is reachable here.

    The port only survives a dropped socket; toggling wireless debugging gives
    adbd a new one, and then the number start_display was given is stale.
    """
    try:
        from java import jclass  # Chaquopy only
        from com.chaquo.python import Python

        context = Python.getPlatform().getApplication()
        scanner = jclass("com.phoneagent.app.AdbPortScanner")
        # A Kotlin `object` is a singleton: Chaquopy exposes the method on the
        # class but still wants the instance, which is INSTANCE.
        instance = getattr(scanner, "INSTANCE", None) or scanner
        found = instance.discover(context)
        return int(found) if found else 0
    except Exception as exc:  # noqa: BLE001 - not fatal, we report instead
        print(f"[agent] could not rediscover the adbd port: {exc}")
        return 0


@dataclass
class Recovery:
    """What a rebuild attempt did.

    A string could not answer both questions at once: success used to come back
    as ``display=367 size=...`` (a developer's sentence) while failure came back
    as Chinese advice, so callers guessed with ``startswith("listening")`` and got
    it wrong. Two fields, and ``str()`` still gives the message for logging.
    """

    ok: bool
    message: str

    def __str__(self) -> str:  # keeps print() and str() working as before
        return self.message


def recover() -> Recovery:
    """Rebuild the client and the display after the connection died.

    The port stays the same -- adbd keeps listening, it just closed our socket --
    so this reconnects and starts a fresh display instead of asking the user to
    restart anything.
    """
    global _device, _client, _hands_off_device
    if not _display_args:
        return Recovery(False, "nothing to recover")
    print("[agent] adb connection is gone; rebuilding the display")
    # The hands-off device wraps the same transport, so it dies with it; leaving
    # the reference behind would keep handing out a dead connection.
    _hands_off_device = None
    for handle in (_device,):
        if handle is not None:
            try:
                handle.stop()
            except Exception:  # noqa: BLE001 - it is already dead
                pass
    _device = None
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001
            pass
    _client = None
    try:
        return Recovery(True, start_display(**_display_args))
    except Exception as exc:  # noqa: BLE001 - try another port before giving up
        print(f"[agent] reconnect on port {_display_args.get('port')} failed: {exc}")
    restored = _restart_server()
    if restored:
        print(f"[agent] server restarted: {restored}")
    port = _rediscover_port()
    if port and port != _display_args.get("port"):
        print(f"[agent] adbd moved to port {port}; rebuilding on it")
        _display_args["port"] = port
        try:
            return Recovery(True, start_display(**_display_args))
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            return Recovery(False, f"recovery failed: {type(exc).__name__}: {exc}")
    # Nothing answered and no port was advertised. That is almost always
    # wireless debugging having been switched off (MIUI turns it off on reboot
    # and after long idle stretches), and "reopen the app" would not fix it --
    # say what actually does.
    if not _wireless_debugging_on():
        return Recovery(False,
                        "adbd 没有监听端口：无线调试已经关闭。"
                        "\n请到「设置 → 开发者选项 → 无线调试」打开开关，再回到这里。")
    return Recovery(False,
                    "adbd 端口变了，但没发现新端口。"
                    "\n请到「设置 → 开发者选项 → 无线调试」看一眼端口，然后回到这里。")


def _restart_server() -> str:
    """Bring the HTTP server back if its thread died; "" when it never ran."""
    global _server
    if _server is not None or not _serve_args:
        return ""
    try:
        return serve(**_serve_args)
    except Exception as exc:  # noqa: BLE001 - reported by the caller
        return f"server restart failed: {type(exc).__name__}: {exc}"


def recover_json() -> str:
    """recover() as JSON, for callers that cannot see a dataclass.

    Kotlin gets a string from Chaquopy either way; asking the object whether it
    worked is not practical there, and guessing from the text is how
    "display=367 size=1280x2772 ..." ended up in the chat.
    """
    import json as _json

    result = recover()
    return _json.dumps({
        "ok": bool(getattr(result, "ok", False)),
        "message": str(result),
    })


def shutdown() -> str:
    """Tear everything down so a new configuration can start clean.

    Called when the user changes the model settings: the server and the display
    live in module globals, so they have to be released or the restart would
    keep the old key and the old display.
    """
    global _server, _device, _client, _hands_off_device
    _hands_off_device = None
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:  # noqa: BLE001 - shutting down anyway
            pass
        _server = None
    if _device is not None:
        try:
            _device.stop()
        except Exception:  # noqa: BLE001 - shutting down anyway
            pass
        _device = None
    if _client is not None:
        try:
            _client.close()
        except Exception:  # noqa: BLE001 - shutting down anyway
            pass
        _client = None
    return "stopped"
