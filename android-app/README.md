# android-app — M0 shell

对话界面的最小可运行壳，用来先打通"能编译、能安装、能跑"这条链路。

```bash
./build.sh                                   # aapt2 → javac → d8 → zipalign → apksigner
adb install -r build/app-debug.apk
adb shell am start -n com.phoneagent.app/.MainActivity
```

## 为什么不走 Gradle（暂时）

- 这台机器没有 JDK，Homebrew 目录不可写，Gradle 发行版下载只有 ~5KB/s；
- `build.sh` 直接用 `build-tools` 里的 `aapt2 / d8 / zipalign / apksigner`，
  依赖为零，12KB 的 APK 十秒内出包。

Gradle 工程文件（`settings.gradle.kts` / `app/build.gradle.kts`）已经写好，
等网络或 JDK 条件允许时可以直接 `./gradlew assembleDebug`；接 Chaquopy
（M2）时也必须回到 Gradle。

## 安装前置（HyperOS）

- 开发者选项 → **USB 安装** 打开（否则 `INSTALL_FAILED_USER_RESTRICTED`）
- 即使打开了，每次安装仍会弹 `com.miui.securitycenter` 的「USB安装提示」，
  点是「继续安装」才会真正安装；没人点就报 `Install canceled by user`

这个弹窗可以自动点（我们已经有输入注入）：

```bash
# 一边跑 install，一边轮询 dump，出现「继续安装」就 tap 它的 bounds 中心
adb shell input tap <cx> <cy>
```

实测：`adb install --no-incremental -r app-debug.apk` + 自动点按 → `Success`。

## 下一步

M1：虚拟屏 helper（`app_process`），先用 `adb shell` 手工验证建屏 → 拉 App → 截图 → 点击。
