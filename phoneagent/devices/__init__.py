"""Device backends. Import the concrete class you need, not this package.

Only :mod:`phoneagent.devices.base` and :mod:`phoneagent.devices.mock` are
dependency-free; ``adb`` needs the adb binary and ``wda`` needs
``phonecli.device`` (i.e. ``requests``).
"""

from .base import (
    BaseDevice,
    DeviceError,
    DeviceInfo,
    DeviceNotReady,
    PhoneDevice,
    UnicodeTextUnsupported,
    UnsupportedOperation,
)
from .mock import MockDevice, MockFailure

__all__ = [
    "BaseDevice",
    "DeviceError",
    "DeviceInfo",
    "DeviceNotReady",
    "PhoneDevice",
    "UnicodeTextUnsupported",
    "UnsupportedOperation",
    "MockDevice",
    "MockFailure",
]
