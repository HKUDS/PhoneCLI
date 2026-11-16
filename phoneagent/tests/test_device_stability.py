"""Two ways the app used to hang itself, both silent.

A command that never came back held the thread that also serves the app's
local API (eleven minutes of silence, no way to ask what it was doing), and
every failed connection rebuild left its virtual display behind (300+ of them
listed by dumpsys).
"""

import pytest

from phoneagent.devices.base import DeviceError
from phoneagent.devices.transports import SelfAdbTransport


def test_a_shell_timeout_becomes_a_device_error():
    class StuckClient:
        def run(self, command, timeout=None):
            raise TimeoutError("read timed out")

    transport = SelfAdbTransport(StuckClient(), timeout=5)

    with pytest.raises(DeviceError) as excinfo:
        transport.shell("dumpsys activity activities")

    assert "timed out" in str(excinfo.value).lower()


def test_stop_display_releases_the_display_and_the_hands_off_device(monkeypatch):
    from phoneagent import ondevice

    class FakeDevice:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    own, hands_off = FakeDevice(), FakeDevice()
    monkeypatch.setattr(ondevice, "_device", own)
    monkeypatch.setattr(ondevice, "_hands_off_device", hands_off)

    assert ondevice.stop_display() == "stopped"
    assert own.stopped and hands_off.stopped
    assert ondevice._device is None and ondevice._hands_off_device is None
