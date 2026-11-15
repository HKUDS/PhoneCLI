"""The accessibility dump's bounds format.

This is not cosmetic: the agent's label parser looks for uiautomator's
``[l,t][r,b]`` and silently found nothing while the dump emitted
``l,t,r,b``, so every task died at the preflight with "no_ui".
"""

from __future__ import annotations

import pytest

from phoneagent.devices.virtual import _parse_bounds


def test_parses_uiautomator_format():
    assert _parse_bounds("[0,182][1280,2772]") == (0, 182, 1280, 2772)


def test_parses_plain_comma_format():
    assert _parse_bounds("0,182,1280,2772") == (0, 182, 1280, 2772)


def test_keeps_negative_offsets():
    assert _parse_bounds("[-10,-20][100,200]") == (-10, -20, 100, 200)


@pytest.mark.parametrize("value", [None, "", "0,0,0,0", "10,10,5,5", "not bounds", "1,2,3"])
def test_rejects_unusable_bounds(value):
    assert _parse_bounds(value) is None


def test_start_skips_helper_diagnostics(monkeypatch):
    """The helper prints INFO/WARN before OK display=N; the first line is not the verdict."""
    from phoneagent.devices import virtual as V

    class FakeProc:
        def __init__(self, lines):
            self._lines = list(lines)
            self.display_id = None

        def read_line(self, timeout=10.0):
            return self._lines.pop(0) if self._lines else ""

        def is_alive(self):
            return True

        def write_line(self, line):
            pass

        def terminate(self):
            pass

    class FakeTransport:
        def __init__(self, lines):
            self.proc = FakeProc(lines)

        def spawn(self, command):
            return self.proc

    device = V.VirtualDisplayDevice(transport=FakeTransport([
        "INFO top-focus stealing disabled: 0x10001",
        "WARN something harmless",
        "OK display=42 size=1280x2772",
    ]))
    assert device.start() == 42


# ---------------------------------------------------------------------------
# reading a tree without blocking the interpreter
# ---------------------------------------------------------------------------


def test_a_finished_dump_is_returned():
    from phoneagent.devices.virtual import VirtualDisplayDevice

    answers = ["", "", "<hierarchy windows=\"1\"/>"]

    def begin(display_id):
        assert display_id == 7
        return 42

    def poll(tag):
        assert tag == 42
        return answers.pop(0)

    tree = VirtualDisplayDevice._poll_dump(begin, poll, 7, 5.0, sleep=lambda _s: None)
    assert tree == "<hierarchy windows=\"1\"/>"


def test_a_dump_that_never_finishes_times_out():
    from phoneagent.devices.virtual import VirtualDisplayDevice

    tree = VirtualDisplayDevice._poll_dump(
        lambda _d: 1, lambda _t: "", 0, 0.0, sleep=lambda _s: None)
    assert tree == ""


# ---------------------------------------------------------------------------
# actions that must not block the interpreter
# ---------------------------------------------------------------------------


def test_an_action_is_polled_until_it_reports():
    from phoneagent.devices.virtual import VirtualDisplayDevice

    states = [-1, -1, 1]
    assert VirtualDisplayDevice._poll_action(
        lambda _t: states.pop(0), 9, 5.0, sleep=lambda _s: None) is True


def test_an_action_that_never_reports_times_out():
    from phoneagent.devices.virtual import VirtualDisplayDevice

    assert VirtualDisplayDevice._poll_action(
        lambda _t: -1, 9, 0.0, sleep=lambda _s: None) is None


class _FakeBridge:
    def __init__(self, async_api: bool, result: bool = True) -> None:
        self.async_api = async_api
        self.result = result
        self.calls: list = []
        if async_api:
            self.beginClick = self._begin
            self.pollAction = lambda tag: 1 if tag == 7 else -1
        else:
            self.clickText = lambda display_id, text: self.result

    def _begin(self, display_id, text):
        self.calls.append((display_id, text))
        return 7


def _device_with(bridge):
    from phoneagent.devices.virtual import VirtualDisplayDevice

    device = VirtualDisplayDevice.__new__(VirtualDisplayDevice)
    device.display_id = 3
    device._bridge = lambda: bridge
    return device


def test_a_click_goes_through_the_async_bridge():
    bridge = _FakeBridge(async_api=True)
    device = _device_with(bridge)
    assert device.click_text("蓝牙") is True
    assert bridge.calls == [(3, "蓝牙")]


def test_an_older_bridge_falls_back_to_the_sync_call():
    bridge = _FakeBridge(async_api=False, result=True)
    device = _device_with(bridge)
    assert device.click_text("蓝牙") is True


def test_a_refused_async_click_is_false_not_none():
    bridge = _FakeBridge(async_api=True)
    bridge.pollAction = lambda _tag: 0
    device = _device_with(bridge)
    assert device.click_text("蓝牙") is False


# ---------------------------------------------------------------------------
# an app that cannot be brought up on this display
# ---------------------------------------------------------------------------


def _launching_device(foreground: str):
    from phoneagent.devices.virtual import VirtualDisplayDevice

    device = VirtualDisplayDevice.__new__(VirtualDisplayDevice)
    device.display_id = 9
    device._shell = lambda _cmd: ""
    device._resolve_activity = lambda _pkg: "com.android.calendar/.homepage.AllInOneActivity"
    device.current_app = lambda: foreground
    return device


def test_launch_that_never_lands_is_refused(monkeypatch):
    from phoneagent.devices.base import LaunchRefused

    monkeypatch.setattr("phoneagent.devices.virtual.time.sleep", lambda _s: None)
    device = _launching_device("com.android.settings")
    with pytest.raises(LaunchRefused) as caught:
        device.launch("com.android.calendar", wait=2)
    assert caught.value.package == "com.android.calendar"
    assert "singleTask" in caught.value.reason


def test_launch_that_lands_is_ok(monkeypatch):
    monkeypatch.setattr("phoneagent.devices.virtual.time.sleep", lambda _s: None)
    device = _launching_device("com.android.calendar")
    assert device.launch("com.android.calendar", wait=2) is True


def test_a_second_attempt_is_made_after_a_hard_stop(monkeypatch):
    """An app running on the phone's display wins the launch; a retry wins it back."""
    from phoneagent.devices.virtual import VirtualDisplayDevice

    monkeypatch.setattr("phoneagent.devices.virtual.time.sleep", lambda _s: None)

    seen = {"calls": 0, "killed": 0}

    def current_app():
        seen["calls"] += 1
        # first attempt never lands, second one does
        return "com.android.notes" if seen["killed"] else "com.android.settings"

    device = VirtualDisplayDevice.__new__(VirtualDisplayDevice)
    device.display_id = 4
    device._resolve_activity = lambda _pkg: "com.android.notes/.NotesListActivity"

    def shell(cmd):
        if cmd.startswith("am force-stop"):
            seen["killed"] += 1
        return ""

    device._shell = shell
    device.current_app = current_app

    assert device.launch("com.android.notes", wait=1) is True
    assert seen["killed"] == 1


def test_a_launch_is_refused_when_the_display_is_gone():
    """A stale display id makes `am start --display N` open on the phone screen."""
    from phoneagent.devices.base import DeviceError

    class Device:
        def display_alive(self):
            return False

    # the check itself is what matters here: launch consults it before starting
    from phoneagent.devices.virtual import VirtualDisplayDevice

    assert Device().display_alive() is False

    device = VirtualDisplayDevice.__new__(VirtualDisplayDevice)
    device.display_id = 99
    device.display_alive = lambda: False
    try:
        device.launch("com.android.settings")
    except DeviceError as exc:
        assert "gone" in str(exc)
    else:
        raise AssertionError("launch should refuse when the display is missing")


def test_a_hands_off_device_drives_display_zero():
    """京东-class apps only run on the user's screen; that is a supported mode."""
    from phoneagent.devices.virtual import VirtualDisplayDevice

    class Transport:
        def __init__(self):
            self.commands = []

        def write_file(self, *a, **k):
            pass

        def shell(self, command):
            self.commands.append(command)
            return ""

        def read_file(self, path):
            return b"\x89PNG\r\n"

    transport = Transport()
    device = VirtualDisplayDevice(transport=transport, hands_off=True)
    assert device.start() == 0
    assert device.is_ready() is True

    # input goes to the default display, with no -d flag
    device.press_back()
    assert transport.commands[-1].strip() == "input keyevent 4"

    # hand-over is already done
    assert device.hand_over("com.example.app") is True
