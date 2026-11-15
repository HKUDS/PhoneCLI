# phoneagent

PhoneCLI 的**算法层**与**设备层**第一次被拆开：同一套 app map + macro 重放 + 路由逻辑，可以跑在 iOS（WebDriverAgent）、Android 真机（adb），以及将来 APK 内的设备后端上。

> 上游 `phonecli/`（iOS）和 `phonecli_android/`（AndroidLab 评测）作为参考实现保留。
> `phonecli_android/` 里有**三处**为在手机上运行而做的改动（都写在文件里、有注释）：
> `evaluation/build_map.py` 的 `_parse_json_items`（容忍多种 JSON 形状）与富化诊断，
> 以及 `phonecli/{llm_client,token_usage}.py` 的 `from __future__ import annotations`
> （App 内的 Python 是 3.8，`list[str]` 注解会在 def 时求值）。其余未改。

---

## 为什么要有这个包

上游的情况是"一份算法、两份实现"：

| 层 | iOS 版 | Android 版 |
|---|---|---|
| 设备传输 | `phonecli/device.py`：12 个扁平函数（WDA HTTP） | `utils_mobile/and_controller.py`：`AndroidController` 类（adb shell） |
| app map | `phonecli/app_map.py`（304 行） | `phonecli_android/evaluation/app_map.py`（429 行） |
| 执行入口 | `phonecli/agent.py`，**靠 `subprocess` 调 `cli.py`** | `evaluation/macro_agent.py`，直接调 controller |

两份 `app_map.py` 的差异只有三处（XML 解析、catalog 格式、`Operation.target_screen`），其余逐字节相同；而 `agent.py` 用子进程调 CLI 的方式在 APK 里根本跑不了。

**所以这里做三件事**：合并 app map、抽出设备协议、把 macro 重放变成纯 Python 调用。

---

## 目录

```
phoneagent/
├── apps.py            已安装 App 与"能不能在虚拟屏上跑"的判定
├── ondevice.py        App 内入口：配对、建虚拟屏、起服务
├── server.py          App 内的任务服务（HTTP，127.0.0.1:8765）
├── build_map.py       爬虫（复用上游 BFS，外加启动校验/判重/预算/富化）
├── core/
│   ├── app_map.py     app map 加载/屏幕识别/操作目录（iOS + Android 双解析器）
│   ├── macro.py       macro 重放器（只依赖 PhoneDevice 协议）
│   ├── agent.py       任务 agent：macro 路由 → 重放 → VLM 兜底（进程内，无 subprocess）
│   ├── llm.py         LLM 抽象：OpenAI 兼容客户端 + MockLLM
│   └── prompts.py     提示词（从上游 vendor 过来后独立演进，不再依赖仓库根目录）
├── devices/
│   ├── base.py        PhoneDevice 协议 + DeviceInfo + 异常
│   ├── adb.py         Android 主机侧后端（adb shell）
│   ├── wda.py         iOS 后端（包装 phonecli/device.py，懒加载）
│   └── mock.py        内存假设备（测试/离线演练）
├── validate.py        地图校验：结构/图/宏/隐私（离线）
├── redact.py          脱敏：手机号/邮箱/设备名/账号名 → 公开副本
├── ondevice.py        设备内后端探针（S4 可行性）
├── check.py           离线体检：把所有 map 过一遍（不需要手机）
├── probe.py           真机探针：adb → 截图 → dump → 识别 → 可选重放
├── bench.py           API 级基准：抽样 NAV 宏，测落点准确率
├── demo.py            离线演示：macro 命中 + VLM 兜底两条路径
└── tests/             57 个离线单测
```

---

## 快速开始

```bash
# 全部离线，不需要手机、不需要 adb、不需要 WDA
python3 -m pytest phoneagent/tests -q      # 57 passed
python3 -m phoneagent.check                # 17 张 map，0 problems
python3 -m phoneagent.demo                 # 看两条执行路径跑起来
```

`demo` 的输出：

```
scenario 1 — macro hit
  routed to: tasks_new_task_set_notification_notification
  device calls: ['force_stop', 'launch', 'tap', 'tap', 'tap', 'tap']
  model calls: 1 (1 text routing, 0 vision)

scenario 2 — VLM fallback
  round 1 [vlm   ] swipe  ok
  round 2 [vlm   ] tap    ok
  round 3 [finish] 3 events today finish
```

`check` 的输出长这样（`flat`/`tree` 是两种 catalog 渲染的字符数，tree 约小 40%）：

```
map                               platform   screens   ops   NAV   ACT   flat   tree  problems
settings_map.yaml                 ios             50   916    93   823 121292  73576  OK
settings_android.yaml             android         50   288    98   190  42285  24921  OK
```

---

## 三种后端

### 1. MockDevice（测试 / 离线演练）

```python
from phoneagent.core import AppMap, MacroExecutor
from phoneagent.devices.mock import MockDevice

app = AppMap("phonecli_android/app_maps/settings_android.yaml")
ops = app.build_operations()
nav_ops = [op for op in ops.values() if op.type == "NAV"]

dev = MockDevice()
result = MacroExecutor(dev, sleep=lambda _: None).run(nav_ops[0].macro)
print(result.summary(), dev.method_names())
```

### 2. AdbDevice（Android 真机，今天就能用）

```python
from phoneagent.devices.adb import AdbDevice
from phoneagent.core import AppMap, MacroExecutor

dev = AdbDevice(serial=None)          # 或 serial="XXXX"，或设 $ANDROID_SERIAL
print(dev.info())                     # DeviceInfo(platform='android', 1080x2400, ...)
app = AppMap("phonecli_android/app_maps/settings_android.yaml")
MacroExecutor(dev).run(app.get_macro("screen_1"))
```

### 3. WdaDevice（iOS，包装现有实现）

```python
from phoneagent.devices.wda import WdaDevice
dev = WdaDevice(wda_url="http://localhost:8100")   # 需要 WDA 已启动 + iproxy
```

---

## 用 agent 跑一个任务

```python
from phoneagent.core import AgentConfig, AppMap, OpenAICompatClient, PhoneAgent
from phoneagent.devices.adb import AdbDevice

device = AdbDevice()                      # 或 MockDevice() / WdaDevice()
app = AppMap("phonecli_android/app_maps/settings_android.yaml")
llm = OpenAICompatClient(
    api_key="...", api_base="https://openrouter.ai/api/v1", model="qwen/qwen3-vl-...",
)

agent = PhoneAgent(
    device, [app], llm,
    config=AgentConfig(max_rounds=20, catalog_style="tree"),
    log=print,
)
result = agent.run("把 Wi-Fi 打开")
print(result.summary(), result.used_op)
for step in result.steps:
    print(step.round, step.kind, step.action, step.detail)
```

执行顺序（与上游一致）：

1. **路由**：一次纯文本调用，把任务映射到 app map 里的某个 operation
2. **重放**：`MacroExecutor` 确定性执行（0 次模型调用）
3. **验证**：`NAV` 类操作交给 VLM 继续；`ACT` 类操作可选用一次截图验证
4. **兜底**：路由失败 → 截图 + 无障碍树 → VLM 逐步执行，直到 `finish` / 循环 / 轮数用尽

## 协议（唯一扩展点）

新增一个后端（比如 APK 内的 shell helper + 虚拟屏）只需要实现这 16 个方法：

```python
info() / is_ready() / screenshot() / dump_ui()
tap() / long_press() / swipe() / input_text() / press_back() / press_home() / press_key()
launch() / force_stop() / current_app()
shell()          # iOS 后端直接抛 UnsupportedOperation
```

`BaseDevice` 提供 `tap_center(normalized)` 等便捷方法；`DeviceInfo.to_pixels()` 负责把 map 里的归一化坐标换算成设备像素。

---

## 完整流程（Redmi K90 / Android 16 实测）

```bash
export PATH="$HOME/Library/Android/sdk/platform-tools:$PATH"

# 0) 链路自检：adb → 截图 → dump → 识别 → 输入注入
python3 -m phoneagent.probe --serial e3fa9e60 --input-check

# 1) 构建地图（默认校验式：点前按标签定位、点后校验屏幕）
python3 -m phoneagent.build_map --serial e3fa9e60 \
    --package com.android.settings --app 设置 \
    --launch-intent com.android.settings/com.android.settings.MiuiSettings \
    --expect-home 搜索系统设置项 \
    --max-screens 8 --max-depth 1 --scroll-pages 1 \
    --no-classify --no-enrich \
    --output phoneagent/maps/k90_settings.yaml

# 2) 校验（结构/图/宏/隐私）+ 静态体检
python3 -m phoneagent.validate --maps phoneagent/maps --strict
python3 -m phoneagent.check --maps phoneagent/maps

# 2b) 导出可分享的脱敏副本（工作地图保持可回放）
python3 -m phoneagent.redact --map phoneagent/maps/k90_scroll.yaml \
    --out phoneagent/maps/k90_scroll.public.yaml --term yangqin

# 3) 量化重放落点
python3 -m phoneagent.bench --serial e3fa9e60 \
    --map phoneagent/maps/k90_settings.yaml --limit 12

# 4) 跑单个任务：路由 → 重放 → 验证
python3 -m phoneagent.run --serial e3fa9e60 \
    --map phoneagent/maps/k90_settings.yaml --task "打开蓝牙" \
    --api-key "$OPENROUTER_API_KEY" --model qwen/qwen3.8-flash \
    --wake --reset-app com.android.settings
```

各步在 K90 上的实测值：

| 环节 | 结果 |
|---|---|
| 构建 depth 1 | 90 s，8 屏，无线电状态不变 |
| 构建 depth 2（校验版） | 398 s，20 屏 |
| 静态体检 | 5 张地图 0 问题 |
| 重放落点 | depth 1 100%、depth 2 92%、上游开环版 depth 2 仅 42% |
| 任务级 | 「打开蓝牙」路由 1 次文本调用 + 重放 0 次模型调用 + 1 次验证 |

> ⚠️ 生成的地图含设备专有信息（账号名、手机号、设备名），`phoneagent/maps/`
> 已加入 `.gitignore`，不要提交。

## 当前状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| S1 | 设备协议 + 三个后端 + 合并 app_map | ✅ 完成 |
| S2 | 统一 agent：macro 路由 → 重放 → 验证 → VLM 兜底，进程内 | ✅ 完成 |
| S2.5 | 记忆 / 跨 App Planner / 像素差分动作反馈 | ⬜ 未移植 |
| S3 | 真机/模拟器验证：`probe` + `bench`（18/18 重放成功，精确落点 10/18） | ✅ 见 [实测报告](../docs/bench-android-2026-11-04.md) |
| S3.5 | 设备内编译 + 校验式爬虫（depth 2 落点 42% → 92%） | ✅ |
| S3.6 | 脱敏（公开副本可回放 86%）、validate、宏失败重试、跨包识别 | ✅ |
| S3.7 | 跨 App Planner（分解 + 逐子任务执行） | ✅ |
| S4 | `OnDeviceDevice`：虚拟屏 helper + 设备内 shell（见 [实测与计划](../docs/s4-ondevice.md)） | 🟡 探针完成，实现需周级 |

---

## 已知限制

- **agent 只移植了主循环**：记忆、跨 App Planner（`TASK_DECOMPOSE_PROMPT`）、像素差分动作反馈都还留在上游，属 S2.5。
- **提示词已 vendor 进 `core/prompts.py`**，`phoneagent` 因此不要求仓库根目录在 `sys.path` 上，能整包打进 APK。
- **`adb shell input text` 只支持 ASCII**：`AdbDevice.input_text()` 遇到非 ASCII 会抛 `UnicodeTextUnsupported`，中文得走剪贴板（S4 会在设备侧补上）。
- **iOS 后端没有 shell**，也没有 `enter/delete` 按键（`phonecli/device.py` 未提供）。
- **屏幕识别阈值**沿用上游的 `coverage ≥ 0.5`，未做调参。
- `AppMap` 默认平台推断顺序：macro 里的 `bundle_id`/`package` → `com.apple.*` → 路径 → 屏幕宽度。第三方 App 的 iOS map（如 `dianping_map.yaml`）靠第一条识别。
