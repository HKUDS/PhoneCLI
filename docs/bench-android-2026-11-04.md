# Android 宏重放落点实测（2026-11-04）

## 目的

验证 PhoneCLI 的核心假设在 Android 上是否成立：**离线编译的 app map + 坐标宏，能否在真机/模拟器上确定性重放到目标屏，且不调用任何模型。**

## 环境

| 项 | 值 |
|---|---|
| 设备 | `sdk_gphone64_arm64`（Android Studio AVD，AndroidLab 评测镜像） |
| 系统 | Android 13（SDK 33），arm64-v8a |
| 分辨率 | 1440 × 3120（与仓库内 Android map 的 `screen_w/h` 完全一致） |
| 已装 App | 9 个与 map 对应的应用（Bluecoins / Calendario / Cantook / DeskClock / Contacts / Maps.me / PiMusic / Settings / Zoom） |
| 测量工具 | `phoneagent.probe` + `phoneagent.bench` |

## 方法

1. `probe` 确认链路：adb → info → screenshot → `uiautomator dump` → 屏幕识别
2. 从指定 map 中抽样 **NAV** 操作（`build_operations` 生成，含 `screen_macros` 全路径）
3. 对每个操作：`MacroExecutor` 重放 → 等 1.5s → dump → `identify_current_screen` → 与操作的 `target_screen` 比对
4. 记录：重放是否报错、落点是否精确命中、命中置信度、耗时

## 结果

### 汇总

| App | 抽样 | 重放成功 | 精确落点 | 耗时/操作 |
|---|---|---|---|---|
| Calendar（9 屏 / 36 操作） | 6 | 6/6 | **6/6 (100%)** | 11.1s |
| Settings（50 屏 / 288 操作） | 6 | 6/6 | 2/6 (33%) | 11.0s |
| Contacts（48 屏 / 229 操作） | 6 | 6/6 | 2/6 (33%) | 11.0s |
| **合计** | 18 | **18/18 (100%)** | **10/18 (56%)** | 11.2s |

> 每次操作 0 次模型调用；11s 主要花在 `force_stop + launch(3s) + dump`。

### 失败的 8 例归因

对每个"未精确落点"的案例做两件事：读落点 dump、算期望屏与落点屏的 Jaccard 相似度。

| 类型 | 数量 | 证据 |
|---|---|---|
| **A. 需要滚动的目标** | 2 | `security` / `wallpaper & style` 的 map 元素 `found_at_scroll=1`，宏里有一次 `swipe`；重放后仍停在 Settings 根屏 |
| **B. 不需要滚动的目标** | 5 | 落点屏与期望屏 Jaccard = 0.00–0.27，**确实是不同屏**（例：Contacts `create_contact_home` 期望 `screen_27`，落点 `screen_10`） |
| **C. 识别歧义** | 1 | `connected_devices` 期望 `screen_27`、落点 `screen_28`，Jaccard **0.78**（父子屏文本高度重叠） |

**滚动与落点的相关性**：含 swipe 的操作 **0/2** 命中，不含 swipe 的 **4/10** 命中。

### 直接观察

对 `security` 和 `wallpaper & style` 的落点 dump 做文本检查，两者都只显示 Settings 根屏（`text="Settings"`），说明 **tap 打在了空白/非目标位置**，而不是导航到了错误页面。

对 Settings 根屏做实时 dump 发现：`Notifications` 可见（y≈2043，与 map 的 0.655 坐标吻合，命中），而 `Security` / `Display` / `Wallpaper & style` **不在当前可见节点里**——它们在列表折叠区以下。

## 解读

1. **宏重放本身从不报错**（18/18）——设备层、坐标换算、swipe/tap 注入都是通的。
2. **落点精度分层很明显**：页面结构简单、元素首屏可见的 App（Calendar）100% 命中；深层设置类页面只有 33%。
3. 失败不是"识别不准"，而是**动作没把 App 带到目标屏**：
   - 需要滚动的目标：`found_at_scroll` 只记了"滚几次"，没记滚动后的**实际位置**，滑动手感一变就偏；
   - 不需要滚动的目标：更可能是 **map 与当前 App 版本/系统版本之间存在漂移**（这批 map 来自 AndroidLab 镜像的某一版 App）。
4. 屏幕识别本身也有弱点：**覆盖率（overlap / map 屏元素数）偏向元素少的屏**，父子屏只差 1–2 个文本时容易认成父屏（C 类）。

## 对方案的启示（下一步）

| 问题 | 建议 |
|---|---|
| 坐标宏对滚动敏感 | 重放时**先用 dump 定位元素再换算坐标**（text-anchored tap），而不是盲点；这也正是 Mika 的 selector 思路 |
| map 漂移无感知 | 每次重放后加一次**落点校验**（`identify_current_screen`），不匹配就重定位或降级到 VLM |
| 识别偏向小屏 | 打分从 `overlap` 改为 **F1（coverage × precision）**，并提高最低覆盖率门槛 |
| 宏失败静默 | `MacroExecutor` 目前只报"步骤是否执行"，应增加**语义结果**（是否到达 `target_screen`） |

## 复现

```bash
export PATH="$HOME/Library/Android/sdk/platform-tools:$PATH"
python3 -m phoneagent.probe                      # 链路自检
python3 -m phoneagent.probe --tap                # 重放一条 NAV 宏
python3 -m phoneagent.bench \
    --map phonecli_android/app_maps/calendar_android.yaml --limit 6
python3 -m phoneagent.bench \
    --map phonecli_android/app_maps/settings_android.yaml \
    --map phonecli_android/app_maps/contacts_android.yaml \
    --limit 6 --save-dumps .probe/bench --out .probe/bench/rows.json
```

---

## 追加实验：跨分辨率重放（同日）

模拟器改为 Redmi K90 的分辨率（1280×2772），仍用按 1440×3120 编译的 Calendar map，
比较"原始坐标"与"按比例缩放"两种重放方式。

| 条件 | 重放成功 | 精确落点 |
|---|---|---|
| 原生 1440×3120（基准） | 6/6 | **6/6 (100%)** |
| 1280×2772，**不缩放** | 6/6 | **1/6 (17%)** |
| 1280×2772，**按比例缩放** | 6/6 | **5/6 (83%)** |

缩放比例 `x = 1280/1440 = 0.889`、`y = 2772/3120 = 0.889`；不缩放时纵向偏
228–337px（约 2 个列表行），足以点到相邻条目。

结论：

1. **坐标宏对分辨率敏感**：换一块屏就几乎全废（100% → 17%）；
2. **一次线性缩放能救回大部分**（17% → 83%），但救不回全部——剩下 1 例大概率仍是
   "元素首屏不可见 / 布局随宽度变化"导致的，需要靠文字锚定定位。

实现：`MacroExecutor(source_size=(w, h))` 会在重放时把 x/y 缩放到设备当前纵横比；
`bench --no-scale` 可关闭以做对照。

## 真机侧观察（Redmi K90，Android 16 / HyperOS 3.0）

| 项 | 结果 |
|---|---|
| adb / 截图 / UI dump | ✅ 可用（`uiautomator dump` 89 节点） |
| `input` 注入 | ⚠️ **默认被 ROM 拦截**：`SecurityException: INJECT_EVENTS`；开启开发者选项里的「USB 调试（安全设置）」（需小米账号 + SIM）后恢复 |
| 仓库自带的 AOSP Settings map | ❌ 对 HyperOS 设置界面 `conf=0.00`，文字完全不同（`我的设备/散热风扇/WLAN/设备互联`） |
| 分辨率 | 1280×2772，与 map 的 1440×3120 不同 |

由此得出的结论：**真机上必须就地编译 app map**，仓库里 AndroidLab 的 map 只适用于对应镜像。

---

## 真机就地编译 + 重放（Redmi K90，同日）

### 构建

```bash
python3 -m phoneagent.build_map --serial e3fa9e60 \
    --package com.android.settings --app 设置 \
    --launch-intent android.settings.SETTINGS \
    --max-screens 8 --max-depth 1 --scroll-pages 1 \
    --no-classify --no-enrich \
    --output phoneagent/maps/k90_settings.yaml
```

| 指标 | 值 |
|---|---|
| 耗时 | 90–108 s（两次构建） |
| 屏数 / 元素 | 8 / 113 |
| 无线电状态（Wi-Fi/蓝牙/飞行模式） | 构建前后**完全一致**，未误触开关 |
| 屏幕 | `svc power stayon true` + WAKEUP + `dismiss-keyguard`，结束恢复 |

### 重放全部 NAV 操作

| 指标 | 值 |
|---|---|
| 重放成功 | **9/9 (100%)** |
| 精确落点 | **9/9 (100%)** |
| 平均耗时 | **10.58 s / 操作** |
| 模型调用 | **0** |

```
2年内可享多次免费维修  screen_2 → screen_2  1.00  OK
wlan                   screen_7 → screen_7  0.97  OK
yangqin                screen_3 → screen_3  0.96  OK
了解micare              screen_2 → screen_2  1.00  OK
我的设备                screen_4 → screen_4  1.00  OK
搜索系统设置项           screen_1 → screen_1  1.00  OK
散热风扇                screen_6 → screen_6  1.00  OK
新版本                  screen_5 → screen_5  1.00  OK
账号_云服务_米金资产等    screen_3 → screen_3  0.96  OK
```

### 五组条件放在一起

| 条件 | 精确落点 |
|---|---|
| 模拟器 + 仓库 AOSP map，原生分辨率 | 6/6 (100%) |
| 模拟器 + 仓库 AOSP map，K90 分辨率，不缩放 | 1/6 (17%) |
| 模拟器 + 仓库 AOSP map，K90 分辨率，缩放 | 5/6 (83%) |
| 真机 + 仓库 AOSP map | 不可用（`conf=0.00`，HyperOS 设置页文本完全不同） |
| **真机 + 就地编译 map** | **9/9 (100%)** |

### 这次踩到的两个真机坑（已修进代码）

1. **`monkey` 在 HyperOS 上静默失败**
   `monkey -p com.android.settings -c LAUNCHER 1` 返回 `Events injected: 1`，
   但前台仍停在别的 App。后果：爬虫后续所有"新屏幕"抓到的其实是桌面
   （第一次构建里 `screen_6` 的内容是`录音机/相册/短信`）。
   修法：`AdbDevice.launch()` 改为**验证前台包名**，失败则回退
   `cmd package resolve-activity` → `am start -n`；map 可携带
   `launch_intent`（如 `android.settings.SETTINGS`）走显式 intent。

2. **编译期坐标在重放时漂移**
   即使是同一台设备，Settings 首页的横幅/卡片会变，坐标点击会落到相邻行
   （实测点到了"密码/云服务"那一行）。修法：`build_operations` 给最后的 tap
   带上元素文本，`MacroExecutor` 重放时先 dump 按文本定位、**找不到才退回坐标**。

### 结论

- **跨设备重放不如就地编译**：同一套技术，AOSP map 在 HyperOS 上直接归零，
  就地编译后 100%。这支持"map 是设备/ROM 绑定的产物"这一判断。
- **文字锚定 + 验证式启动**是两个必要的工程补丁，缺一个都到不了 100%。
- 编译成本 90–108 s/App（深度 1、8 屏），重放 10.6 s/操作、0 次模型调用。

---

## 深度曲线：多爬一层，落点掉一半（Redmi K90，同日）

同一台设备、同一个 App，只改 BFS 深度：

| 条件 | 屏 | 元素 | 边 | 操作 | 编译耗时 | 重放精确落点 |
|---|---|---|---|---|---|---|
| **depth = 1** | 8 | 138 | 9 | 136 | **90 s** | **9/9 (100%)** |
| **depth = 2** | 20 | 376 | 35 | 370 | **326 s** | **5/12 (42%)** |
| depth = 2 + 验证式点击 | 20 | 369 | 37 | 362 | 361 s | 4/12 (33%) |

覆盖率翻了（8 → 20 屏、9 → 35 条边），编译成本涨 3.6×，**但重放落点从 100% 掉到约 40%**。

### 为什么深度会让地图变差

单独复现两个失败用例（`应用设置`、`省电与电池`），发现**导航其实是对的**：

```
应用设置 → 前台 com.miui.securitycenter，当前屏 ['1.47MB','应用双开','返回',...]
省电与电池 → 前台 com.miui.securitycenter，当前屏 ['电池保护','耗电检测','可用时长',...]
```

但地图里 `screen_11` / `screen_12` 记录的内容与这两页重叠只有 1/14、1/19——**边被记到了错误的屏幕上**。

原因：编译器在"点开父屏 → 再点元素"这条多步路径上，只要中间有一次点击因布局漂移落到相邻元素，它仍会把随后 dump 到的屏幕记成目标元素的 `leads_to`。深度越深，路径越长，这种**静默错误归属**的概率越高；depth=1 的路径只有 `force_stop + launch + tap`，几乎不会错。

### 试过的修法（部分有效，未根治）

给编译器的 `controller.tap(x,y)` 加"按标签重新定位"：用最近一次 dump 找到坐标对应的标签，再对实时屏重新解析标签后点击。

结果：46 次点击里只有 **5 次**命中了可验证的标签。原因是 `_last_xml` 在点击时通常是**上一屏**的 dump（父屏 dump 发生在路径回放之前），坐标对不上，于是 41 次退回盲点。

**结论**：要根治必须让爬虫在点击前**显式传入目标标签**（需要改上游 `build_app_map` 的调用约定，或自己重写 BFS 主循环）。这属于编译器层面的接口改动，值得单独做一轮。

### 对使用者的直接建议

- 需要"打开某个设置页"这类**一级导航**：`--max-depth 1`，90 秒编译，重放 100%；
- 需要"进入子页再操作"：**不要指望加深 BFS**，应该按需对单个目标页做"定点编译"（只爬那一条路径），或者直接走纯 GUI + VLM。

---

## 追加：最小配置 vs 完整配置（回答"原脚本是否被用完整了"）

同一台 K90、同一个 App、depth=1，只改开关：

| 配置 | 屏 | NAV 操作 | 编译耗时 | 重放精确落点 |
|---|---|---|---|---|
| **最小**：`--no-classify --no-enrich --scroll-pages 1` | 8 | 9 | **90 s** | **9/9 (100%)** |
| **完整**：`--classify --enrich --scroll-pages 3` | 8 | 7 | **200 s** | **7/7 (100%)** |
| 最小 + `--max-depth 2` | 20 | 35 | 326 s | 5/12 (42%) |

### 结论

1. **原脚本在 depth=1 上是可靠的**：最小配置和完整配置都 100%。之前那个 42%
   不是"脚本烂"，而是**深度**造成的。
2. 完整配置的代价是**编译时间翻倍（90→200 s）、NAV 覆盖略降（9→7）**——分类器
   把横幅/推广元素判成 DYNAMIC 后过滤掉了（有一屏甚至"1 stable, 20 dynamic"），
   换来的是地图里没有易变元素。落点没有提升，因为 depth=1 本来就不会漂。
3. depth=2 的崩塌与 classify 无关（那组没开 classify），根因仍是**多步路径的
   开环归属**：编译器无法确认"我点的这个元素"就是"产生下一屏的那个元素"。

### 顺带修掉的三个真机障碍（都在 `build_map.py` 里）

| 问题 | 现象 | 修法 |
|---|---|---|
| 包名冲突 | 从仓库根跑时 `from phonecli.llm_client import ...` 解析到 **iOS 包**，`text_completion` 没有 `label` 参数 → classify/enrich 全挂 | 导入爬虫前把 `phonecli_android/` 插到 `sys.path[0]` 并清掉已缓存的 `phonecli` 模块 |
| 推理模型返回空 | `qwen/qwen3.8-flash` 在 classify 提示下把 token 全花在思考上，`content` 为空 → 所有元素被判 DYNAMIC，地图塌成 1 屏 | LLM 调用改走 `OpenAICompatClient(disable_reasoning=True)` |
| 起始屏不可控 | `am start -a android.settings.SETTINGS` 会被小米云"首月特惠"页或账号页接管；一次误点还能把设备卡在 `com.miui.cloudservice/WXEntryActivity`（BACK 退不掉） | `--expect-home` 启动自检（失败重试 + 中止）；`launch_intent` 支持组件形式 `pkg/.Activity` |

---

## 校验版编译器：深度 2 从 42% 救回 92%

针对"开环归属"这个根因，重写了 BFS 循环（`phoneagent/build_map.py::_crawl_verified`），
上游 `evaluation/build_map.py` 保持未改动，只复用它的解析/签名/分类辅助函数。

### 三处改动

1. **点击前按标签重定位**：每次 tap 前重新 dump，用标签找到元素真实中心再点；
   标签不在屏上就**跳过这个分支**，不再用过期坐标盲点。
2. **点击后校验屏幕**：新屏签名与父屏相同 → 记为"该元素不导航"（`no_op`），
   不再凭空造出一个幽灵屏。
3. **记录跨包跳转**：前台包名变化时打日志（HyperOS 把很多设置页交给
   `com.miui.securitycenter` / `com.xiaomi.account`）。

### 结果（同一个 App、同一台 K90、depth=2、`--no-classify`）

| 编译器 | 屏 | 编译耗时 | 重放精确落点 | 每操作耗时 |
|---|---|---|---|---|
| 上游开环版 | 20 | 326 s | **5/12 (42%)** | 11.2 s |
| **校验版** | 20 | 398 s (+22%) | **11/12 (92%)** | 13.4 s |

**+50 个百分点，代价是编译时间 +22%、重放 +2 秒/操作。**

### 完整对照表

| 配置 | 屏 | 编译 | 精确落点 |
|---|---|---|---|
| depth 1 · 最小 | 8 | 90 s | 9/9 (100%) |
| depth 1 · 完整（classify+enrich+scroll 3） | 8 | 200 s | 7/7 (100%) |
| depth 2 · 上游开环 | 20 | 326 s | 5/12 (42%) |
| **depth 2 · 校验版** | 20 | 398 s | **11/12 (92%)** |

### 校验版构建时的统计

```
{'taps': 30, 'skipped': 2, 'no_op': 3, 'foreign': 19}
```

- `skipped=2`：目标标签在父屏上找不到，直接放弃该分支（开环版会盲点）
- `no_op=3`：点完屏幕没变，开环版会把它当成"导航到了新屏"
- `foreign=19`：19 次点击后前台换了包（HyperOS 的设置页大量委托给系统组件）

唯一失败的 1 例是两张几乎相同的云服务促销页（`screen_16` vs `screen_17`，
落点置信度 0.95），属于识别歧义而非导航错误。

---

## 脱敏与折叠区元素（同日）

### 脱敏（`phoneagent/redact.py`）

地图里会照抄屏幕上的文字，个人手机上就是账号名、手机号、设备名（实测：
`yangqin`、`手机号：181****8488`、`yangqin的REDMI K90 Ultra`）。iOS 侧有
`sanitize_map.py`，Android 侧此前没有。

实现：手机号/邮箱正则 + 设备名自动采集（`settings get global device_name`
与 `ro.product.model`）+ `X的<设备名>` 前缀自动提取为账号名 + `--redact-term`
手工补充。命中的元素标 `redacted: true`，对应的宏锚点被移除（回放退回坐标）。

**实测权衡**：直接把工作地图就地脱敏后，重放落点从 **100% 掉到 50%**——因为
屏幕识别和文字锚定都依赖那些被替换掉的标签。所以构建时改为**只输出一份
`*.public.yaml` 副本**，工作地图保持原样：

```
redact : 6 elements -> k90_scroll.public.yaml (working map kept unredacted)
```

| 文件 | 用途 | PII |
|---|---|---|
| `k90_scroll.yaml` | 本地回放 | 有（已 gitignore） |
| `k90_scroll.public.yaml` | 分享/提交 | **0 残留** |

### 折叠区元素（`found_at_scroll`）

校验版爬虫对每个新屏都用 `_collect_screen` 滚动采集，元素带 `found_at_scroll`，
宏里自动插入对应次数的 swipe：本次构建 **74/164 个操作**含 swipe。

同时修了一个被这个功能暴露出来的识别缺陷：重放后的 dump 只含首屏内容，而地图
里存了折叠元素，覆盖率被稀释。`identify_current_screen` 现在只拿
`found_at_scroll == 0` 的元素做匹配（若无可见元素则退回全部）。

| 版本 | 折叠区操作 | 重放精确落点 |
|---|---|---|
| 仅修识别前 | 74/164 | 4/8 (50%) |
| **修识别后** | 74/164 | **7/8 (88%)** |

---

## 脱敏版可回放 / 校验 / 失败恢复（同日，收尾）

### ① 脱敏版现在能跑了：50% → 86%

原方案把标签替换成 `<personal>` 后，识别仍拿它去比对（永远匹配不上），覆盖率被
稀释。改成**排除法**：`redacted: true` 的元素

- 不参与 `identify_current_screen` 的覆盖率计算
- 不进入 `build_operations`（标签没了，路由也用不上）
- 对应的宏文字锚点被移除，回放退回坐标

| 地图 | 元素 | 操作 | 重放精确落点 |
|---|---|---|---|
| 工作地图（未脱敏） | 170 | 164 | 8/8 (100%) |
| 公开副本（脱敏） | 170（6 个 redacted） | 158 | **6/7 (86%)** |
| 公开副本（改排除法之前） | 170 | — | 4/8 (50%) |

唯一失败的一例正是被脱敏掉标签的促销行——预期之内的损失。公开副本还能直接跑任务：

```
$ python3 -m phoneagent.run --map k90_scroll.public.yaml --task "打开WLAN"
  round 0 [route ] wlan  设置
  round 0 [macro ] wlan  macro ok (3/3 steps)
  round 0 [verify] wlan  True
result : ok (macro) via wlan
```

### ② validate 阶段（`phoneagent/validate.py`）

iOS 侧有 `validate_map.py`，Android 侧此前没有，现在补上。检查项：

| 类别 | 规则 |
|---|---|
| 结构 | 必需键、`screen_0` 存在、屏 id 不重复、center ∈ [0,1] |
| 图 | `leads_to` 指向存在的屏、每个屏有 launch 前缀宏（可达性） |
| 宏 | tap 步必须有 x/y |
| 隐私 | 残留手机号/邮箱报 **error**（400/800 客服热线不算） |
| 质量 | 元素 < 2 的屏、Jaccard ≥ 0.9 的重复屏、脱敏比例 > 30% 报警告 |

**立刻抓到了我之前漏掉的真 PII**：

```
ERROR  E_PII  screen_10: unredacted personal data  +8618124576373
ERROR  E_PII  screen_19: unredacted personal data  3081956668
```

（这两条在 `k90_scroll.yaml` 的工作地图里，说明设备名/账号名之外的**完整号码**
也要靠正则兜住——脱敏模块已经覆盖，只是旧地图没重跑。）

### ③ 失败恢复（`PhoneAgent`）

宏重放后会**校验落点**：识别出的屏 ≠ 操作的 `target_screen` 时，重置重放一次
（`macro_retries`，默认 1），仍然不对就交给 VLM 兜底，并在步骤里留下 `retry` 记录。

```python
landed = self._landed(app_map)          # dump + identify_current_screen
while landed not in (None, op.target_screen) and attempts < retries:
    replay; landed = self._landed(app_map)
if landed not in (None, op.target_screen):
    return None                          # VLM takeover
```

代价是每次宏重放多一次 dump（约 0.6 s），换来"落错不静默"。
