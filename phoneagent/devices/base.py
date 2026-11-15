"""Device abstraction shared by every transport.

The agent and macro layers never talk to a phone directly; they talk to a
:class:`PhoneDevice`. That is the only seam needed to move the same algorithm
from a host-driven iOS setup (WebDriverAgent) to an Android APK (on-device
shell helper + virtual display).

Coordinate convention
---------------------
All coordinates are **physical pixels in the device's current screen**, or
normalised ``[0, 1]`` where explicitly stated (``AppMap`` element centers).
Use :func:`to_pixels` to convert a map element center into device pixels.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DeviceError(RuntimeError):
    """Base class for device failures."""


class DeviceNotReady(DeviceError):
    """The transport is up but the device is not usable (no session, locked...)."""


class UnsupportedOperation(DeviceError):
    """The backend cannot do this (e.g. raw shell on a locked-down iOS device)."""


class UnicodeTextUnsupported(DeviceError):
    """The backend can only type ASCII (``adb shell input text``)."""


class NeedsHandover(DeviceError):
    """The app opened on the user's own display instead of the agent's.

    Taobao's login activity is one example: `am start --display N` succeeds in
    the sense that the app is running, but it put itself on display 0, where
    the user can act on it and the agent cannot. That is not a failure to
    launch; it is an invitation to hand the step over.
    """

    def __init__(self, package: str, reason: str = "") -> None:
        self.package = package
        self.reason = reason
        super().__init__(
            f"{package} opened on the user's display"
            + (f": {reason}" if reason else "")
        )


class LaunchRefused(DeviceError):
    """The app exists but cannot be brought up on this display.

    Android pins an activity declared ``launchMode="singleTask"`` to its own
    task: a launch request with a display id is delivered to that task, on
    whatever display it already lives on. On a secondary display the app then
    never appears, and no amount of retrying, force-stopping or
    ``--activity-multiple-task`` changes that. Retrying is pure waste; the
    agent should say so and stop.
    """

    def __init__(self, package: str, reason: str = "") -> None:
        self.package = package
        self.reason = reason
        super().__init__(
            f"{package} cannot be started on this display"
            + (f": {reason}" if reason else "")
        )


# ---------------------------------------------------------------------------
# Device description
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceInfo:
    """What the algorithm needs to know about the screen it is driving."""

    platform: str  # "ios" | "android"
    screen_width: int
    screen_height: int
    model: str = ""
    os_version: str = ""

    @property
    def size(self) -> tuple[int, int]:
        return self.screen_width, self.screen_height

    def to_pixels(self, center: tuple[float, float]) -> tuple[int, int]:
        """Map a normalised ``(x, y)`` in ``[0, 1]`` to device pixels."""
        return (
            round(center[0] * self.screen_width),
            round(center[1] * self.screen_height),
        )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class PhoneDevice(Protocol):
    """Everything the agent needs from a phone.

    Implementations must be safe to call sequentially; concurrency is the
    caller's problem.
    """

    # --- perception -------------------------------------------------------
    def info(self) -> DeviceInfo:
        """Screen size / platform / model."""

    def is_ready(self) -> bool:
        """Cheap health check; must not raise."""

    def screenshot(self, output_path: Optional[str] = None) -> bytes:
        """PNG bytes, optionally also written to ``output_path``."""

    def dump_ui(self) -> str:
        """Accessibility tree as XML (XCUITest or UIAutomator format)."""

    # --- actions ----------------------------------------------------------
    def tap(self, x: int, y: int) -> bool:
        """Single tap at pixel coordinates."""

    def long_press(self, x: int, y: int, duration_ms: int = 3000) -> bool:
        """Press and hold at pixel coordinates."""

    def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400
    ) -> bool:
        """Linear swipe between two pixel coordinates."""

    def input_text(self, text: str) -> bool:
        """Type into the currently focused field."""

    def press_back(self) -> bool:
        """Platform back."""

    def press_home(self) -> bool:
        """Go to home / launcher."""

    def press_key(self, name: str) -> bool:
        """Named key: enter, delete, power, recent, or a numeric keycode."""

    # --- app lifecycle ----------------------------------------------------
    def launch(self, package: str) -> bool:
        """Bring an app to the foreground (bundle id on iOS, package on Android)."""

    def force_stop(self, package: str) -> bool:
        """Terminate an app."""

    def current_app(self) -> str:
        """Foreground package / bundle id, or ``"unknown"``."""

    # --- escape hatch -----------------------------------------------------
    def shell(self, command: str) -> str:
        """Run a raw shell command; backends may raise UnsupportedOperation."""


# ---------------------------------------------------------------------------
# Helpers shared by backends
# ---------------------------------------------------------------------------


class BaseDevice:
    """Small conveniences on top of the protocol; backends may inherit."""

    platform: str = "unknown"

    def info(self) -> DeviceInfo:  # pragma: no cover - overridden
        raise NotImplementedError

    def is_ready(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def shell(self, command: str) -> str:
        raise UnsupportedOperation(f"{type(self).__name__} has no shell access")

    # Convenience wrappers used by MacroExecutor and tests.
    def tap_center(self, center: tuple[float, float]) -> bool:
        x, y = self.info().to_pixels(center)
        return self.tap(x, y)

    def tap_element(self, element_text: str, app_map) -> bool:
        """Tap a map element by its normalised center. Raises KeyError if absent."""
        for screen in app_map.screens:
            for e in screen.elements:
                if e.text == element_text:
                    return self.tap_center(e.center)
        raise KeyError(f"element not found in map: {element_text!r}")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        close = getattr(self, "close", None)
        if callable(close):
            close()
