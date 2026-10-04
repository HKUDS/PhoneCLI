# phonecli_android

[English](./README.md) · **中文文档**

面向 **AndroidLab** 基准测试（9 个 app / 138 个任务）的 Android GUI 智能体测试框架。
它在同一套任务上并排运行两个智能体：

- **PhoneCLI** —— 先把 app 的导航离线编译成 **app map**；运行时把任务路由到确定性的
  ADB **宏回放**（亚秒级、零 VLM 开销），app map 覆盖不到的情况再回退到 VLM。
- **基线** —— 同样的 VLM 循环，但没有 app map、没有回放，即"去掉 PhoneCLI"。

设备控制走 ADB，操作对象是克隆出来的 AVD；app map 是由 BFS 爬取生成的 YAML 导航图。

```
┌─────────────┐         ┌───────────────────────────────┐
│  eval.py    │         │  evaluation/auto_test.py      │
│  任务运行器 │         │  克隆 AVD → 启动 → 逐个跑任务 │
                        └───────────────┬───────────────┘
                                        │ get_agent()
                          ┌─────────────┴─────────────────────────────┐
                          ▼                                           ▼
       ┌─────────────────────────────────────┐        ┌──────────────────────────────┐
       │ MacroAgentTask_AutoTest  (PhoneCLI) │        │ ScreenshotCloudTask_AutoTest │
       │  第 1 轮：LLM 路由 + ADB 宏回放     │        │  （基线）                    │
       │  第 2+ 轮：VLM 兜底                 │        │  每轮一次 VLM 调用           │
       │                                     │        │  无 app map、无回放          │
       └─────────────────────────────────────┘        └──────────────────────────────┘
                          │ 读取
       ┌─────────────────────────────┐                ┌────────────────────────────────┐
       │ app_maps/<app>_android.yaml │◀─────│ build_android_map.py           │
       │ screens · elements · macros │ 构建 │ 克隆 AVD → BFS 爬取 → LLM 富化 │
       └─────────────────────────────┘                └────────────────────────────────┘
```

> **工作目录**：本文档中所有命令都在 `phonecli_android/` 目录内执行。代码里的跨模块
> import 全部是以进程工作目录为根的绝对导入，配置里的路径（`./app_maps/...`、
> `./logs/...`、`./evaluation/config`）也都是相对路径——所以请先 `cd phonecli_android`。
>
> 特别地，**不要**在仓库根目录用 `python -m phonecli_android.eval` 运行：那样
> `import evaluation` 会解析到仓库自己的 `evaluation/` 包，最终报
> `AttributeError: Class MacroAgentTask_AutoTest not found`。

---

## 1. 环境配置

### 前置依赖

```bash
conda create -n Android-Lab python=3.11     # 或复用已有环境
conda activate Android-Lab
pip install -r requirements.txt             # 仅首次
```

还需要 Android SDK 与模拟器/AVD：

- `~/.android/avd/` 下需有 Android 13（**API 33**）、名为 `Pixel_7_Pro_API_33` 的 AVD。
  `eval.py` 会为每个任务克隆它，并恢复干净快照。
- 仓库内置的 app map 是在该设备分辨率 **1440 × 3120** 下构建的。
- SDK/AVD 的逐步配置见 `../docs/prepare_for_mac.md`、`../docs/prepare_for_linux.md`。

### 配置模型

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

所有智能体配置都是**不含密钥**的：密钥写成 `"${OPENROUTER_API_KEY}"`，由
`config_env.load_config()` 在读取配置时展开。变量未设置时展开为空字符串。

### 快速测试

```bash
python eval.py --help          # 入口可导入、接线正常
adb devices                    # 模拟器/设备是否可见
```

---

## 2. 快速上手

```bash
conda activate Android-Lab
export OPENROUTER_API_KEY="sk-or-v1-..."

# 1) 用 PhoneCLI 跑一个 Settings 任务
python eval.py -c configs/test_macro.yaml -n macro_smoke \
  --task_config evaluation/config/setting.yaml --task_id setting_0

# 2) 同一个任务，跑不含 PhoneCLI 的基线
python eval.py -c configs/test_screen_cloud.yaml -n cloud_smoke \
  --task_config evaluation/config/setting.yaml --task_id setting_0

# 3) 对记录下来的 trace 判分
python run_eval_judge.py --traces logs/evaluation/macro_smoke \
  --config evaluation/config/setting.yaml --test-config configs/test_macro.yaml
```

`configs/test_macro.yaml` 里的 `task.args.app_map` 指向
`./app_maps/settings_android.yaml`；换 app 时复制该配置并把这一项指向对应的 map
（见[第 3 节](#3-构建-app-map)）。

下一步：[第 4 节](#4-运行基准测试)跑整套任务，[第 6 节](#6-判分)做判分，
[第 9 节](#9-常见问题排查)排查故障。

---

## 3. 构建 app map

`evaluation/app_map.py` 负责加载 map；`build_android_map.py` 负责构建：克隆 AVD、无头启动、
通过 ADB 爬取 app，再用 LLM 对每个屏幕的元素做分类与富化。

### 一键构建

```bash
# 自动管理生命周期：克隆 AVD → 无头启动 → BFS 爬取 → 清理
python build_android_map.py -p com.android.settings -a Settings \
  -o ./app_maps/settings_android.yaml

# 针对已在运行的设备
python build_android_map.py -p com.android.settings -a Settings \
  -o ./app_maps/settings_android.yaml --device emulator-5554 \
  --max-screens 50 --max-depth 3 --scroll-pages 3
```

### 参数说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-p, --package` | *（必填）* | Android 包名 |
| `-a, --app` | *（必填）* | 可读的 app 名称 |
| `-o, --output` | `./app_maps/app_map.yaml` | 输出路径 |
| `-c, --config` | 无 | YAML 配置（与测试配置同格式），用于提供 AVD/SDK 路径 |
| `-d, --device` | 无 | 使用已有设备，跳过模拟器生命周期管理 |
| `--avd-name` | `Pixel_7_Pro_API_33` | 用于克隆的源 AVD |
| `--avd-base` | `~/.android/avd` | AVD 所在目录 |
| `--show-avd` | 关 | 显示模拟器窗口（默认无头） |
| `--max-screens` | `50` | 发现屏幕数上限 |
| `--max-depth` | `3` | 最大导航深度 |
| `--scroll-pages` | `3` | 每个屏幕采集的滚动页数 |
| `--no-classify` | 关 | 跳过 LLM 的元素「稳定/动态」分类 |
| `--no-enrich` | 关 | 跳过 LLM 富化（别名、语义类型、描述） |
| `--llm-api-key/base/model` | 取自环境/配置 | 覆盖建图所用 LLM |

### app map 结构

```yaml
app: Clock
package: com.google.android.deskclock
screen_w: 1440
screen_h: 3120
launch_behavior: resume_last
screens:
  - id: screen_0
    description: "Main Alarm screen with the alarm list and bottom navigation."
    elements:
      - text: Alarm
        center: [0.1295, 0.0821]      # 归一化坐标
        fixed: false
        found_at_scroll: 0
        leads_to: screen_0            # 自环 = 不发生跳转
        aliases: [闹钟, alarms, alarm tab]
        semantic_type: tab
screen_macros:                        # 从 screen_0 出发的完整回放路径，绝对像素
  screen_1:
    - {action: force_stop, package: com.google.android.deskclock, wait: 0.5}
    - {action: launch,     package: com.google.android.deskclock, wait: 3.0}
    - {action: tap, x: 1370, y: 256, wait: 1.5}
common_tasks:
  - Create and configure a new alarm
  - Use the stopwatch to track elapsed time
  # … 另有 7 条
known_limitations:
  - Bedtime feature requires completing an onboarding flow before it can be used
  # … 另有 3 条
```

### 内置 map

`app_maps/` 内置 9 张 map，每个基准 app 一张——`evaluation/config/` 里的每个 app 都有对应
map，因此宏智能体不会出现"无图可查"的情况：

| App | 包名 | 任务数 | 屏幕数 | 元素数 | 操作数 |
|-----|------|-------:|-------:|-------:|-------:|
| Clock | `com.google.android.deskclock` | 27 | 31 | 318 | 107 |
| Settings | `com.android.settings` | 23 | 50 | 383 | 288 |
| Bluecoins | `com.rammigsoftware.bluecoins` | 15 | 50 | 415 | 411 |
| Contacts | `com.google.android.contacts` | 15 | 48 | 399 | 229 |
| Maps.me | `com.mapswithme.maps.pro` | 15 | **1** | 2 | 2 |
| Calendar | `com.skuld.calendario` | 14 | 9 | 42 | 36 |
| Cantook | `com.aldiko.android` | 12 | 31 | 234 | 207 |
| PiMusic | `com.Project100Pi.themusicplayer` | 12 | 50 | 540 | 317 |
| Zoom | `us.zoom.videomeetings` | 5 | 50 | 489 | 420 |
| **合计** | | **138** | **320** | **2 822** | **2 017** |

> `map_android.yaml`（Maps.me）是一张**占位图**：只有 1 个屏幕、2 个操作。因此宏智能体在
> Maps.me 上会退化为纯 VLM 路径——可用的 map 覆盖应按 8 个 app 计；需要的话请重新构建该图。

---

## 4. 运行基准测试

各变体共用同一套任务，区别只在 `task.class`（以及 `app_map`）：

| 变体 | 配置 | 任务类 | 智能体实现 | 使用 app map | 确定性回放 |
|------|------|--------|-----------|:--:|:--:|
| **PhoneCLI**（宏） | `configs/test_macro.yaml` | `MacroAgentTask_AutoTest` | `evaluation/macro_agent.py::MacroAgentTask` | ✅ | ✅ |
| **基线** | `configs/test_screen_cloud.yaml` | `ScreenshotCloudTask_AutoTest` | `evaluation/evaluation.py::ScreenshotCloudTask` | ✗ | ✗ |
| 基线 + 导航参考 | 复制基线配置，改 `task.class: ScreenshotCloudMapTask_AutoTest` 并加 `app_map:` | `ScreenshotCloudMapTask_AutoTest` | `ScreenshotCloudMapTask` | 仅作参考 | ✗ |

```bash
# 单个任务（调试）
python eval.py -c configs/test_macro.yaml -n debug \
  --task_config evaluation/config/setting.yaml --task_id setting_0

# 某个 app 的全套任务
python eval.py -c configs/test_macro.yaml -n macro_setting_v1 \
  --task_config evaluation/config/setting.yaml

# 一次跑多个 app
python eval.py -c configs/test_macro.yaml -n macro_v1 \
  --task_config evaluation/config/setting.yaml evaluation/config/clock.yaml

# 全部任务（省略 --task_config 即为 9 个配置），4 个并行 worker
python eval.py -c configs/test_macro.yaml -n macro_v1 -p 4

# 再按 app 名过滤
python eval.py -c configs/test_macro.yaml -n macro_v1 --app Settings
```

说明：

- `--task_config` 既接受文件也接受**目录**（由 `find_all_task_files()` 展开）。若没有任何
  匹配项，运行会在约 1 秒内中止，**不会**去克隆 AVD。
- 用同一个 `-n <name>` 重跑时，已有 trace 的任务会被跳过。
- `-p N` 把任务分到 N 个 worker 进程（`evaluation/parallel.py`）。
- 在 `task.args` 里设置 `share_instance: true`，可让同一 app 的多个任务复用同一个模拟器
  实例，使有依赖关系的任务能累积状态。
- 想让 PhoneCLI 作用于另一个 app，复制 `configs/test_macro.yaml`，把
  `task.args.app_map` 指向 `./app_maps/<app>_android.yaml` 即可。

---

## 5. 智能体特性

`MacroAgentTask`（`evaluation/macro_agent.py`）实现混合式循环：

| 特性 | 说明 |
|------|------|
| **LLM 任务→操作路由** | 第 1 阶段把指令映射到 app map 操作目录中的一个操作（`OP:` / `MACRO_VLM:` / `NEED_VLM:` / `FINISH:`）；第 2 阶段校验候选目标页面，出错时退回第 1 阶段的结果 |
| **确定性回放** | `force_stop` → `launch` → 记录好的 `tap`/`swipe` 步骤，全部走 ADB——亚秒级、零 VLM token |
| **落地校验** | 回放后依据 XML dump 识别当前屏幕；不一致则重启 app 并把该轮交给 VLM |
| **VLM 兜底** | 无 map、无匹配操作、回放失败或校验失败，都会落到与基线相同的云端 VLM 循环——因此 PhoneCLI 的得分不会低于基线 |
| **第 2+ 轮的屏幕提示** | 把目标屏幕的描述/元素注入 VLM 历史（由 `landing_hint_level` 控制） |
| **卡住恢复** | 连续多轮无进展时注入搜索/关闭广告类提示；动作报错时追加自我纠正提示 |
| **token 记账** | 按标签（`macro_map_task`、`agent_vlm`、`judge_*` 等）累计用量并写入 `token_usage.json` |

可选开关位于 `task.args.llm_config`：

| 键 | 默认值 | 作用 |
|----|--------|------|
| `force_macro_vlm` | `false` | 把所有 `OP:` 降级为 `MACRO_VLM:`（始终让 VLM 参与） |
| `skip_landing_check` | `false` | 消融：关闭落地不一致的校验 |
| `landing_hint_level` | `2` | `1` = 仅描述，`2` = 描述 + 元素（并可选冷读） |
| `cold_read_warmup` | `false` | 交给 VLM 之前额外截一次图 |
| `api_key` / `api_base` / `model` | 同智能体 | 用于路由与校验的 LLM |

---

## 6. 判分

trace 落在 `logs/evaluation/<name>/<task_id>_<timestamp>/`，且**刻意不纳入版本管理**。
判分按 app 进行：程序化的 XML/操作检查，加上面向查询类任务的 LLM 检查
（位于 `evaluation/tasks/<app>/…`，通过各 app 的 `function_map` 注册）。

```bash
python run_eval_judge.py \
  --traces logs/evaluation/macro_setting_v1 \
  --config evaluation/config/setting.yaml \
  --test-config configs/test_macro.yaml

# 打印逐任务明细
python run_eval_judge.py --traces logs/evaluation/macro_setting_v1 \
  --config evaluation/config/setting.yaml --test-config configs/test_macro.yaml --detail
```

- `--test-config` 可省略：省略时 `run_eval_judge.py` 会扫描 `configs/test_*.yaml`，
  从中读取智能体的 `api_key`/`api_base`。
- 判分模型还可由 `--api-key/--api-base/--model` 或环境变量 `API_KEY` / `API_BASE` /
  `MODEL_NAME` 指定（默认 `https://openrouter.ai/api/v1`，默认模型 `qwen/qwen3.7-plus`）。
- 一次运行的 token 汇总写入 `logs/evaluation/<name>/token_usage.json`，每个任务目录内
  另有一份副本。

---

## 7. 命令参考

### `eval.py` —— 运行任务

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-c, --config` | `config-mllm-0409.yaml` | **智能体**配置（agent / task / eval 三段） |
| `-n, --name` | `test` | 运行名 → `logs/evaluation/<name>/` |
| `--task_config` | `evaluation/config/*.yaml` 全部 | 任务配置文件或目录（可多个） |
| `--task_id` | 全部任务 | 指定任务 id，如 `setting_0` |
| `--app` | 全部 app | 按 app 名过滤 |
| `-p, --parallel` | `1` | worker 进程数 |
| `--debug` | 关 | 调试模式 |

### `run_eval_judge.py` —— 对 trace 重新判分

| 参数 | 说明 |
|------|------|
| `--traces` | trace 根目录（如 `logs/evaluation/macro_v1`） |
| `--config` | 产出这些 trace 的任务配置 YAML |
| `--test-config` | 智能体配置 YAML；可省略（自动发现 `configs/test_*.yaml`） |
| `--api-key` / `--api-base` / `--model` | 覆盖判分 LLM |
| `--detail` | 打印逐任务明细 |

### `build_android_map.py` —— 构建 app map

参数见[第 3 节](#参数说明)。

---

## 8. 环境变量

| 变量 | 使用方 | 说明 |
|------|--------|------|
| `OPENROUTER_API_KEY` | 智能体配置 | 读取配置时展开进 `api_key`；未设置则为空字符串 |
| `API_KEY` | LLM 判分 | 判分凭据回退值（未设置时为 `EMPTY`） |
| `API_BASE` | LLM 判分 | 回退的 base URL（默认 `https://openrouter.ai/api/v1`） |
| `MODEL_NAME` | LLM 判分 | 回退的判分模型（默认 `qwen/qwen3.7-plus`） |
| `DINO_EXECUTOR_URL` | `page_executor/utils.py` | 智能体设置 `relative_bbox: true` 时使用的 grounding 服务（默认 `http://localhost:24020/v1/executor`） |
| `PHONECLI_ENV` | `phonecli/__init__.py` | 备用的 `.env` 路径 |

本目录下的 `.env`（或 `$PHONECLI_ENV`）会在任何 `phonecli.*` 模块被导入时自动加载；
环境里已有的变量优先，不会被覆盖。

---

## 9. 常见问题排查

### `AttributeError: Class MacroAgentTask_AutoTest not found`

`task.class` 是从 `eval.py` 的 globals 里解析的，而这些名字来自
`from evaluation.auto_test import *`。报这个错说明 `evaluation` 解析到了**别的**包——
几乎总是因为在错误的目录下启动，或用了模块方式启动：

```bash
cd phonecli_android && python eval.py -c configs/test_macro.yaml ...   # ✅
python -m phonecli_android.eval ...                                    # ❌ 在仓库根执行
```

### `openai.OpenAIError: The api_key client option must be set …`

没有导出 `OPENROUTER_API_KEY`，`${OPENROUTER_API_KEY}` 展开成了空字符串：

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

### `No task config files found. Pass --task_config …`

传给 `--task_config` 的路径不存在，或目录里没有 `*.yaml`。这是有意设计：运行会在约 1 秒内
停止，而不是为零个任务去克隆 AVD。

### 模拟器 / AVD 问题

- 找不到 `Pixel_7_Pro_API_33` → 创建一个 API 33、名称完全一致的 AVD（或改写
  `eval.avd_name` / `--avd-name`）。
- `PermissionError: … ~/.android/avd/<avd>_0.avd` → AVD 目录不可写；把
  `eval.avd_base` / `--avd-base` 指到可写位置。
- 残留克隆：正常运行会在 `Instance.__del__` 里删掉自己的克隆（`<avd>_0.avd` 及配套
  `.ini`），只有被强杀的运行才会残留——手动删除
  `~/.android/avd/Pixel_7_Pro_API_33_0.avd` 与
  `~/.android/avd/Pixel_7_Pro_API_33_0.ini` 即可。
- 启动慢是设计使然：框架只冷启动一次，保存 `clean` 快照，之后每个任务都从快照恢复。

### 宏回放落到了错误的屏幕

map 过时了（app UI 有改动），或者建图时的分辨率与当前不同——点击坐标是由
`center × screen_w/screen_h` 换算出的绝对像素。请在你实际评测的 AVD 上重新建图：

```bash
python build_android_map.py -p <package> -a <App> -o ./app_maps/<app>_android.yaml
```

### 与日期/位置相关的任务

每个任务开始前，控制器会把设备时钟固定为 **2024-05-10 12:00:00**（Maps.me 任务除外），
并把模拟器定位设为旧金山（`adb emu geo fix -122.156 37.438`）。如果任务文本里写的是别的
日期——例如 Bluecoins 的题目目前写的是 2025 年——就依赖 app 内置数据与该措辞相匹配；
这类任务若表现异常，请先核对夹具数据。

### trace 缺失 / "Task … already run, skipping"

用同一个 `-n <name>` 重跑会跳过已有 trace 的任务。换个运行名，或删除
`logs/evaluation/<name>/`。

---

## 10. 代码结构

```
phonecli_android/
├── eval.py                  任务运行器（智能体配置 + 任务配置 → logs/evaluation/<name>）
├── run_eval_judge.py        判分：对已录制的 trace 重新打分（程序化 XML / LLM）
├── build_android_map.py     app map 构建 CLI（克隆 AVD → BFS 爬取 → YAML）
├── config_env.py            读取 YAML 并展开 ${ENV_VAR}
├── generate_result.py       结果聚合辅助函数
├── adb_client.py            注入 Docker 容器的 ADB 辅助
├── requirements.txt         Python 依赖（与仓库根目录完全一致）
├── agent/                   LLM/MLLM 客户端封装（OpenAI 兼容、Qwen、GLM、Claude）
├── evaluation/              Android-Lab 框架
│   ├── macro_agent.py       PhoneCLI 宏智能体        ← PhoneCLI 的实现
│   ├── evaluation.py        任务基类，含 ScreenshotCloudTask（基线）
│   ├── app_map.py           app map 运行时：加载 / 查询 / 操作目录 / 导航参考
│   ├── build_map.py         BFS 爬虫 + LLM 元素分类与富化
│   ├── auto_test.py         逐任务编排、AVD 生命周期、token 记账
│   ├── configs.py           AppConfig / TaskConfig
│   ├── task.py, definition.py, utils.py, docker_utils.py, parallel.py
│   ├── config/<app>.yaml    任务定义：138 个任务 / 9 个 AndroidLab app
│   └── tasks/<app>/         各 app 的判分实现（`function_map`）
├── phonecli/                仅保留宏智能体所需的 PhoneCLI 层
│   ├── prompts.py           MACRO_PLAN / MACRO_VERIFY / VLM_VERIFY + 建图提示词
│   ├── llm_client.py        OpenRouter 兼容的文本/视觉调用
│   └── token_usage.py       按标签统计 token
├── app_maps/                9 张预构建 map，每个基准 app 一张
├── configs/                 两个智能体配置（宏 / 基线）
├── templates/               云端 VLM 系统提示词（SYSTEM_PROMPT_ANDROID_MLLM_CLOUD_V0）
├── utils_mobile/            AndroidController（ADB）、XML 树工具
├── page_executor/           VLM 动作执行器
└── recorder/, tools/        trace 录制与维护脚本
```

**范围说明。** 基准测试就是上游 THUDM Android-Lab 的原生集合（9 个 app / 138 个任务）。
父项目扩展的三个 app——Gmail、TikTok、Reddit——因为从未使用过已在本目录移除，
其任务配置与判分实现均不再存在。
