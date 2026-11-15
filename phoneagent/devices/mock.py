"""In-memory backend for tests and offline dry-runs.

It records every call and returns canned perception results, so the macro
executor and the app-map layer can be exercised without a phone, an emulator
or a WDA session.

    dev = MockDevice(ui_xml=open("dump.xml").read())
    MacroExecutor(dev).run(app_map.build_operations()["settings_wifi"].macro)
    assert ("tap", 108, 415) in dev.calls
"""

from __future__ import annotations

from typing import Any, Optional

from .base import BaseDevice, DeviceInfo, UnsupportedOperation

_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class MockDevice(BaseDevice):
    """A fake phone that records calls.

    Parameters
    ----------
    info:
        Reported device info; defaults to a 1080x1920 Android phone.
    ui_xml:
        Returned verbatim by :meth:`dump_ui`.
    fail_on:
        Method names that should raise :class:`MockFailure` instead of working,
        e.g. ``{"tap"}`` to test macro failure paths.
    """

    platform = "android"

    def __init__(
        self,
        info: Optional[DeviceInfo] = None,
        # A default dump has to contain at least one labelled node, otherwise
        # PhoneAgent's preflight treats the fake device as locked.
        ui_xml: str = (
            '<hierarchy rotation="0">'
            '<node text="Mock Screen" content-desc="" bounds="[0,0][400,200]" />'
            "</hierarchy>"
        ),
        fail_on: Optional[set[str]] = None,
    ) -> None:
        self._info = info or DeviceInfo(
            platform="android", screen_width=1080, screen_height=1920
        )
        self.ui_xml = ui_xml
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.fail_on = set(fail_on or ())
        self.current = ""
        self.shell_commands: list[str] = []

    # -- helpers ----------------------------------------------------------

    def _record(self, name: str, *args: Any, **kwargs: Any) -> bool:
        self.calls.append((name, args, kwargs))
        if name in self.fail_on:
            raise MockFailure(f"{name} was told to fail")
        return True

    def called(self, name: str) -> list[tuple[str, tuple[Any, ...], dict[str, Any]]]:
        return [c for c in self.calls if c[0] == name]

    def method_names(self) -> list[str]:
        return [c[0] for c in self.calls]

    def reset(self) -> None:
        self.calls.clear()
        self.shell_commands.clear()

    # -- perception -------------------------------------------------------

    def info(self) -> DeviceInfo:
        return self._info

    def is_ready(self) -> bool:
        return True

    def screenshot(self, output_path: Optional[str] = None) -> bytes:
        self._record("screenshot", output_path)
        if output_path:
            with open(output_path, "wb") as fh:
                fh.write(_PNG_1PX)
        return _PNG_1PX

    def dump_ui(self) -> str:
        self._record("dump_ui")
        return self.ui_xml

    # -- actions ----------------------------------------------------------

    def tap(self, x: int, y: int) -> bool:
        return self._record("tap", x, y)

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> bool:
        return self._record("long_press", x, y, duration_ms)

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400
    ) -> bool:
        return self._record("swipe", x1, y1, x2, y2, duration_ms)

    def input_text(self, text: str) -> bool:
        return self._record("input_text", text)

    def press_back(self) -> bool:
        return self._record("press_back")

    def press_home(self) -> bool:
        return self._record("press_home")

    def press_key(self, name: str) -> bool:
        return self._record("press_key", name)

    # -- app lifecycle ----------------------------------------------------

    def launch(self, package: str) -> bool:
        self.current = package
        return self._record("launch", package)

    def force_stop(self, package: str) -> bool:
        if self.current == package:
            self.current = ""
        return self._record("force_stop", package)

    def current_app(self) -> str:
        self._record("current_app")
        return self.current or "unknown"

    # -- escape hatch -----------------------------------------------------

    def shell(self, command: str) -> str:
        self.shell_commands.append(command)
        self._record("shell", command)
        if command.startswith("force_stop"):
            raise UnsupportedOperation("mock does not emulate shell semantics")
        return ""


class MockFailure(RuntimeError):
    """Raised by :class:`MockDevice` when a method is configured to fail."""
