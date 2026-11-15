"""iOS backend: a thin adapter over the existing ``phonecli/device.py``.

Nothing in ``phonecli/`` is modified; this class only reshapes its 12 flat
functions into the :class:`~phoneagent.devices.base.PhoneDevice` protocol so
the same agent code can drive an iPhone through WebDriverAgent.

Note that ``requests`` is imported lazily: on a machine that only drives
Android, importing this module must not require the iOS dependencies.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

from .base import (
    BaseDevice,
    DeviceError,
    DeviceInfo,
    DeviceNotReady,
    UnsupportedOperation,
)


def _wda_module():
    """Import ``phonecli.device`` on demand with a helpful error message."""
    try:
        from phonecli import device as wda  # type: ignore

        return wda
    except Exception as exc:  # pragma: no cover - depends on layout
        raise DeviceError(
            "cannot import phonecli.device; run from the repository root "
            "and install `requests` (pip install -r requirements.txt)"
        ) from exc


class WdaDevice(BaseDevice):
    """Drive an iOS device through WebDriverAgent.

    Parameters
    ----------
    wda_url:
        WDA base URL, e.g. ``http://localhost:8100`` (default: env
        ``PHONECLI_WDA_URL`` or ``http://localhost:8100``).
    session_id:
        Reuse an existing WDA session; a new one is created when omitted.
    """

    platform = "ios"

    def __init__(
        self,
        wda_url: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> None:
        self._wda = _wda_module()
        self.wda_url = wda_url or os.environ.get("PHONECLI_WDA_URL") or "http://localhost:8100"
        if session_id is None:
            if not self._wda.is_wda_ready(self.wda_url):
                raise DeviceNotReady(
                    f"WDA not reachable at {self.wda_url}; start WebDriverAgent "
                    "and `iproxy 8100 8100`"
                )
            ok, sid = self._wda.create_session(self.wda_url)
            if not ok:
                raise DeviceNotReady(f"could not create WDA session: {sid}")
            session_id = sid
        self.session_id = session_id
        self._info_cache: Optional[DeviceInfo] = None

    # -- perception -------------------------------------------------------

    def info(self) -> DeviceInfo:
        if self._info_cache is None:
            width, height = self._wda.get_screen_size(self.wda_url, self.session_id)
            self._info_cache = DeviceInfo(
                platform="ios", screen_width=int(width), screen_height=int(height)
            )
        return self._info_cache

    def is_ready(self) -> bool:
        try:
            return bool(self._wda.is_wda_ready(self.wda_url))
        except Exception:
            return False

    def screenshot(self, output_path: Optional[str] = None) -> bytes:
        path = output_path or os.path.join(
            tempfile.gettempdir(), f"wda-shot-{os.getpid()}.png"
        )
        if not self._wda.take_screenshot(path, self.wda_url, self.session_id):
            raise DeviceError("WDA screenshot failed")
        with open(path, "rb") as fh:
            data = fh.read()
        if not output_path:
            os.unlink(path)
        return data

    def dump_ui(self) -> str:
        path = os.path.join(tempfile.gettempdir(), f"wda-src-{os.getpid()}.xml")
        try:
            if not self._wda.dump_xml(path, self.wda_url, self.session_id):
                raise DeviceError("WDA page source dump failed")
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        finally:
            if os.path.exists(path):
                os.unlink(path)

    # -- actions ----------------------------------------------------------

    def tap(self, x: int, y: int) -> bool:
        return bool(self._wda.tap(int(x), int(y), self.wda_url, self.session_id))

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> bool:
        return bool(
            self._wda.long_press(int(x), int(y), int(duration_ms), self.wda_url, self.session_id)
        )

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400
    ) -> bool:
        return bool(
            self._wda.swipe(
                int(x1), int(y1), int(x2), int(y2), int(duration_ms), self.wda_url, self.session_id
            )
        )

    def input_text(self, text: str) -> bool:
        return bool(self._wda.input_text(text, self.wda_url, self.session_id))

    def press_back(self) -> bool:
        return bool(self._wda.back(self.wda_url, self.session_id))

    def press_home(self) -> bool:
        return bool(self._wda.home(self.wda_url, self.session_id))

    def press_key(self, name: str) -> bool:
        lowered = name.lower()
        if lowered == "back":
            return self.press_back()
        if lowered == "home":
            return self.press_home()
        raise UnsupportedOperation(f"iOS backend has no key {name!r}")

    # -- app lifecycle ----------------------------------------------------

    def launch(self, package: str) -> bool:
        return bool(self._wda.launch_app(package, self.wda_url, self.session_id))

    def force_stop(self, package: str) -> bool:
        return bool(self._wda.force_stop(package, self.wda_url, self.session_id))

    def current_app(self) -> str:
        return self._wda.get_current_app(self.wda_url, self.session_id) or "unknown"

    # -- escape hatch -----------------------------------------------------

    def shell(self, command: str) -> str:
        raise UnsupportedOperation("iOS/WDA backend has no shell access")

    def resolve_bundle_id(self, name: str) -> Optional[str]:
        """Map a human app name to an iOS bundle id using PhoneCLI's table."""
        return self._wda.resolve_bundle_id(name)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"WdaDevice(url={self.wda_url!r}, session={self.session_id!r})"
