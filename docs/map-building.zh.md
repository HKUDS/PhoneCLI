[English](map-building.md) · 中文

# 地图构建：一条经过测量的流水线

这份文档记录当前 map building 的设计、每一处改动的**实测依据**，以及已知的未解决问题。
所有数字都来自同一台设备（Redmi K90 / HyperOS 3 / Android 16 / 1280×2772）。

## 1. 血统

爬虫**不是重写的**。`phoneagent/build_map.py` 通过 `_load_crawler()` 加载上游
`phonecli_android/evaluation/build_map.py`，走的是 PhoneCLI 原有的 BFS 算法：

```
1. 启动 App，dump + 滚动首页
2. BFS：对每个元素，完整路径回放 → 点击 → dump 新屏
3. 记录边（leads_to）与宏（从 screen_0 的完整路径）
4. LLM 元素分类（STABLE / DYNAMIC）
5. 输出 YAML
```

我们对上游文件只做了一处侵入式修改（`_parse_json_items`，见 §3）。其余改动
都在适配层，或者是对 BFS **另写的一条 verified 路径**。

## 2. 流水线

```
build_map  →  profile 清洗  →  sync-maps.sh  →  bench 回放验证
```

| 阶段 | 命令 |
|---|---|
| 爬取 | `python3 -m phoneagent.build_map --serial <S> --package <P> --app <名> --llm-api-base … --llm-api-key … --output maps/<名>.yaml` |
| 清洗 | `python3 -m phoneagent.core.profile --map maps/<名>.yaml --out maps/<名>_clean.yaml` |
| 打包 | `./android-app/sync-maps.sh`（复制进 APK assets） |
| 验证 | `python3 -m phoneagent.bench --map maps/<名>.yaml --limit 10 --serial <S>` |

## 3. 每一处改动及其原因

| 改动 | 触发它的真实故障 | 效果 |
|---|---|---|
| **启动校验** `_ensure_home` 始终执行 | 爬"时钟"录到了日历；爬"计算器"录到了 PhoneAgent 自己的聊天界面 | 前台包名不匹配即中止 |
| **富化接入 verified 路径** | 四张地图 `description` 恒为 0，因为默认走 verified 而该路径从不调用 `_enrich_map` | 屏幕描述 0 → **100%** |
| **富化解析** `_parse_json_items`（改上游） | 元素富化要求"一行一个 JSON"，模型返回数组/围栏块时**静默丢弃**，别名恒为空 | 别名 0 → 1741（三图） |
| **相似度判重** `_match_screen` | 精确签名下计算器 50 屏里只有 17 种描述（66% 重复）；同一面板元素数在 37/38/39 间抖动 | 重复率 66% → **8%** |
| **时间预算** `--max-minutes` | 两次"卡 25 分钟、55 次点击、零产出" | 40 分钟准点收尾 |
| **单步看门狗** `--step-timeout` | 卡死发生在**轮次内部**（dump/采集/回放链），轮次间的检查管不到 | 超时分支跳过，`stats["timeout"]` 可见 |
| **增量落盘** `_save_partial` | 3 小时爬取被 kill 后全部丢失 | 每新屏落盘一次 |
| **收尾富化** | 被 kill 的图没有描述 | 提前停止也富化 |
| **宏的屏幕前置条件** | — | ⚠️ 见 §5 |

## 4. 实测结果

同一台手机、同一批操作（`--limit 10`）、**零模型调用**：

| 地图与机制 | 屏数 | replay success | 精确落点 |
|---|---|---|---|
| 旧地图（精确判重、无富化） | 42 | 100% | 40% |
| 相似度判重（中断、无富化） | 10 | 100% | 60% |
| **时间预算内完成 + 富化** | 15 | 100% | **70%** |

三张深爬地图（旧判重）的产出，作为规模参考：

| 地图 | 屏 | 描述 | 别名 | 操作 | NAV |
|---|---|---|---|---|---|
| 时钟 | 42 | 42 | 373 | 573 | 38 |
| 日历 | 50 | 50 | 261 | 496 | 108 |
| 计算器 | 50 | 50 | 1107 | 482 | 42 |

### 4.1 四张地图今天的实测（2026-11-08）

同一台手机、同一批操作（`--limit 10`）、**零模型调用**：

| 地图 | 屏 | 派生操作 | replay success | 精确落点 | 同 App |
|---|---|---|---|---|---|
| **设置（修复后重爬）** | 14 | 135 | 100% | **90%** | 100% |
| 设置（随 APK 发布的旧图） | 20 | 330 | 100% | 10% | 40% |
| 时钟（新流水线） | 15 | 244 | 100% | 70% | 100% |
| 日历（旧判重） | 50 | 496 | 100% | 30% | 100% |
| 计算器（旧判重） | 50 | 482 | 100% | 20% | 20% |

设置从 10% 到 90% 不是调参，是 §5.2 / §5.3 那两个 bug 修掉之后的直接结果。

## 5. 负面结果（同样重要）

### 5.1 宏的屏幕前置条件，中止版是错的

```
中止版：exact landing  0/10，9 个操作在第 2 步就死
警告版：exact landing  7/10，全部正常回放
```

原因不是守卫逻辑，而是**信号本身不可靠**：爬取时识别的屏幕 id 和回放时识别的
id 经常不一致。同一个界面，两次识别给出不同答案。

所以它被降级为 `MacroExecutor.screen_warnings`——记录下来，但不再中止。
元数据仍保存在地图的每个 tap 步骤里（`"screen": parent_id`），等识别稳定后可以
重新启用。

### 5.2 启动 intent 会被别的 App 接管

这是最贵的一个 bug，因为它在报告里看起来完全正常：宏"执行成功"、每一步都 ok。

```
$ adb shell am start -a android.settings.SETTINGS
Starting: Intent { act=android.settings.SETTINGS }
Warning: Activity not started, its current task has been brought to the front
mCurrentFocus=com.android.phone/com.android.phone.settings.MobileNetworkSettings
```

HyperOS 把 `android.settings.SETTINGS` 交给了 `com.android.phone`。而
`MacroExecutor` 发完 intent **没有检查包名是否真的起来了**，直接返回 success。
于是后面每个 tap 都是"在设置页上量的坐标"，砸进另一个 App。

同一张设置地图、同一批操作：

| | 精确落点 | 同 App |
|---|---|---|
| 修复前 | 0/10 | 0/10 |
| 修复后（旧地图） | 1/10 | 4/10 |
| 修复后 + 重爬（新地图） | **9/10** | 100% |

修法：`device.launch()` 本来就返回"目标包是否真的在前台"，宏现在看这个返回值；
没落到就退回按包名启动（走 component 解析）。

### 5.3 点击跑到别的 App，那一屏不是这个 App 的屏

同一个根因在爬虫里，后果更隐蔽：**脏屏被当成新屏存进地图**。

```
29 / 49 次点击之后前台是别的 App
24 屏的地图里有 4 屏是计算器（screen_13/14/16/22）
```

识别按文本重合度打分，计算器屏元素多、文本独特，回放时**比真正的设置屏分还高**。

修法两条：分支启动同样检查返回值并回退；点击后前台属于无关 App 的那一屏
**不记录**（`com.android.phone` / `com.miui.securitycenter` / `com.xiaomi.account`
保留，它们确实托管了设置子页）。

重爬后 14 屏全部属于设置，没有计算器屏。

## 6. 已知未解决

| 问题 | 现象 | 可能的方向 |
|---|---|---|
| 屏幕识别不稳定 | 两次识别同一屏给出不同 id | 用 `_stable_keys` 的结构指纹做识别，而非文本匹配 |
| 单屏仍有重复 | 重爬的 14 屏里，主菜单出现两次、蓝牙相关三屏 | 相似度阈值对"同一页不同滚动位置"不灵 |
| 爬取慢 | 2.5 秒/分支起，30 分钟只走完 14 屏、67 次点击 | 分支前不重复 force_stop+launch，改用返回键 |
| 地图会过时 | 同一张设置地图：10 月 40%、11 月 10%、重爬后 90% | 落点校验 + 失败换路径，而不是信任静态图 |
| 屏数上限是硬截断 | 日历/计算器都顶到 50 屏 | 上限之外还应报告"是否被截断" |
| 只在一台设备验证 | Android 13 需 CAPTURE_VIDEO_OUTPUT（不支持）；14/15 未测 | 原厂 14+ 真机跑 `probe` |

## 7. 稳定性结论

爬虫现在具备：启动校验、时间预算、单步看门狗、增量落盘、收尾富化、相似度判重、
跨 App 污染拦截、启动结果校验。连续运行的实测中，30 分钟预算的运行**自行准点
结束**（`stopped_by: time_budget`），`timeout: 0`，富化完成，全部屏幕有描述。

设置地图的落点从 10% 提到 90%，说明此前"剩余 30% 落点失败是识别问题"的判断
是错的——**大部分是回放根本没在目标 App 里**。

**这是第一次全程无人工干预、且回放可用的完整产出。**

## 8. 在 App 里爬（2026-11-09 验证）

爬虫现在可以在 App 内跑，驱动**虚拟屏**，物理屏不受影响。

```
POST /crawl {"package":"com.android.settings","app":"设置","max_screens":6,"max_minutes":6}
  -> {"id":"14fbb5d3","output":"/data/.../files/crawls/设置poc6.yaml"}
GET  /crawl/<id>   -> {"state":"done","lines":[...]}
```

实测（同一台机）：

| | 宿主机爬虫 | App 内爬虫 |
|---|---|---|
| 驱动对象 | display 0（物理屏） | 虚拟屏 |
| 手机能否同时用 | ❌ 被占用 | ✅ 全程可用 |
| 速度 | 14 屏 / 1600 秒 ≈ **114 秒/屏** | 6 屏 / 94 秒 ≈ **15.7 秒/屏** |
| 依赖 | Mac + USB/adb | 无 |

快 7 倍的原因：宿主机每次 dump 要起一个 `uiautomator` 子进程、每次点击走
`input tap`；App 内直接走无障碍桥（`ACTION_CLICK`）、dump 也走同一棵树。

验证时物理屏始终停在 PhoneAgent 自己的界面：

```
Display #0  topResumedActivity=com.phoneagent.app/.MainActivity
Display #97 topResumedActivity=com.android.settings/.SubSettings   <- 爬虫在这里
```

### 为此改的四处

| 改动 | 原因 |
|---|---|
| `build_map.main(device=...)` | 复用 App 已经持有的 `VirtualDisplayDevice`，不再新开一条连到 display 0 的 adb |
| `VirtualDisplayDevice.launch(intent, wait)` | 宏回放和爬虫都用这个签名；虚拟屏上只需要 component |
| `_guarded` 捕获 `ValueError` | `signal` 只能在主线程用，App 内爬取跑在工作线程 |
| 上游三个模块加 `from __future__ import annotations` | App 内的 Python 是 3.8，`list[str]` 注解在 def 时求值会 TypeError |

### 还没做的

- 端点目前固定 `--no-classify --no-enrich`（不调模型），所以产出的地图没有描述和别名。
  接上 App 里已有的 API Key 就能跑完整流水线。
- App 里还没有"构建地图"的界面，现在只能通过 HTTP 触发。

## 9. 富化接进 App（2026-11-09）

`/crawl` 现在带 App 里已有的 API Key，跑的是和宿主机**同一条**流水线：
元素分类 → 元素富化（别名/类型）→ 屏幕描述 → App 元数据。

一次真机实测（设置，6 屏，含富化）：

```
screens=6  elements=81  described=6/6  aliased=81/81  typed=81/81
耗时 151 秒  ->  25 秒/屏（含富化）
```

同一台机、宿主机爬虫是 **114 秒/屏**——即便算上富化，App 内仍然快约 4.5 倍。

### 9.1 富化曾经"跑了但等于没跑"

第一次接通后：6 屏全有描述，**81 个元素里只有 1 个别名**。日志里没有异常，
`[Enrich] Done.` 照常打印。

查出原因：模型这次把对象**格式化成了多行**

```
{
  "text": "返回",
  "aliases": [
    "back",
    "go back"
  ]
}
```

而 `_parse_json_items` 的最后兜底是"按行 split，每行一个 JSON"。多行对象在每一行
都解析不了，于是**解析出 0 项，静默丢掉**。

修法：不再按行切，改用 `json.JSONDecoder().raw_decode` 扫描——从头找一个 `{`，
解出一个对象，再跳到它结尾继续。一行一个、数组、围栏、`{"elements": [...]}`、
`1. {...}`、多行美化、夹带解释文字，同一个循环全覆盖。

修完复测：**81/81 元素拿到别名和类型**。

### 9.2 这条经验对宿主机同样有效

已发布的三张图是用旧解析器爬的，别名覆盖率只有约一半
（设置 73/135、日历 22/84、时钟 86/264）。解析器修好后，宿主机重爬也会受益。

### 9.3 一条可诊断的日志

每屏现在打印一行 `[Enrich] screen_1: 986 chars -> 10 items`；解析为 0 时还会打印
原始回复的前 200 字符。"富化悄悄失败"这种问题，下次一眼就能看见。

## 10. App 内重爬设置并 A/B（2026-11-09）

用修好的解析器、在 App 内重爬设置（16 屏 / 89 次点击 / 28 分钟）：

```
screens=16  elements=167  described=16/16  aliased=167/167  typed=167/167
```

### 10.1 地图必须在它将来运行的那块屏上测

同一个 bench（`--limit 10`），同一台机，两张设置地图：

| 地图 | 屏 | 别名 | 在**虚拟屏**上测 | 在**物理屏**上测 |
|---|---|---|---|---|
| 旧（宿主机爬，已发布） | 14 | 73/135 | **4/10** | 9/10 |
| 新（App 内爬） | 16 | 167/167 | **8/10** | 2/10 |

两张图在各自**被爬取的那块屏**上都好，换一块屏就掉一半以上。原因很实际：
坐标、元素位置、甚至哪些屏会出现，都跟那块屏的状态有关。

结论：**App 内爬的图要在 App 内测**。为此加了 `POST /bench`——它用 App 自己的
`VirtualDisplayDevice` 跑 `bench.run_one`，和宿主机 bench 是同一段代码。

### 10.2 发布的设置地图换成 App 内爬的这张

`settings.yaml` 来源从 `k90_settings_fresh.yaml`（宿主机 14 屏）换成
`k90_settings_inapp.yaml`（App 内 16 屏，别名 100%）。理由就是上面那张表：
agent 跑在虚拟屏上，8/10 > 4/10。

### 10.3 顺带发现

宿主机 bench 现在**不能**在 App 运行时跑：虚拟屏会抢走 `dumpsys window` 里
"当前焦点"的位置，`AdbDevice.current_app()` 于是返回虚拟屏上的 App，
`launch ... returned false`，10 个操作全变 MISS。测宿主机路径前要先把 App 停掉。

## 11. 时钟重爬成功，日历被系统 App 挡住（2026-11-09）

### 11.1 时钟：2 屏，9/9

App 内重爬时钟只得到 **2 屏 / 27 个元素**（首页的可点标签很少，其余都是同一个
闹钟列表的滚动位置），但回放 **9/9 全部精确落点**——而原来那张 15 屏的宿主机地图
在虚拟屏上是 **0/10**。

小地图 + 高命中，比大地图 + 低命中更有用：宏走不通时 agent 会退回 VLM，
但宏走错地方时它要先浪费一步才发现。

### 11.2 日历：启动校验过不去

三次尝试全部停在启动校验：

```
start attempt 1: the dump is com.android.settings, not com.android.calendar
start attempt 2: the dump is com.xiaomi.account,  not com.android.calendar
start attempt 3: the dump is com.android.phone,    not com.android.calendar
```

一路上修掉了四个真实缺陷（都不是日历特有）：

| 缺陷 | 现象 | 修法 |
|---|---|---|
| 桥的 dump 不带 `package` 属性 | 启动校验读不到包名，永远判"不是目标 App" | `appendNode` 输出 `package="..."` |
| 权限弹窗被当成"App 没起来" | 三次重试都 ABORT | 识别 允许/拒绝、同意/退出 这类按钮，**先点拒绝**（不替用户同意隐私协议），不行就 force-stop 那个干扰 App |
| 树比窗口慢 | 冷启动后 3 秒内的 dump 还是上一屏 | 重试取 dump 最多 4 次，空 dump 不算"有弹窗" |
| MIUI 的弹窗不走 AOSP 包名 | `com.miui.securitycenter` 的 `SystemAppPermissionDialogActivity` | 用"授权按钮 + 拒绝按钮同时出现"判定为弹窗 |

仍未解决：日历是系统 App，进程会被系统拉起，任务残留在 display 0 上，
`am start -S --display N` 于是只是把旧任务提到前面，虚拟屏上还停在上一屏。
下一步要么在启动前清掉该包的所有任务，要么给日历单独放宽启动校验。

## 12. App 内的"构建地图"界面（2026-11-09）

界面上多了一个「地图」按钮，点开是一张表：选 App（可启动的都已列出，排除了
PhoneAgent 自己）、最多爬几屏、时间预算。点「开始」后进度直接写进聊天记录：

```
构建地图
开始：设置（com.android.settings），最多 2 屏 / 20 分钟
地图会写到 /data/user/0/com.phoneagent.app/files/crawls/设置.yaml
  radios : before {'wifi': '1', 'bluetooth': '1', 'airplane': '0'}
  llm    : qwen/qwen3.8-flash (reasoning off, max_tokens=4000)
  [Verified] depth=1 tap '搜索系统设置项' (from screen_0)
  [Verified] depth=1 tap '账号、云服务、会员权益等' (from screen_0)
  [Enrich] screen_0: 2943 chars -> 26 items
  [Enrich] screen_1: 1535 chars -> 15 items
构建地图
完成。
map    : /data/user/0/com.phoneagent.app/files/crawls/设置.yaml  (80.4s)
```

细节：

- 下拉框记住上次爬的 App（`SharedPreferences`），重复作业不用每次翻列表
- 进度每 2.5 秒拉一次 `/crawl/<id>`，只打印有新内容的行
- 爬取不占用界面：虚拟屏在跑，物理屏仍是聊天页，可以一边爬一边看进度
- 进度行里过滤掉了 HTTP 访问日志和心跳——它们是调用方自己的请求，不是爬取进展

### 11.3 日历为什么进不了虚拟屏（结论）

不是"App 不支持多屏"，而是**日历首页那个 Activity 被系统钉在默认屏**：

```
am start -S --display 145 -n com.android.calendar/.homepage.AllInOneActivity
  -> Display #0  topResumedActivity=com.android.calendar/.homepage.AllInOneActivity

am start -S --display 145 -n com.android.calendar/.selectcalendars.CalendarAccountManagerActivity
  -> Display #145 topResumedActivity=com.android.calendar/.selectcalendars.CalendarAccountManagerActivity
```

同一个 App 的**别的** Activity 能正常落到虚拟屏，只有首页不行——典型的
`singleTask`/`singleInstance` 行为：系统总是把这类 Activity 放回它"自己的"任务，
而那个任务在默认屏上。`--activity-multiple-task` 也没用，force-stop 之后再启动
同样被拉回 display 0。

设置和时钟的首页都没有这个问题，所以它们能爬。

**处理**：日历地图**不再随 APK 发布**。它旧的那张（宿主机爬、7 屏）在虚拟屏上
是 0/10，留着只会让 agent 先白跑一次宏再退回模型。等哪天有办法把首页放上虚拟屏
（例如换成 Launcher 入口、或用 `VirtualDisplay` 的 `setDisplayId` 变体）再回来。

顺带修了一个相关缺陷：`extractMaps()` 以前只往 `files/maps` 里**加**文件，从 APK
里撤掉的地图在设备上会一直留着。现在解压前先清掉旧的 `*.yaml`。

## 13. 起不来的 App：快速失败并说清原因（2026-11-09）

之前遇到日历这种"首页上不了虚拟屏"的 App，agent 会：宏落空 → VLM 连续 launch →
planner 放弃，用户看到的是转了半天然后 `failed: planner`，完全不知道发生了什么。

现在 `VirtualDisplayDevice.launch` **会验证**：`am start` 之后轮询
`current_app()`，没落到目标包就抛 `LaunchRefused`。

```
[launch] com.android.calendar -> com.android.calendar/.homepage.AllInOneActivity on display 153
  cannot start com.android.calendar here: singleTask activities stay on the display of their own task
  this app's home activity is pinned to its own task; aborting
```

agent 侧同样：

```
state: failed
answer: 打不开「com.android.calendar」：它的主界面声明为 singleTask，
        系统只会把它放回自己所在的那块屏幕，虚拟屏上起不来。
        换一个 App，或在手机上手动操作。
steps:  vlm | home | ok
        launch | com.android.calendar | refused
```

2 步结束，而不是 11 步。关键是 `LaunchRefused` **不能被 `_execute_action` 吞掉**——
那里原本把所有 `DeviceError` 都变成 `False`（等于"这次动作失败，继续试"），
对"这个 App 根本起不来"是错的。

### 判定依据

| App | 入口 Activity | launchMode | 能上虚拟屏 |
|---|---|---|---|
| 日历 | `homepage.AllInOneActivity` | **singleTask (2)** | ❌ |
| 时钟 | `DeskClockTabActivity` | 未设置（standard） | ✅ |
| 设置 | `MiuiSettings` | 未设置（standard） | ✅ |

同一个日历 App 的其它 Activity（如 `CalendarAccountManagerActivity`）能上，
所以问题精确地落在"首页这个 Activity 的启动模式"上，不是 App 不支持多屏。

## 14. 爬取提速：已知屏不再重新采集（2026-11-09）

一次爬取里绝大多数点击都落在**地图里已有的屏**上，而原来的流程对每一条分支都做
同样昂贵的动作：三次上滑 + 三次 dump + 一次 LLM 分类。

现在点击后先做一次**廉价 dump**，与已知屏比对；命中就不再采集，只记录这条边：

```
[Verified]   known screen screen_4 (overlap 0.93, no scroll)
```

另外去掉了重复等待：`launch()` 本身已经轮询到目标 App 在前台，后面那句
`sleep(3.0)` 相当于为同一个等待付两次钱，改成 0.8 秒。

### 实测（设置，同一台机，App 内）

| | 点击数 | 已知屏命中 | 耗时 | 秒/点击 | 秒/屏 | 落点 |
|---|---|---|---|---|---|---|
| 之前 | 89 | — | 1673 s | 18.8 | 105 | 8/10 |
| **现在** | 75 | **25** | 1068 s | **14.2** | **82** | 7/10 |

秒/点击 **−24%**，秒/屏 **−22%**，落点没有变化（10 个样本，7 vs 8 在一个样本的
误差内）。富化仍然 100%（13 屏 152 个元素全部有别名）。

### 下一步还能省的

秒/点击里现在最大的一块是**每分支重新 force-stop + 启动**（约 4.5 秒）。
如果上一条分支结束时正好停在父屏上，完全可以直接接着点。风险在于父屏可能需要
滚动才能看到目标元素，所以只在"父屏不需要滚动"时才跳过——还没做。

### 14.1 负面结果：用返回键"回父屏"省不了时间

想法很直接：每条分支开头都 force-stop + 重新启动要 ~4.5 秒，而上一分支结束时
大概率就在父屏的下一层，按一次返回键（一次 dump，约 1 秒）就够了。

实测（设置，16 屏预算，与上一张表同样的条件）：

| | 点击 | 已知屏命中 | 返回键成功 | 耗时 |
|---|---|---|---|---|
| 只有"已知屏跳过采集" | 75 | 25 | — | 1068 s |
| **再加"返回键回父屏"** | 69 | 19 | **2** | 1098 s |

返回键只成功了 **2 次**（69 次点击里）。原因大概是：多数分支的父屏不是"上一层"，
返回一次落不到；而每次尝试失败还要多花两次 dump，抵掉了省下的重启。

结论：**这条优化撤销了**，代码回到只用"已知屏跳过采集"的版本。记录下来，免得
以后有人再试一遍。

## 15. App 覆盖：三个新 App 的实测（2026-11-09）

| App | 结果 | 原因 |
|---|---|---|
| 浏览器 | ❌ 拒绝启动 | `BrowserActivity` 是 **singleTask**，和日历同款 |
| 笔记 | ❌ 卡在启动校验 | 起来后被 MIUI 权限弹窗盖住，弹窗的按钮不在我们的"允许/拒绝"词表里 |
| 文件管理 | ⚠️ 能爬，但只有 1 屏 | 首页可导航标签很少，9 次点击被跳过 |

覆盖率没有扩大，但暴露了三个真问题，都修了：

### 15.1 启动验证太严：弹窗盖住 ≠ 起不来

文件管理和笔记都被判成"无法在虚拟屏启动"，实际是**起来了**，只是被权限弹窗盖住，
`current_app()` 返回的是弹窗的包名（`com.miui.securitycenter`）。

修法两条：
- `launch()` 里，前台是已知弹窗宿主时也认为启动成功（调用方本来就懂怎么关弹窗）
- 第一次没落到目标包时，**先硬停再试一次**再下结论——因为"App 已经在手机自己的屏上
  跑着"会让 `am start --display N` 把那个旧任务提上来

### 15.2 "合法宿主"必须是按 App 的

`FOREIGN_HOSTS` 原本是一个全局集合（`com.android.phone` / `com.miui.securitycenter`
/ `com.xiaomi.account`），那是为设置页设的——移动网络页确实由电话 App 托管。
但对文件管理来说，这些包就是"爬虫跑出 App 了"：一次文件管理爬取把**小米账号页**
记成了它的一屏。

现在改成"爬取哪个 App 时允许交给哪些宿主"：

```python
FOREIGN_HOSTS = {
    "com.android.settings": {"com.android.phone", "com.miui.securitycenter", "com.xiaomi.account"},
}
```

其它 App 一律按污染处理。重爬文件管理后，账号页不再出现。

### 15.3 结论

App 覆盖能不能扩大，取决于 App 本身：

- **singleTask 首页**（浏览器、日历）——虚拟屏上无解，已能快速拒绝并说明原因
- **有强权限/隐私弹窗**（笔记）——卡在启动校验，需要扩展弹窗词表
- **页面结构简单**（文件管理）——能爬，但没多少可导航的东西，价值不大

下一次要扩，应该先按"launchMode + 有没有首启弹窗"筛一遍，而不是逐个试。

## 16. 先筛选，再爬：`GET /crawl/apps`（2026-11-09）

逐个试 App 太贵（一个失败要几分钟）。现在 App 里点「地图」会先问一次服务器：

```
GET /crawl/apps
[{"package":"com.android.settings","label":"设置","launch_mode":0,
  "state":"crawlable","reason":"标准启动模式，可以爬"},
 {"package":"com.android.browser","label":"浏览器","launch_mode":0,
  "state":"refused","reason":"上次爬取确认起不来（首页最终落在默认屏）"}]
```

界面里每个 App 前面带标记：**✅ 可以爬 / 📌 已有地图 / ⛔ 上不了虚拟屏**，
选中 ⛔ 的会直接弹窗说明，不会白跑一次爬取。

判定分两层：

| 层 | 依据 | 特点 |
|---|---|---|
| **静态** | `PackageManager` 里 launcher activity 的 `launchMode` | 立刻能得到，但对"入口 Activity 自己不是 singleTask、却把任务交给一个 singleTask 页面"的 App 会误判 |
| **动态** | 真爬一次失败后记住这个包 | 一定准确，代价是一次失败 |

浏览器的 SplashActivity 就是静态误判的例子（standard，但真正的页面在别处、
被钉在默认屏）。所以失败会被写进 `files/refused-apps.txt`，下次直接标 ⛔。

### 现状（这台机器，10 个可启动 App）

```
✅ 可以爬   设置 文件管理 笔记 相机 应用商店 大众点评 美团 小米换机
⛔ 起不来   浏览器（动态确认） 未成年人模式（singleTask）
```

### 16.1 静态判定只能当"提示"，不能当结论

`launchMode` 是静态信息，两个方向都会错：

| App | 静态判定 | 实际 |
|---|---|---|
| 浏览器 | standard | ❌ 起不来（入口把任务交给被钉住的页面） |
| 时钟 | **singleTask** | ✅ 能爬，而且 9/9 落点 |
| 日历 | **singleTask** | ❌ 起不来 |

第一版把"非 standard"直接标成 ⛔ 并禁止爬取，结果**时钟被打成不可爬**——而我们发布的地图正是它。

所以现在分三档：

```
✅ 可以爬      静态是 standard
⚠️ 可能起不来   静态是 singleTask —— 允许试一次
⛔ 试过，起不来  真爬失败过 —— 记住，不再推荐
```

`refused-apps.txt` 也从只增变成双向：某个 App 后来爬成功了，就把这条记录删掉
（它曾经累积到 16 条，其中一半其实能爬）。
