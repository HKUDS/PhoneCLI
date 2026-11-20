[English](README.md) · 中文

<div align="center">

# PhoneGo：你手机上的个人 AI Agent

**CLI+GUI · 快 · 强 · 可靠 · 成本低 的手机操作 Agent**

<p>
  <a href="https://github.com/HKUDS/PhoneGo/releases/download/phonego-v0.4.4/PhoneGo-0.4.4-arm64.apk"><img src="https://img.shields.io/badge/%E2%AC%87%20%E4%B8%8B%E8%BD%BD-PhoneGo%200.4.4%20%C2%B7%20Android-00d9ff?style=for-the-badge&logo=android&logoColor=white&labelColor=0b0f19" height="34" alt="下载 PhoneGo 0.4.4（Android）"></a>
  <a href="#-怎么用"><img src="https://img.shields.io/badge/%F0%9F%93%96%20%E5%AE%89%E8%A3%85%E6%8C%87%E5%8D%97-4ecdc4?style=for-the-badge&logoColor=white&labelColor=1a1a2e" height="34" alt="先看安装指南"></a>
</p>
<p>
  <a href="./Communication.md"><img src="https://img.shields.io/badge/💬飞书-群-07c160?style=for-the-badge&logoColor=white&labelColor=1a1a2e" height="22"></a>
  <a href="./Communication.md"><img src="https://img.shields.io/badge/微信-群-07c160?style=for-the-badge&logo=wechat&logoColor=white&labelColor=1a1a2e" height="22"></a>
  <a href='https://arxiv.org/abs/2609.35671'><img src='https://img.shields.io/badge/📄arXiv-PhoneCLI_2609.35671-ff6b6b?style=for-the-badge&logo=arxiv&logoColor=white&labelColor=1a1a2e' height="22"></a>
  <a href='https://arxiv.org/abs/2510.22009'><img src='https://img.shields.io/badge/📄arXiv-OpenPhone_2510.22009-8b5cf6?style=for-the-badge&logo=arxiv&logoColor=white&labelColor=1a1a2e' height="22"></a>
</p>

</div>

下载 PhoneGo，直接在安卓手机上运行。

告诉 PhoneGo 你想要什么：它打开并操作对应的 App，把事情做完，再把结果报回来。

- **下载即用**，直接跑在手机上。
- **用自然语言描述任务**。
- **让 PhoneGo 去操作**相关的 App。
- **PhoneGo 在后台干活**，手机仍然是你自己的。
- **任务完成时拿到结果**。

## PhoneGo 有什么不一样？

### ⚡ 可复用的 App 命令

PhoneGo 把常见的 App 流程变成可复用的命令：

- 打开某个特定页面。
- 搜索信息。
- 完成一次预订，或其他多步任务。
- 重放验证过的流程，不用每一次都重新摸索怎么点。

**结果：** 执行更快、结果更一致、模型调用更少。

### 📱 看得懂界面的操作

对于必须直接操作屏幕的 App，PhoneGo 可以：

- 看懂屏幕上显示的是什么。
- 点按按钮、走通菜单。
- 滚动页面、返回上一屏。
- 处理没见过的界面和意外状态。

**结果：** 即使这个 App 没有 API、也没有可复用命令，PhoneGo 照样能用它。

### 🔀 CLI（命令）+ GUI（看图）混合执行

PhoneGo 会自己挑合适的做法：

- **可复用命令**用于熟悉、可重复的流程。
- **看图操作**用于必须点按、滑动的地方。
- **视觉模型**只在陌生界面或新情况时介入。
- **同一条任务内**，命令和看图操作可以随时切换。

**结果：** 命令级的速度和可靠性，加上像人一样操作 App 的灵活性。

## 🎬 一条真实任务

```
你     find a hotel near HKU on Xiaohongshu, pick one, then check tonight's price on Ctrip

App    · 小红书：搜索 → 打开一篇笔记 →「合和酒店」
       · 携程：搜索「合和酒店」→ 11 月 17–18 日
       · 今晚「您选的日期暂无可预订房型」，最近可订——
         11/18–19 ¥3379 起 · 11/19–20 ¥3790 起 · 11/20 ¥2303 起
       · 需要我把这个酒店页面转到你的手机上继续操作吗？
```

<p align="center">
  <img src="docs/showcase/app-xhs-ctrip.gif" width="720" alt="PhoneGo 把小红书上读到的酒店名输入携程，并报出今晚价格">
</p>

## ✨ PhoneGo 的核心特性

| | |
|---|---|
| 🗺️ **为每个 App 建立可执行的地图** | 界面和导航路径变成可复用的命令。 |
| 🧭 **调用命令，而不是每一步靠猜** | 已知路径跑得快、结果确定，且不调用视觉模型。 |
| 👁️ **地图覆盖不到时才看图** | 陌生界面和动态内容交给视觉。 |
| 🔀 **命令与看图操作结合** | 命令带来速度，看图应对变化。 |
| 🔗 **跨 App 传递上下文** | 在一个 App 里找到的信息，直接流入下一个。 |
| 📵 **不占用你的手机** | 只要可能，PhoneGo 就在后台运行。 |
| ↩️ **做完把控制权还给你** | 只在必要时借用主屏，用完立刻归还。 |
| 🔒 **敏感状态留在设备上** | 地图、密钥、会话、设置都在本地。 |

> **命令处理已知的部分，视觉处理全新的部分。**

<p align="center">
  <img src="docs/showcase/lightagent-demo.gif" width="640" alt="PhoneCLI 的 harness 在 AndroidLab 基准上驱动 Android 模拟器">
</p>

## ⚙️ 从 App 界面到可调用的命令

PhoneGo 把"可以事先准备"和"必须运行时理解"分开：

```text
离线      探索 App → 建立导航地图        （每个 App 一次）
在线      理解任务 → 选择并重放命令      （快的那条路）
兜底      遇到新情况 → 退回到看图操作    （只在需要时）
```

## 📊 真机实测

同一句指令、同一台手机，对比 PhoneGo 的「地图 + 重放」与纯看图 agent。

| 任务 | PhoneGo：地图 + 重放 | 纯看图 |
|---|---:|---:|
| 在小红书上查香港大学参观怎么预约 | **17 步 · 64 秒 · 92.3k tokens** | 32 步 · 146 秒 · 207.1k tokens |

两种方式都完成了任务——但 PhoneGo 用了：

- **少 47% 的步骤**
- **少 56% 的时间**
- **少 55% 的 token**

差别来自一个很简单的想法：熟悉的导航被当作一条验证过的命令重放，而不是每一步都用眼睛重新发现。

并排录像见 [docs/showcase/](docs/showcase/)。

## 📲 怎么用

PhoneGo 直接跑在安卓手机上——不需要电脑。

### 1. 下载与安装

下载 **[PhoneGo-0.4.4-arm64.apk](https://github.com/HKUDS/PhoneGo/releases/download/phonego-v0.4.4/PhoneGo-0.4.4-arm64.apk)**。

**系统要求：** Android 14 及以上 · ARM64

打开 APK，允许"安装未知来源应用"。MIUI 设备会弹一次确认，点**继续安装**。

如果装不上、或者装好之后配不了对，屏幕上那句话可以在 **[报错对照表](docs/errors.zh.md)** 里查到。

### 2. 把 PhoneGo 接上手机

PhoneGo 用安卓自带的无线调试在本地操作 App。

1. 在开发者选项里打开**无线调试**。
2. 启用 **PhoneGo 的无障碍服务**。
3. 按 App 里的配对提示操作。

PhoneGo 会从系统弹窗里读出配对码，自动连上手机本地的 `adbd` 服务。

> **不需要数据线，也不需要另一台电脑。**

### 3. 接上模型

进入 **设置 → 模型设置**。

1. 点**提供商**选一个：DeepSeek 官方、DeepSeek、OpenRouter、智谱、阿里百炼、硅基流动、
   月之暗面 Kimi、OpenAI、Gemini、Anthropic、本地，或**自定义**（任何兼容接口）。
   选完会自动填好 base URL 和模型名。
2. 填入 API Key——以 `sk-or-v1-…` 开头的会自动选中 OpenRouter。
3. 其余（模型 id、base URL、视觉模型、步数上限）都在**高级设置**里。**保存时会自动测一次连接**并给出结果。

PhoneGo 不内置模型，也不内置密钥——用哪个模型端点，由你决定。

### 4. 交给它一条任务

用自然语言写下你的请求。

第一次运行，建议挑一条不需要登录的任务：

> *打开设置，看看现在连的是哪个 Wi-Fi。*

PhoneGo 会打开 App、执行必要的步骤，然后汇报结果。

**哪一步没走通**，最省事的是照屏幕上那句话去查：
**[报错对照表](docs/errors.zh.md)** 收了 App 会打印的各种提示——配对、本地 agent、模型密钥、
地图——每条都写了原因和处理办法。

## 🌐 语言与升级

- PhoneGo 支持**中文与英文**。
- 界面默认跟随手机的系统语言。
- 也可以在 **设置 → 语言** 里手动切换。
- 新版本可以直接覆盖安装，不需要清掉现有配置。

完整的图文流程与排错指南，见 **[快速上手](docs/getting-started.zh.md)**。

## ❓ 常见问题

### PhoneGo 会把我的屏幕传上去吗？

只在需要"看懂屏幕"的时候。

这种情况会把相关界面发到你配置的模型端点。下面这些始终留在你的设备上：

- App 导航地图
- 配对密钥
- 会话
- 设置
- 可复用命令

已知流程可以直接跑本地地图，不需要把每一屏都发给视觉模型。

### 需要电脑吗？

不需要。

PhoneGo 直接跑在手机上，通过安卓自带的无线调试连接。

开发时可以用数据线连电脑，但正常使用不需要。

### PhoneGo 能操作哪些 App？

凡是可以靠点按、滑动、输入文本来操作的 App 都可以。

- **有地图的 App** 走快速、可复用的命令。
- **没有地图的界面** 退回看图操作。
- **界面变了的 App** 可以重新探索、重建地图。

已经有地图的 App 通常更快，也更少调用模型。

### PhoneGo 会替我下单付款吗？

涉及敏感动作时，PhoneGo 不会自作主张。

在支付、下单、提交交易这类动作之前，它会停下来等你确认。

密码、验证码等身份验证步骤，会交回给你。

这是默认行为。设置里有一个开关「**花钱时不再问我**」，打开后不再询问（打开前会先确认一次，
它对会改动手机的 shell 命令同样生效）。

### 出问题了，该看哪里？

先看 App 打印的那句话。**[报错对照表](docs/errors.zh.md)** 就是按原文索引的：是什么意思、
怎么办、对应的细节在[使用手册](docs/manual.zh.md)哪一节。表里没有的话，提 issue 时附上那句话
和你刚做过的操作。

## 📚 文档

不知道从哪开始？先看 **[快速上手](docs/getting-started.zh.md)**，装好并跑完第一条任务。

| 指南 | 你会看到什么 |
|---|---|
| 🧯 **[报错对照表](docs/errors.zh.md)** | 拿屏幕上的原句查：是什么意思、该怎么办。 |
| 📘 **[使用手册](docs/manual.zh.md)** | 每个界面、每个设置、每项功能与常见问题的完整说明。 |
| 🚀 **[快速上手](docs/getting-started.zh.md)** | 从安装 APK 到跑完第一条任务的逐步指引。 |
| 🧩 **[PhoneGo 是怎么工作的](docs/product-app.zh.md)** | 产品设计与系统架构的清晰介绍。 |
| 🗺️ **[建立 App 导航地图](docs/map-building.zh.md)** | PhoneGo 如何探索一个 App，把界面与导航路径编译成可复用命令。 |
| 🛠️ **[安卓开发指南](android-app/README.zh.md)** | 用 Gradle、Chaquopy 和虚拟屏 helper 构建这个 App。 |
| 🎬 **[示例与录像](docs/showcase/)** | 真机演示，以及与纯看图 agent 的并排对比。 |
| 📦 **[版本变更](CHANGELOG.md)** | 每个版本改了什么，附 APK 名称与校验值（中英双语）。 |

## 🌟 引用

PhoneGo 把两个研究项目合到了一个实用 App 里：

- **PhoneCLI** 提供 App 导航地图、命令编译与确定性重放。
- **OpenPhone** 提供开源手机 agent 模型，用于视觉理解和兜底交互。

如果 PhoneGo、PhoneCLI 或 OpenPhone 对你的研究有帮助，欢迎引用相应论文：

```bibtex
@article{jiang2026phonecli,
  title   = {PhoneCLI: From App Interfaces to Callable Commands for Mobile Agents},
  author  = {Jiang, Yangqin and Xu, Lingrui and Huang, Chao},
  journal = {arXiv preprint arXiv:2609.35671},
  year    = {2026}
}

@inproceedings{jiang2026openphone,
  title     = {OpenPhone: Mobile Agentic Foundation Models},
  author    = {Jiang, Yangqin and Huang, Chao},
  booktitle = {Findings of the Association for Computational Linguistics: ACL 2026},
  pages     = {30362--30380},
  year      = {2026}
}
```

---

## 🔗 相关项目

- [AndroidLab](https://github.com/THUDM/Android-Lab)
- [R1-V](https://github.com/StarsfieldAI/R1-V)
- [LLaMA Factory](https://github.com/hiyouga/LLaMA-Factory)
- [Mika](https://github.com/wangzan101/Mika)

PhoneCLI 原有的研究组件——包括 `phonecli/`、评测套件和训练配方——仍然保留在本仓库中。

PhoneCLI 的原始文档见 **[归档的 PhoneCLI README](docs/archive/README-phonecli-zh.md)**。

---

## 📜 许可

本项目以 [MIT License](./LICENSE) 发布。

<div align="center">

**如果这个项目对你有帮助，欢迎给个 Star🌟**

**🤖 Exploring AI Phone with Agents!**

<br>

<p align="center">
  <em> ❤️ 感谢访问 ✨ PhoneGo！</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.OpenPhone&style=for-the-badge&color=00d4ff" alt="Views">
</p>

</div>
