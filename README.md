English · [Chinese](README_CN.md)

<div align="center">

# PhoneGo: Personal AI Agent on Your Phone

**CLI+GUI · Fast · Powerful · Reliable · Cost-Effective Phone-Use Agent**

<p>
  <a href="https://github.com/HKUDS/PhoneGo/releases/download/phonego-v0.4.0/PhoneGo-0.4.0-arm64.apk"><img src="https://img.shields.io/badge/%E2%AC%87%20Download-PhoneGo%200.4.0%20%C2%B7%20Android-00d9ff?style=for-the-badge&logo=android&logoColor=white&labelColor=0b0f19" height="34" alt="Download PhoneGo 0.4.0 for Android"></a>
  <a href="docs/README.md"><img src="https://img.shields.io/badge/Docs-product%20%7C%20build-4ecdc4?style=for-the-badge&labelColor=0b0f19" height="34"></a>
  <a href='https://github.com/HKUDS/OpenPhone'><img src='https://img.shields.io/badge/🔥Project-Page-00d9ff?style=for-the-badge&logo=github&logoColor=white&labelColor=1a1a2e' height="34"></a>
</p>
<p>
  <a href="https://github.com/HKUDS/PhoneGo/stargazers"><img src='https://img.shields.io/github/stars/HKUDS/PhoneGo?color=00d9ff&style=for-the-badge&logo=star&logoColor=white&labelColor=1a1a2e' /></a>
  <a href="./Communication.md"><img src="https://img.shields.io/badge/💬Feishu-Group-07c160?style=for-the-badge&logoColor=white&labelColor=1a1a2e"></a>
  <a href="./Communication.md"><img src="https://img.shields.io/badge/WeChat-Group-07c160?style=for-the-badge&logo=wechat&logoColor=white&labelColor=1a1a2e"></a>
  <a href='https://arxiv.org/abs/2609.35671'><img src='https://img.shields.io/badge/📄arXiv-PhoneCLI_2609.35671-ff6b6b?style=for-the-badge&logo=arxiv&logoColor=white&labelColor=1a1a2e'></a>
  <a href='https://arxiv.org/abs/2510.22009'><img src='https://img.shields.io/badge/📄arXiv-OpenPhone_2510.22009-8b5cf6?style=for-the-badge&logo=arxiv&logoColor=white&labelColor=1a1a2e'></a>
</p>

</div>

Download PhoneGo and run it directly on your Android Phone.

Tell PhoneGo what you want. It opens and operates the right apps, gets the task done, and reports back.

- **Download and run** directly on your phone.
- **Describe the task** in natural language.
- **Let PhoneGo operate** the relevant apps.
- **PhoneGo works in the background**, so your phone remains yours to use.
- **Get the result** when the task is complete.

---

PhoneGo combines **CLI efficiency with GUI flexibility**:

> **Use CLI when the path is known. Use GUI when the screen demands it.**

PhoneGo runs inside your phone. You type a sentence, it opens the app, does the
work, and tells you what it found — while you keep using the phone. If it has to
borrow your screen for an app that refuses to run anywhere else, it says so, and
gives the screen back when the step is done.

Underneath is PhoneCLI's idea. Most of what an agent does on a phone is
**navigation** — opening, tapping, scrolling, going back — and that part is not a
model's job. The screens a task touches are compiled offline into a map, so at run
time the agent **calls a command** instead of asking a model what to do next: a
replayed step costs milliseconds and gives the same result every time. The vision
model is only asked about screens the map has never seen.

<p align="center">
  <img src="docs/showcase/lightagent-demo.gif" width="640" alt="PhoneCLI's harness driving an Android emulator through the AndroidLab benchmark">
</p>

## ✨ What makes it different

| | |
|---|---|
| 🧭 **It calls commands, it doesn't guess** | Navigation is compiled offline into a map: one deterministic replay, **no vision calls**, a replayed tap in well under a second. |
| 👁 **It looks only when the map runs out** | An unfamiliar screen goes to the vision model for that part alone — then you get a plain answer, not a log. |
| 🔗 **It carries what it read across apps** | What it reads in one app it uses in the next: *"see which hotel Xiaohongshu recommends, then check tonight's price on Ctrip"*. |
| 📵 **It doesn't take the phone** | The app is driven on a screen of its own, invisible to you. Some apps only run on the main screen; PhoneGo follows them there, finishes, and puts them back. |
| ↩️ **It gives the phone back** | When the task ends the app returns to its own screen, the chat comes forward, and it asks whether you want that page moved over to continue. |
| 🔒 **It stays on your device** | Pairing keys, maps, sessions and settings live in the app. Only the screens it actually has to look at are sent. |

## 🎬 What a task looks like

```
you   find a hotel near HKU on Xiaohongshu, pick one, then check tonight's price on Ctrip

app   · Xiaohongshu: search → open a note → "Hopewell Hotel"
      · Ctrip: search Hopewell Hotel → 17–18 Nov
      · tonight: "no rooms for the dates you picked". Next available —
        18–19 Nov from CNY 3379 · 19–20 Nov from CNY 3790 · 20 Nov from CNY 2303
      · want me to move this hotel page to your phone to continue?
```

<p align="center">
  <img src="docs/showcase/app-xhs-ctrip.gif" width="720" alt="PhoneGo typing a hotel name it read on Xiaohongshu into Ctrip, then reporting tonight's prices">
</p>

## ⚙️ From app screens to commands

```
offline    a crawler walks an app and compiles it into a map   (once per app)
online     route the task -> replay the map's macro            (one small routing call)
fallback   unknown screen -> vision model drives that part     (only there)
```

A map is compiled once per app: every screen becomes a node, every element that
leads somewhere an edge, and a task turns into an ordered list of steps. Running
that list is the fast path — 64 s instead of 146 s on the same instruction — and
the vision model stays the exception handler, not the engine.

## 📊 Measured on a real phone

| Task | Map + replay | Vision only |
|---|---|---|
| Find how to book a HKU visit (Xiaohongshu) | **17 steps / 64 s / 92.3k tokens** | 32 steps / 146 s / 207.1k tokens |

Same phone, same instruction, both successful. The recordings live in
[docs/showcase/](docs/showcase/).

## 📲 Install

1. Download **[PhoneGo-0.4.0-arm64.apk](https://github.com/HKUDS/PhoneGo/releases/download/phonego-v0.4.0/PhoneGo-0.4.0-arm64.apk)**
   (**Android 14+, arm64**) and open it on the phone. Allow installation from this
   source; MIUI asks once (tap "continue").
2. Turn on **wireless debugging**, then enable PhoneGo's **accessibility service**:
   the app reads the pairing code off the system dialog and pairs itself with the
   phone's own `adbd`, so the service has to be on first. No computer needed.
3. Tap **Settings → Model settings**: pick a preset (OpenRouter, DeepSeek, Zhipu,
   Alibaba Bailian, SiliconFlow, or an endpoint of your own), paste **your own API
   key**, press **Test connection**. Nothing is bundled.
4. Type an instruction. Start with something that needs no login —
   *"open Settings and tell me which Wi-Fi is connected"*.

The interface is English or Chinese — it follows the phone's language, and
Settings → Language overrides it. The walkthrough in
[docs/getting-started.md](docs/getting-started.md) names every label both ways.

Updates install in place: every release is signed with the same key. The full
walkthrough, including what each screen should look like and the five things that
usually go wrong, is in **[docs/getting-started.md](docs/getting-started.md)**.

## ❓ Questions people ask

**Does it upload my screen?**
Only the screens a model call has to look at, to the endpoint you configured. Maps,
sessions, pairing keys and settings never leave the app.

**Do I need a computer?**
No. The app uses the phone's own wireless-debugging connection; the same code
also runs from a laptop over USB for development.

**Which apps work?**
Any app you can drive by tapping. An app with a map is several times faster on the
same task; the rest go through the vision fallback, which is slower and costs model
calls. Maps are built per app, on your phone, and can be rebuilt any time.

**Will it buy things for me?**
No. Anything that spends money (pay, order, submit) stops and asks first, and
password or verification-code screens are handed to you.

## 📚 Documentation

| | |
|---|---|
| [docs/manual.md](docs/manual.md) | **the manual**: every screen, every setting, every failure |
| [docs/getting-started.md](docs/getting-started.md) | from the APK to a finished task, with the first-run checks |
| [docs/product-app.md](docs/product-app.md) | what the app is, layer by layer |
| [docs/map-building.md](docs/map-building.md) | how a map is compiled, with the measurement behind each decision |
| [android-app/README.md](android-app/README.md) | build it: Gradle, Chaquopy, the virtual-display helper |
| [docs/showcase/](docs/showcase/) | recordings, including the side-by-side comparison |

## 🌟 Citation

PhoneGo is those two papers in app form: the map-and-replay harness comes from **PhoneCLI**, and the open model behind the fallback path is **OpenPhone**. If either is useful to your research, please cite both.

```
@article{jiang2026phonecli,
  title={PhoneCLI: From App Interfaces to Callable Commands for Mobile Agents},
  author={Jiang, Yangqin and Xu, Lingrui and Huang, Chao},
  journal={arXiv preprint arXiv:2609.35671},
  year={2026}
}

@inproceedings{jiang2026openphone,
  title={OpenPhone: Mobile Agentic Foundation Models},
  author={Jiang, Yangqin and Huang, Chao},
  booktitle={Findings of the Association for Computational Linguistics: ACL 2026},
  pages={30362--30380},
  year={2026}
}
```

---

## 🔗 Related Projects

PhoneGo builds on PhoneCLI and OpenPhone, which in turn build upon excellent open-source projects. We sincerely thank their authors and contributors:

- [AndroidLab](https://github.com/THUDM/Android-Lab) - The benchmark framework.
- [R1-V](https://github.com/StarsfieldAI/R1-V) - Implementation details for the GRPO training methodology.
- [LLaMA Factory](https://github.com/hiyouga/LLaMA-Factory) - The unified training framework enabling efficient model fine-tuning.

The CLI side of this repository (`phonecli/`, the evaluation suites, the training recipe) is unchanged; its original README is kept at [docs/archive/README-phonecli-en.md](docs/archive/README-phonecli-en.md).

---

## 📜 License

This project is released under the [MIT License](./LICENSE).

<div align="center">

**If this project helps you, please give us a Star🌟**

**🤖 Empower AI Phone with Agents!**

<br>

<p align="center">
  <em> ❤️ Thanks for visiting ✨ PhoneGo!</em><br><br>
  <img src="https://visitor-badge.laobi.icu/badge?page_id=HKUDS.OpenPhone&style=for-the-badge&color=00d4ff" alt="Views">
</p>

</div>
