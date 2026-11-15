# M4：APK 自己把虚拟屏拉起来

M3 打通了"App 连自己的 adbd"。M4 是把这条链路接进产品：**没有电脑，APK 自己完成
找端口 → TLS 认证 → 推 helper → 建虚拟屏 → 截帧 → 按标签点击。**

## 全链路

```
mDNS/扫描        找到 adbd 端口
  ↓
TLS 1.3          用配对时装进手机的 RSA 密钥（CN=adb 的自签证书）
  ↓
adb shell        已经是 shell（uid 2000），不再是普通 app
  ↓
app_process      pa-helper.jar 建 canHostTasks 的虚拟屏
  ↓
ImageReader      helper 存 PNG 到 /data/local/tmp
  ↓
exec:cat         读回给 App（exec 没有 pty，二进制不会被打乱）
```

实测日志（`adb logcat -s Pairing`）：

```
agent: adbd port 44063
agent: display=23 size=1280x2772 dpi=520 app=com.android.settings
agent: /data/user/0/com.phoneagent.app/files/frame.png 134318 bytes
agent: tapped '开发者选项' at 640,1744
agent: /data/user/0/com.phoneagent.app/files/frame-text.png 271324 bytes
```

---

## 一、Transport：把"shell 从哪来"抽出来

`VirtualDisplayDevice` 原来直接 `subprocess adb`，进了 APK 就跑不起来。
现在它只要一个 `Transport`：

| 方法 | 宿主机 | 设备内 |
|---|---|---|
| `shell(cmd)` | `adb shell` | `AdbClient.run`（shell,v2） |
| `spawn(cmd)` | `Popen(["adb","shell",...])` | `shell,raw:` 长连接服务 |
| `read_file(p)` | `adb exec-out cat` | `exec:cat` |
| `write_file(d,p)` | base64 管道 | base64 管道 |

同一个 `VirtualDisplayDevice` 两种后端都能跑，开发时用宿主机、发布时用设备内，
行为一致。**验证方式**：先用 `SelfAdbTransport` 从 Mac 跑通（等效于 APK 内），
再在 APK 内跑同一条路径。

---

## 二、端口从哪来：mDNS 会骗人

这是 M4 最坑的一处。

| 手段 | 结果 |
|---|---|
| `service.adb.tls.port` | ❌ 空的，且是 adbd 私有 |
| `ss -ltn` / `netlink` | ❌ `Permission denied`（netlink 要权限） |
| `/proc/net/tcp6` | ❌ app uid 读不了（`Permission denied`） |
| mDNS `_adb-tls-connect._tcp` | ⚠️ **有，但广播的是过期端口** |

实测：mDNS 解析出 **45341**，而 `ss -ltn` 显示 adbd 实际听在 **44063**。
（名字是 `adb-e3fa9e60-TNxoRb`，说明 mdnsd 缓存了 adbd 上一次绑定时的记录，
端口变了记录没跟着变。）拿 45341 去连，直接 `connection closed`。

**可靠做法**：扫描回环 + 用协议确认身份。

```
for port in 32768..60999:
    connect(127.0.0.1, port)
    发明文 CNXN，看回的是不是 STLS  → 是就是 adbd
```

关闭的端口在回环上立即 RST，所以 192 个线程 1.3 秒扫完。
mDNS 仍然先试一次（便宜），但它只作为提示，**是否可信由 STLS 探测决定**。

> `/data/local/tmp` 同理：app uid 进不去（0771 shell:shell），
> 所以 helper 是 App 通过 shell 通道 base64 写进去的。

---

## 三、无障碍服务：凭什么点这个按钮

`input -d <id> tap x y` 能点，但**它不知道自己在点什么**。

这不是理论问题。测试时按截图估算坐标点了 (640,1661)，实际落在"撤消 USB 调试授权"
上，弹出了"是否针对您之前授权的所有计算机撤消 USB 调试的访问权限？"。
（只发生在虚拟屏，物理屏未受影响，但它足以说明盲点的危险。）

加了无障碍服务之后：

```
<hierarchy display="22" windows="1">
  <node class="Button" bounds="65,26,221,156" desc="返回" id="up" clickable="true">
  <node class="TextView" bounds="510,47,770,134" text="更多设置" id="action_bar_title">
  ...
  <node class="ViewGroup" bounds="39,1081,1241,1221" clickable="true">
    <node class="TextView" bounds="91,1093,201,1167" text="开发者选项" id="title">
```

于是可以 `tap_text("开发者选项")` → 找到最近的可点击祖先 → 点它的中心 → (640,1744)。

### 分工：无障碍**读**，shell **点**

`AccessibilityService.dispatchGesture` **没有 displayId 参数**，永远作用于默认屏，
所以在虚拟屏上注入手势只能走 `input -d`。反过来，读其他 app 的窗口树只有无障碍
服务能做。两者正好互补：

| 能力 | 由谁提供 |
|---|---|
| 读虚拟屏控件树、文字、资源 id、bounds | 无障碍服务（`getWindowsOnAllDisplays`） |
| 在虚拟屏上点击/滑动/输入 | `input -d <displayId>`（shell） |

### 开启方式

系统设置里手动开，或调试时用 adb：

```bash
adb shell settings put secure enabled_accessibility_services \
    com.phoneagent.app/com.phoneagent.app.PhoneAgentAccessibilityService
adb shell settings put secure accessibility_enabled 1
```

配置里 **`flagRetrieveInteractiveWindows` 必须有**，否则
`getWindowsOnAllDisplays()` 只返回默认屏，虚拟屏看着是空的。

Python 侧通过 Chaquopy 调 Kotlin 静态方法：

```python
from java import jclass
tree = str(jclass("com.phoneagent.app.PhoneAgentAccessibilityService").dump(display_id))
```

---

## 四、三个踩过的坑

### 1. 释放虚拟屏会把任务甩到物理屏

`helper` 退出 → display 释放 → 它上面的 Settings 任务**迁移到 display 0**。
测试里那个"撤消 USB 调试授权"对话框就是这么跑到真实屏幕上的。

**结论**：释放前先把虚拟屏上的 app 停掉/回桌面，否则用户会看到屏幕"跳变"。

### 2. helper 的命是 app 进程给的

helper 是 `app_process`，挂在 **App 那条 adb shell 会话**下面。App 进程一死，
会话断，helper 跟着死，虚拟屏释放。

**结论**：`AgentService` 成功后**不能 `stopSelf()`**，要一直保持前台。
（第一版就是 stopSelf，结果每次跑完虚拟屏都没了。）

### 3. 帧读回不能用 `shell:`

`shell:` 会分配 pty，换行被改写、输入被回显，PNG 直接废掉。
用 `exec:`（等价 `adb exec-out`）：无 pty、无 v2 封装，二进制原样。

---

## 五、当前状态

| 能力 | 状态 |
|---|---|
| 配对（SPAKE2 + TLS 客户端证书） | ✅ |
| TLS 1.3 + ALPN 连 adbd | ✅ |
| shell / exec / 长连接服务 | ✅ |
| 端口自动发现（mDNS + 扫描兜底） | ✅ |
| 虚拟屏创建、启动 app、截帧 | ✅ |
| 按控件标签点击 | ✅ |
| 无障碍服务读控件树 | ✅ |
| 虚拟屏释放时任务回迁 | ⚠️ 需在释放前清理 |
| 主界面接进 in-process agent | ⬜ 仍走 `adb reverse` 的宿主机进程 |

### 下一步

1. `server.py` 支持注入设备，在 APK 内起 HTTP 服务，MainActivity 指到 127.0.0.1
2. 释放虚拟屏前 `am force-stop` 相关 app，避免任务回迁
3. 无障碍手势如果将来支持 displayId，就把 `input -d` 换掉
