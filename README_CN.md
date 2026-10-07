<div align="center">
  <picture>
      <img src="./figures/phonecli_log.png" width="18%" style="border: none; box-shadow: none;" alt="PhoneCLI">
  </picture>
</div>

<div align="center">

# ✨PhoneCLI✨：让每一个移动 App 都 Agent-Native

</div>

<div align="center">
  <img src="https://readme-typing-svg.demolab.com?font=Orbitron&size=24&duration=3000&pause=1000&color=00D9FF&center=true&vCenter=true&width=600&lines=Compile+once,+Replay+forever.;Making+ALL+Mobile+Apps+Agent-Native;Model+%2B+Harness" alt="Compile once, replay forever." />
</div>

<div align="center">
  <img src="./demo/lightagent_demo.gif" width="800" height="400" alt="harness 在 AndroidLab 基准上驱动 Android 模拟器">
</div>

<div align="center">
  <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 15px; padding: 25px; text-align: center;">
    <p>
      <a href='https://github.com/HKUDS/OpenPhone'><img src='https://img.shields.io/badge/🔥Project-Page-00d9ff?style=for-the-badge&logo=github&logoColor=white&labelColor=1a1a2e'></a>
      <a href="https://huggingface.co/datasets/hkuds/OpenPhone_dataset"><img alt="Hugging Face" src="https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Dataset-ffc107?style=for-the-badge&color=ffc107&logoColor=white&labelColor=1a1a2e"/></a>
      <a href="https://huggingface.co/hkuds/OpenPhone_model"><img alt="Hugging Face" src="https://img.shields.io/badge/%F0%9F%A4%97%20Hugging%20Face-Model-ffc107?style=for-the-badge&color=ffc107&logoColor=white&labelColor=1a1a2e"/></a>
      <a href='https://github.com/THUDM/Android-Lab'><img src='https://img.shields.io/badge/⚡Based%20on-AndroidLab-4ecdc4?style=for-the-badge&logo=lightning&logoColor=white&labelColor=1a1a2e'></a>
    </p>
    <p>
      <a href="https://github.com/HKUDS/OpenPhone/stargazers"><img src='https://img.shields.io/github/stars/HKUDS/OpenPhone?color=00d9ff&style=for-the-badge&logo=star&logoColor=white&labelColor=1a1a2e' /></a>
      <a href="./Communication.md"><img src="https://img.shields.io/badge/💬Feishu-Group-07c160?style=for-the-badge&logoColor=white&labelColor=1a1a2e"></a>
      <a href="./Communication.md"><img src="https://img.shields.io/badge/WeChat-Group-07c160?style=for-the-badge&logo=wechat&logoColor=white&labelColor=1a1a2e"></a>
      <a href=""><img src="https://img.shields.io/badge/Platform-Android%20%7C%20iOS-d3d3d3?style=for-the-badge&logo=android&logoColor=white&labelColor=1a1a2e"/></a>
      <a href="./README.md"><img src="https://img.shields.io/badge/🇬🇧English-1f6feb?style=for-the-badge&logo=readthedocs&logoColor=white&labelColor=1a1a2e"/></a>
      <a href='https://arxiv.org/abs/2609.35671'><img src='https://img.shields.io/badge/📄arXiv-PhoneCLI_2609.35671-ff6b6b?style=for-the-badge&logo=arxiv&logoColor=white&labelColor=1a1a2e' alt='PhoneCLI 论文 —— arXiv:2609.35671'></a>
      <a href='https://arxiv.org/abs/2510.22009'><img src='https://img.shields.io/badge/📄arXiv-OpenPhone_2510.22009-8b5cf6?style=for-the-badge&logo=arxiv&logoColor=white&labelColor=1a1a2e' alt='OpenPhone 论文 —— arXiv:2510.22009（ACL 2026 Findings）'></a>
    </p>
  </div>
</div>

</div>

<div align="center">
  <div style="width: 100%; height: 2px; margin: 20px 0; background: linear-gradient(90deg, transparent, #00d9ff, transparent);"></div>
</div>

<div align="center">
  <img src="./figures/phone_cli.png" width="78%" alt="PhoneCLI：离线构建阶段与在线运行阶段" />
</div>

**手机智能体的瓶颈，从来不是"模型不够大"。** 今天几乎所有 GUI 智能体都在跑同一个循环：截图 → 调用视觉语言模型（VLM）→ 发出动作。它慢（每步 2–5 秒）、贵（每张截图都是一次 API 调用）、脆（VLM 会编造坐标），而且必须联网——你的屏幕内容要离开设备。这与手机的本质——端侧、实时、私有——天生冲突。而这套循环里做的绝大部分事，其实是**导航**；导航是静态的、有序的、被重复一万遍的。

所以我们没有去做更大的模型，而是把**模型和 harness 一起做**：

| | |
|---|---|
| **① 🖥 Harness**<br/>*GUI + CLI 双模态* | 离线阶段，BFS 爬虫把 app 的导航结构编译成 YAML **app map**，每个屏幕对应一条确定性命令。在线阶段，这些命令在设备上确定性回放——iOS 走 WebDriverAgent、Android 走 ADB——**亚秒级完成、VLM 开销为零**；没见过的界面交给 GUI 模式。CLI 路径失败会自动回退到 GUI，因此**编译只会更好**。 |
| **② 📱 端侧优先**<br/>*一个请求，三级执行* | 一个请求按 **CLI → 端侧模型 → 云端** 逐级服务：CLI 层以零模型开销吃掉导航，只有剩下的步骤才会碰到模型。端到端实测云调用**减少约 10%**；配合高效记忆（**10–20 步上下文**），才能让一台手机长时间跑下去。 |
| **③ 🤖 模型**<br/>*开源、可替换* | CLI 路径**完全不需要模型**，回退路径**接受任意**模型——通用 LLM 或 GUI 微调模型皆可。我们开源的 3B 端侧模型是引擎，不是主角。 |

**实测收益** —— AndroidLab，9 个 app / 138 个任务，云端模型为 Qwen3.7-Plus，判分口径一致：

- **成功率 63.0% vs 50.7%** —— 比纯 VLM 循环高 **12.3 个百分点**。
- **步数 7.52 → 6.72（−11%）**、**tokens 40.1k → 34.4k（−14%）** —— 同样的任务，更少的模型调用。
- **执行一条已编译命令只需 0 次模型调用**：导航以确定性回放完成，亚秒级。

➜ **[iOS 真机完整文档](./phonecli/README_CN.md)** —— 环境搭建、WebDriverAgent、app map 构建、CLI 参考与排错。

➜ **[AndroidLab 评测文档](./phonecli_android/README_CN.md)** —— 官方 AndroidLab 套件（9 个 app / 138 个任务）、每个 app 一张编译好的 app map、宏智能体及其纯 VLM 基线，以及判分方式。

➜ **[PhoneCLI 论文](https://arxiv.org/abs/2609.35671)** — arXiv:2609.35671 &nbsp;·&nbsp; **[OpenPhone 论文](https://arxiv.org/abs/2510.22009)** — arXiv:2510.22009

➜ **[阅读方法细节 ↓](#-phonecli从-app-界面到移动智能体的可调用命令)** —— app map、三个阶段，以及为什么"编译只会更好"。

## 📖 目录
- [🎯 瓶颈从来不是模型不够大](#-瓶颈从来不是模型不够大)
- [🖥 PhoneCLI：从 App 界面到移动智能体的可调用命令](#-phonecli从-app-界面到移动智能体的可调用命令)
  - [核心理念](#核心理念)
  - [工作原理](#工作原理)
  - [为什么重要](#为什么重要)
  - [快速开始](#快速开始)
- [📱 端侧优先：CLI → 端侧模型 → 云端](#-端侧优先cli--端侧模型--云端)
  - [💾 记忆机制——让一台手机能一直跑下去](#-记忆机制让一台手机能一直跑下去)
  - [📈 实测效果](#-实测效果)
- [🤖 模型：开源、可替换的引擎](#-模型开源可替换的引擎)
  - [📦 我们提供什么](#-我们提供什么)
  - [为什么是 3B](#为什么是-3b)
  - [🧠 训练：SFT + RL](#-训练sft--rl)
  - [⚡ 推理速度](#-推理速度)
- [🚀 快速开始](#-快速开始)
- [🧪 评测](#-评测)
  - [📱 AndroidLab 环境配置](#-androidlab-环境配置)
  - [运行任务](#运行任务)
  - [判分](#判分)
  - [📊 结果](#-结果)
- [🌟 引用](#-引用)
- [🔗 相关项目](#-相关项目)
- [📜 许可证](#-许可证)

## 🎯 瓶颈从来不是模型不够大

今天几乎所有 GUI 智能体都在跑同一个循环：**截图 → 调用视觉语言模型（VLM）→ 发出动作**。它能用，但对手机来说并不合适：

| | |
|---|---|
| 🐌 **慢** | 每步 2–5 秒——每一步都在等模型 |
| 💸 **贵** | 每张截图都是一次 API 调用 |
| 🎲 **脆** | VLM 会编造坐标，一次点错就让任务跑偏 |
| 🌐 **必须联网** | 屏幕内容要离开设备，与私有、实时的使用方式冲突 |

**而这套循环里做的绝大部分事，其实是导航。** 打开 Wi-Fi、查看今天的日程、找一个联系人——这些是固定的动作序列，以同样的顺序执行成千上万次。每次都靠 VLM 重新发现一遍，既是最贵的部分，也恰恰是从不变的部分。既然存在确定性路径，VLM 循环就是用错了工具：

<div align="center">
  <img src="./figures/phonecli_case.png" width="92%" alt="PhoneCLI 用 12 步完成任务；纯 VLM 智能体则陷入错误循环" />
</div>

所以结论不是"训练一个更大的模型"，而是：**把导航编译一次，然后确定性地回放；模型只留给真正新的东西。** 这正是 PhoneCLI 做的事——而这个仓库里其余的，就是围绕它搭起来的整套栈。

---
## 🖥 PhoneCLI：从 App 界面到移动智能体的可调用命令

### 核心理念

PhoneCLI 不把每个任务都当作一次全新的 GUI 探索，而是**把 app 的导航编译成可调用命令**。离线阶段，它从外部探索 app，把发现的东西记录成一张 **app map**——屏幕、可交互元素，以及它们之间的边。在线阶段，任务被路由到其中一条命令并确定性回放；只有在遇到真正新的情况，或需要校验结果时，才会请模型介入。

### 工作原理

<div align="center">
  <img src="./figures/phonecli_framework.png" width="92%" alt="PhoneCLI 流程：阶段 1 离线编译、阶段 2 在线调用、阶段 3 运行时解释" />
</div>

**1. 构建 app map** —— BFS 爬虫通过 WebDriverAgent 系统性地探索 app 的每个屏幕，记录所有可点击元素、它们的坐标，以及屏幕之间的跳转路径。每个元素都会被分类（稳定 / 动态），并由 LLM 补充语义元数据。

```yaml
# app map 把每个屏幕编码为节点，每个元素编码为边
screens:
  - id: screen_0
    description: "Settings main page"
    elements:
      - text: Wi-Fi
        center: [0.50, 0.15]      # 归一化坐标
        fixed: true                 # 跨版本稳定不变
        leads_to: screen_1          # 点击后导航到哪个屏幕

screen_macros:
  screen_1: [force_stop, launch, tap(195, 126)]  # 精准回放序列
```

**2. 运行任务** —— 智能体用**一次纯文本调用**（不带截图）把自然语言任务映射到某个宏操作，随后确定性回放，最后可选地做一次截图校验。因此一个常规任务的成本是**一次纯文本路由调用，外加至多一次截图校验**——而不是每一步一次 VLM 调用。

**3. 处理意外** —— 当任务匹配不到任何宏（例如"找一家附近营业到很晚的餐厅"），智能体会回退到纯 VLM 推理。只有在需要时才优雅降级到 GUI 模式。

**4. 跨 app 组合** —— 跨越多个 app 的任务会先被拆解为单 app 子任务，每个子任务由它自己 app 的 map 与命令服务（`run.py --app-map` 可重复传入，正是为此设计）。

### 为什么重要

道理很简单：**不要让 VLM 去重新发现你已经知道的东西。** 把导航图预先算一次，然后可靠地永远回放。这与"CLI 工具比 GUI 更快"是同一个原理——只是用在了手机自动化上。

### 快速开始

```bash
# 1. 为任意 iOS app 构建 map（一次性，约 10 分钟）
python phonecli/cli.py macro auto-build -b com.apple.Preferences -a Settings

# 2. 运行任务
python phonecli/run.py --task "Turn on airplane mode"

# 3. 交互式 daemon（持续多任务会话）
python phonecli/run.py --interactive
```

仓库自带 **8 个 app 的预构建 map**（微博、foodpanda、Calendar、京东、Dianping、小红书、Music、Settings）。每张覆盖至多 50 个屏幕——50 是爬虫的默认上限——单个 app 含 400–1,300 个元素：**合计 400 个屏幕、6,366 个元素**。

➜ **[iOS 真机完整文档](./phonecli/README_CN.md)** —— 环境搭建、WebDriverAgent、app map 构建、CLI 参考与排错。

➜ **[AndroidLab 评测文档](./phonecli_android/README_CN.md)** —— 官方 AndroidLab 基准（9 个 app / 138 个任务）、编译好的 app map、宏智能体及其纯 VLM 基线，以及判分方式。

---
## 📱 端侧优先：CLI → 端侧模型 → 云端

每个请求都会被路由到**能服务它的最便宜一层**，只有当这一层做不到时才升级：

1. **🖥 CLI** —— 如果某条已编译的命令覆盖了任务中的导航部分，它就在设备上以**零模型调用**执行：确定性、亚秒级回放。
2. **📱 端侧模型** —— CLI 路径未覆盖的部分交给本地视觉语言模型处理，屏幕内容不必离开手机。
3. **☁️ 云端** —— 只有本地模型确实做不到的部分才会上云。

路由是动态决定的：运行时评估任务复杂度，并随着执行进展与失败情况在本地与云端之间重新分配。

### 💾 记忆机制——让一台手机能一直跑下去

在手机上跑长任务，首先是记忆问题，其次才是模型问题：

- **长程推理** —— 多步思维链配合反思式纠错。
- **文本化摘要** —— 截图被压缩成紧凑的文本状态，而不是一直以图像形式携带。
- **10–20 步上下文** —— 在手机量级的 token 预算内保留。

### 📈 实测效果

<div align="center">
  <img src="./figures/device_cloud_per.png" width="49%" alt="执行步数在云端与端侧模型之间的分布" />
  <img src="./figures/device_cloud_reduce.png" width="47%" alt="加入端侧执行后云 API 调用的下降幅度" />
</div>

*左：执行步数在云端与端侧模型之间的分布。右：加入端侧执行后云 API 调用的下降幅度。*

- **云端仍承担约 65% 的步数。** 小体量端侧模型无法承载全部推理——这正是 CLI 层存在的意义：在任何模型被调用之前，先以零模型开销把导航那部分工作量去掉。
- **云 API 调用减少约 10%。** 在纯云端基线上加入端侧执行，云调用量大约下降十分之一。
- **云端模型越强，需要的帮助越少。** 例如 GLM-4.5V 这类更强的模型，云端依赖的下降幅度更小，因为它们能独立完成更多任务。

CLI 层自身的贡献在 AndroidLab 套件上单独衡量——见下方评测一节。

---
## 🤖 模型：开源、可替换的引擎

CLI 路径**完全不需要模型**，回退路径**接受任意**模型——通用 LLM/VLM，或经过 GUI 微调的模型皆可。PhoneCLI 是 harness，不是模型。我们提供一个引擎，是因为手机是约束很紧的部署目标；而替换它只是改配置，不是重写。

### 📦 我们提供什么

| | |
|---|---|
| **模型权重** | [OpenPhone-3B（Hugging Face）](https://huggingface.co/hkuds/OpenPhone_model) —— 开放用于研究与商业用途 |
| **数据集** | [OpenPhone dataset](https://huggingface.co/datasets/hkuds/OpenPhone_dataset) |
| **部署** | vLLM 推理脚本：[`vllm_script/`](./vllm_script/) |
| **训练** | 完整配方：[`model_training/`](./model_training/README.md) |
| **数据生成** | 流水线：[`prepare_data/`](./prepare_data/README.md) |

### 为什么是 3B

我们相信移动 AI 的未来不在于把模型做大，而在于让它在真实约束下可部署：

- **硬件适配** —— 3B 匹配消费级 GPU 显存（8–12 GB）与新兴移动 NPU 的算力预算。
- **速度** —— 每步比 7B 方案快 3–5×，精度上仍能支撑亚秒级的 GUI 响应。
- **功耗** —— 更小的体积意味着更长的续航，而这正是移动部署的真实成本。
- **隐私** —— 模型就装在设备上，任务可以在屏幕内容不离开手机的前提下完成。
- **性能** —— 配合合适的训练配方，3B 在 GUI 任务上能达到 7B–9B 模型的区间。

### 🧠 训练：SFT + RL

- **合成数据** —— 用更强的 MLLM 生成推理链训练数据，绕开人工标注稀缺的问题。
- **两阶段** —— SFT 注入 GUI 基础知识，随后用 GRPO 风格的 RL 优化任务完成率。
- **小模型增强** —— 结构化训练正是让 3B 模型在 GUI 任务上能与 7B–9B 竞争的原因。

### ⚡ 推理速度

使用 vLLM 时每步的平均推理耗时。注意 GLM-4.1V-9B-Thinking 因上下文长度限制无法在单张 3090 上运行：

<div align="center">

| Model                  | GPUs        | Size | SR   | Time Cost / Step |
| ---------------------- | ----------- | ---- | ---- | ---------------- |
| Qwen2.5-VL-7B-Instruct | Single 3090 | 7B   | 10.1 | 6289.15 ms       |
| OpenPhone              | Single 3090 | 3B   | 15.2 | 4170.63 ms       |
| GLM-4.1V-9B-Thinking   | Two 3090s   | 9B   | 24.6 | 14584.89 ms      |
| Qwen2.5-VL-7B-Instruct | Two 3090s   | 7B   | 10.1 | 4587.79 ms       |
| OpenPhone              | Two 3090s   | 3B   | 15.2 | 3524.25 ms       |

</div>

- 当 OpenPhone 跑在单张 3090、而 GLM 需要两张时，**快 3.5×**；两者都用两张时**快 4×**。
- 9B 模型无法在单张 3090 上运行，恰恰是边缘部署最在意的那个约束。

<img src="./figures/model_large.png" style="zoom:100%;" alt="OpenPhone 总览：高效推理 GUI 智能体、基于 GRPO 的端侧模型微调，以及端云协同智能体系统" />

---
## 🚀 快速开始

这个仓库就是上文所述的整套栈：

| | |
|---|---|
| 🖥 **Harness（iOS）** | [`phonecli/`](./phonecli/README_CN.md) —— 构建 app map、运行任务、交互式 daemon |
| 🧪 **Harness（Android）** | [`phonecli_android/`](./phonecli_android/README_CN.md) —— AndroidLab 基准，9 个 app / 138 个任务 |
| 🤖 **模型** | [OpenPhone-3B](https://huggingface.co/hkuds/OpenPhone_model)，训练配方见 [`model_training/`](./model_training/README.md) |
| 🔧 **数据** | 合成数据流水线：[`prepare_data/`](./prepare_data/README.md) |

**构建 map 并运行任务（iOS）** —— 完整流程见[快速开始](#快速开始)：

```bash
python phonecli/cli.py macro auto-build -b com.apple.Preferences -a Settings   # 一次性，约 10 分钟
python phonecli/run.py --task "Turn on airplane mode"
```

**部署模型（可选）** —— CLI 路径完全不需要模型。推理脚本在 [`vllm_script/`](./vllm_script/)：下载权重、用 vLLM 起服务，然后把 agent 配置指向该端点。

**凭据** —— 示例配置跑的是本地 vLLM 服务（[`configs/example_xml_cloud_hyper.yaml`](./configs/example_xml_cloud_hyper.yaml)，`api_base: http://localhost:8002/v1`），因此不需要 key。云端配置则从环境变量读取 `${OPENROUTER_API_KEY}`（`export OPENROUTER_API_KEY=...`，由 [`config_env.py`](./config_env.py) 解析）。旧版 `ScreenshotTask` 路径在 [`evaluation/evaluation.py`](./evaluation/evaluation.py) 中仍保留三处占位符（第 63、75、81 行），LLM 判分器的 key 来自 [`evaluation/tasks/llm_evaluator.py`](./evaluation/tasks/llm_evaluator.py)（第 10、12 行）或 `generate_result.py --api_key`。

在找基准测试？环境配置、批量脚本、判分与结果都在[评测](#-评测)一节。

---
## 🧪 评测

### 📱 AndroidLab 环境配置

安装：请遵循官方 [AndroidLab](https://github.com/THUDM/Android-Lab) 文档完成环境配置。

- **推荐模式**：Mac（arm64）上的 AVD —— 我们实验中验证过的配置。
- **App 准备**：需要手动安装并按任务要求逐项配置。
- **兼容性说明**：官方 AndroidLab 的 Docker 镜像与 AVD 环境不兼容。

### 运行任务

```bash
# 单个任务
python eval.py -n all_cloud_v1_hyper -c ./configs/example_xml_cloud_hyper.yaml --task_id zoom_1

# 多个任务
python eval.py -n all_cloud_v1_hyper -c ./configs/example_xml_cloud_hyper.yaml --task_id zoom_1 clock_1

# 配置中的全部任务（省略 --task_id）
python eval.py -n all_cloud_v1_hyper -c ./configs/example_xml_cloud_hyper.yaml
```

批量脚本位于 [`test_script/`](./test_script)：

- `all_test_cloud_v1_hyper.sh` —— 全部 138 个 AndroidLab 基准任务。
- `all_test_cloud_v1_hyper_add.sh` —— 另外四个 app 的任务。

在 138 个 AndroidLab 任务之外，仓库还提供**额外 25 个任务**，覆盖另外四个移动 app（Chrome、Gmail、TikTok、Reddit）——见[更多 App 文档](./docs/new_apps.md)。

Android 侧的 harness 在 [`phonecli_android/`](./phonecli_android/README_CN.md) 中自带编译好的 app map 与运行器：9 个 app / 138 个任务，每个 app 一张 map（其中八张覆盖 9–50 个屏幕；Maps.me 只有单屏，因此更依赖 VLM 回退），并包含下文对比所用的纯 VLM 基线。

### 判分

我们的实现用 **LLM 评估**取代了 AndroidLab 的规则式评估，对部分成功的判定更细致。

在 [`evaluation/tasks/llm_evaluator.py`](./evaluation/tasks/llm_evaluator.py) 中配置凭据（第 10 行：API 配置；第 12 行：服务地址），然后生成结果：

```bash
python generate_result.py --input_folder ./logs/evaluation/ --output_folder ./logs/evaluation/ --output_excel ./logs/evaluation/test_name.xlsx
```

⚠️ 使用批量脚本时，请先把生成的评估文件从脚本目录移动到 `./logs/`，再执行上面的命令——这样可以避免路径冲突。

### 📊 结果

<div align="center">
  <img src="./figures/phonecli_attribution.png" width="78%" alt="各 AndroidLab app 的成功率：PhoneCLI、去掉回放的 PhoneCLI、去掉 app map 的 PhoneCLI，以及每个成功任务的步数与 token" />
</div>

在九个 AndroidLab app 上的消融：完整 harness、**去掉回放**的 harness（有 app map 但没有确定性执行）、以及**去掉 app map**（纯 VLM）。真正起作用的是编译——同时具备 map 与回放时，智能体解出更多任务，同时每个成功任务消耗的步数与 token 都更少（**6.72 vs 7.52 步**，**34.4k vs 40.1k tokens**）。

#### 🏆 小模型，大性能
- **规模 vs 性能**：OpenPhone-3B 达到 9B 模型的区间，同时保留紧凑架构的部署优势。
- **效率之选**：真正的"小钢炮"，挑战移动 AI 中"越大越好"的假设。

#### 🥊 有竞争力的表现
- **对比闭源模型**：在标准基准上，与闭源模型的轻量版本相比有可观表现。
- **小模型的潜力**：验证了紧凑、开源路线在手机智能体上的可行性。

#### 🔄 三级执行有效
- **兼顾性能与效率**：混合架构在保持接近最优性能的同时降低云端用量。
- **智能路由**：合理的任务路由带来实际节省，而不牺牲能力。

#### 🧠 更长的 prompt 并非总是更好
- **上下文要看对象**：只有当云端模型足够强时，加长 prompt 才有收益。
- **匹配复杂度**：让推理复杂度与模型能力相匹配，而不是假定 prompt 越长越好。

<p align="center">
  <img src="./figures/three_subplots_corrected.png" width="90%"/>
</p>

---
## 🌟 引用

如果本工作对你的研究有帮助，请考虑引用我们的论文。

```
@article{jiang2026phonecli,
  title={PhoneCLI: From App Interfaces to Callable Commands for Mobile Agents},
  author={Jiang, Yangqin and Xu, Lingrui and Huang, Chao},
  journal={arXiv preprint arXiv:2609.35671},
  year={2026}
}

@inproceedings{jiang2026openphone,
  title={OpenPhone: Mobile Agentic Foundation Models},
  author={Jiang, Yangqin and Huang, Chao},
  booktitle={Findings of the Association for Computational Linguistics: ACL 2026},
  pages={30362--30380},
  year={2026}
}
```

---
## 🔗 相关项目

OpenPhone 建立在优秀的开源项目之上。我们衷心感谢它们的作者和贡献者：

- [AndroidLab](https://github.com/THUDM/Android-Lab) —— 基准测试框架。
- [R1-V](https://github.com/StarsfieldAI/R1-V) —— GRPO 训练方法的实现细节。
- [LLaMA Factory](https://github.com/hiyouga/LLaMA-Factory) —— 统一训练框架，支持高效微调。

---
## 📜 许可证

本项目基于 [MIT License](./LICENSE) 发布。

<div align="center">

**如果这个项目对你有帮助，请给我们一个 Star🌟**

**🤖 Empower AI Phone with Agents!**

<br>

<p align="center">
  <em> ❤️ 感谢访问 ✨ OpenPhone · PhoneCLI！</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.OpenPhone&style=for-the-badge&color=00d4ff" alt="Views">
</p>
