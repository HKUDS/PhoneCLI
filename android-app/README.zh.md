[English](README.md) · 中文

# android-app

App 本体：给 [`../phoneagent`](../phoneagent) 套上对话界面。目标 App 在一块用户看不见的
屏上工作，手里那块屏基本不受打扰；只肯在主屏上运行的 App，会被跟过去做完再搬回来。

| 部件 | 位置 |
|---|---|
| 对话界面、设置、模式切换、备份 | `app/src/main/java/com/phoneagent/app/MainActivity.java` |
| 持有 Python 运行时的前台服务 | `AgentService.kt` |
| 无障碍桥：执行动作、读取控件树 | `PhoneAgentAccessibilityService.kt` |
| 虚拟屏客户端（与 helper 通信） | `AgentService.kt` + `../android-helper` |
| 无线调试配对 | `PairingService.kt`、`pairing/`、`MdnsAdbFinder.kt`、`AdbPortScanner.kt` |
| 在其他 App 上悬浮的小胶囊 | `CapsuleService.kt` |
| 内置浏览器（读网页类结果） | `BrowserActivity.kt`、`BrowserBridge.kt` |
| 重装后恢复一切所需的导出/导入 | `Backup.java` |

## 构建

APK 里打包了 CPython（Chaquopy）和 shell helper，所以 Gradle 之前要先同步两样东西：

```bash
./sync-python.sh     # ../phoneagent（+ 两个上游文件）→ app/src/main/python
./sync-helper.sh     # ../build-helper/pa-helper.jar  → app/src/main/assets
./sync-maps.sh       # phoneagent/maps/*.yaml         → app/src/main/assets/maps

../.toolchain/gradle-8.11.1/bin/gradle --no-daemon assembleDebug
../.toolchain/gradle-8.11.1/bin/gradle --no-daemon assembleRelease
```

`assembleRelease` 用 `keystore.properties` 里指定的密钥签名（`storeFile`、
`storePassword`、`keyAlias`、`keyPassword`）。这个文件和 `.jks` 都不在仓库里，
缺了它们打出来的 release 包是未签名的。

Gradle 需要一个 Python 给 Chaquopy 用：依次读 `local.properties` 的 `python.path`、
`$BUILD_PYTHON`、最后 `python3`。

`build.sh` 则完全不用 Gradle，用 `aapt2 → javac → d8 → zipalign → apksigner`
打调试包；它没法打包 Chaquopy，所以只适合做界面壳。

## APK 里有什么

| 包内路径 | 内容 |
|---|---|
| `assets/maps/settings.yaml` | 一张爬好的示例地图，随包发布 |
| `assets/pa-helper.jar` | 虚拟屏 helper，运行时释放并启动 |
| `assets/chaquopy/` | CPython 运行时、`phoneagent`，以及它 import 的两个上游模块 |

## 运行前提

- **Android 14 及以上**，仅 arm64：helper 和 Python 运行时都是按 `arm64-v8a` 编的。
- **必须打开无线调试**。App 通过 mDNS 找 adbd 端口、自己完成配对（配对码要它自己从系统
  弹窗里读，所以得先开无障碍服务），之后走回环地址跟自己的 adbd 通信——不需要电脑。
- **无障碍服务**：在系统设置里给 PhoneGo 打开。
- **模型 API Key**：在「设置」里填，用 Android Keystore 加密后存在本机。

## 几个设置项

| 项 | 作用 |
|---|---|
| 自动 / 看图 | 有地图时：路由 → 回放；没有时：每步都靠截图决策 |
| 设置 → 导出备份 | 把配对身份、地图、会话、模型设置写进 `下载/PhoneGo-backup-*.zip`（key 在里面是明文，否则无法跨设备迁移） |
| 设置 → 导入备份 | 恢复该 zip 并重启 agent |
| 长按「设置」 | 系统相关页：无障碍、电池优化，以及上面两个备份入口 |

## 在 HyperOS / MIUI 上安装

开发者选项里的 **USB 安装** 必须打开，否则安装会报 `INSTALL_FAILED_USER_RESTRICTED`。
即便打开了，手机还会弹一个 `com.miui.securitycenter` 的确认框，必须点「继续安装」才
真正安装；没人点，几秒后它会自己取消。
