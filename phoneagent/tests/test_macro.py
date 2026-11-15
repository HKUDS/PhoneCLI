"""Offline tests for macro replay against the mock device."""

from __future__ import annotations

from pathlib import Path

import pytest

from phoneagent.core.app_map import AppMap
from phoneagent.core.macro import MacroExecutor, validate_macro
from phoneagent.devices.base import DeviceInfo
from phoneagent.devices.mock import MockDevice, MockFailure

REPO = Path(__file__).resolve().parents[2]
IOS_MAP = REPO / "phonecli" / "app_maps" / "settings_map.yaml"
ANDROID_MAP = REPO / "phonecli_android" / "app_maps" / "settings_android.yaml"


def _executor(device, **kwargs):
    return MacroExecutor(device, sleep=lambda _s: None, **kwargs)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_replays_android_macro():
    dev = MockDevice()
    macro = [
        {"action": "force_stop", "package": "com.android.settings", "wait": 0.5},
        {"action": "launch", "package": "com.android.settings", "wait": 3.0},
        {"action": "tap", "x": 540, "y": 960, "wait": 1.0},
    ]
    result = _executor(dev).run(macro)
    assert result.ok
    assert result.executed == 3
    # current_app is a read the replayer makes to wait for the window before
    # tapping; the actions themselves are what this asserts.
    assert [m for m in dev.method_names() if m != "current_app"] == [
        "force_stop", "launch", "tap"]
    assert dev.called("tap")[0][1] == (540, 960)
    assert dev.current == "com.android.settings"


def test_replays_ios_macro_with_bundle_id_key():
    dev = MockDevice()
    macro = [
        {"action": "force_stop", "bundle_id": "com.apple.Preferences", "wait": 1.0},
        {"action": "launch", "bundle_id": "com.apple.Preferences", "wait": 3.0},
    ]
    result = _executor(dev).run(macro)
    assert result.ok
    assert dev.called("launch")[0][1] == ("com.apple.Preferences",)


def test_empty_macro_is_ok():
    dev = MockDevice()
    assert _executor(dev).run([]).ok
    assert dev.calls == []


def test_waits_are_scaled_and_skippable():
    slept = []
    dev = MockDevice()
    executor = MacroExecutor(dev, sleep=slept.append, wait_scale=0.5)
    executor.run(
        [
            {"action": "tap", "x": 1, "y": 2, "wait": 2.0},
            {"action": "wait", "duration": 1.0},
        ]
    )
    assert slept == [1.0, 0.5]


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------


def test_stops_at_first_failure():
    dev = MockDevice(fail_on={"tap"})
    macro = [
        {"action": "force_stop", "package": "pkg"},
        {"action": "launch", "package": "pkg"},
        {"action": "tap", "x": 10, "y": 20},
        {"action": "tap", "x": 30, "y": 40},
    ]
    result = _executor(dev).run(macro)
    assert not result.ok
    assert result.executed == 2
    assert "tap" in result.error
    assert len(dev.called("tap")) == 1  # the second tap never ran


class IntentDevice(MockDevice):
    """A device that answers an intent with whatever the ROM decides.

    HyperOS hands ``android.settings.SETTINGS`` to the phone app's mobile
    network page, so "the intent was sent" and "the package came up" are two
    different facts. This mock can separate them.
    """

    def __init__(self, hijacked: bool = False) -> None:
        super().__init__()
        self.hijacked = hijacked
        self.launches: list[tuple[str, str]] = []

    def launch(self, package: str, intent: str = "", wait: float = 6.0) -> bool:
        self.launches.append((package, intent))
        if intent and self.hijacked:
            self.current = "com.android.phone"
            return False
        self.current = package
        return True


def test_launch_intent_that_lands_is_used_once():
    dev = IntentDevice(hijacked=False)
    macro = [{"action": "launch", "package": "com.android.settings", "wait": 3.0}]
    result = _executor(dev, launch_intent="android.settings.SETTINGS").run(macro)
    assert result.ok
    assert dev.launches == [("com.android.settings", "android.settings.SETTINGS")]
    assert dev.current == "com.android.settings"


def test_launch_intent_hijacked_by_another_app_falls_back_to_the_package():
    dev = IntentDevice(hijacked=True)
    macro = [
        {"action": "force_stop", "package": "com.android.settings", "wait": 0.5},
        {"action": "launch", "package": "com.android.settings", "wait": 3.0},
        {"action": "tap", "x": 336, "y": 1277, "wait": 1.0},
    ]
    result = _executor(dev, launch_intent="android.settings.SETTINGS").run(macro)
    assert result.ok
    # the intent was tried, rejected by the foreground check, then the package
    # itself was launched -- otherwise every tap below lands in the phone app
    assert dev.launches == [
        ("com.android.settings", "android.settings.SETTINGS"),
        ("com.android.settings", ""),
    ]
    assert dev.current == "com.android.settings"
    assert result.executed == 3


def test_launch_that_never_lands_is_an_error():
    dev = IntentDevice(hijacked=True)
    dev.launch = lambda package, intent="", wait=6.0: False  # type: ignore[method-assign]
    macro = [{"action": "launch", "package": "com.android.settings"}]
    result = _executor(dev, launch_intent="android.settings.SETTINGS").run(macro)
    assert not result.ok
    assert "landed elsewhere" in result.error


def test_unknown_action_fails_without_touching_device():
    dev = MockDevice()
    result = _executor(dev).run([{"action": "teleport"}])
    assert not result.ok
    assert "unknown action" in result.error
    assert dev.calls == []


def test_dry_run_touches_nothing():
    dev = MockDevice()
    macro = [
        {"action": "force_stop", "package": "pkg"},
        {"action": "tap", "x": 1, "y": 2},
    ]
    result = _executor(dev, dry_run=True).run(macro)
    assert result.ok
    assert result.executed == 2
    assert dev.calls == []


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_validate_macro_reports_problems():
    problems = validate_macro(
        [
            {"action": "launch"},  # no package
            {"action": "tap", "x": 1},  # no y
            {"action": "swipe", "x1": 1, "y1": 2},  # incomplete
            {"action": "spin"},  # unknown
            {"action": "type"},  # no text
        ]
    )
    assert len(problems) == 5
    assert all(issue.startswith("step ") for issue in problems)


# ---------------------------------------------------------------------------
# end-to-end: map -> operation -> replay
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [IOS_MAP, ANDROID_MAP])
def test_every_operation_macro_replays(path):
    """Every generated operation must replay end to end on the mock."""
    app = AppMap(path)
    ops = app.build_operations()
    assert ops
    for op in ops.values():
        dev = MockDevice()
        result = _executor(dev).run(op.macro)
        assert result.ok, f"{op.id}: {result.error}"
        assert dev.method_names()[-1] == "tap"


def test_device_info_to_pixels():
    info = DeviceInfo("android", 1080, 1920)
    assert info.to_pixels((0.5, 0.25)) == (540, 480)
    assert info.size == (1080, 1920)


def test_tap_center_uses_device_size():
    dev = MockDevice(DeviceInfo("android", 1000, 2000))
    dev.tap_center((0.25, 0.5))
    assert dev.called("tap")[0][1] == (250, 1000)


def test_mock_failure_is_runtimeerror():
    dev = MockDevice(fail_on={"launch"})
    with pytest.raises(MockFailure):
        dev.launch("pkg")


# ---------------------------------------------------------------------------
# cross-resolution scaling
# ---------------------------------------------------------------------------


def test_coordinates_are_scaled_to_the_device():
    dev = MockDevice(DeviceInfo("android", 720, 1600))  # half of the map size
    executor = MacroExecutor(dev, sleep=lambda _s: None, source_size=(1440, 3200))
    result = executor.run([{"action": "tap", "x": 720, "y": 1600}])
    assert result.ok
    assert dev.called("tap")[0][1] == (360, 800)


def test_no_scaling_without_source_size():
    dev = MockDevice(DeviceInfo("android", 720, 1600))
    executor = MacroExecutor(dev, sleep=lambda _s: None)
    executor.run([{"action": "tap", "x": 720, "y": 1600}])
    assert dev.called("tap")[0][1] == (720, 1600)


def test_scaling_is_identity_when_sizes_match():
    dev = MockDevice(DeviceInfo("android", 1440, 3120))
    executor = MacroExecutor(dev, sleep=lambda _s: None, source_size=(1440, 3120))
    executor.run([{"action": "swipe", "x1": 720, "y1": 2184, "x2": 720, "y2": 624}])
    assert dev.called("swipe")[0][1][:4] == (720, 2184, 720, 624)


def test_scaling_applies_to_swipe_and_long_press():
    dev = MockDevice(DeviceInfo("android", 640, 1386))
    executor = MacroExecutor(dev, sleep=lambda _s: None, source_size=(1440, 3120))
    executor.run([
        {"action": "swipe", "x1": 720, "y1": 2184, "x2": 720, "y2": 624},
        {"action": "long_press", "x": 1440, "y": 3120},
    ])
    assert dev.called("swipe")[0][1][:4] == (320, 970, 320, 277)
    assert dev.called("long_press")[0][1][:2] == (640, 1386)


# ---------------------------------------------------------------------------
# text-anchored tap
# ---------------------------------------------------------------------------

_WLAN_XML = (
    '<hierarchy rotation="0">'
    '<node text="WLAN" content-desc="" clickable="true" bounds="[40,180][240,260]" />'
    "</hierarchy>"
)


def test_tap_prefers_text_over_drifted_coordinates():
    dev = MockDevice(ui_xml=_WLAN_XML)
    executor = MacroExecutor(dev, sleep=lambda _s: None, source_size=(1000, 2000))
    result = executor.run([{"action": "tap", "x": 900, "y": 1800, "text": "WLAN"}])
    assert result.ok
    assert dev.called("tap")[0][1] == (140, 220)  # node center, not the scaled coords


def test_tap_falls_back_to_coordinates_when_label_is_gone():
    dev = MockDevice(
        ui_xml='<hierarchy><node text="Other" clickable="true" bounds="[0,0][100,100]" /></hierarchy>'
    )
    executor = MacroExecutor(dev, sleep=lambda _s: None)
    result = executor.run([{"action": "tap", "x": 33, "y": 44, "text": "WLAN"}])
    assert result.ok
    assert dev.called("tap")[0][1] == (33, 44)


def test_resolve_text_can_be_disabled():
    dev = MockDevice(ui_xml=_WLAN_XML)
    executor = MacroExecutor(dev, sleep=lambda _s: None, resolve_text=False)
    executor.run([{"action": "tap", "x": 33, "y": 44, "text": "WLAN"}])
    assert dev.called("tap")[0][1] == (33, 44)


def test_generated_operations_carry_element_text():
    app = AppMap(ANDROID_MAP)
    ops = app.build_operations()
    taps = [op.macro[-1] for op in ops.values()]
    assert any(step.get("text") for step in taps)


# ---------------------------------------------------------------------------
# accessibility click inside a macro
# ---------------------------------------------------------------------------


class ClickDevice(MockDevice):
    """Remembers whether the accessibility action was used."""

    def __init__(self, accepts=True):
        super().__init__()
        self.accepts = accepts
        self.clicks = []

    def click_text(self, label):
        self.clicks.append(label)
        return self.accepts


def test_tap_step_uses_the_accessibility_action():
    dev = ClickDevice(accepts=True)
    result = _executor(dev).run([{"action": "tap", "x": 282, "y": 1851, "text": "蓝牙"}])

    assert result.ok
    assert dev.clicks == ["蓝牙"]
    # the coordinate path must not have run at all
    assert dev.called("tap") == []


def test_tap_step_falls_back_to_coordinates_when_the_action_is_refused():
    dev = ClickDevice(accepts=False)
    result = _executor(dev).run([{"action": "tap", "x": 282, "y": 1851, "text": "蓝牙"}])

    assert result.ok
    assert dev.clicks == ["蓝牙"]          # tried first
    assert dev.called("tap")[0][1] == (282, 1851)


def test_tap_step_without_a_label_stays_coordinate_based():
    dev = ClickDevice()
    result = _executor(dev).run([{"action": "tap", "x": 100, "y": 200}])

    assert result.ok
    assert dev.clicks == []
    assert dev.called("tap")[0][1] == (100, 200)


def test_human_mode_taps_inside_the_element_and_varies_the_wait():
    """The same pixel in the same rhythm is what automation looks like."""
    from phoneagent.core.macro import MacroExecutor

    class Node:
        center = (500, 1000)
        bounds = (400, 900, 600, 1100)

    class Device(MockDevice):
        def __init__(self):
            super().__init__()
            self.points = []

        def tap(self, x, y):
            self.points.append((x, y))
            return True

        def dump_ui(self):
            return "<hierarchy/>"

    device = Device()
    executor = MacroExecutor(device, sleep=lambda _s: None, human=True, resolve_text=False)
    executor._lookup = lambda _label: Node()
    macro = [{"action": "tap", "text": "好", "x": 500, "y": 1000, "wait": 1.0},
             {"action": "tap", "text": "好", "x": 500, "y": 1000, "wait": 1.0}]
    executor.run(macro)

    assert len(device.points) == 2
    for x, y in device.points:
        assert 400 < x < 600 and 900 < y < 1100
    assert device.points[0] != device.points[1]      # not the same pixel twice
