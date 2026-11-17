English · [中文](README.zh.md)

# android-app

The APK: a chat interface around the agent in
[`../phoneagent`](../phoneagent). The target app runs on a screen the phone does not
show, so the user keeps the one they were using; an app that only runs on the main
screen is followed there, and moved back when the step is done.

| Piece | Where |
|---|---|
| Chat UI, settings, mode switch, backup | `app/src/main/java/com/phoneagent/app/MainActivity.java` |
| Foreground service that owns the Python runtime | `AgentService.kt` |
| Accessibility bridge: actions and the control tree | `PhoneAgentAccessibilityService.kt` |
| Virtual-display client (talks to the helper) | `AgentService.kt` + `../android-helper` |
| Wireless-debugging pairing | `PairingService.kt`, `pairing/`, `MdnsAdbFinder.kt`, `AdbPortScanner.kt` |
| Floating capsule over other apps | `CapsuleService.kt` |
| Built-in browser for pages the agent reads | `BrowserActivity.kt`, `BrowserBridge.kt` |
| Export/import of everything a reinstall needs | `Backup.java` |

## Building

The APK bundles CPython (Chaquopy) and the shell helper, so two things have to be
staged before Gradle runs:

```bash
./sync-python.sh     # ../phoneagent (+ two upstream files) -> app/src/main/python
./sync-helper.sh     # ../build-helper/pa-helper.jar   -> app/src/main/assets
./sync-maps.sh       # phoneagent/maps/*.yaml          -> app/src/main/assets/maps

../.toolchain/gradle-8.11.1/bin/gradle --no-daemon assembleDebug
../.toolchain/gradle-8.11.1/bin/gradle --no-daemon assembleRelease
```

`assembleRelease` signs with the key named in `keystore.properties`
(`storeFile`, `storePassword`, `keyAlias`, `keyPassword`). That file and the
`.jks` are not in the repository; without them the release APK is unsigned.

Gradle needs a Python for Chaquopy: it reads `python.path` from
`local.properties`, then `$BUILD_PYTHON`, then `python3`.

`build.sh` builds a debug APK with `aapt2 → javac → d8 → zipalign → apksigner`
and no Gradle at all. It cannot package Chaquopy, so it only suits the UI shell.

## The APK contains

| Path inside | Contents |
|---|---|
| `assets/maps/settings.yaml` | A crawled app map shipped as a starting example |
| `assets/pa-helper.jar` | The virtual-display helper, extracted and started at runtime |
| `assets/chaquopy/` | CPython runtime, `phoneagent`, and the two upstream modules it imports |

## Running it

- **Android 14+**, arm64 only: the helper and the Python runtime are built for `arm64-v8a`.
- **Wireless debugging** must be on. The app finds adbd's port over mDNS, pairs
  itself (it reads the pairing code from the system dialog, which is why the
  accessibility service has to be enabled first), and talks to its own adbd over
  loopback — no computer involved.
- **Accessibility service** for PhoneGo, enabled in system settings.
- **A model API key** typed into 设置; it is encrypted with a key in the Android
  Keystore and never leaves the device.

## Settings worth knowing

| Item | Effect |
|---|---|
| 自动 / 看图 | With maps: route → replay. Without: every step decided from a screenshot |
| 设置 → 导出备份 | Writes pairing identity, maps, sessions and model settings to `Download/PhoneGo-backup-*.zip` (the key is in the clear there; it cannot be moved any other way) |
| 设置 → 导入备份 | Restores that zip and restarts the agent |
| 长按「设置」 | System pages: accessibility, battery exemption, the two backup entries |

## Installing on HyperOS / MIUI

Developer options → **Install via USB** has to be on, otherwise the install fails
with `INSTALL_FAILED_USER_RESTRICTED`. Even then the phone shows a
`com.miui.securitycenter` dialog asking to continue; installing only completes
after「继续安装」is tapped, and the dialog cancels itself after a few seconds.
