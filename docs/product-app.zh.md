[English](product-app.md) · 中文

# PhoneGo：对话式 App + 虚拟屏执行

**PhoneGo**（工程名仍是 `phonecli` / `phoneagent`，包名 `com.phoneagent.app` 不变）。

目标：一个 APK，打开是对话界面；用户说一句话，agent 在一块看不见的虚拟屏上
操作目标 App 完成任务，主屏不受打扰。对标 Mika，但**导航层沿用 PhoneCLI 的
app map + 宏**（编译一次、确定性重放），而不是全靠 VLM 逐步试探。

## 分层

```
┌───────────────────────────────────────────────┐
│ ① 对话界面（Compose）                          │  用户说话、看步骤、接管审批
├───────────────────────────────────────────────┤
│ ② 任务服务（前台 Service + 通知 + 队列）        │  后台跑、可插话、可取消
├───────────────────────────────────────────────┤
│ ③ agent 核心（phoneagent/，Python 现有代码）    │  路由 → 宏重放 → 校验 → VLM 兜底
├───────────────────────────────────────────────┤
│ ④ 设备层（PhoneDevice 协议）                    │  adb（电脑）/ on-device（手机内）
├───────────────────────────────────────────────┤
│ ⑤ 设备内 shell + 虚拟屏 helper                  │  唯一全新的大件
└───────────────────────────────────────────────┘
```

① ② ⑤ 是新的；③ ④ 已经有了，且有 83 个测试兜底。

## 关键决策：Python 怎么进 APK

| 方案 | 做法 | 优点 | 代价 |
|---|---|---|---|
| **A. Chaquopy**（推荐先走） | Gradle 插件把 CPython 打进 APK，`phoneagent/` **原样复用** | 一周内能跑通端到端；算法层零重写；调参/迭代仍在电脑上 | 包体 +20MB；启动慢 1–2s；JNI 边界调试麻烦 |
| **B. Kotlin 重写** | agent / app_map / macro 全部翻译 | 启动快、包小、可上架 | 2–4 人月；两套实现会漂移 |

**建议：先 A 后 B。** 先用 Chaquopy 把产品链路跑通（对话 → 任务 → 虚拟屏 →
完成），验证交互和可靠性；等形态稳定了再考虑把热路径翻成 Kotlin。

Python 侧要做的最小改动：`OnDeviceDevice`（实现 `PhoneDevice`，通过 JNI 调
Kotlin 的 helper 客户端），以及把 `print` 换成回调。

## 里程碑

| 阶段 | 内容 | 可验证的结果 | 依赖 |
|---|---|---|---|
| **M0** | 空壳 APK：Compose 对话页 + 前台 Service，假 agent 回显 | 手机上能装、能对话 | JDK + SDK |
| **M1** | 虚拟屏 helper（`app_process`）+ 手工拉起：建屏 → 把设置拉上去 → `screencap -d` 出图 → `input -d` 点击 | 命令行跑通"后台操作一个 App" | 无 APK 依赖 |
| **M2** | Chaquopy 集成：APK 里跑 `PhoneAgent`，设备层换成 Java 桥 | 对话触发一次真实宏重放 | M0+M1 |
| **M3** | 设备内 shell（先 Shizuku） | 拔掉电脑也能跑 | M2 |
| **M4** | 会话体验：步骤流、插话、接管、审批 | 像 Mika 那样"边看边管" | M3 |
| **M5** | 自配对无线调试（去 Shizuku） | 零外部依赖 | M3 |

M1 和 M3 是硬骨头（各 1–2 周 / 3–5 天），其余是把已有能力搬进壳里。

## 现在就能复用的

| 模块 | 复用方式 |
|---|---|
| `core/app_map.py` | 原样，YAML 格式不变 |
| `core/macro.py` | 原样，新增 `OnDeviceDevice` 即可 |
| `core/agent.py` | 原样（路由/兜底/重试/planner 都已就绪） |
| `core/llm.py` | 原样（OpenAI 兼容，可指向任何服务商） |
| `build_map.py` / `validate.py` / `redact.py` | 原样，可作为"手机上重建地图"的工具 |
| `devices/base.py` | 就是 ①→⑤ 的接口，`OnDeviceDevice` 实现它 |

## 与 Mika 的差异（也是卖点）

Mika 的路线是**从成功运行里学路线**（在线、需先成功一次）。
我们的路线是**离线编译 app map**（一次编译、多次 0 模型调用重放），
实测 depth1 落点 100%、每操作 10.6 s、0 次模型调用。

两者可以互补：编译覆盖不到的界面，退回 VLM 逐步探索；探索成功的轨迹再固化成宏。

## 风险

1. **虚拟屏权限**：`DisplayManager`/`SurfaceControl` 属灰区，厂商可能收紧（HyperOS
   已经把 `cmd display` 裁过）。
2. **设备内 shell**：无线调试自配对是周级工作，Shizuku 让用户多装一个 App。
3. **Python 包体**：Chaquopy 方案 APK ≈ 60–80MB，需评估是否可接受。
4. **后台存活**：国产 ROM 的省电策略是长期敌人，参考 Mika 的"兼容性自检"思路。

---

## M2 完成：Python 跑进 APK（2026-11-05）

### 环境准备（全部绕开沙箱限制）

| 障碍 | 解法 |
|---|---|
| Homebrew 目录不可写 | 用 Android Studio 自带的 JBR 21 作 JDK |
| Gradle 下载 5KB/s | 用户开代理后 3.1MB/s；`GRADLE_USER_HOME` 指到工作区内 |
| AGP 要写 `$SDK/.android` | 把 SDK 复制进工作区（345MB），`ANDROID_USER_HOME` 指到工作区 |
| `ANDROID_SDK_HOME` 与 `ANDROID_USER_HOME` 冲突 | 只保留后者 |
| Chaquopy 无 Kotlin-DSL srcDir | `sync-python.sh` 把 `phoneagent/` 同步进 `app/src/main/python/` |

### 结果

```
Python 已就绪：3.8.20 (default, Oct 12 2024, 20:34:19)
phoneagent 0.1.0
app_map 模块：已加载
```

- APK 14MB（arm64-v8a），含 `libpython3.8.so` + 我们的包
- `phoneagent/core/prompts.py` 改为**自包含**（vendored 自 `phonecli/prompts.py`），
  因为 APK 里没有上游 `phonecli` 包

### 构建命令

```bash
cd android-app && ./sync-python.sh
unset ANDROID_SDK_HOME ANDROID_PREFS_ROOT
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export GRADLE_USER_HOME="$PWD/../.toolchain/gradle-home"
export ANDROID_HOME="$PWD/../.toolchain/android-sdk"
export ANDROID_USER_HOME="$PWD/../.toolchain/android-home"
../.toolchain/gradle-8.11.1/bin/gradle --no-daemon assembleDebug
```

### 还差什么

- App 里调用 `PhoneAgent.run()` 需要设备通道（shell）→ M3
- 目前 App 只验证了"Python 能起、包能 import"
