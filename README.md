English · [中文](README_CN.md)

<div align="center">

# PhoneGo

**A phone agent that works on a screen of its own — so the one in your hand stays yours.**

[![Download](https://img.shields.io/badge/%E2%AC%87%20Download-PhoneGo%200.3.0%20%C2%B7%20Android-00d9ff?style=for-the-badge&logo=android&logoColor=white&labelColor=0b0f19)](https://github.com/HKUDS/PhoneCLI/releases/download/phonego-v0.3.0/PhoneGo-0.3.0-arm64.apk)
[![Docs](https://img.shields.io/badge/Docs-product%20%7C%20build-4ecdc4?style=for-the-badge&labelColor=0b0f19)](docs/README.md)

</div>

You type a sentence. PhoneGo opens the app, does the work, and tells you what it
found — while you keep using your phone. If it has to borrow your screen for an
app that refuses to run anywhere else, it says so, and gives the screen back when
the step is done.

It is not another chat window over a vision model. Most of what an agent does on a
phone is **navigation**: opening, tapping, scrolling, going back. PhoneGo compiles
that navigation offline into a map, replays it deterministically, and only asks a
model about the parts a map cannot cover.

## What makes it different

| | |
|---|---|
| **It doesn't take the phone** | The app is driven on a screen of its own, invisible to you. Some apps only run on the main screen; PhoneGo follows them there, finishes, and puts them back. |
| **It doesn't guess every step** | Navigation comes from a map compiled offline: one deterministic replay, **zero model calls**, sub-second per step. |
| **It knows when to look** | When a map does not apply, PhoneGo falls back to a vision model and works that screen like a person would — then hands you a plain answer, not a log. |
| **It crosses apps** | What it reads in one app it uses in the next: *"see which hotel 小红书 recommends, then check tonight's price on 携程"*. |
| **It gives the phone back** | When the task ends the app returns to its own screen, the chat comes forward, and it asks whether you want that page moved over to continue. |
| **It stays on your device** | Pairing keys, maps, sessions and settings live in the app. Only the screenshots a model actually needs are sent. |

## What a task looks like

```
you   小红书看看港大附近推荐哪家酒店，挑一家，再去携程查这家今晚多少钱

app   · 小红书: search → open a note → 「合和酒店」
      · 携程: search 合和酒店 → 11月17日–18日
      · 今晚「您选的日期暂无可预订房型」，最近可订：
        11/18–19 ¥3379 起 · 11/19–20 ¥3790 起 · 11/20 ¥2303 起
      · 需要我把这个酒店页面转到你的手机上继续操作吗？
```

49 steps, 200 seconds, one instruction, two apps.

## How it works

```
offline    a crawler walks an app and compiles it into a map   (once per app)
online     route the task → replay the map's macro            (0 model calls)
fallback   unknown screen → vision model drives that part     (only there)
```

The routing step is one small text call. The replay is deterministic — the same
taps, in the same order, every time. The vision model is the exception handler,
not the engine, which is why a task can finish in a minute instead of five.

## Measured on a real phone

| Task | Map + replay | Vision only |
|---|---|---|
| Find how to book a HKU visit (小红书) | **17 steps / 64 s / 92k tokens** | 32 steps / 146 s / 207k tokens |

Same phone, same instruction, both successful. The recordings live in
[docs/showcase/](docs/showcase/).

## Install

1. Download `PhoneGo-0.3.0-arm64.apk` (**Android 14+, arm64**) and open it. Allow
   installation from this source; MIUI asks once (「继续安装」).
2. Turn on **wireless debugging**, and enable PhoneGo's **accessibility service** —
   the app pairs itself with the phone's own `adbd`, no computer needed.
3. Open 设置, paste your own **model API key** (DeepSeek, or any OpenAI- or
   Anthropic-compatible endpoint). Nothing is bundled.
4. Type an instruction.

Updates install in place: every release is signed with the same key.

## Questions people ask

**Does it upload my screen?**
Only the screenshots a model call needs, to the endpoint you configured. Maps,
sessions, pairing keys and settings never leave the app.

**Do I need a computer?**
No. The app uses the phone's own wireless-debugging connection; the same code
also runs from a laptop over USB for development.

**Which apps work?**
Any app you can drive by tapping. Apps with a map replay in seconds; the rest go
through the vision fallback, which is slower and costs model calls. Maps are built
per app, on your phone, and can be rebuilt any time.

**Will it buy things for me?**
No. Anything that spends money (支付 / 下单 / 提交订单) stops and asks first, and
password or verification-code screens are handed to you.

## Documentation

| | |
|---|---|
| [docs/product-app.md](docs/product-app.md) | what the app is, layer by layer |
| [docs/map-building.md](docs/map-building.md) | how a map is compiled, with the measurement behind each decision |
| [android-app/README.md](android-app/README.md) | build it: Gradle, Chaquopy, the virtual-display helper |
| [docs/showcase/](docs/showcase/) | recordings, including the side-by-side comparison |

## The research behind it

PhoneGo is the app form of **PhoneCLI**: navigation compiled offline into callable
commands, with an open 3B model as the fallback engine.

- PhoneCLI — [arXiv:2609.35671](https://arxiv.org/abs/2609.35671)
- OpenPhone model and dataset — [arXiv:2510.22009](https://arxiv.org/abs/2510.22009),
  [model](https://huggingface.co/hkuds/OpenPhone_model),
  [dataset](https://huggingface.co/hkuds/OpenPhone_dataset)
- The CLI side of this repository (`phonecli/`, the evaluation suites, the training
  recipe) is unchanged; the original README is kept at
  [docs/archive/README-phonecli-en.md](docs/archive/README-phonecli-en.md).

MIT licensed.
