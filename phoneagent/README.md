English · [中文](README.zh.md)

# phoneagent

The agent core: the same algorithm runs from a computer over `adb`, and inside
the phone itself (the app in [`../android-app`](../android-app)).

It is split into three parts that upstream keeps apart: **app maps**,
**macro replay**, and a **task agent** that routes to a replay and falls back to
vision when the map does not apply. A device backend is the only thing that
differs between hosts.

Upstream's `phonecli/` (iOS) and `phonecli_android/` (AndroidLab evaluation) stay
in the repository as reference implementations. This package loads the upstream
crawler and reuses its BFS, so the two stay comparable.

## Layout

| Path | What it is |
|---|---|
| `core/agent.py` | The task agent: route → replay → verify → vision fallback, in-process |
| `core/app_map.py` | App map: load YAML, identify the current screen, build the operation catalog |
| `core/macro.py` | Replays abstract macro steps against any `PhoneDevice` |
| `core/llm.py` | Minimal OpenAI-compatible client used by the agent |
| `core/prompts.py` | Prompt texts the agent uses |
| `core/memory.py` | What the agent remembers between runs |
| `core/profile.py` | Per-app profile: which on-screen text is dynamic noise, which is structure |
| `core/ui.py` | Small helpers for reading a UIAutomator hierarchy |
| `devices/base.py` | The `PhoneDevice` protocol every transport implements |
| `devices/adb.py` | Android backend over the host-side `adb` binary |
| `devices/virtual.py` | On-device backend: drives apps on a display the phone does not show |
| `devices/wda.py` | iOS backend, a thin adapter over `phonecli/device.py` |
| `devices/mock.py` | In-memory backend for tests and offline dry runs |
| `devices/pairing/` | Wireless-debugging pairing: SPAKE2, TLS, Ed25519, ported from AOSP |
| `server.py` | Task daemon for the chat UI (HTTP on `127.0.0.1:8765`) |
| `ondevice.py` | Entry points the app calls through Chaquopy: pairing, display, serving |
| `web.py` | Reading and driving a page the app itself hosts |
| `tests/` | 383 offline unit tests, about 20 seconds, no phone needed |

## Command-line tools

| Command | Purpose |
|---|---|
| `python -m phoneagent.probe` | Check that a device works: adb → screenshot → dump → identify → inject |
| `python -m phoneagent.build_map` | Crawl one app into a map (`--expect-home`, `--no-classify`, `--no-enrich`) |
| `python -m phoneagent.validate --maps DIR` | Static checks: structure, graph, macros, privacy |
| `python -m phoneagent.check --maps DIR` | Walk every map's screens and operations offline |
| `python -m phoneagent.redact --map M --out P` | Produce a shareable copy with personal data removed |
| `python -m phoneagent.bench --map M` | Replay sampled operations and measure landing accuracy |
| `python -m phoneagent.run "task"` | One task end to end, no app involved |
| `python -m phoneagent.demo` | Fully offline demonstration of both paths |

## Quick start

```bash
python3 -m pytest phoneagent/tests -q      # 383 passed
python3 -m phoneagent.check                # walks every map, no phone
python3 -m phoneagent.demo                 # macro hit and vision fallback
```

## Running a task

```python
from phoneagent.core import AgentConfig, AppMap, OpenAICompatClient, PhoneAgent
from phoneagent.devices.adb import AdbDevice

device = AdbDevice()                       # or MockDevice() / OnDeviceDevice()
app = AppMap("phonecli_android/app_maps/settings_android.yaml")
llm = OpenAICompatClient(api_key="...", api_base="https://api.openai.com/v1", model="...")

agent = PhoneAgent(device, [app], llm, config=AgentConfig(max_rounds=20), log=print)
result = agent.run("Turn on Wi-Fi")
print(result.summary())
```

The agent runs in four phases, in this order:

1. **Route** — one text call maps the task to an operation in a map.
2. **Replay** — `MacroExecutor` runs that operation's macro deterministically, zero model calls.
3. **Verify** — `NAV` operations continue with the model from the landed screen; `ACT` operations can be checked with one screenshot.
4. **Fallback** — if routing fails, the model drives step by step from a screenshot and the accessibility tree until it reports `finish`.

## Backends

A new transport implements the protocol in `devices/base.py`: `info`, `is_ready`,
`screenshot`, `dump_ui`, `tap`, `long_press`, `swipe`, `input_text`, `press_back`,
`press_home`, `press_key`, `launch`, `force_stop`, `current_app`, `shell`, plus
the hands-off mode the on-device backend uses.

```python
from phoneagent.core import AppMap, MacroExecutor
from phoneagent.devices.mock import MockDevice

app = AppMap("phonecli_android/app_maps/settings_android.yaml")
op = next(o for o in app.build_operations().values() if o.type == "NAV")
device = MockDevice()
print(MacroExecutor(device, sleep=lambda _: None).run(op.macro).summary())
```

## Notes

- Coordinates in a map are normalised; `DeviceInfo.to_pixels()` converts them,
  so one map works on screens of different sizes.
- Maps are device data: they carry the text that was on screen when they were
  crawled. `phoneagent/maps/` is not in the repository for that reason.
- The package needs `requests`, `PyYAML` and (for pairing) `spake2` and
  `cryptography`. Everything else is the standard library.
- `adb shell input text` is ASCII only; `AdbDevice.input_text()` raises
  `UnicodeTextUnsupported` on anything else, and the on-device backend types
  through the accessibility bridge instead.
