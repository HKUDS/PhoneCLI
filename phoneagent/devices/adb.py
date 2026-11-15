"""Android backend over the host-side ``adb`` binary.

This is the *bridge* backend: it lets the unified agent run on a real Android
phone (or emulator) **today**, with no APK and no on-device helper, by reusing
exactly the same ``adb shell`` primitives that the AndroidLab evaluation code
uses:

    screenshot   -> adb exec-out screencap -p
    ui tree      -> adb shell uiautomator dump /sdcard/window_dump.xml + cat
    tap/swipe    -> adb shell input tap / swipe
    text         -> adb shell input text
    launch       -> adb shell monkey -p <pkg> -c LAUNCHER 1
    force stop   -> adb shell am force-stop <pkg>
    foreground   -> adb shell dumpsys window / activity

A later backend (``OnDeviceDevice``) will implement the same protocol from
inside the APK, driving a virtual display instead of the physical screen.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from typing import Optional, Sequence

from .base import (
    BaseDevice,
    DeviceError,
    DeviceInfo,
    DeviceNotReady,
    UnicodeTextUnsupported,
)

_ANDROID_KEYCODES = {
    "back": 4,
    "home": 3,
    "enter": 66,
    "delete": 67,
    "recent": 187,
    "power": 26,
    "wakeup": 224,
}

_FOCUS_PATTERNS = (
    re.compile(r"mCurrentFocus=Window\{[^}]*?\s+([A-Za-z0-9_.]+)/"),
    re.compile(r"mFocusedApp=.*?\s+([A-Za-z0-9_.]+)/"),
    re.compile(r"mResumedActivity.*?\s+([A-Za-z0-9_.]+)/"),
    re.compile(r"topResumedActivity.*?\s+([A-Za-z0-9_.]+)/"),
)


def _remote_quote(text: str) -> str:
    """Quote an argument for the *device* shell (not the local one)."""
    return "'" + text.replace("'", "'\\''") + "'"


class AdbDevice(BaseDevice):
    """Drive an Android device through ``adb``.

    Parameters
    ----------
    serial:
        Device serial as shown by ``adb devices``. ``None`` picks the single
        attached device (and fails loudly when there are several).
    adb:
        Path to the adb binary; defaults to $ADB or ``adb`` on PATH.
    timeout:
        Default per-command timeout in seconds.
    """

    platform = "android"

    def __init__(
        self,
        serial: Optional[str] = None,
        adb: Optional[str] = None,
        timeout: float = 20.0,
    ) -> None:
        self.adb = adb or os.environ.get("ADB") or shutil.which("adb") or "adb"
        self.timeout = timeout
        self.serial = serial or os.environ.get("ANDROID_SERIAL") or None
        if self.serial is None:
            self.serial = self._single_device()
        self._info_cache: Optional[DeviceInfo] = None

    # -- plumbing ---------------------------------------------------------

    def _single_device(self) -> str:
        out = self._exec(["devices"], timeout=10).decode("utf-8", "replace")
        devices = [
            line.split("\t")[0]
            for line in out.splitlines()[1:]
            if line.strip() and line.strip().endswith("device")
        ]
        if not devices:
            raise DeviceNotReady(
                "no adb device found; plug a phone in or run `adb connect <ip:port>`"
            )
        if len(devices) > 1:
            raise DeviceNotReady(
                f"several adb devices ({', '.join(devices)}); pass serial=... "
                "or set $ANDROID_SERIAL"
            )
        return devices[0]

    def _exec(
        self,
        args: Sequence[str],
        timeout: Optional[float] = None,
        check: bool = True,
    ) -> bytes:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout or self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:  # adb not installed
            raise DeviceError(f"adb binary not found: {self.adb}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DeviceError(f"adb timed out: {' '.join(cmd)}") from exc
        if check and proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise DeviceError(f"adb failed ({proc.returncode}): {' '.join(cmd)}: {err}")
        return proc.stdout

    def shell(self, command: str) -> str:
        return self._exec(["shell", command]).decode("utf-8", "replace")

    # -- perception -------------------------------------------------------

    def is_ready(self) -> bool:
        try:
            state = self._exec(["get-state"], timeout=5).decode().strip()
            return state == "device"
        except Exception:
            return False

    def info(self) -> DeviceInfo:
        if self._info_cache is not None:
            return self._info_cache
        width, height = self._screen_size()
        model = self._getprop("ro.product.model")
        version = self._getprop("ro.build.version.release")
        self._info_cache = DeviceInfo(
            platform="android",
            screen_width=width,
            screen_height=height,
            model=model,
            os_version=version,
        )
        return self._info_cache

    def _getprop(self, key: str) -> str:
        try:
            return self._exec(["shell", f"getprop {key}"]).decode("utf-8", "replace").strip()
        except DeviceError:
            return ""

    def _screen_size(self) -> tuple[int, int]:
        """Current logical size, honouring an override if one is set."""
        try:
            out = self.shell("wm size")
        except DeviceError:
            return 1080, 1920
        override = re.search(r"Override size:\s*(\d+)x(\d+)", out)
        if override:
            return int(override.group(1)), int(override.group(2))
        physical = re.search(r"Physical size:\s*(\d+)x(\d+)", out)
        if physical:
            return int(physical.group(1)), int(physical.group(2))
        return 1080, 1920

    def screenshot(self, output_path: Optional[str] = None) -> bytes:
        png = self._exec(["exec-out", "screencap", "-p"], timeout=30)
        if not png.startswith(b"\x89PNG"):
            raise DeviceError("screencap did not return a PNG")
        if output_path:
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            with open(output_path, "wb") as fh:
                fh.write(png)
        return png

    def dump_ui(self) -> str:
        """UIAutomator hierarchy as XML.

        ``uiautomator dump`` writes to a file on the device; we pull it back
        with ``exec-out cat`` so nothing is left behind in the workspace.
        """
        remote = "/sdcard/window_dump.xml"
        self._exec(["shell", f"uiautomator dump {remote}"], timeout=40)
        try:
            data = self._exec(["exec-out", "cat", remote], timeout=20)
        except DeviceError:
            data = self._exec(["shell", f"cat {remote}"], timeout=20)
        xml = data.decode("utf-8", "replace")
        if "<hierarchy" not in xml:
            raise DeviceError(f"unexpected uiautomator output: {xml[:120]!r}")
        return xml

    # -- actions ----------------------------------------------------------

    def tap(self, x: int, y: int) -> bool:
        self._exec(["shell", f"input tap {int(x)} {int(y)}"])
        return True

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> bool:
        # `input` has no long-press verb; a stationary swipe with a duration is
        # the standard trick (same one AndroidLab uses).
        self._exec(
            ["shell", f"input swipe {int(x)} {int(y)} {int(x)} {int(y)} {int(duration_ms)}"]
        )
        return True

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400
    ) -> bool:
        self._exec(
            [
                "shell",
                f"input swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {max(int(duration_ms), 1)}",
            ]
        )
        return True

    def input_text(self, text: str) -> bool:
        if not text:
            return True
        if not text.isascii():
            raise UnicodeTextUnsupported(
                "adb `input text` only supports ASCII; use a clipboard/IME path "
                "(the on-device backend will provide one)"
            )
        # `input text` treats %s as a space and needs no shell escaping once quoted.
        payload = _remote_quote(text.replace("%", "\\%").replace(" ", "%s"))
        self._exec(["shell", f"input text {payload}"])
        return True

    def press_back(self) -> bool:
        return self.press_key("back")

    def press_home(self) -> bool:
        return self.press_key("home")

    def press_key(self, name: str) -> bool:
        key = _ANDROID_KEYCODES.get(name.lower())
        if key is None:
            if name.isdigit():
                key = int(name)
            else:
                raise KeyError(f"unknown key: {name!r}")
        self._exec(["shell", f"input keyevent {key}"])
        return True

    # -- app lifecycle ----------------------------------------------------

    def launch(self, package: str, intent: str = "", wait: float = 6.0) -> bool:
        """Bring an app to the foreground, verified.

        ``monkey`` reports success even when the ROM refuses to raise the app
        (HyperOS does this), so the only trustworthy signal is the foreground
        package afterwards. Tries monkey -> resolve-activity -> explicit intent.
        """
        deadline = time.time() + wait
        if intent:
            # "pkg/.Activity" means an explicit component; anything else is an
            # intent action. HyperOS sometimes hijacks the SETTINGS action and
            # opens the account app instead, so components are the safer form.
            start = f"am start -n {intent}" if "/" in intent else f"am start -a {intent}"
            try:
                self.shell(start)
            except DeviceError:
                pass
            return self._wait_foreground(package, deadline)

        try:
            self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        except DeviceError:
            pass
        if self._wait_foreground(package, min(deadline, time.time() + 2.0)):
            return True

        activity = self._resolve_activity(package)
        if activity:
            try:
                self.shell(f"am start -n {activity}")
            except DeviceError:
                return False
            if self._wait_foreground(package, deadline):
                return True

        try:
            self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
        except DeviceError:
            return False
        return self._wait_foreground(package, deadline)

    def _wait_foreground(self, package: str, deadline: float) -> bool:
        while time.time() < deadline:
            if self.current_app() == package:
                time.sleep(0.8)  # let the first frame settle
                return True
            time.sleep(0.4)
        return False

    def _resolve_activity(self, package: str) -> str:
        try:
            out = self.shell(f"cmd package resolve-activity --brief {package}")
        except DeviceError:
            return ""
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith(package) and "/" in line:
                return line
        return ""

    def force_stop(self, package: str) -> bool:
        self.shell(f"am force-stop {package}")
        return True

    def current_app(self) -> str:
        for command in ("dumpsys window", "dumpsys activity activities"):
            try:
                out = self.shell(command)
            except DeviceError:
                continue
            for pattern in _FOCUS_PATTERNS:
                match = pattern.search(out)
                if match:
                    return match.group(1)
        return "unknown"

    # -- convenience ------------------------------------------------------

    def close(self) -> None:  # pragma: no cover - nothing to release
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"AdbDevice(serial={self.serial!r})"
