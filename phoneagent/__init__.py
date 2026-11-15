"""phoneagent — a platform-agnostic phone agent core.

This package is the new home for the PhoneCLI algorithm (app maps, macro
replay, macro/VLM routing) with the device transport pulled out behind a
single protocol, so the same agent can run against:

    * iOS via WebDriverAgent        -> phoneagent.devices.wda.WdaDevice
    * Android via adb (host side)   -> phoneagent.devices.adb.AdbDevice
    * Android on-device (APK)       -> future: shell helper + virtual display
    * a canned recording (tests)    -> phoneagent.devices.mock.MockDevice

Layout::

    phoneagent/
      core/
        app_map.py     load/query YAML app maps (iOS + Android)
        macro.py       replay abstract macro steps against any device
      devices/
        base.py        PhoneDevice protocol + shared types
        adb.py         Android host-side backend (adb shell)
        wda.py         iOS backend (wraps phonecli/device.py)
        mock.py        in-memory backend for tests

The upstream ``phonecli/`` and ``phonecli_android/`` trees are kept
untouched as reference implementations.
"""

__all__ = ["core", "devices"]
__version__ = "0.1.0"
