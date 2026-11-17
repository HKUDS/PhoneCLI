[English](README.md) · 中文

<div align="center">

# PhoneGo：你手机上的 AI 智能体

**一个在自己那块屏上干活的手机 agent —— 你手里的屏，还是你的。**

[![下载](https://img.shields.io/badge/%E2%AC%87%20%E4%B8%8B%E8%BD%BD-PhoneGo%200.3.0%20%C2%B7%20Android-00d9ff?style=for-the-badge&logo=android&logoColor=white&labelColor=0b0f19)](https://github.com/HKUDS/PhoneCLI/releases/download/phonego-v0.3.0/PhoneGo-0.3.0-arm64.apk)
[![文档](https://img.shields.io/badge/%E6%96%87%E6%A1%A3-%E4%BA%A7%E5%93%81%20%7C%20%E6%9E%84%E5%BB%BA-4ecdc4?style=for-the-badge&labelColor=0b0f19)](docs/README.zh.md)

</div>

你说一句话，PhoneGo 打开 App、把事情做完、把结果告诉你——这期间你照常用手机。
如果某个 App 只肯在主屏上跑、必须借用一下你的屏幕，它会先说明，做完再把屏幕还给你。

它不是"套在视觉模型外面的一个聊天框"。手机上 agent 做的大部分事其实是**导航**：
打开、点击、滑动、返回。PhoneGo 把这部分离线编译成一张地图，确定性地重放；
只有地图覆盖不到的界面，才去问模型。

## 它和别的不一样在哪

| | |
|---|---|
| **不占用你的屏幕** | 目标 App 在一块它自己的、你看不见的屏上运行。少数 App 只肯在主屏跑，遇到这种它会跟过去做完，再搬回来。 |
| **不是每一步都靠猜** | 导航来自离线编译好的地图：一次确定性重放，**0 次模型调用**，每步亚秒级。 |
| **需要看图时会看图** | 地图不适用时，退回视觉模型像人一样操作那一屏，最后给你一句人话，而不是一堆日志。 |
| **能跨 App 接着做** | 在一个 App 里读到的信息，直接用在下一个：*"小红书看看推荐哪家酒店，再去携程查今晚多少钱"*。 |
| **做完把屏幕还给你** | 任务结束时目标 App 回到它自己的屏，对话回到前台，并问你要不要把那个页面切过来继续操作。 |
| **数据留在手机上** | 配对密钥、地图、会话、设置都在 App 里；只有模型调用真正需要的那几张截图会发出去。 |

## 一条任务长什么样

```
你     小红书看看港大附近推荐哪家酒店，挑一家，再去携程查这家今晚多少钱

App    · 小红书：搜索 → 打开一篇笔记 →「合和酒店」
       · 携程：搜索「合和酒店」→ 11月17日–18日
       · 今晚「您选的日期暂无可预订房型」，最近可订：
         11/18–19 ¥3379 起 · 11/19–20 ¥3790 起 · 11/20 ¥2303 起
       · 需要我把这个酒店页面转到你的手机上继续操作吗？
```

49 步、200 秒、一句话、两个 App。

## 它是怎么跑的

```
离线     爬虫走一遍 App，把它编译成一张地图          （每个 App 一次）
在线     任务路由 → 重放地图里的宏                  （0 次模型调用）
兜底     地图没覆盖的界面 → 视觉模型只处理那一部分   （仅在那里）
```

路由是一次很小的文本调用；重放是确定性的——同样的点击、同样的顺序，每次都一样。
视觉模型是"异常处理"，不是引擎，所以一条任务能在一分钟内做完，而不是五分钟。

## 真机实测

| 任务 | 地图 + 重放 | 纯看图 |
|---|---|---|
| 查香港大学参观怎么预约（小红书） | **17 步 / 64 秒 / 92k tokens** | 32 步 / 146 秒 / 207k tokens |

同一台手机、同一句指令，两条都完成了。录像在 [docs/showcase/](docs/showcase/)。

## 安装

1. 下载 `PhoneGo-0.3.0-arm64.apk`（**Android 14 及以上、arm64**），在手机上打开安装。
   需要允许"安装未知来源应用"；MIUI 会弹一次确认（继续安装）。
2. 打开**无线调试**，并在系统设置里启用 PhoneGo 的**无障碍服务**——App 会自己和手机上的
   `adbd` 配对，不需要电脑。
3. 打开「设置」，填入你自己的**模型 API Key**（DeepSeek，或任何 OpenAI / Anthropic
   兼容的接口）。App 不内置任何密钥。
4. 输入一句指令。

升级直接覆盖安装：每个版本都用同一把密钥签名。

## 常见问题

**会把我的屏幕传上去吗？**
只有模型调用真正需要的那几张截图，发到你配置的接口。地图、会话、配对密钥和设置不出 App。

**需要电脑吗？**
不需要。App 用的是手机自己的无线调试通道；同一套代码在开发时也能从电脑走 USB 跑。

**哪些 App 能用？**
能用手指点的都能用。有地图的 App 秒级完成；没有的走视觉兜底，会慢一些、也更费模型调用。
地图是按 App 在你手机上建的，随时可以重建。

**会替我买东西吗？**
不会。涉及花钱的动作（支付 / 下单 / 提交订单）会先停下来问你；密码、验证码这类界面会交给你处理。

## 文档

| | |
|---|---|
| [docs/product-app.zh.md](docs/product-app.zh.md) | 这个 App 是什么，逐层拆开讲 |
| [docs/map-building.zh.md](docs/map-building.zh.md) | 地图怎么编译，每处改动背后的实测 |
| [android-app/README.zh.md](android-app/README.zh.md) | 怎么构建：Gradle、Chaquopy、虚拟屏 helper |
| [docs/showcase/](docs/showcase/) | 录像，包括两种模式的并排对比 |

## 背后的研究

PhoneGo 是 **PhoneCLI** 的 App 形态：把导航离线编译成可调用的命令，用开源的 3B 模型做兜底引擎。

- PhoneCLI — [arXiv:2609.35671](https://arxiv.org/abs/2609.35671)
- OpenPhone 模型与数据集 — [arXiv:2510.22009](https://arxiv.org/abs/2510.22009)、
  [模型](https://huggingface.co/hkuds/OpenPhone_model)、
  [数据集](https://huggingface.co/datasets/hkuds/OpenPhone_dataset)
- 仓库里的 CLI 部分（`phonecli/`、评测套件、训练配方）没有改动；原始 README 保留在
  [docs/archive/README-phonecli-zh.md](docs/archive/README-phonecli-zh.md)。

MIT 许可。
