"""The agent's display can be emptied by the system between tasks.

A virtual display outlives the app that was on it, so a task started an hour
later can find an empty screen. That used to surface as
"no_ui: the screen is off, locked, ..." which is both wrong and unactionable.
"""

from __future__ import annotations

from phoneagent.server import _ensure_content, _has_labels


class FakeDevice:
    def __init__(self, app="unknown", readable_after_launch=True):
        self.display_id = 7
        self.app = app
        self.readable_after_launch = readable_after_launch
        self.launched = []

    def current_app(self):
        return self.app

    def launch(self, package):
        self.launched.append(package)
        self.app = package if self.readable_after_launch else "unknown"
        return True

    def start(self, initial_app=""):
        self.launched.append(initial_app or "start")
        self.app = initial_app or "somewhere"
        return self.display_id


def test_has_labels_ignores_empty_attributes():
    assert _has_labels('<node text="蓝牙" bounds="[0,0][10,10]"/>')
    assert _has_labels('<node content-desc="返回"/>')
    assert not _has_labels('<node text="" content-desc=""/>')
    assert not _has_labels("<hierarchy display=6 windows=0></hierarchy>")
    assert not _has_labels("")


def test_content_is_left_alone_when_something_is_resumed():
    device = FakeDevice(app="com.android.settings")
    assert _ensure_content(device, "com.android.settings")
    assert device.launched == []  # nothing was touched


def test_empty_display_gets_an_app_again():
    device = FakeDevice(app="unknown")
    assert _ensure_content(device, "com.android.settings")
    assert device.launched == ["com.android.settings"]


def test_launch_that_never_lands_is_reported():
    device = FakeDevice(app="unknown", readable_after_launch=False)
    assert not _ensure_content(device, "com.android.settings")
    assert device.launched == ["com.android.settings"]
