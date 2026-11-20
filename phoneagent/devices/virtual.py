"""On-device backend: drive apps on a virtual display the phone does not show.

This is the M2 bridge. It starts the shell helper (``android-helper/``), which
creates a *hostable* virtual display, and then implements the same
:class:`~phoneagent.devices.base.PhoneDevice` protocol the adb backend does:

    screenshot  -> the helper grabs a frame from its own ImageReader
    tap/swipe   -> input -d <display-id>
    launch      -> resolve-activity + am start --display <display-id>
    dump_ui     -> not available yet (uiautomator only dumps the default
                   display); callers that need labels must wait for the
                   accessibility service in the APK

The helper runs as ``shell`` (uid 2000) because only shell may create a
display with ``canHostTasks``. Where that shell comes from is decided by the
:class:`~phoneagent.devices.transports.Transport` handed in: the host's ``adb``
binary during development, the app's own adbd connection in the packaged app.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Optional

from .base import (
    BaseDevice,
    DeviceError,
    DeviceInfo,
    LaunchRefused,
    NeedsHandover,
    UnsupportedOperation,
)
from .transports import HostAdbTransport, Transport

#: Packages that put a prompt in front of the app that was just launched.
#: The launch worked; the app is simply behind a dialog, and the caller knows
#: how to dismiss one.
DIALOG_HOSTS = frozenset({
    "com.android.permissioncontroller",
    "com.google.android.permissioncontroller",
    "com.miui.securitycenter",
})

HELPER_JAR = "/data/local/tmp/pa-helper.jar"
HELPER_CLASS = "com.phoneagent.helper.Main"
FRAME_PATH = "/data/local/tmp/pa-frame.png"


def _parse_bounds(value: Optional[str]) -> Optional[tuple[int, int, int, int]]:
    """Parse ``"[0,182][1280,2772]"`` (uiautomator) or ``"0,182,1280,2772"``."""
    if not value:
        return None
    numbers = re.findall(r"-?\d+", value)
    if len(numbers) != 4:
        return None
    left, top, right, bottom = (int(part) for part in numbers)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


#: The helper writes one frame file. Two callers reading it at once get a torn
#: PNG ("frame magic mismatch for 0x474e5089"), which is exactly what happened
#: once the app started polling /frame while a task was running.
_CAPTURE_LOCK = threading.Lock()


class VirtualDisplayDevice(BaseDevice):
    """Runs apps on a virtual display created by the shell helper."""

    platform = "android"

    def __init__(
        self,
        serial: Optional[str] = None,
        adb: Optional[str] = None,
        width: int = 1280,
        height: int = 2772,
        dpi: int = 520,
        name: str = "phoneagent",
        timeout: float = 30.0,
        transport: Optional[Transport] = None,
        hands_off: bool = False,
    ) -> None:
        self.transport = transport or HostAdbTransport(
            adb=adb or os.environ.get("ADB") or "adb",
            serial=serial,
            timeout=timeout,
        )
        self.adb = adb or os.environ.get("ADB") or "adb"
        self.serial = serial
        self.width, self.height, self.dpi = width, height, dpi
        self.name = name
        self.timeout = timeout
        self.display_id: Optional[int] = None
        #: Drive the phone's own screen instead of a virtual one. For apps that
        #: cannot run anywhere else -- 京东 hands its launcher over to a
        #: singleTask activity that the system always places on display 0 -- the
        #: only way to work them is with the user's screen, and the user knowing.
        self.hands_off = hands_off
        self._proc = None

    # -- lifecycle --------------------------------------------------------

    def start(self, initial_app: str = "") -> int:
        """Launch the helper and return the display id.

        ``initial_app`` is started right away: a fresh virtual display has no
        content, so the frame reader stays empty until something is drawn.
        """
        if self._is_hands_off():
            # Nothing to create: the phone's own screen is already there. The
            # accessibility bridge reads display 0 like any other.
            self.display_id = 0
            return 0
        if self._proc is not None:
            return int(self.display_id)
        command = (
            f"CLASSPATH={HELPER_JAR} app_process /system/bin "
            f"--nice-name=pa-helper {HELPER_CLASS} "
            f"{self.width} {self.height} {self.dpi} {self.name}"
        )
        self._proc = self.transport.spawn(command)
        # The helper prints diagnostics (INFO/WARN) before its result, so read
        # until the verdict instead of trusting the first line.
        line = ""
        for _ in range(20):
            line = self._read_line(timeout=25)
            if not line:
                break
            if line.startswith("OK") or line.startswith("ERROR"):
                break
            if not line.startswith(("INFO", "WARN")):
                break
        match = re.search(r"display=(\d+)", line or "")
        if not match:
            self.stop()
            raise DeviceError(f"helper did not report a display: {line!r}")
        self.display_id = int(match.group(1))
        if initial_app:
            try:
                self.launch(initial_app)
            except DeviceError:
                pass
        return self.display_id

    def stop(self) -> None:
        if self._proc is None:
            return
        # Releasing the display re-homes its tasks onto the default display,
        # where they pop up on the user's actual screen. Stopping the app first
        # is the difference between a quiet shutdown and the phone suddenly
        # showing a Settings page nobody opened.
        try:
            app = self.current_app()
            if app not in ("", "unknown", "com.phoneagent.app"):
                self._shell(f"am force-stop {app}")
        except Exception:
            pass
        try:
            if self._proc.is_alive():
                self._proc.write_line("stop")
                time.sleep(0.8)
        except Exception:
            pass
        finally:
            self._proc.terminate()
            self._proc = None
            self.display_id = None

    def close(self) -> None:
        self.stop()

    def __enter__(self):
        self.start()
        return self

    # -- protocol ---------------------------------------------------------

    def info(self) -> DeviceInfo:
        return DeviceInfo(
            platform="android",
            screen_width=self.width,
            screen_height=self.height,
        )

    def _is_hands_off(self) -> bool:
        """True when this object is driving the phone's own screen.

        That is either by construction (the user chose 托管模式) or because the
        app moved itself to display 0 and the agent followed it there. The
        difference only matters for the record; input, screenshots and the tree
        all work the same way on display 0.
        """
        if getattr(self, "display_id", None) == 0:
            return True
        return bool(getattr(self, "hands_off", False))

    def follow_to_user_display(self) -> bool:
        """Retarget at display 0: the app is on the user's screen, not ours.

        An app that starts an activity on the default display cannot be kept on
        a virtual one (launchMode decides that, not us). Following it is the
        difference between "I cannot see it any more" and carrying on.
        """
        current = getattr(self, "display_id", None)
        if current is None or current == 0:
            return False
        if getattr(self, "_own_display_id", None) is None:
            self._own_display_id = current
        self.display_id = 0
        return True

    def return_to_own_display(self) -> None:
        """Undo follow_to_user_display(); called when a task ends."""
        own = getattr(self, "_own_display_id", None)
        if own:
            self.display_id = own
            self._own_display_id = None

    def is_ready(self) -> bool:
        if self._is_hands_off():
            return True
        return self._proc is not None and self._proc.is_alive() and self.display_id is not None

    def display_alive(self) -> bool:
        """Is the display this object believes in still there?

        The helper runs as a child of an adb shell session. When that session
        dies -- adbd drops the connection, which happens -- the helper goes with
        it and the display disappears. ``display_id`` then points at nothing,
        and `am start --display N` for a display that no longer exists falls
        back to the *default* display: the app the agent is driving appears on
        the user's own screen.
        """
        if self.display_id is None:
            # Hands-off drives display 0, which cannot be "gone".
            return self._is_hands_off()
        try:
            # No trailing space in the dump: `mDisplayId=79` is the whole token,
            # and matching one with a space after it counted zero every time --
            # which made every launch look like the display had died.
            out = self._shell(
                f"dumpsys display | grep -cE 'mDisplayId={self.display_id}([^0-9]|$)'"
            )
        except Exception:  # noqa: BLE001 - cannot ask means cannot tell
            return True
        parts = out.strip().split()
        if not parts or not parts[0].isdigit():
            # An answer we cannot read is not evidence that the display died;
            # only a clear zero is.
            return True
        return int(parts[0]) > 0

    def screenshot(self, output_path: Optional[str] = None) -> bytes:
        if self._is_hands_off():
            # No helper process to ask: the phone's own screen is readable with
            # screenshot, which is the same image the user sees.
            #
            # /data/local/tmp is traversable by every app on the phone and these
            # files came out 0666, so a screenshot of the user's own screen sat
            # somewhere another app could read it. Lock it down, then remove it.
            path = "/data/local/tmp/pa-hands-off.png"
            self._shell(f"screencap -p {path}; chmod 600 {path}")
            try:
                data = self.transport.read_file(path)
            finally:
                self._shell(f"rm -f {path}")
            if output_path:
                with open(output_path, "wb") as fh:
                    fh.write(data)
            return data
        if not self.is_ready():
            raise DeviceError("virtual display is not running")
        target = FRAME_PATH
        with _CAPTURE_LOCK:
            # One retry, because a display change can rewrite the frame while it
            # is being read: moving a task back from the user's screen (小红书's
            # search page) produced "frame magic mismatch for 0x003e0001" and
            # killed a whole task over a picture. A torn picture is not a reason
            # to abandon the work; the next frame is fine.
            last: Exception | None = None
            data = b""
            for attempt in range(2):
                try:
                    self._send(f"frame {target}")
                    reply = self._read_line(timeout=15)
                    if not reply.startswith("FRAME"):
                        raise DeviceError(f"helper did not produce a frame: {reply!r}")
                    # The helper writes as shell (0666 in a traversable
                    # directory); this is a picture of the user's screen, so it
                    # is owner-only and gone again before anything else can
                    # look at it.
                    self._shell(f"chmod 600 {target}")
                    try:
                        data = self.transport.read_file(target)
                    finally:
                        self._shell(f"rm -f {target}")
                    if data[:8] == b"\x89PNG\r\n\x1a\n":
                        break
                    last = DeviceError(f"frame is not a PNG ({data[:4]!r})")
                except Exception as exc:  # noqa: BLE001 - AdbError is not a DeviceError
                    # transport.read_file raises AdbError (a RuntimeError) on a
                    # torn sync stream, and that exception used to escape the
                    # retry and end the whole task.
                    last = exc
                if attempt == 0:
                    time.sleep(0.7)
            else:
                if isinstance(last, DeviceError):
                    raise last
                raise DeviceError(f"could not read a frame: {last}") from last
        if output_path:
            with open(output_path, "wb") as fh:
                fh.write(data)
        return data

    #: How long to wait for a tree before giving up on this read. A dump that
    #: cannot finish is worth an empty screen; it is never worth a frozen app.
    dump_timeout = 8.0

    def dump_ui(self) -> str:
        """The control tree of the virtual display, as XML.

        Only an accessibility service may look inside another app's windows,
        so this crosses into Kotlin through Chaquopy. The call is started on a
        Java worker and polled, never waited on: the tree walk goes through
        system_server, which can block for as long as an activity is being
        relaunched, and a blocked call held the interpreter's lock long enough
        that the task server stopped answering altogether.
        """
        if self.display_id is None and not self._is_hands_off():
            return '<hierarchy rotation="0"></hierarchy>'
        # Hands-off means display 0, and 0 is a real key in the service's window
        # map -- so the same path works, it just needs the right number.
        wanted = 0 if self.display_id is None else int(self.display_id)
        try:
            from java import jclass  # Chaquopy only

            bridge = jclass("com.phoneagent.app.PhoneAgentAccessibilityService")
            begin, poll = bridge.beginDump, bridge.pollDump
        except Exception:  # noqa: BLE001 - no JVM, or an older bridge
            return self._dump_blocking()
        tree = self._poll_dump(begin, poll, wanted, self.dump_timeout)
        if not tree:
            return '<hierarchy rotation="0" error="dump timed out"></hierarchy>'
        return self._wrap_dump_error(tree)

    #: How long a click or a scroll may take before it is treated as failed.
    action_timeout = 10.0

    @staticmethod
    def _poll_action(poll, tag: int, timeout: float,
                     sleep=time.sleep) -> Optional[bool]:
        """Wait for a started action; None if it never finished.

        The action runs on a Java worker, so a busy system_server can delay it
        but cannot freeze the interpreter -- which is exactly what happened
        while these calls were made synchronously from Python.
        """
        deadline = time.monotonic() + timeout
        while True:
            state = int(poll(tag))
            if state >= 0:
                return bool(state)
            if time.monotonic() >= deadline:
                return None
            sleep(0.05)

    def _run_bridge_action(self, begin_name: str, sync_name: str, *args,
                           timeout: float = 0.0) -> Optional[bool]:
        """Call begin*/pollAction, or the named sync method on an older bridge."""
        bridge = self._bridge()
        if bridge is None:
            return None
        begin = getattr(bridge, begin_name, None)
        poll = getattr(bridge, "pollAction", None)
        if begin is not None and poll is not None:
            display = 0 if self.display_id is None else int(self.display_id)
            tag = int(begin(display, *args))
            return self._poll_action(poll, tag, timeout or self.action_timeout)
        sync = getattr(bridge, sync_name, None)
        if sync is None:
            return None
        return bool(sync(int(self.display_id), *args))

    @staticmethod
    def _poll_dump(begin, poll, display_id: int, timeout: float,
                   sleep=time.sleep) -> str:
        """Wait for a started dump, up to ``timeout``; "" if it never finished."""
        tag = int(begin(display_id))
        deadline = time.monotonic() + timeout
        while True:
            tree = str(poll(tag))
            if tree:
                return tree
            if time.monotonic() >= deadline:
                return ""
            sleep(0.05)

    def _dump_blocking(self) -> str:
        """The synchronous bridge, for a JVM without beginDump/pollDump."""
        try:
            from java import jclass  # Chaquopy only

            bridge = jclass("com.phoneagent.app.PhoneAgentAccessibilityService")
            tree = str(bridge.dump(int(self.display_id)))
        except Exception as exc:  # noqa: BLE001 - no JVM, or Java bridge missing
            return f'<hierarchy rotation="0" error="{exc}"></hierarchy>'
        return self._wrap_dump_error(tree)

    @staticmethod
    def _wrap_dump_error(tree: str) -> str:
        if tree.startswith("<error>"):
            return f'<hierarchy rotation="0" error="{tree}"></hierarchy>'
        return tree

    def find_text(self, needle: str) -> Optional[tuple[int, int, int, int]]:
        """Bounds of the first node whose text or label contains ``needle``.

        Prefers a clickable node: a list row usually is one, while its label is
        a child that would otherwise give a tap target with no effect.
        """
        import xml.etree.ElementTree as ET

        tree = self.dump_ui()
        if "<hierarchy" not in tree:
            return None
        try:
            root = ET.fromstring(tree)
        except ET.ParseError:
            return None
        parent = {child: node for node in root.iter("node") for child in node}
        fallback = None
        for node in root.iter("node"):
            label = (node.get("text") or "") + " " + (node.get("desc") or "")
            if needle not in label:
                continue
            bounds = _parse_bounds(node.get("bounds"))
            if bounds is None:
                continue
            # climb to the nearest clickable ancestor, which is what a tap hits
            cursor = node
            for _ in range(6):
                if cursor.get("clickable") == "true":
                    found = _parse_bounds(cursor.get("bounds")) or bounds
                    return found
                cursor = parent.get(cursor)
                if cursor is None:
                    break
            fallback = fallback or bounds
        return fallback

    def _bridge(self):
        """The accessibility service class, or None when it is unavailable."""
        try:
            from java import jclass  # Chaquopy only

            return jclass("com.phoneagent.app.PhoneAgentAccessibilityService")
        except Exception:  # noqa: BLE001 - no JVM, or the class is missing
            return None

    def click_text(self, text: str) -> bool:
        """Click by accessibility action instead of by coordinate.

        ``performAction(ACTION_CLICK)`` acts on the node itself, so it needs no
        display id and no pixel maths, and it returns false when nothing
        matched rather than tapping whatever happened to be at that point.
        """
        try:
            return self._run_bridge_action("beginClick", "clickText", text) is True
        except Exception:  # noqa: BLE001 - fall back to coordinates
            return False

    def scroll(self, direction: str = "down") -> bool:
        """Scroll the display.

        The accessibility action is exact and needs no coordinates; a swipe is
        the fallback for when the service is off, since `dispatchGesture` has no
        display argument and cannot be aimed at a virtual display.
        """
        forward = direction.lower() in ("down", "forward", "next", "page_down")
        try:
            if self._run_bridge_action("beginScroll", "scroll", forward):
                return True
        except Exception:  # noqa: BLE001 - fall back to a swipe
            pass
        middle = self.width // 2
        if forward:
            return self.swipe(middle, int(self.height * 0.75), middle, int(self.height * 0.30), 300)
        return self.swipe(middle, int(self.height * 0.30), middle, int(self.height * 0.75), 300)

    def tap_text(self, text: str) -> bool:
        """Click the control labelled ``text``.

        Accessibility first, coordinates second: the action is exact and
        self-checking, while the coordinate path exists for when the service is
        switched off.
        """
        if self.click_text(text):
            return True
        bounds = self.find_text(text)
        if bounds is None:
            return False
        left, top, right, bottom = bounds
        return self.tap((left + right) // 2, (top + bottom) // 2)

    def tap(self, x: int, y: int) -> bool:
        self._input("tap", x, y)
        return True

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> bool:
        self._input("swipe", x, y, x, y, int(duration_ms))
        return True

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> bool:
        self._input("swipe", x1, y1, x2, y2, max(int(duration_ms), 1))
        return True

    def input_text(self, text: str) -> bool:
        # Always try the accessibility bridge, hands-off included: in that mode
        # display_id is None by design, and gating on it here sent every Chinese
        # keystroke down the ASCII-only shell path -- "input text only supports
        # ASCII without the accessibility service" while the service was
        # connected and working. That is why 小红书 tasks could not type a query.
        if True:
            try:
                # ACTION_SET_TEXT writes in one go and takes any character,
                # unlike `input text`, which is ASCII-only and goes through
                # the shell
                if self._run_bridge_action("beginSetText", "setText", text):
                    return True
            except Exception:  # noqa: BLE001 - fall back to the shell
                pass
        if not text.isascii():
            raise UnsupportedOperation("input text only supports ASCII without the accessibility service")
        self._input("text", text)
        return True

    def press_back(self) -> bool:
        self._input("keyevent", 4)
        return True

    def collapse_shade(self) -> bool:
        """Close the notification shade without touching it.

        `cmd statusbar collapse` is a shell command, so it cannot land a tap on
        a notification -- one of which ("已连接到无线调试") would turn off the
        connection the agent runs on.
        """
        try:
            self._shell("cmd statusbar collapse")
            return True
        except DeviceError:
            return False

    def press_home(self) -> bool:
        self._input("keyevent", 3)
        return True

    def press_key(self, name: str) -> bool:
        codes = {"back": 4, "home": 3, "enter": 66, "delete": 67}
        code = codes.get(name.lower())
        if code is None:
            raise UnsupportedOperation(f"unsupported key {name!r}")
        self._input("keyevent", code)
        return True

    def launch(self, package: str, intent: str = "", wait: float = 6.0) -> bool:
        """Start an app on the virtual display.

        ``-S`` force-stops first, which is not cosmetic: a task that has been
        moved between displays (or released and re-hosted) keeps its old
        accessibility connection, and an accessibility service then gets a null
        root for its window even though the pixels render fine. A fresh task
        always has a readable tree.

        The launch is verified, and retried once after a hard stop: an app that
        is already running on the phone's own display can otherwise have its
        existing task returned instead of a new one being created here. Only
        after that does a refusal mean the app really cannot come up -- which is
        what ``launchMode="singleTask"`` guarantees, because such an activity is
        always delivered to its own task.
        """
        # In hands-off mode display_id is None *by design*: the display being
        # driven is the phone's own. Guarding on None here made every hands-off
        # launch raise "no display to drive", so the branch below it could never
        # run -- measured on 微博, which never came to the front.
        if self.display_id is None and not self._is_hands_off():
            raise DeviceError("no display to drive")
        if self._is_hands_off():
            # Everything is on display 0 by definition, so the escape checks
            # below ("it opened on the user's screen") would fire on every app.
            # Only "did it come to the front" is meaningful here.
            activity = self._resolve_activity(package)
            if not activity:
                raise LaunchRefused(package, "no launcher activity")
            if activity.startswith("com.phoneagent.app"):
                # `am start -S` stops the target package first. Resolving our
                # own component here would stop the process that is running the
                # crawl, which is exactly how a hands-off crawl died one second
                # after its first tap.
                raise LaunchRefused(package, f"resolved to the agent itself: {activity}")
            self._shell(f"am start -S -n {activity}")
            # A cold start of a large app (小红书) takes longer than six seconds
            # on this phone, and the launch was being reported as refused while
            # the app was still drawing.
            unread = 0
            for _ in range(14):
                time.sleep(1.0)
                try:
                    if self.current_app() == package:
                        return True
                    unread = 0
                except DeviceError:
                    # "I could not read the screen" is not "the launch failed".
                    # dumpsys activity is a large dump and the shell it travels
                    # over sometimes drops it; refusing on that blocked every
                    # app on the user's display.
                    unread += 1
                    if unread >= 3:
                        return True
            raise LaunchRefused(package, "did not come to the front")
        if not self.display_alive():
            # Refuse rather than launch into the void: without this check the
            # app is created on display 0 and the user sees it take over their
            # screen while the agent believes it is working on its own display.
            raise DeviceError(
                f"virtual display {self.display_id} is gone; the app would open on "
                "your own screen"
            )
        activity = intent if "/" in (intent or "") else ""
        activity = activity or self._resolve_activity(package)
        if activity.startswith("com.phoneagent.app"):
            raise DeviceError(
                f"{package} resolved to the agent's own component ({activity}); "
                "`am start -S` would stop this process")
        print(f"[launch] {package} -> {activity or '(unresolved)'} on display {self.display_id}")
        if not activity:
            self._shell(f"am force-stop {package}")
            self._shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
            time.sleep(1.0)
            if self.current_app() == package:
                return True
            raise LaunchRefused(package, "no launcher activity could be resolved")

        for attempt in (1, 2):
            where = "" if self._is_hands_off() else f"--display {self.display_id} "
            out = self._shell(f"am start -S {where}-n {activity}")
            if "Error" in out or "does not exist" in out:
                print(f"[launch]   am start said: {out.strip()[:160]}")
            for _ in range(max(1, min(4, int(wait)))):
                try:
                    front = self.current_app()
                except DeviceError:
                    break
                if front == package:
                    return True
                if front in DIALOG_HOSTS:
                    # A prompt is in front. Dismiss it and look again: accepting
                    # it as "launched" hid the calendar case, where a leftover
                    # task made the prompt look like the app's own.
                    print(f"[launch]   {package} is behind {front}; dismissing once")
                    try:
                        self.press_back()
                    except DeviceError:
                        pass
                    time.sleep(1.0)
                    if self.current_app() == package:
                        return True
                    continue
                time.sleep(1.0)
            if attempt == 1:
                # A permission dialog left by the first launch of an app sits on
                # top of the display and nothing else can come to the front:
                # "did not come up here" then means "a dialog is in the way".
                # Clear it before blaming the app.
                try:
                    front = self.current_app()
                except DeviceError:
                    front = ""
                if front and front in DIALOG_HOSTS:
                    print(f"[launch]   a dialog from {front} is in the way; dismissing it")
                    try:
                        self.press_back()
                    except DeviceError:
                        pass
                    time.sleep(0.8)
                    try:
                        if self.current_app() == package:
                            return True
                    except DeviceError:
                        pass
                # a task of this app living on another display wins the launch;
                # remove it and try once more before giving up
                print(f"[launch]   {package} did not come up here; hard stop and retry")
                self._shell(f"am force-stop {package}")
                time.sleep(1.0)
                # Two different ways a page can hold the launch:
                #
                #  - it is a task of its own (MIUI's account centre), which a
                #    force-stop clears;
                #  - it sits on top of *this* app's task, because the app handed
                #    the screen over (Settings hands 移动网络 to com.android.phone).
                #    The launcher activity is singleTask, so the relaunch is
                #    delivered to that same task and nothing moves. Only a back
                #    press unwinds it -- measured: back twice, launch works.
                for _ in range(3):
                    try:
                        front = self.current_app()
                    except DeviceError:
                        break
                    # "unknown" means the dump could not be read; pressing back
                    # against that is pressing back blind, and the log then says
                    # "unwinding unknown", which is not a package at all.
                    if not front or front == package or "." not in front:
                        break
                    print(f"[launch]   unwinding {front}, which is holding the task")
                    try:
                        self.press_back()
                    except DeviceError:
                        break
                    time.sleep(0.6)
                try:
                    blocker = self.current_app()
                except DeviceError:
                    blocker = ""
                if blocker and blocker != package and "." in blocker:
                    print(f"[launch]   clearing {blocker}, which is holding the screen")
                    self._shell(f"am force-stop {blocker}")
                    time.sleep(0.8)
        # Before calling it unlaunchable: did it open on the user's screen?
        # That is a different outcome -- the step can be handed over.
        if self.has_task_on(0, package):
            raise NeedsHandover(
                package, "it started on the phone's own display, where the user can act"
            )
        raise LaunchRefused(
            package,
            "singleTask activities stay on the display of their own task",
        )


    def force_stop(self, package: str) -> bool:
        if package == "com.phoneagent.app":
            print("[device] refused to force-stop the agent's own package")
            return False
        self._shell(f"am force-stop {package}")
        return True

    def settle(self, timeout: float = 2.5, interval: float = 0.2, stable_reads: int = 1) -> str:
        """Wait until the control tree stops changing, then return it.

        A tap is fire-and-forget: the next read can land mid-animation, on a
        half-drawn page, and the agent then acts on a screen that no longer
        exists. Reading until the dump repeats turns "unchanged" into a fact
        instead of a race; one repeat is enough, and each read costs a round
        trip.
        """
        deadline = time.time() + max(timeout, 0.0)
        last = ""
        same = 0
        while True:
            try:
                current = self.dump_ui()
            except DeviceError:
                current = ""
            if current and current == last:
                same += 1
                if same >= stable_reads:
                    return current
            else:
                same = 0
            last = current
            if time.time() >= deadline:
                return last
            time.sleep(interval)

    def home_package(self) -> str:
        """The launcher package, so a fresh display can show something."""
        try:
            out = self._shell(
                "cmd package resolve-activity --brief "
                "-a android.intent.action.MAIN -c android.intent.category.HOME"
            )
        except DeviceError:
            return ""
        for line in reversed(out.splitlines()):
            line = line.strip()
            if "/" in line:
                return line.split("/")[0]
        return ""

    def hand_over(self, package: str) -> bool:
        """Put the app on the phone's own screen so the user can act on it.

        Only measured way to let someone type a password or read a code: the
        app on this display is invisible to them, and `am start` without a
        display id just reuses the task that is already here. An explicit
        `--display 0` is what moves a copy to the screen they are looking at.
        """
        if not package:
            return False
        if self._is_hands_off():
            return True          # already on the screen the user is looking at
        activity = self._resolve_activity(package)
        if not activity:
            return False
        print(f"[handover] {package} -> display 0 for the user")
        self._shell(f"am start -S --display 0 -n {activity}")
        return True

    def _roots_by_display(self) -> dict:
        """display id -> [(task id, package, visible)], from ``am stack list``.

        ``dumpsys activity activities`` was the old source and it lies about
        this: a task that was moved between displays keeps appearing in the
        block of the display it left (measured: task 9349 listed under
        ``Display #0`` and ``Display #182`` at the same time). Everything built
        on "is this app on the phone's screen" then answers yes for an app that
        never went there -- which triggers a pointless follow, and the follow is
        exactly the screen flip the user notices.

        ``am stack list`` prints one RootTask per real display with an explicit
        ``displayId=``, and the tasks under it. Falls back to dumpsys when the
        command is unavailable, so older devices keep working.
        """
        try:
            out = self._shell("am stack list")
        except DeviceError:
            out = ""
        roots: dict = {}
        display = None
        for line in out.splitlines():
            match = re.match(r"\s*RootTask id=\d+ .*displayId=(\d+)", line)
            if match:
                display = int(match.group(1))
                roots.setdefault(display, [])
                continue
            task = re.match(r"\s*taskId=(\d+): (\S+)/", line)
            if task and display is not None:
                roots[display].append(
                    (int(task.group(1)), task.group(2), "visible=true" in line))
        if roots:
            return roots
        return self._roots_from_dumpsys()

    def _roots_from_dumpsys(self) -> dict:
        """The old parse, kept only as a fallback for platforms without am stack."""
        try:
            out = self._shell("dumpsys activity activities")
        except DeviceError:
            return {}
        roots: dict = {}
        display = None
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("Display #"):
                match = re.match(r"Display #(\d+)", stripped)
                if match:
                    display = int(match.group(1))
                    roots.setdefault(display, [])
                continue
            if display is None or "/" not in stripped:
                continue
            task = re.search(r"\bt(\d+)\b", stripped)
            package = re.search(r"([a-zA-Z0-9_.]+)/[a-zA-Z0-9_.$]+", stripped)
            if task and package:
                roots[display].append((int(task.group(1)), package.group(1), False))
        return roots

    def has_task_on(self, display_id: int, package: str) -> bool:
        """True when ``display_id`` really holds a task of ``package``."""
        return bool(self.task_on(display_id, package))

    def task_on(self, display_id: int, package: str) -> int:
        """The task id of ``package`` on ``display_id``, or 0.

        A visible task wins over a hidden one: an app can have a background
        task here and the same app in front on another display, and the one the
        user is looking at is the one a move should carry.
        """
        roots = self._roots_by_display()
        best = 0
        for task, name, visible in roots.get(display_id, []):
            if name != package:
                continue
            if visible:
                return task
            best = best or task
        return best

    def pull_back(self, package: str) -> bool:
        """Bring a task that jumped to the phone's screen back onto ours.

        小红书's search page is a NEW_TASK activity, and the system hosts it on
        the default display even when the app runs on a virtual one (measured:
        tapping 搜索 at (1192,224) on display 644 left 644 with Settings while
        the whole 小红书 task moved to display 0). Nothing in the app can stop
        that, but the task can be moved: `am display move-stack <task> <display>`
        took task 8155 back to 644, after which the search page's EditText
        (id=mSearchToolBarEt) was readable and typeable there.

        Following the app to the user's screen also "works", at the price of
        taking the phone over -- and of hiding the app that is driving it.
        """
        if self.display_id in (None, 0):
            return False
        task = self.task_on(0, package)
        if not task:
            return False
        try:
            self._shell(f"am display move-stack {task} {self.display_id}")
        except DeviceError:
            return False
        time.sleep(1.2)
        return not self.has_task_on(0, package)

    def has_task(self, package: str) -> bool:
        """True when this display holds a task belonging to ``package``.

        Used to tell "the app came up behind a permission prompt" from "the
        app never came up at all, and something else is showing a prompt".
        Shares task_on's source so both answer from the same reading.
        """
        if self.display_id is None and not self._is_hands_off():
            return False
        wanted = 0 if self.display_id is None else self.display_id
        return bool(self.task_on(wanted, package))

    def current_app(self) -> str:
        """Package on top of *this* display.

        ``dumpsys activity activities`` groups its dump by display, so the
        default display's top activity must not be mistaken for ours -- the
        phone's own foreground app is usually the agent itself.
        """
        # None means hands-off: the display being driven *is* display 0. Saying
        # "unknown" there made every screen look foreign to the crawler -- a
        # hands-off 微博 crawl counted 36 of 36 taps as "left the app" and
        # recorded a single screen.
        wanted = 0 if self.display_id is None else self.display_id
        out = self._shell("dumpsys activity activities")
        in_display = False
        fallback = ""
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.startswith("Display #"):
                in_display = stripped.startswith(f"Display #{wanted} ")
                continue
            if not in_display:
                continue
            # A task stack on a secondary display often has no
            # topResumedActivity line -- only `Task{... A=10275:com.miui.calculator`.
            # Without this fallback current_app() answered "unknown", which the
            # escape check and the crawl's display clearing both depend on.
            if not fallback:
                owner = re.search(r"A=\d+:([\w.]+)", stripped)
                if owner:
                    fallback = owner.group(1)
            if "topResumedActivity" in stripped:
                match = re.search(r"u\d+\s+([\w.]+)/", stripped)
                if match:
                    return match.group(1)
        return fallback or "unknown"

    def shell(self, command: str) -> str:
        return self._shell(command)

    # -- plumbing ---------------------------------------------------------

    def _shell(self, command: str) -> str:
        # The agent must never stop its own host: a crawl that force-stops
        # com.phoneagent.app kills the process running the crawl, and the only
        # evidence is an empty log and a dead API (measured on a hands-off
        # crawl of 携程: the kill arrived one second after the first tap).
        guard = self._self_stop_guard(command)
        if guard:
            print(f"[device] refused: {guard}")
            return ""
        return self.transport.shell(command)

    @staticmethod
    def _self_stop_guard(command: str) -> str:
        """Why this command would stop the app we are running in, or ""."""
        text = (command or "").strip()
        if "force-stop" not in text and "kill " not in text:
            return ""
        if "com.phoneagent.app" in text:
            return f"would stop the agent's own process: {text[:120]}"
        return ""

    def _input(self, *args) -> None:
        if self.display_id is None and not self._is_hands_off():
            raise DeviceError("no display to drive")
        joined = " ".join(str(a) for a in args)
        if self._is_hands_off():
            self._shell(f"input {joined}")     # the phone's own screen has no -d
        else:
            self._shell(f"input -d {self.display_id} {joined}")

    def _resolve_activity(self, package: str) -> str:
        try:
            out = self._shell(f"cmd package resolve-activity --brief {package}")
        except DeviceError:
            return ""
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith(package) and "/" in line:
                return line
        return ""

    def _send(self, line: str) -> None:
        if not self._proc:
            raise DeviceError("helper is not running")
        self._proc.write_line(line)

    def _read_line(self, timeout: float = 10.0) -> str:
        if not self._proc:
            return ""
        return self._proc.read_line(timeout)

    def __repr__(self) -> str:  # pragma: no cover
        return f"VirtualDisplayDevice(serial={self.serial!r}, display={self.display_id})"
