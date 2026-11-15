English · [中文](product-app.zh.md)

# PhoneGo: a chat app that executes on a virtual display

**PhoneGo** (the project names stay `phonecli` / `phoneagent`, the package stays
`com.phoneagent.app`).

Goal: one APK that opens as a chat. The user says one sentence; the agent
operates the target app on an invisible virtual display and finishes the task,
leaving the main screen alone. The reference point is Mika, but **navigation
still comes from PhoneCLI's app maps + macros** (compiled once, replayed
deterministically) rather than from the VLM exploring step by step.

## Layers

```
┌───────────────────────────────────────────────┐
│ ① Chat UI (Compose)                           │  the user speaks, watches steps, takes over
├───────────────────────────────────────────────┤
│ ② Task service (foreground Service + queue)   │  runs in the background, interruptible, cancellable
├───────────────────────────────────────────────┤
│ ③ Agent core (phoneagent/, the Python code)   │  route → macro replay → verify → VLM fallback
├───────────────────────────────────────────────┤
│ ④ Device layer (PhoneDevice protocol)         │  adb (from a computer) / on-device (inside the phone)
├───────────────────────────────────────────────┤
│ ⑤ In-device shell + virtual-display helper    │  the only genuinely new piece
└───────────────────────────────────────────────┘
```

① ② ⑤ are new; ③ ④ already existed and came with 83 tests.

## Key decision: how Python gets into the APK

| Option | Approach | Upside | Cost |
|---|---|---|---|
| **A. Chaquopy** (do this first) | A Gradle plugin packages CPython into the APK; `phoneagent/` is **reused as is** | End to end within a week; zero rewrite of the algorithm; tuning still happens on the host | +20 MB, 1–2 s slower start, awkward JNI debugging |
| **B. Rewrite in Kotlin** | Translate agent / app_map / macro entirely | Fast start, small APK, could be published | 2–4 person-months; two implementations drift apart |

**Recommendation: A first, B later.** Get the product path working with
Chaquopy (chat → task → virtual display → done) and validate the interaction
and the reliability; only then consider moving the hot path to Kotlin.

The minimum Python-side change is `OnDeviceDevice` (implementing `PhoneDevice`
by calling the Kotlin helper client through JNI) plus replacing `print` with
callbacks.

## Milestones

| Stage | Contents | What you can check | Depends on |
|---|---|---|---|
| **M0** | Empty-shell APK: Compose chat page + foreground Service, a fake agent echoes | Installs, holds a conversation | JDK + SDK |
| **M1** | Virtual-display helper (`app_process`) driven by hand: create display → move Settings onto it → `screencap -d` → `input -d` | "Operating an app in the background" from the command line | nothing from the APK |
| **M2** | Chaquopy integration: `PhoneAgent` runs inside the APK, device layer swapped for the Java bridge | One real macro replay triggered from the chat | M0+M1 |
| **M3** | In-device shell (Shizuku first) | Works with the computer unplugged | M2 |
| **M4** | Conversation experience: step flow, interrupt, takeover, approval | "Watch and steer", like Mika | M3 |
| **M5** | Self-paired wireless debugging (drop Shizuku) | No external dependency | M3 |

M1 and M3 are the hard parts (1–2 weeks and 3–5 days); the rest is moving
capabilities that already exist into the shell.

## What can be reused today

| Module | How |
|---|---|
| `core/app_map.py` | As is; the YAML format does not change |
| `core/macro.py` | As is; only `OnDeviceDevice` has to be added |
| `core/agent.py` | As is (routing, fallback, retries and the planner are already there) |
| `core/llm.py` | As is (OpenAI-compatible, can point at any provider) |
| `build_map.py` / `validate.py` / `redact.py` | As is; they become the "rebuild a map on the phone" tools |
| `devices/base.py` | This is the ①→⑤ interface that `OnDeviceDevice` implements |

## How this differs from Mika (and why it matters)

Mika learns routes **from successful runs** (online, needs one success first).
This project **compiles app maps offline** (compile once, replay many times
with zero model calls): measured, depth 1 lands 100% of the time, 10.6 s per
operation, 0 model calls.

The two are complementary: screens the compiled map cannot cover fall back to
the VLM exploring, and a trajectory that succeeds can be frozen into a macro.

## Risks

1. **Virtual-display permission**: `DisplayManager`/`SurfaceControl` sit in a
   grey area and vendors may tighten it (HyperOS already trimmed `cmd display`).
2. **In-device shell**: self-pairing wireless debugging is weeks of work, and
   Shizuku makes the user install another app.
3. **Python footprint**: the Chaquopy APK is ≈ 60–80 MB; whether that is
   acceptable has to be decided.
4. **Background survival**: power management on Chinese ROMs is a permanent
   enemy; Mika's "compatibility self-check" is a good model.

---

## M2 done: Python inside the APK (2026-11-05)

### Environment (every obstacle worked around)

| Obstacle | Workaround |
|---|---|
| Homebrew directories not writable | Use the JBR 21 that ships with Android Studio as the JDK |
| Gradle downloads at 5 KB/s | A proxy brought it to 3.1 MB/s; `GRADLE_USER_HOME` points inside the workspace |
| AGP writes to `$SDK/.android` | Copy the SDK into the workspace (345 MB), point `ANDROID_USER_HOME` there |
| `ANDROID_SDK_HOME` conflicts with `ANDROID_USER_HOME` | Keep only the latter |
| Chaquopy has no Kotlin-DSL srcDir | `sync-python.sh` copies `phoneagent/` into `app/src/main/python/` |

### Result

```
Python 已就绪：3.8.20 (default, Oct 12 2024, 20:34:19)
phoneagent 0.1.0
app_map 模块：已加载
```

- APK 14 MB (arm64-v8a), containing `libpython3.8.so` and our package
- `phoneagent/core/prompts.py` became **self-contained** (vendored from
  `phonecli/prompts.py`), because the APK does not carry the upstream
  `phonecli` package

### Build commands

```bash
cd android-app && ./sync-python.sh
unset ANDROID_SDK_HOME ANDROID_PREFS_ROOT
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export GRADLE_USER_HOME="$PWD/../.toolchain/gradle-home"
export ANDROID_HOME="$PWD/../.toolchain/android-sdk"
export ANDROID_USER_HOME="$PWD/../.toolchain/android-home"
../.toolchain/gradle-8.11.1/bin/gradle --no-daemon assembleDebug
```

### What was still missing

- Calling `PhoneAgent.run()` from the app needed a device channel (shell) → M3
- At that point the app only proved that Python starts and the packages import
