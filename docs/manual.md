English · [中文](manual.zh.md)

# PhoneGo manual

Everything the app does, screen by screen. Labels are given as the English
interface shows them; the app can also run in Chinese (Settings → Language).

If you have not installed it yet, read [getting started](getting-started.md)
first — this page assumes the app is paired and has a model key.

---

## 1. The screen

```
┌──────────────────────────────────────────────┐
│ PhoneGo                         Maps Usage Settings │
│ ‹chat name› ▾                                │
│  ┌────────────────────────────────┐  ┌──────┐│
│  │ messages, step lines, answers  │  │ live ││
│  │                                │  │frame ││
│  └────────────────────────────────┘  └──────┘│
│ [ Message PhoneGo…            ]  ⚡ Auto  ↑  │
└──────────────────────────────────────────────┘
```

| Element | What it is |
|---|---|
| **Maps / Usage / Settings** | the three chips, top right |
| **‹chat name› ▾** | the current chat; tap to switch, create or delete |
| **messages** | your instruction, the agent's step lines, the answer |
| **live frame** | what the agent's own screen shows right now — tap to enlarge, pinch to zoom, drag to pan |
| **input** | type here; the arrow sends |
| **⚡ Auto / 👁 Vision** | which mode the next task uses |

During a task a status line appears above the input — *Working… 12 steps* — with
**Cancel** next to it.

---

## 2. Chats

Tap the chat name to open the list.

| Action | Effect |
|---|---|
| pick a chat | switches to it; the previous one stays intact |
| **＋ New chat** | starts an empty conversation; earlier ones are still under the list |
| **Delete this chat** | removes the conversation log only. Maps on the phone and finished task results are untouched |

A chat is also the unit the agent remembers: within one chat it can refer back to
what it did earlier. Switching chats while a task runs is refused — cancel first.

---

## 3. Running a task

Type an instruction and send. Things you will see:

- **step lines** — one per action, in order. With a map they read like
  `Map replay: go to "Hotel_search" · 5 actions`; without one, one line per tap.
- **answers** — the agent ends with a sentence, not a log. If it stopped
  somewhere you might continue, it ends with one question offering to move that
  page to your screen.
- **partial results** — if it got part of the way, the answer says `Partly done:`
  and why.
- **Cancel** — stops the task at the next safe point; the map and anything
  already learned are kept.

Some steps need you:

| The agent asks | You reply |
|---|---|
| *This step spends money: "Submit order", amount ¥2401. Continue?* | reply 「继续」 / *go ahead* to allow, anything else cancels |
| *Needs your answer* (a captcha, a login, a choice) | do it on the phone, then reply anything |
| *PhoneGo needs a moment from you* | same — the task is paused, not failed |

Spending is stopped **before** the tap, not after: the agent reads the amount off
the screen and puts it in the question.

---

## 4. Two modes

The chip next to the send button switches the next task.

| Mode | How it works | When to use |
|---|---|---|
| **⚡ Auto** (default) | route → replay the app map's macro; the vision model is only used where the map has no answer | almost always; it is several times faster and deterministic |
| **👁 Vision** | every step is decided from a screenshot | an app with no map yet, or a screen the map keeps getting wrong |

Auto falls back to vision by itself — you do not have to switch to get out of a
dead end. Vision mode costs model calls on every step.

---

## 5. Hands-off mode

In **Settings** there is a checkbox: *Drive your own screen (hands-off)*.

| Off (default) | On |
|---|---|
| the app runs on a virtual display the phone does not show | the app runs on **your** screen, so you watch every step |
| you keep using the phone | the phone is busy while the task runs |

Some apps refuse to run anywhere but the main screen — JD, Taobao and other
apps that check for a visible window. Those only work hands-off. The agent still
returns your screen when the task ends.

---

## 6. Maps

**Maps** lists what has been compiled on this phone.

Each entry shows: the app, how many screens and actions the map holds, and its
**landings** — a self-test that replays a few paths at random and checks they end
where they were recorded (`Landings 4/4`, or *not measured*, or *(older data)*).

| Button | Effect |
|---|---|
| **Pin** | make this map the one used for that package |
| **Re-measure landings** | replay a few paths again; takes a minute |
| **Re-crawl** | build the map again, keeping the old one until the new one is done |
| **Delete map** | removes it from the phone; the app goes back to having no map |

### Building a map

**＋ Build a new map** asks three things:

| Setting | Default | Meaning |
|---|---|---|
| **Max screens** | 50 | how much of the app to record |
| **Time budget (minutes)** | 30 | stops when it runs out |
| **Max depth** | — | how many taps from the home screen; 1 = only what the home screen lists |

The app list marks each app: ✅ can be crawled, 📌 already has a map, ⚠️ may not
launch (try it), ⛔ tried and it cannot start on the virtual display.

Crawling runs on the agent's display; you can keep using the phone. Progress
appears line by line in the chat, and the map lands in **Maps** when it is done.

---

## 7. Usage

**Usage** counts what the agent spent.

| Section | Shows |
|---|---|
| day chart | tokens per day, today and the last days |
| **By task** | calls and tokens per task, most expensive first |
| **By model** | the same split across the models you configured |

Only model calls are counted: a replayed step costs no tokens, which is why a
mapped task looks cheap next to a vision one.

---

## 8. Settings

Tap **Settings**. The sheet is the model configuration.

The sheet is short on purpose: two things to fill in, one link to test them, and
the rest folded away.

| Element | Notes |
|---|---|
| **API Key** | yours; encrypted with a key in the Android Keystore, never leaves the phone. A key that starts with `sk-or-v1-` selects OpenRouter, `sk-ant-` selects Anthropic |
| **Provider** | opens a list of twelve: DeepSeek (official), DeepSeek, OpenRouter, Zhipu, Alibaba Bailian, SiliconFlow, Moonshot Kimi, OpenAI, Gemini, Anthropic, local, custom. Picking one fills the base URL and the model id |
| **Test connection** | one cheap call; reports `✅ Works:` or the error. Saving runs it for you |
| **Advanced** (closed) | **model**, **api base** (which protocol is spoken: `…/v1` is OpenAI-style, `…/anthropic` is Anthropic-style), **vision model** (empty = the same model looks at screenshots), **max steps per task** (default 35) |
| **Never ask me about money** | off by default. When on, steps that pay or order run without asking; it also skips the confirmation on shell commands that change the phone. Turning it on asks once |
| **Diagnostics** | the runtime: Python version, display id, heartbeat age, and a reconnect button |
| **Pair wireless debugging** | run the pairing flow again |
| **Save / Cancel** | saving restarts the local agent; a running task is unaffected |

---

## 9. Long-press Settings

Hold the Settings chip for the system pages:

| Item | What it opens |
|---|---|
| **Accessibility service** | the system page for the service the pairing code reader needs |
| **Battery optimisation** | the system page to set PhoneGo to unrestricted, so the cleaner does not kill it in the background |
| **Export backup** | writes a zip to *Download* with the pairing identity, the maps, the chats and the model settings. **The API key inside is plain text** — do not share the file |
| **Import backup** | restores that zip and restarts the agent; use it after a reinstall or on a new phone |
| **Language** | Follow system · 中文 · English |

---

## 10. What it does, and what it will not

**It does:** open apps, tap, scroll, type, read what is on screen, carry a value
from one app to another, keep a page for you to continue on, and tell you what it
found.

**It will not:**

- **spend money without asking** — payment, ordering and submitting stop and ask
  first (unless "never ask me about money" is on);
- **type your passwords or one-time codes** — those screens are handed to you;
- **work on every app out of the box** — an app with no map is slower and costs
  model calls;
- **run on a locked phone** — the screen has to be on and unlocked;
- **guarantee a result** — apps change, and a map can go stale. The answer says
  what actually happened.

---

## 11. Troubleshooting

The full list of messages the app can print, with the exact wording, is in
[errors.md](errors.md).

| Symptom | Cause | Fix |
|---|---|---|
| *No pairing identity yet* | the app has never paired | turn on wireless debugging, reopen the app, or press **Pair wireless debugging** |
| worked, then stopped after a reboot | Android turns wireless debugging off at boot | turn it back on; the app reconnects by itself |
| tasks die in the background | the system cleaner reclaimed the app | set battery saving to unrestricted (long-press Settings) |
| *PhoneGo's background process is stuck (N s without a heartbeat)* | the agent process died or froze | the app restarts it once by itself; if the message comes back, swipe it away from Recents and open it again |
| the chat looks empty after a task finishes | a second chat window used to open before the transcript was saved | fixed in 0.4.2; on older versions, reopen the app and the conversation is still under Chats |
| no pairing dialog seen | “Pair device with pairing code” is not open in Wireless debugging | open it, then press Pair again within two minutes |
| steps read `👁` / no replay | this app has no map on this phone yet | expected; build one under Maps |
| a map crawl starts with ⛔ | that app cannot start on the virtual display | use hands-off, or pick another app |
| *Test connection* fails | wrong key, base URL or model id | check which protocol the base URL implies |
| an app is always on your own screen | its pages are tasks the system hosts on the default display | the agent moves it back up to twice, then follows it for the rest of the task |

---

## 12. Glossary

| Term | Meaning |
|---|---|
| **map** | a compiled record of an app's screens and the actions that lead between them |
| **macro** | a named path in a map (e.g. *Hotel_search*), replayable end to end |
| **replay** | executing a macro — deterministic, no model calls |
| **landing** | the map's self-test result: how many sampled paths ended where recorded |
| **virtual display** | a screen the system creates for the agent, which the phone does not show |
| **hands-off** | running on your own screen instead, so you watch every step |
| **never ask me about money** | money steps are not asked about |
| **capsule** | the floating window that mirrors the agent's screen |

---

Next: [getting started](getting-started.md) ·
[what the app is](product-app.md) ·
[how a map is compiled](map-building.md)
