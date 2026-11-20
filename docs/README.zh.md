[English](README.md) · 中文

# 文档索引

这里放两件东西：**这个 App 是什么**，以及**地图流水线怎么工作的**。开发日志、里程碑复盘和对比
笔记不随仓库发布（见文末），所以你在别处看到的旧链接可能已经失效。

## 产品与地图

| 文件 | 内容 |
|---|---|
| [errors.zh.md](errors.zh.md) | 报错对照表：拿屏幕上的原句查原因与处理办法 |
| [manual.zh.md](manual.zh.md) | 使用手册：每个界面、每个设置、不会做什么，以及故障排查表 |
| [../CHANGELOG.md](../CHANGELOG.md) | 版本历史：每个版本改了什么、对应哪个 APK（中英双语） |
| [getting-started.zh.md](getting-started.zh.md) | 从装好 APK 到跑完第一条任务：首次启动的检查、模型设置、以及最常遇到的五个问题 |
| [product-app.zh.md](product-app.zh.md) | PhoneGo 是什么：一个 APK，对话式界面 + 虚拟屏执行；包名、工程名和三块代码的关系 |
| [map-building.zh.md](map-building.zh.md) | 爬虫流水线：每一处改动对应的真实故障、实测数字、负面结果 |

## 演示素材

| 文件 | 内容 |
|---|---|
| [showcase/app-xhs-hku-compare-3x.mp4](showcase/app-xhs-hku-compare-3x.mp4) | 同一句话、同一台手机，两种模式并排 3×（时长 55 秒，带步数计数器） |
| [showcase/app-xhs-ctrip.mp4](showcase/app-xhs-ctrip.mp4) | 一句话跨两个 App：小红书的一篇笔记 → 笔记里的酒店 → 携程今晚的价格（3 分 40 秒） |
| [showcase/app-xhs-ctrip-4x.mp4](showcase/app-xhs-ctrip-4x.mp4) | 同一条任务的 4× 配乐版（43 秒） |
| [showcase/app-xhs-ctrip-en.mp4](showcase/app-xhs-ctrip-en.mp4) | 同类任务在英文界面下的完整流程（2 分 54 秒） |
| [showcase/app-xhs-ctrip-en-4x.mp4](showcase/app-xhs-ctrip-en-4x.mp4) | 英文版 4× 配乐版（43 秒） |
| [showcase/app-xhs-ctrip.gif](showcase/app-xhs-ctrip.gif) | 同一条任务的三联动图，README 里在用 |
| [showcase/lightagent-demo.gif](showcase/lightagent-demo.gif) | harness 在 AndroidLab 基准上的演示，README 里在用 |

左边是**自动**（地图 + 宏回放）：17 步 / 64 秒 / 92.3k token；右边是**纯看图**（每步都靠截图决策）：
32 步 / 146 秒 / 207.1k token。两条都完成了任务。

## 参考源码

| 路径 | 内容 |
|---|---|
| [reference/aosp-pairing/](reference/aosp-pairing/) | AOSP 的无线调试配对实现（SPAKE2 / TLS / AES-GCM），移植 `phoneagent/devices/pairing` 时的对照 |

## 上游文档（PhoneCLI 原仓库带来）

| 文件 | 内容 |
|---|---|
| [modify_androidlab.md](modify_androidlab.md) | AndroidLab 评测环境的改造说明 |
| [new_apps.md](new_apps.md) | 上游新增 App 的流程 |
| [prepare_for_linux.md](prepare_for_linux.md) / [prepare_for_mac.md](prepare_for_mac.md) | 上游环境准备 |

## 命令行工具各管什么（名字相近，容易混）

| 命令 | 作用 | 什么时候用 |
|---|---|---|
| `python -m phoneagent.probe` | 设备连通性探针（可选回放一条宏） | 换设备/换系统后先跑这个 |
| `python -m phoneagent.build_map` | 爬一个 App 生成地图 | 要新地图时 |
| `python -m phoneagent.bench` | 回放采样操作，量"落点" | 地图改过之后 |
| `python -m phoneagent.check` | 离线跑一遍地图的图/操作（不连设备） | 提交前冒烟 |
| `python -m phoneagent.validate` | 离线查结构/宏/隐私 | 发布地图前 |
| `python -m phoneagent.redact` | 把地图里的个人数据做成公开副本 | 地图要分享/提交时 |
| `python -m phoneagent.core.profile` | 按 App 的 profile 标出"动态文本"，让它们不进指纹 | 爬个人手机时 |
| `python -m phoneagent.run` | 一条任务端到端（路由→回放→校验） | 调算法层时 |
| `python -m phoneagent.demo` | 完全离线的演示 | 没有设备时 |

`check` 和 `validate` 不重复：前者跑图、验证"每个操作能不能走通"，后者看结构、宏定义和
隐私字段。`redact` 和 `profile` 也不重复：前者产出分享用的副本，后者影响抓取时哪些文本
进指纹。

## 代码里的说明

两处注释比文档更可靠，因为它们就在被描述的那行代码旁边：

- `phoneagent/build_map.py` 顶部：爬虫与上游 BFS 的关系、每处校验的由来
- `android-app/app/src/main/java/com/phoneagent/app/AgentService.kt`：服务、虚拟屏、唤醒锁的取舍

## 不在这份索引里的内容

开发日志（逐条记录问题与决策）、里程碑计划（配对、上机、进 APK）、实测报告和设计评审都在
开发过程中写过，但它们的价值主要在过程中：数字和结论已经进了上面的两份文档和代码注释，
剩下的日期细节对读代码的人没有帮助，因此不随仓库发布。需要时可以在提交历史里找到。
