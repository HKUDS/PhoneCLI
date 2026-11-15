"""Offline tests for the adb backend, using a fake `adb` executable.

No phone, no emulator, no adb install required: the fake binary logs its
arguments and answers with canned output, which is enough to pin down the
command shapes this backend sends to a real device.
"""

from __future__ import annotations

import stat

import pytest

from phoneagent.devices.adb import AdbDevice
from phoneagent.devices.base import DeviceError, UnicodeTextUnsupported

FAKE_ADB = r"""#!/bin/sh
echo "$@" >> "$FAKE_ADB_LOG"
case "$*" in
  *get-state*) echo device ;;
  *"wm size"*) printf 'Physical size: 1080x2400\nOverride size: 1080x1920\n' ;;
  *"getprop ro.product.model"*) echo "Pixel Test" ;;
  *"getprop ro.build.version.release"*) echo "14" ;;
  *"uiautomator dump"*) echo "UI hierchary dumped to: /sdcard/window_dump.xml" ;;
  *"cat /sdcard/window_dump.xml"*) printf '<hierarchy rotation="0"><node text="Wi-Fi" /></hierarchy>' ;;
  *"dumpsys window"*) echo "  mCurrentFocus=Window{1a2b u0 com.android.settings/com.android.settings.Settings}" ;;
  *"monkey -p"*) echo "Events injected: 1" ;;
  *"exec-out screencap -p"*) printf '\211PNG\r\n\032\n' ;;
  *"input text"*) ;;
  *) echo "" ;;
esac
"""


@pytest.fixture()
def adb(tmp_path, monkeypatch):
    script = tmp_path / "adb"
    script.write_text(FAKE_ADB)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "adb.log"
    log.write_text("")
    monkeypatch.setenv("FAKE_ADB_LOG", str(log))
    device = AdbDevice(serial="FAKE", adb=str(script))
    return device, log


def _log_lines(log):
    return [line for line in log.read_text().splitlines() if line.strip()]


# ---------------------------------------------------------------------------


def test_info_prefers_override_size(adb):
    device, _ = adb
    info = device.info()
    assert info.platform == "android"
    assert info.size == (1080, 1920)  # override beats physical
    assert info.model == "Pixel Test"
    assert info.os_version == "14"


def test_dump_ui_pulls_uiautomator_xml(adb):
    device, log = adb
    xml = device.dump_ui()
    assert 'text="Wi-Fi"' in xml
    commands = _log_lines(log)
    assert any("uiautomator dump /sdcard/window_dump.xml" in c for c in commands)
    assert any("exec-out cat /sdcard/window_dump.xml" in c for c in commands)


def test_screenshot_returns_png(adb):
    device, _ = adb
    png = device.screenshot()
    assert png.startswith(b"\x89PNG")


def test_tap_and_swipe_commands(adb):
    device, log = adb
    assert device.tap(10, 20)
    assert device.swipe(1, 2, 3, 4, 500)
    assert device.long_press(5, 6, 1000)
    commands = _log_lines(log)
    assert any(c.endswith("input tap 10 20") for c in commands)
    assert any(c.endswith("input swipe 1 2 3 4 500") for c in commands)
    assert any(c.endswith("input swipe 5 6 5 6 1000") for c in commands)


def test_press_key_maps_to_keycodes(adb):
    device, log = adb
    device.press_back()
    device.press_home()
    device.press_key("enter")
    commands = _log_lines(log)
    assert any(c.endswith("input keyevent 4") for c in commands)
    assert any(c.endswith("input keyevent 3") for c in commands)
    assert any(c.endswith("input keyevent 66") for c in commands)


def test_input_text_quotes_and_escapes_spaces(adb):
    device, log = adb
    device.input_text("hello world")
    commands = _log_lines(log)
    # `input text` turns %s back into a space on the device.
    assert any("input text 'hello%sworld'" in c for c in commands)


def test_input_text_rejects_unicode(adb):
    device, _ = adb
    with pytest.raises(UnicodeTextUnsupported):
        device.input_text("你好")


def test_current_app_parses_dumpsys_focus(adb):
    device, _ = adb
    assert device.current_app() == "com.android.settings"


def test_launch_uses_monkey(adb):
    device, log = adb
    assert device.launch("com.android.settings")
    assert any("monkey -p com.android.settings" in c for c in _log_lines(log))


def test_is_ready(adb):
    device, _ = adb
    assert device.is_ready()


def test_missing_adb_binary_reports_clearly(tmp_path):
    device = AdbDevice(serial="X", adb=str(tmp_path / "nope"))
    with pytest.raises(DeviceError, match="adb binary not found"):
        device.tap(1, 1)
