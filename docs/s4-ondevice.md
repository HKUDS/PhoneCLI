# S4：设备内后端（虚拟屏）——可行性实测与实施计划

日期：2026-11-05　设备：Redmi K90（M332BF，Android 16 / HyperOS 3.0）

## 目标

把 `devices/adb.py` 里那套**电脑侧**能力搬到手机内部：APK 自己建一块虚拟屏、
把目标 App 拉上去、读屏、点按，用户主屏不受影响。这样 `PhoneAgent` 的上下层
（app map / 宏重放 / 路由 / 兜底）一行都不用改。

## 实测：这台机器具备什么

```
$ python3 -m phoneagent.ondevice --serial e3fa9e60

PASS  shell uid                      uid=2000(shell)
PASS  app_process                    /system/bin/app_process64
FAIL  cmd display create-virtual-display   not offered by this ROM
PASS  am start --display             flag accepted (display does not exist)
PASS  screencap -d                   supported
PASS  wireless debugging setting     adb_wifi_enabled=0
PASS  display devices                1 display device(s)
```

结论：**硬性条件（shell 身份、app_process、按 display 启动、按 display 截图）
全部具备；唯一缺的是"谁来创建这块虚拟屏"。**

- `cmd display` 没有 `create-virtual-display` 子命令（只有亮度/日志类命令），
  所以**不可能用 shell 一行命令建屏**；
- 但 `app_process` 在，且 `am start --display` / `screencap -d` 都支持——
  也就是说**只要有一个 shell 身份的 Java 进程把屏建出来，后面的活都能干**。

## 缺口与实现路径

| 缺口 | 说明 | 方案 | 工作量 |
|---|---|---|---|
| ① 建屏 | `DisplayManager.createVirtualDisplay` / `SurfaceControl` 需要 Java 进程，且要 `CAPTURE_VIDEO_OUTPUT` 一类权限（shell 有） | 写一个 `app_process` 启动的 helper（scrcpy 的 server 就是这么做的），暴露一个本地 socket（JSON 行协议） | 1–2 周 |
| ② 设备内 shell | APK 不是 shell，拿不到 `INJECT_EVENTS` / `am` | 复用 Mika 的路线：**无线调试自配对**（SPAKE2 + adb 协议），或先接 Shizuku | 自配对 3–4 周；Shizuku 3–5 天 |
| ③ 通道 | App ↔ helper | AIDL（同进程组）或 127.0.0.1 socket；helper 由 `app_process` 起，App 用 Binder/文件描述符交接 | 含在 ① 里 |

### 建议的落地顺序

1. **A（1–2 周）**：写 helper + 协议，先用 `adb shell app_process` 手工启动验证：
   `createDisplay(1280x2772)` → `launch(pkg, displayId)` → `screencap -d` 出图 →
   `input -d` 点击 → 回收。这一步不碰 App，纯 shell，能独立验证。
2. **B（3–5 天）**：接 Shizuku，让 APK 拿到 shell 身份并启动 A 的 helper。
3. **C（3–4 周）**：把 Shizuku 换成自配对无线调试，去掉外部依赖。

## 代码现在的状态

- `phoneagent/ondevice.py`：能力探针（已可用）+ 每个检查项对 S4 的含义
- `PhoneDevice` 协议（16 个方法）已与传输解耦，`OnDeviceDevice` 只需实现它，
  agent / macro / app map 全部复用
- 回放层不依赖"屏幕是不是虚拟的"：`source_size`、`launch_intent`、文字锚定
  都是设备无关的

## 风险

1. **权限随版本收紧**：`CAPTURE_VIDEO_OUTPUT`、`am start --display` 在零售版
   Android 上属于灰区，Mika 自己也是靠"用户授权无线调试"绕过的。
2. **厂商差异**：HyperOS 已经把 `cmd display` 裁过一轮，说明 ROM 会持续改动这块。
3. **焦点与输入法**：虚拟屏打字需要把 IME 绑到那块屏上，Mika 文档里也提到
   Android 14 之前会抢主屏焦点。
4. **音量/通知**：目标 App 在虚拟屏上仍会出声，需要 `appops set PLAY_AUDIO ignore`。

## 结论

S4 **不是"再写一个 backend"那么简单**：协议和算法层已经就绪，真正的两块硬骨头是
**虚拟屏 helper** 和 **设备内 shell 获取**。本机实测证明这两块都有成熟路径，
但各自是周级工作量，不适合和 S1–S3 混在同一次迭代里。

---

## M1 实测：建屏这一步到底卡在哪（2026-11-05 追记）

三条路都试过了，结论如下。

### ① App 自己建屏 —— 能建，但装不下别人的 App

真机日志（Redmi K90，Android 16）：

```
I PhoneAgent: VD: id=2 1280x2772
D dumpsys: DisplayDeviceInfo{"pa-probe": uniqueId="virtual:com.phoneagent.app,10338,pa-probe,0", ...}
           owner com.phoneagent.app (uid 10338), FLAG_OWN_CONTENT_ONLY
           canHostTasks false
```

- **不需要任何权限**，只要 `VIRTUAL_DISPLAY_FLAG_PUBLIC | VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY`
- 关键限制：`canHostTasks false` —— 这块屏只能画 App 自己的内容，**拉不进第三方 App**
- 踩过的坑：`PUBLIC` 与 `SHOW_WHEN_LOCKED_INSECURE`（bit 5）互斥，一起传会抛
  `IllegalArgumentException`；不带 `OWN_CONTENT_ONLY` 则要求
  `CAPTURE_VIDEO_OUTPUT`/`MediaProjection`

### ② shell 进程建屏 —— 被 uid/包名检查拦住

用 `app_process`（uid 2000）调公开 API：

```
SecurityException: packageName must match the calling uid
  at IDisplayManager$Stub$Proxy.createVirtualDisplay(...)
```

原因：`DisplayManager` 用**它持有的 system context** 的包名（`android`），
而调用者是 shell（`com.android.shell`）。包一层 `ContextWrapper` 覆盖
`getPackageName()` 也没用——`DisplayManagerGlobal` 用的是自己的 `mContext`。

### ③ shell + 反射 DisplayManagerGlobal —— 签名在 API 33 变了

反射列出的实际签名（API 33）：

```
createVirtualDisplay params=5
    Context / MediaProjection / VirtualDisplayConfig / Callback / Executor
```

不再有「(context, packageName, name, w, h, dpi, surface, flags…)」那种老签名，
包名要放进 `VirtualDisplayConfig`。**这是唯一还没走通、但看起来可行的路**：
构造 `VirtualDisplayConfig.Builder(...)` → `setSurface/setFlags` → 传入
`createVirtualDisplay`，让 display 归属 shell，从而可能拿到 `canHostTasks=true`。

### 结论（对产品的直接影响）

- 「App 建屏 + shell 拉 App」这个组合**不成立**（App 建的屏装不下任务）。
- 建屏必须由**有 `ADD_TRUSTED_DISPLAY`/`INTERNAL_SYSTEM_WINDOW` 一类身份**的进程完成，
  也就是 shell 或系统。
- 所以 S4 的真正门槛仍是：**让 shell 身份建出一块 hostable 虚拟屏**（路径 ③），
  其余（`am start --display`、`screencap -d`、`input -d`）都已实测可用。
- 工作量估计不变：1–2 周，主要花在 `VirtualDisplayConfig` 反射 + 兼容多版本。

---

## M1 结论：shell 身份建出可承载任务的虚拟屏 ✅（2026-11-05）

### 走通的配方

```
app_process (uid 2000)
  └─ ActivityThread.systemMain() 反射取 system context
  └─ ContextWrapper 覆盖 getPackageName() → "com.android.shell"   ← 关键
  └─ ImageReader.newInstance(1280, 2772, RGBA_8888, 3)
  └─ VirtualDisplayConfig.Builder(name, w, h, dpi)
        .setSurface(reader.getSurface())
        .setFlags(VIRTUAL_DISPLAY_FLAG_PUBLIC)
        .setUniqueId("virtual:com.android.shell/<name>")
  └─ DisplayManagerGlobal.createVirtualDisplay(ctx, null, config, null, null)  ← 反射
```

真机输出：

```
helper: OK display=4 size=1280x2772
dumpsys: canHostTasks = true          ← 关键：能装任务
am start --display 4 -n com.android.settings/.MiuiSettings
  → topResumedActivity = com.android.settings/.MiuiSettings
frame → FRAME /data/local/tmp/pa-frame.png 1280x2772   ← 设置首页正常渲染
input -d 4 tap 400 1030                                ← 点击生效
frame → 画面已跳到小米账号页
```

### 三个坑（都已解决）

| 坑 | 现象 | 解法 |
|---|---|---|
| 包名检查 | `SecurityException: packageName must match the calling uid` | 不能用公开 `DisplayManager`（它用自己持有的 system context）；改为反射 `DisplayManagerGlobal` 并传入自定义 context |
| API 33 签名变化 | 老的 `(context, packageName, name, w, h, dpi, surface, flags…)` 不存在 | 改用 `VirtualDisplayConfig`（`Builder(name,w,h,dpi)` + `setSurface/setFlags/setUniqueId`） |
| 帧回调不触发 | `ImageReader` 永远 `NOFRAME` | 主线程阻塞在 stdin，回调需要独立的 `HandlerThread` |

### 还需要补的两块（不影响可行性）

1. **读控件树**：`uiautomator dump` 只 dump 默认屏；虚拟屏上的树要靠 App 自己的
   `AccessibilityService`（`getWindowsOnAllDisplays`），或用 shell 的
   `uiautomator` 无法覆盖——这是 App 侧的活。
2. **设备内启动 helper**：现在还是 `adb shell app_process` 拉起来的，
   APK 要用 Shizuku / 无线调试自配对才能自己起。

### 对产品的意义

**S4 最大的不确定性已经消除**：虚拟屏能建、能装 App、能出画面、能点击。
剩下的都是工程拼接，不再是"能不能做到"的问题。

---

## M2：agent 驱动虚拟屏 ✅（2026-11-05）

`phoneagent/devices/virtual.py` 把 helper 包成 `PhoneDevice`：

| 协议方法 | 实现 |
|---|---|
| `screenshot` | helper 在自己的 ImageReader 上抓帧 → PNG |
| `tap/swipe/long_press` | `input -d <display-id> ...` |
| `launch` | `cmd package resolve-activity` + `am start --display <id> -n ...` |
| `press_back/home/key` | `input -d <id> keyevent ...` |
| `dump_ui` | 暂返回空树（`uiautomator` 只 dump 默认屏；等 APK 的无障碍服务） |

### 实测：纯截图 VLM 在虚拟屏上完成任务

```
display = 13
settings launched
  round 1 [vlm   ] tap     ok
  round 2 [vlm   ] tap     ok
  round 3 [finish] 已成功打开蓝牙设置
result: ok in 3 round(s)
```

结束帧确认：屏幕停在「蓝牙」页（开关、可用设备、高级设置），**主屏全程未受影响**。

### 顺手修的一个坑

`ImageReader` 原来「取完即清空」，界面不动时第二次抓帧会 `NOFRAME`。
改成缓存 `Bitmap`（新帧到达才替换），重复抓帧稳定返回上一帧。

### 现在的产品链路

```
对话界面 (APK, M0)
   ↓
agent 核心 (phoneagent, Python)
   ↓
VirtualDisplayDevice  ← M2 新增
   ↓
shell helper 建的虚拟屏 (M1)
   ↓
真实 App（设置/蓝牙/…），主屏不动
```

缺的只剩两块：**设备内自己起 helper**（Shizuku/自配对，M3）和**读控件树**
（无障碍服务，影响文字锚定与屏幕识别，不影响纯截图模式）。

---

## M2.5：对话 App ↔ 主机 agent ↔ 虚拟屏（2026-11-05）

### 为什么不是 Chaquopy

本机实测：`repo.maven.apache.org` / `dl.google.com` / `services.gradle.org` **全部超时**。
没有 Gradle 就没有 Chaquopy，也没有 Shizuku（都从 Maven 拉包）。所以先把"大脑"留在
主机，手机当遥控器——架构上诚实，且当天就能验证整条交互。

### 实现

```
手机 App（Compose/View 对话页）
   │  POST /task  {"text": "..."}      ← adb reverse tcp:8765 tcp:8765
   │  GET  /task/<id>  → steps + frame
   ▼
主机 phoneagent.server（stdlib http.server）
   └─ PhoneAgent + VirtualDisplayDevice（纯截图模式）
        └─ shell helper → 虚拟屏 → 真实 App
```

- `phoneagent/server.py`：任务队列 + 轮询接口，帧以 base64 随状态返回
- App 侧：`HttpURLConnection` + `org.json`（**零第三方依赖**，仍可用 `build.sh` 打包）
- 虚拟屏刚建好时没有内容，`ImageReader` 不出帧 → 加 `--start-app` 先把一个 App 放上去

### 实测

```
POST /task {"text":"open bluetooth settings"}  → id=6ad158cd
GET  /task/6ad158cd → state=done, steps=3, frame=161492 bytes(base64)
```

App 端已能显示步骤与虚拟屏画面；`am start ... --es task "..."` 可绕过输入法直接派任务
（`input text` 打不进 EditText 的问题尚未定位，属输入法焦点问题）。

### 已知问题

1. **主屏焦点**：虚拟屏上启动 App 时，主屏 `mCurrentFocus` 会短暂变 null；
   显式把 App 拉回前台后恢复正常。
2. **输入法**：ADB `input text` 无法写入 App 的 EditText（真机输入法焦点问题），
   测试用 intent extra 绕过。
3. **全在手机上**：仍需要 Chaquopy/Shizuku，卡在 Gradle 网络。
