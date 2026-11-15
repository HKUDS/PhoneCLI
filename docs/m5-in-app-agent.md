# M5：agent 跑进 APK

M4 结束时，App 已经能自己建虚拟屏、自己截帧、按标签点击。但"大脑"还在电脑上：
MainActivity 通过 `adb reverse` 把任务 POST 给宿主机进程。

M5 把这一环拿掉。**手机自己跑 agent，自己开 HTTP 服务，界面对 127.0.0.1 说话。**

```
mDNS/扫描 → TLS 1.3 → adb shell → app_process helper → 虚拟屏
                                                        ↓
         MainActivity ←── HTTP 127.0.0.1:8765 ←── agent（Chaquopy 内）
```

## 一、把 server 拆成可注入的

```python
@dataclass
class ServerConfig:
    device_factory: Callable[[], object]   # 宿主机: VirtualDisplayDevice(serial=...)
                                           # 设备内: 已存在的那个 device
    llm_factory: Callable[[], object]      # 真模型 / MockLLM
    keep_device: bool = False              # 设备内必须 True
```

宿主机和设备内共用同一套 HTTP 接口，行为一致，只是设备从哪来不同。

一个细节：`RUN_LOCK` 让任务串行。虚拟屏只有一块，两个 agent 同时点会互相打架。

## 二、配置

`filesDir/agent.json`：

```json
{
  "api_key": "sk-or-v1-...",
  "api_base": "https://openrouter.ai/api/v1",
  "model": "qwen/qwen3.8-flash",
  "mock": "tap(0.5,0.62);finish(done)"
}
```

`mock` 用 `;` 分隔一串视觉模型回复，**不需要网络也不需要 key**，用来验证链路本身。

调试时写入：

```bash
adb push agent.json /data/local/tmp/agent.json
adb shell run-as com.phoneagent.app cp /data/local/tmp/agent.json files/agent.json
```

## 三、四个卡了一整轮的 bug

### 1. 坐标格式对不上，所有标签被丢掉

无障碍服务输出的 `bounds="x1,y1,x2,y2"`，而 agent 的解析器（`_BOUNDS_RE`）要的是
uiautomator 的 `bounds="[x1,y1][x2,y2]"`。结果 `_ui_texts()` 永远返回空，
预检直接判定 `no_ui`。

**现在输出 uiautomator 格式**，顺带把 `desc` 改成标准的 `content-desc`。

### 2. 搬过家的任务拿不到无障碍根节点

`getRoot()` 返回 `null`，但像素是正常的、截图没问题。
`dumpsys accessibility` 里那个窗口一直 `focused=false, active=false`。

原因是这个任务被 `am start --display` 在不同 display 之间搬来搬去（taskId 一直是
90），它的无障碍连接早就失效了。**强制 `force-stop` 后新建任务（taskId=207）立刻可读。**

```python
# launch() 现在带 -S
am start -S --display <id> -n <activity>
```

### 3. MIUI 桌面拒绝在副屏启动

`am start --display N -n com.miui.home/.launcher.Launcher` **什么任务都不建**，
屏幕空着。Settings 则可以。

空屏的后果不只是黑屏——`ImageReader` 拿到的是**本屏镜像**，所以第一版"截图"里
赫然是我们自己的聊天界面。

**`start_display` 现在会回退到 `com.android.settings`**，并轮询 `current_app()`
确认真的有东西resumed。
（`canHostTasks` 在 `dumpsys display` 里始终是 `false`，`VirtualDisplayConfig.Builder`
上也没有对应的 setter；`setHomeSupported` 改不动它。不影响使用。）

### 4. force-stop 会清掉无障碍服务

```
Enabled services:{com.phoneagent.app/...}   ← 装好后
Enabled services:{}                          ← force-stop 之后
```

所以测试时每次重装都要重新开：

```bash
adb shell settings put secure enabled_accessibility_services \
    com.phoneagent.app/com.phoneagent.app.PhoneAgentAccessibilityService
adb shell settings put secure accessibility_enabled 1
```

## 四、无障碍不可用时的退路

既然服务不是必然在的，agent 就不该因此直接失败。`serve()` 先探一次树：

| 无障碍 | 模式 | agent 的行为 |
|---|---|---|
| 已开启且可读 | `[labels]` | prompt 里带控件标签，可以用 `tap_text` |
| 关闭或读不到 | `[pixels only]` | 纯截图，回到 PhoneCLI 最初的形态 |

两种都实测通过（`state: done`，`[vlm] tap ok` → `[finish] done`）。

## 五、实测

```
agent: display=35 size=1280x2772 dpi=520 app=com.android.settings
agent: startup tree 16224 bytes for display 35
agent: listening on 127.0.0.1:8765 (mock) [labels]
```

```json
// POST /task {"text": "点一下屏幕中间"}
{"state": "done", "answer": "done",
 "steps": [{"kind": "vlm", "action": "tap", "detail": "ok"},
           {"kind": "finish", "action": "done", "detail": "finish"}]}
```

附带的 `frame` 是 agent 自己截的那一帧（base64，约 226 KB PNG）。

## 六、当前状态

| 能力 | 状态 |
|---|---|
| 配对 / TLS / adb shell / 虚拟屏 / 截帧 / 点击 | ✅ |
| 无障碍读控件树 + 按标签点击 | ✅ |
| **agent 在 APK 内运行，HTTP 服务在本进程** | ✅ |
| 无 key 时的脚本化调试（MockLLM） | ✅ |
| 无障碍关闭时的纯像素降级 | ✅ |
| 从界面配置 API key | ⬜ 目前靠 `agent.json` |
| 无障碍未开启时引导用户去设置页 | ⬜ |
| 释放虚拟屏前清理任务（避免回迁到物理屏） | ⬜ |

### 下一步建议

1. 首启引导：检测无障碍/通知/电池白名单，一键跳到对应设置页
2. 设置界面写 `agent.json`（现在是 adb 写入，正式版必须让用户能填）
3. `stop_display()` 之前先 `am force-stop` 屏上的 app

---

## 七、可用性补齐（同日）

### 设置界面

界面上多了「设置」按钮，直接写 `filesDir/agent.json` 并重启 agent。

重启不能只是 `startService`：`ondevice.shutdown()` 必须先释放服务器、虚拟屏和
adbd 连接（它们都在模块全局里），否则重启后仍是旧密钥、旧屏。

「设置」长按、或对话框里的「无障碍设置」按钮，直接跳到系统无障碍页；
无障碍没开时，聊天区会先提示一句。

### adbd 端口要记住

每次重启都扫 28k 个端口不合适，而且有一次扫描**输给了 adbd 拆旧会话的时序**，
报"找不到端口"（其实 44063 一直在听）。

现在顺序是：**上次成功的端口 → mDNS → 扫描（失败重试一次）**，每个候选都要过
STLS 探测才作数，命中的写进 SharedPreferences。

```
using the remembered port 44063      ← 第二次启动，跳过扫描
agent: listening on 127.0.0.1:8765 [labels]
```

### 模型报错不该变成 max_rounds

`_vlm_loop` 原来把 `LLMError` 当成可重试，密钥错了会安静地跑满 6 轮，
最后只回一个 `max_rounds`。现在连续两次失败就返回真实原因：

```
llm_error: HTTP 401 from https://openrouter.ai/api/v1/chat/completions:
           {"error":{"message":"Missing Authentication header","code":401}}
```

这条同时证明了一件事：**APK 里的 agent 真的能连外网**。

---

## 八、首次使用：不输入任何东西（2026-11-05 20:11）

之前新用户根本用不了：配对得靠

```bash
adb shell am startservice -n com.phoneagent.app/.PairingService \
    --es pair "824511@192.168.2.104:39803"
```

**配对码和端口要人肉从屏幕上抄下来。** 这条路走不通，原因有两条，都是实测出来的：

### 1. 配对服务只在弹窗打开时存在

弹窗一打开，mDNS 立刻出现 `_adb-tls-pairing._tcp`；切到别的 App，记录**立刻消失**
——配对服务器被拆了。

所以"先看码 → 切回我们 App → 输入"这个顺序天然不成立：
等用户回到我们这边，服务已经没了。

### 2. 那就不用用户输入

无障碍服务本来就能读别的 App 的窗口。让它读那个弹窗：

```kotlin
// 必须先看到「配对码」这三个字，避免从数字里瞎猜
if (!all.contains("配对码")) return ""
val code = Regex("\\b(\\d{6})\\b").find(all)?.value ?: return ""
val address = Regex("(\\d+\\.\\d+\\.\\d+\\.\\d+):(\\d+)").find(all) ?: return ""
return "$code@${address.value}"
```

`PairingService` 加了 watch 模式：前台服务每 700ms 扫一次，最多 150 秒
（配对码有效期），读到就直接配对，成功后再把 agent 拉起来。

### 实测（从零开始）

```
# 删掉 identity，打开 App，然后在设置里打开「使用配对码配对设备」
watch: read 824511@192.168.2.104:39803 from the dialog
watch result: 配对成功，type=1 设备信息=adb-e3fa9e60-TNxoRb，正在启动 agent
agent: display=44 size=1280x2772 dpi=520 app=com.android.settings
```

**用户除了打开那个弹窗，什么都没做。**

### 顺带修掉的一处

`VirtualDisplayDevice.stop()` 现在会先 `am force-stop` 屏上的 app 再释放屏。
原因在第四节写过：释放虚拟屏会把任务甩到默认屏，用户会莫名其妙看到
一个自己没打开的设置页。
