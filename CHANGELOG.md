# Changelog

The PhoneGo Android app, one section per release. Newest first. Each release is a
`phonego-vX.Y.Z` tag with an APK on the [releases page](https://github.com/HKUDS/PhoneGo/releases);
every build is signed with the same key, so a newer one installs over an older one.

PhoneGo App 的版本变更记录，最新在前。每个版本对应 `phonego-vX.Y.Z` 标签与一个 APK
（见 [releases 页面](https://github.com/HKUDS/PhoneGo/releases)）；所有版本同一把签名，
新版本可直接覆盖安装。

The format follows [Keep a Changelog](https://keepachangelog.com/), and the
versions are the app's own — the CLI in this repository has its own history.

---

## 0.4.4 — 2026-11-21

`PhoneGo-0.4.4-arm64.apk` · SHA-256 `fd639ff3694c4d895fa166fc060ff56e11ba405f8daa905534e151ed613d774b`

**English**

- **Changed**: a page that jumps to the phone's screen is pulled back to the
  virtual display, and only followed after two attempts. Measured on
  小红书 → 携程: four pulls, one follow, task done in 3m31s.
- **Fixed**: the chat could hang on 执行中 forever — a `finish`-only step batch
  returned before the terminal-state handling ran.
- **Fixed**: the transcript and session index are written atomically (temp file,
  fsync, rename, `.bak` fallback); a kill mid-write could leave an empty chat.
- **Fixed**: the connection to adbd heals itself after `ssl.SSLEOFError`; a read
  timeout retries five times instead of ending the poll.
- **Fixed**: `returnToChat` no longer creates a second MainActivity instance; the
  wake lock is renewed while a task is watched.
- **Fixed**: `task_on` reads a task's own display from `am stack list` (a moved
  task used to appear under both displays and trigger pointless follows).

**中文**

- **变更**：跳到手机屏幕的页面会先被搬回虚拟屏，连续两次才改为跟随。小红书 → 携程实测：
  4 次搬回、1 次跟随，3 分 31 秒完成。
- **修复**：对话可能永远停在「执行中…」——只有 `finish` 步骤时提前返回，跳过了终态处理。
- **修复**：会话文件与会话索引改为原子写入（临时文件 + fsync + 重命名，读取回退 `.bak`），
  写一半被杀不会再出现空对话。
- **修复**：与 adbd 的连接在 `ssl.SSLEOFError` 后会自己重建；读超时改为重试 5 次而不是结束观察。
- **修复**：`returnToChat` 不再开第二个 MainActivity 实例；唤醒锁在任务期间持续续期。
- **修复**：`task_on` 改用 `am stack list` 读取任务所在屏（迁移过的任务曾同时出现在两块屏下，
  导致无谓的跟随）。

---

## 0.4.3 — 2026-11-20

`PhoneGo-0.4.3-arm64.apk` · SHA-256 `e0cef8e8d3a858eb1bd8ef0b263b82d8e43ee957e747eb648243e1ad80950f9c`

**English**

- **Fixed**: `Failed to connect to /127.0.0.1:8765` could still appear after
  0.4.2. The restart was gated on `AgentService.isReady()`, a flag that is set
  once and never cleared, so an agent that hung after starting looked healthy.
  The submission loop now restarts on ten refusals regardless of the flag, and
  `restart()` clears it before rebuilding.
- **Changed**: the failure message says the app already tried a restart.
- **Known gap**: if the agent hangs during a task, polling stops and the task is
  not resumed.

**中文**

- **修复**：0.4.2 之后仍可能看到 `Failed to connect to /127.0.0.1:8765`。重启条件挂在
  `AgentService.isReady()` 上，而这个标志只置位、从不回退，服务假死时仍显示健康。现在提交循环
  在连续 10 次拒绝后无条件重启，`restart()` 会先清掉该标志。
- **变更**：失败提示会说明"已经尝试自动重启一次"。
- **已知缺口**：任务执行中如果服务失联，轮询会停止，不会自动接回该任务。

---

## 0.4.2 — 2026-11-19

`PhoneGo-0.4.2-arm64.apk` · SHA-256 `81be9339552c1757453b6f1733f2e1961894f2ac55832d8b6404ff4144ad0d54`

**English**

- **Fixed**: a finished task could leave an empty chat. The agent brings the chat
  forward when a task ends, and the window had no launch mode, so that start
  opened a second window which read the transcript before the first had saved it.
  The window is `singleTop` now and the transcript is flushed at once.
- **Fixed**: the session list dropped every conversation listed after the current
  one (their files stayed on disk). Talking in an older chat lost the newer ones.
- **Changed**: model settings is one provider row instead of five fields. Twelve
  providers plus **Custom**; base URL, model, vision model and step budget hide
  under Advanced; a pasted `sk-or-v1-…` / `sk-ant-…` key picks its provider; saving
  runs the connection test.
- **Changed**: 自主模式 is now 花钱时不再问我 / "Never ask me about money". It only
  ever skipped the confirmation before money steps and destructive shell commands.
  It asks once before switching on, defaults to off, and a stored old value is
  reset once.

**中文**

- **修复**：任务结束后聊天可能变成空的。收尾时 agent 会把对话拉到前台，而窗口没有设置
  launchMode，于是又开了一个新窗口——新窗口在前一个还没落盘时读取记录，读到空文件。现在
  窗口是 `singleTop`，任务结束立刻落盘。
- **修复**：会话列表会丢掉排在当前会话之后的所有会话（文件还在磁盘上）。在旧会话里发消息就会
  让更新的会话从列表里消失。
- **变更**：模型设置改为「一行提供商」而不是五个字段。12 个提供商 + **自定义**；base URL、
  模型、视觉模型、步数上限收进「高级设置」；粘贴 `sk-or-v1-…` / `sk-ant-…` 会自动选提供商；
  保存时自动测试连接。
- **变更**：「自主模式」改为「花钱时不再问我」。它本来就只跳过花钱步骤和危险 shell 命令的确认。
  现在打开前会确认一次、默认关闭，旧的已存值会被重置一次。

---

## 0.4.1 — 2026-11-19

`PhoneGo-0.4.1-arm64.apk` · SHA-256 `0adc9c56162e54e4ea76ef0e8b3eb4009c5614ed0337be6092a2e49adf4811de`

**English**

- **Fixed**: a stalled agent now restarts itself. A heartbeat older than two
  minutes used to end with "swipe PhoneGo away from Recents"; the app stops and
  starts the local agent once and only asks you if that fails. `Failed to connect
  to 127.0.0.1:8765` means the app's own server is not up, and gets the same
  treatment.
- **Fixed**: the first message waits up to 100 s (it was 60) and restarts the
  agent once if it is still silent at 25 s.
- **Fixed**: "Test connection" no longer prints a raw Java exception; it restarts
  the agent and says to try again in ten seconds.
- **Added**: a `.sha256` file next to the APK. "There was a problem parsing the
  package" is almost always an incomplete download.

**中文**

- **修复**：卡住的 agent 会自己重启。心跳超过两分钟时，以前只会提示"从最近任务里划掉它"；
  现在先停掉再启动一次，只有这次也失败才让你手动处理。`Failed to connect to
  127.0.0.1:8765` 说明 App 自己的服务没起来，同样处理。
- **修复**：首次发消息会重试 100 秒（原来 60），25 秒还没响应就中途重启一次 agent。
- **修复**：「测试连接」不再直接打印 Java 异常，而是触发重启并提示十秒后再试。
- **新增**：APK 旁边附带 `.sha256` 文件——「解析包时出现问题」几乎都是下载不完整。

---

## 0.4.0 — 2026-11-17

`PhoneGo-0.4.0-arm64.apk` · SHA-256 `ca8d8d1fc2060d4a7b9a8fd07638eb9080a4fae31622e378c5efa91ab80bf6c5`

**English**

- **Added**: an English interface. Every label goes through one translation table
  (300 literals in the chat screen alone); resource strings are translated by a
  pass over the view tree.
- **Added**: Settings → Language — Follow system · 中文 · English. The app follows
  the phone's language by default.
- Notes: the agent's answers follow the language you type, and old chat history
  keeps the language it was written in.

**中文**

- **新增**：英文界面。所有文案走一张翻译表（仅聊天界面就有 300 处）；资源字符串通过遍历
  视图树翻译。
- **新增**：设置 → 语言——跟随系统 · 中文 · English，默认跟随手机语言。
- 说明：agent 的回答语言取决于你输入的语言；历史消息保留写入时的语言。

---

## 0.3.0 — 2026-11-17

`PhoneGo-0.3.0-arm64.apk` · SHA-256 `3003dbe08cfc8e5e2bf56cb2335a84977dffcd8d99b1ca67a43ffdc6b593cbee`

**English**

- **Added**: cross-app hand-over. What a subtask reads is carried into the next
  one; the search box prefers the name quoted in that report; each subtask opens
  its own app first; a step that must produce a name has to open an item and read
  its body. Measured: 小红书 → note → 「合和酒店」 → 携程, 49 steps, 200 s.
- **Added**: the phone comes back at the end of a task — the app returns to its own
  screen, a full-screen viewer is closed, the chat comes forward with the answer
  and one question about moving that page over.
- **Fixed**: false endings. Waiting stops at 20 minutes instead of six, a subtask's
  failure reason reaches the step list, a cold app gets six seconds to draw.
- **Fixed**: the start-up warm-up no longer opens other apps on the agent's display
  (it looked like the agent wandering off in a demo).
- **Fixed**: crawl leaks and crashes — the replaced display is released, shell
  calls have timeouts, the partial save no longer crashes.

**中文**

- **新增**：跨 App 传递。子任务读到的内容会带进下一步；搜索框优先使用报告里引号中的名字；
  每个子任务先打开自己的 App；要产出名字的步骤必须打开一条内容读正文。实测：小红书 → 笔记
  →「合和酒店」→ 携程，49 步 / 200 秒。
- **新增**：任务结束把手机还给你——目标 App 回到自己的屏、关掉全屏查看器、对话带着回答回到
  前台，并问一句要不要把那个页面转过来。
- **修复**：假失败。等待上限从 6 分钟提到 20 分钟，子任务失败原因进入步骤流，冷启动的 App
  有 6 秒绘制时间。
- **修复**：启动预热不再在虚拟屏上打开别的 App（演示里看起来像 agent 走错了）。
- **修复**：爬取的泄漏与崩溃——被替换的虚拟屏会释放、shell 调用有超时、部分保存不再崩溃。

---

## 0.2.0 — 2026-11-15

`PhoneGo-0.2.0-arm64.apk` · SHA-256 `ff842d55f5932e32d5ecd3cce6e666c9dac60c8ba7f52f976c3ee786e89c8453`

**English**

- First public build: the agent runs **inside** the phone and drives the target app
  on a virtual display, so the phone's own screen stays usable.
- Requirements: Android 14+, arm64, wireless debugging, the accessibility service
  (it reads the pairing code), and your own model API key.
- Added after the first upload: export/import a backup zip (pairing identity,
  maps, chats, model settings) so a reinstall does not start from zero.

**中文**

- 首个公开版本：agent 跑在**手机内部**，在一块虚拟屏上驱动目标 App，手机自己的屏幕照常可用。
- 要求：Android 14+、arm64、无线调试、无障碍服务（用来读配对码），以及自己的模型 API Key。
- 首次上传后补充：备份导出/导入（配对身份、地图、会话、模型设置），重装不用从零开始。

---

## Where the details live / 细节在哪里

Each release's full notes are on its release page — install steps, signing, the
measured numbers behind a change. 每个版本的完整说明在其 release 页面：安装步骤、签名、
改动背后的实测数据。

- Releases: <https://github.com/HKUDS/PhoneGo/releases>
- Manual for the current version: [docs/manual.md](docs/manual.md) · [中文](docs/manual.zh.md)
- First-run walkthrough: [docs/getting-started.md](docs/getting-started.md) · [中文](docs/getting-started.zh.md)
