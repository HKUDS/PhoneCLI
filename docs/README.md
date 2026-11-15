# 文档索引

这个仓库里同时躺着三种文档，混在一起看很容易把"历史记录"当成"当前事实"。
按用途分：

## 当前事实（先看这两份）

| 文件 | 内容 |
|---|---|
| [status.md](status.md) | 现状与已知问题。按日期分节，**以最新一节为准**，前面的小节保留不改（能看出当时的判断和后来被推翻的地方） |
| [map-building.md](map-building.md) | 爬虫流水线：每一处改动对应的真实故障、实测数字、负面结果 |

## 历史记录（当时写的，不再更新）

| 文件 | 时期 |
|---|---|
| [m3-pairing-port.md](m3-pairing-port.md) | 无线调试配对移植 |
| [m4-on-device.md](m4-on-device.md) | 首次上真机 |
| [m5-in-app-agent.md](m5-in-app-agent.md) | agent 进入 APK |
| [s4-ondevice.md](s4-ondevice.md) | 端侧运行 |
| [bench-android-2026-11-04.md](bench-android-2026-11-04.md) | 2026-11-04 的性能记录 |
| [product-app.md](product-app.md) | 产品形态设想 |

## 上游参考资料（PhoneCLI 原仓库带来）

| 文件 | 内容 |
|---|---|
| [modify_androidlab.md](modify_androidlab.md) | AndroidLab 评测环境的改造说明 |
| [new_apps.md](new_apps.md) | 上游新增 App 的流程 |
| [prepare_for_linux.md](prepare_for_linux.md) / [prepare_for_mac.md](prepare_for_mac.md) | 上游环境准备 |
| [reference/aosp-pairing/](reference/aosp-pairing/) | AOSP 配对协议源码，移植时的对照 |

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
