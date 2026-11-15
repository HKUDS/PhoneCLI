[English](README.md) · 中文

# phoneagent

agent 核心：同一套算法，既能在电脑上通过 `adb` 跑，也能跑在手机内部
（[`../android-app`](../android-app) 那个 App 里）。

它把上游混在一起的三件事拆开：**app map**、**宏重放**、以及一个**先路由到重放、
地图不适用时退回视觉**的任务 agent。换一个设备后端是主机之间唯一的区别。

上游的 `phonecli/`（iOS）和 `phonecli_android/`（AndroidLab 评测）作为参考实现留在
仓库里；这个包直接加载上游爬虫、复用它的 BFS，所以两边始终可比。

## 目录

| 路径 | 内容 |
|---|---|
| `core/agent.py` | 任务 agent：路由 → 重放 → 校验 → 视觉兜底，全程进程内 |
| `core/app_map.py` | app map：读 YAML、识别当前屏、构建操作目录 |
| `core/macro.py` | 宏重放器，只依赖 `PhoneDevice` 协议 |
| `core/llm.py` | agent 用的最小 OpenAI 兼容客户端 |
| `core/prompts.py` | agent 用到的提示词 |
| `core/memory.py` | 跨轮次记住的东西 |
| `core/profile.py` | 按 App 的 profile：哪些屏上文字是动态噪声、哪些是结构 |
| `core/ui.py` | 读 UIAutomator 层级的小工具 |
| `devices/base.py` | 所有传输层实现的 `PhoneDevice` 协议 |
| `devices/adb.py` | 主机侧 Android 后端（走 `adb`） |
| `devices/virtual.py` | 设备内后端：在手机看不见的虚拟屏上操作 App |
| `devices/wda.py` | iOS 后端，包装 `phonecli/device.py` |
| `devices/mock.py` | 内存假设备，用于测试和离线演练 |
| `devices/pairing/` | 无线调试配对：SPAKE2、TLS、Ed25519，移植自 AOSP |
| `server.py` | 对话界面用的任务服务（HTTP，`127.0.0.1:8765`） |
| `ondevice.py` | App 通过 Chaquopy 调用的入口：配对、建屏、起服务 |
| `web.py` | 读并驱动 App 自己托管的网页 |
| `tests/` | 383 个离线单测，约 20 秒，不需要手机 |

## 命令行工具

| 命令 | 作用 |
|---|---|
| `python -m phoneagent.probe` | 验证设备可用：adb → 截图 → dump → 识别 → 注入 |
| `python -m phoneagent.build_map` | 把一个 App 爬成地图（`--expect-home`、`--no-classify`、`--no-enrich`） |
| `python -m phoneagent.validate --maps DIR` | 静态校验：结构、图、宏、隐私 |
| `python -m phoneagent.check --maps DIR` | 离线走一遍每张地图的屏和操作 |
| `python -m phoneagent.redact --map M --out P` | 产出脱敏副本，可对外分享 |
| `python -m phoneagent.bench --map M` | 抽样重放，量落点准确率 |
| `python -m phoneagent.run "任务"` | 一条任务端到端（不涉及 App） |
| `python -m phoneagent.demo` | 完全离线的两条路径演示 |

## 快速开始

```bash
python3 -m pytest phoneagent/tests -q      # 383 passed
python3 -m phoneagent.check                # 不需要手机
python3 -m phoneagent.demo                 # 宏命中 + 视觉兜底
```

## 跑一条任务

```python
from phoneagent.core import AgentConfig, AppMap, OpenAICompatClient, PhoneAgent
from phoneagent.devices.adb import AdbDevice

device = AdbDevice()                       # 或 MockDevice() / OnDeviceDevice()
app = AppMap("phonecli_android/app_maps/settings_android.yaml")
llm = OpenAICompatClient(api_key="...", api_base="https://api.openai.com/v1", model="...")

agent = PhoneAgent(device, [app], llm, config=AgentConfig(max_rounds=20), log=print)
result = agent.run("把 Wi-Fi 打开")
print(result.summary())
```

agent 按这个顺序跑四个阶段：

1. **路由** —— 一次纯文本调用，把任务映射到某张地图里的一个操作
2. **重放** —— `MacroExecutor` 确定性执行该操作的宏，0 次模型调用
3. **校验** —— `NAV` 类操作落到新屏后交给模型继续；`ACT` 类可用一次截图确认
4. **兜底** —— 路由失败时，模型看图 + 无障碍树逐步执行，直到 `finish`

## 后端

新增一个传输层只需实现 `devices/base.py` 里的协议：`info`、`is_ready`、
`screenshot`、`dump_ui`、`tap`、`long_press`、`swipe`、`input_text`、`press_back`、
`press_home`、`press_key`、`launch`、`force_stop`、`current_app`、`shell`，以及
设备内后端用的"放手模式"。

```python
from phoneagent.core import AppMap, MacroExecutor
from phoneagent.devices.mock import MockDevice

app = AppMap("phonecli_android/app_maps/settings_android.yaml")
op = next(o for o in app.build_operations().values() if o.type == "NAV")
device = MockDevice()
print(MacroExecutor(device, sleep=lambda _: None).run(op.macro).summary())
```

## 说明

- 地图里是归一化坐标，`DeviceInfo.to_pixels()` 负责换算，所以一张图能跨不同尺寸的屏。
- 地图属于设备数据：它带着爬取时屏上的文字。因此 `phoneagent/maps/` 不进仓库。
- 依赖只有 `requests`、`PyYAML`，配对还要 `spake2` 和 `cryptography`，其余是标准库。
- `adb shell input text` 只支持 ASCII，`AdbDevice.input_text()` 遇到其它字符会抛
  `UnicodeTextUnsupported`；设备内后端改走无障碍桥输入。
