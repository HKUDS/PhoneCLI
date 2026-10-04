# phonecli_android

**English** · [中文文档](./README_CN.md)

Android GUI-agent harness for the **AndroidLab** benchmark (9 apps / 138 tasks).
It runs two agents over the same task suite:

- **PhoneCLI** — an app's navigation is compiled offline into an **app map**; each
  task is then routed to a deterministic ADB **macro replay** (sub-second, zero VLM
  cost), with a VLM fallback for anything the map cannot do.
- **Baseline** — the same VLM loop with no app map and no replay, i.e. "PhoneCLI
  removed".

Device control is ADB against a cloned AVD; app maps are YAML navigation graphs
produced by a BFS crawler.

```
┌──────────────────────┐  configs/test_macro.yaml       ┌───────────────────────────────┐
│  eval.py             │ ─────────────────────────────→ │  evaluation/auto_test.py      │
│  task runner         │  configs/test_screen_cloud.yaml│  clone AVD → boot → N tasks   │
└──────────────────────┘                                └───────────────┬───────────────┘
                                                                        │ get_agent()
              ┌─────────────────────────────────────────────────────────┴─────────────────────┐
              ▼                                                                               ▼
 ┌──────────────────────────────────────┐                        ┌────────────────────────────────┐
 │ MacroAgentTask_AutoTest  (PhoneCLI)  │                        │ ScreenshotCloudTask_AutoTest   │
 │  Round 1  LLM task → app-map op      │                        │  (baseline)                    │
 │           ADB macro replay           │                        │  one VLM call per round        │
 │           landing verification       │                        │  no app map, no replay         │
 │  Round 2+ VLM loop (fallback)        │                        └────────────────────────────────┘
 └───────────────┬──────────────────────┘
                 │ reads
 ┌───────────────▼──────────────────────┐        ┌───────────────────────────────────────────┐
 │ app_maps/<app>_android.yaml          │ ◀───── │ build_android_map.py                      │
 │ screens · elements · screen_macros   │  build │ clone AVD → BFS crawl → LLM enrich        │
 └──────────────────────────────────────┘        └───────────────────────────────────────────┘
```

> **Working directory**: all commands in this README are run from
> `phonecli_android/` itself. Imports are absolute and rooted at the process
> working directory, and every configured path (`./app_maps/...`, `./logs/...`,
> `./evaluation/config`) is relative — so `cd phonecli_android` first.
>
> In particular, do **not** run it as `python -m phonecli_android.eval` from the
> repository root: `import evaluation` would then resolve to the repository's own
> `evaluation/` package and the run dies with
> `AttributeError: Class MacroAgentTask_AutoTest not found`.

---

## 1. Setup

### Prerequisites

```bash
conda create -n Android-Lab python=3.11     # or reuse an existing env
conda activate Android-Lab
pip install -r requirements.txt             # first time only
```

You also need the Android SDK plus an emulator/AVD:

- Android 13 (**API 33**) AVD named `Pixel_7_Pro_API_33` under `~/.android/avd/`.
  `eval.py` clones it per task and restores a clean snapshot.
- The shipped app maps were built at that device's resolution, **1440 × 3120**.
- Step-by-step SDK/AVD setup: `../docs/prepare_for_mac.md`, `../docs/prepare_for_linux.md`.

### Configure the model

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

All agent configs are **secret-free**: the key is written as
`"${OPENROUTER_API_KEY}"` and expanded by `config_env.load_config()` when the
config is read. An unset variable simply expands to an empty string.

### Quick test

```bash
python eval.py --help          # entry point is importable and wired up
adb devices                    # emulator/device visible?
```

---

## 2. Quick Start

```bash
conda activate Android-Lab
export OPENROUTER_API_KEY="sk-or-v1-..."

# 1) PhoneCLI on one Settings task
python eval.py -c configs/test_macro.yaml -n macro_smoke \
  --task_config evaluation/config/setting.yaml --task_id setting_0

# 2) the same task without PhoneCLI
python eval.py -c configs/test_screen_cloud.yaml -n cloud_smoke \
  --task_config evaluation/config/setting.yaml --task_id setting_0

# 3) score the recorded traces
python run_eval_judge.py --traces logs/evaluation/macro_smoke \
  --config evaluation/config/setting.yaml --test-config configs/test_macro.yaml
```

`configs/test_macro.yaml` points `task.args.app_map` at
`./app_maps/settings_android.yaml`; copy the config and repoint that field to run
another app (see [Section 3](#3-app-maps)).

What's next: [Section 4](#4-run-the-benchmark) to run whole suites,
[Section 6](#6-judging) for scoring, [Section 9](#9-troubleshooting) when stuck.

---

## 3. App maps

`evaluation/app_map.py` loads a map; `build_android_map.py` builds one by cloning
the AVD, booting it headless, crawling the app over ADB, and asking an LLM to
classify/enrich each screen's elements.

### Build one

```bash
# Auto lifecycle: clone AVD → headless boot → BFS crawl → clean up
python build_android_map.py -p com.android.settings -a Settings \
  -o ./app_maps/settings_android.yaml

# Against an already-running device
python build_android_map.py -p com.android.settings -a Settings \
  -o ./app_maps/settings_android.yaml --device emulator-5554 \
  --max-screens 50 --max-depth 3 --scroll-pages 3
```

### Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `-p, --package` | *(required)* | Android package name |
| `-a, --app` | *(required)* | Human-readable app name |
| `-o, --output` | `./app_maps/app_map.yaml` | Output path |
| `-c, --config` | none | YAML config (same format as the test configs) for AVD/SDK paths |
| `-d, --device` | none | Use an existing device, skip the emulator lifecycle |
| `--avd-name` | `Pixel_7_Pro_API_33` | Source AVD to clone |
| `--avd-base` | `~/.android/avd` | AVD home directory |
| `--show-avd` | off | Show the emulator window (default: headless) |
| `--max-screens` | `50` | Cap on discovered screens |
| `--max-depth` | `3` | Maximum navigation depth |
| `--scroll-pages` | `3` | Scroll pages collected per screen |
| `--no-classify` | off | Skip the LLM stable/dynamic element classification |
| `--no-enrich` | off | Skip LLM enrichment (aliases, semantic types, descriptions) |
| `--llm-api-key/base/model` | from env/config | Override the map-building LLM |

### App map structure

```yaml
app: Clock
package: com.google.android.deskclock
screen_w: 1440
screen_h: 3120
launch_behavior: resume_last
screens:
  - id: screen_0
    description: "Main Alarm screen with the alarm list and bottom navigation."
    elements:
      - text: Alarm
        center: [0.1295, 0.0821]      # normalized coordinates
        fixed: false
        found_at_scroll: 0
        leads_to: screen_0            # self-loop = no navigation
        aliases: [闹钟, alarms, alarm tab]
        semantic_type: tab
screen_macros:                        # full replay path from screen_0, absolute pixels
  screen_1:
    - {action: force_stop, package: com.google.android.deskclock, wait: 0.5}
    - {action: launch,     package: com.google.android.deskclock, wait: 3.0}
    - {action: tap, x: 1370, y: 256, wait: 1.5}
common_tasks:
  - Create and configure a new alarm
  - Use the stopwatch to track elapsed time
  # … 7 more entries
known_limitations:
  - Bedtime feature requires completing an onboarding flow before it can be used
  # … 3 more entries
```

### Built-in maps

Nine maps ship in `app_maps/`, one per benchmark app — every app in
`evaluation/config/` has one, so the macro agent never runs map-less:

| App | Package | Tasks | Screens | Elements | Ops |
|-----|---------|------:|--------:|---------:|----:|
| Clock | `com.google.android.deskclock` | 27 | 31 | 318 | 107 |
| Settings | `com.android.settings` | 23 | 50 | 383 | 288 |
| Bluecoins | `com.rammigsoftware.bluecoins` | 15 | 50 | 415 | 411 |
| Contacts | `com.google.android.contacts` | 15 | 48 | 399 | 229 |
| Maps.me | `com.mapswithme.maps.pro` | 15 | **1** | 2 | 2 |
| Calendar | `com.skuld.calendario` | 14 | 9 | 42 | 36 |
| Cantook | `com.aldiko.android` | 12 | 31 | 234 | 207 |
| PiMusic | `com.Project100Pi.themusicplayer` | 12 | 50 | 540 | 317 |
| Zoom | `us.zoom.videomeetings` | 5 | 50 | 489 | 420 |
| **Total** | | **138** | **320** | **2 822** | **2 017** |

> `map_android.yaml` (Maps.me) is a **stub**: one screen, two operations. The macro
> agent therefore degrades to the pure-VLM path for Maps.me — treat the usable map
> coverage as 8 apps, and rebuild that map if you need it.

---

## 4. Run the benchmark

The variants share one task suite; only `task.class` (plus `app_map`) differs:

| Variant | Config | Task class | Agent | App map | Replay |
|---------|--------|-----------|-------|:--:|:--:|
| **PhoneCLI** (macro) | `configs/test_macro.yaml` | `MacroAgentTask_AutoTest` | `evaluation/macro_agent.py::MacroAgentTask` | ✅ | ✅ |
| **Baseline** | `configs/test_screen_cloud.yaml` | `ScreenshotCloudTask_AutoTest` | `evaluation/evaluation.py::ScreenshotCloudTask` | ✗ | ✗ |
| Baseline + map as reference | copy the baseline config, set `task.class: ScreenshotCloudMapTask_AutoTest` and `app_map:` | `ScreenshotCloudMapTask_AutoTest` | `ScreenshotCloudMapTask` | reference only | ✗ |

```bash
# Single task (debug)
python eval.py -c configs/test_macro.yaml -n debug \
  --task_config evaluation/config/setting.yaml --task_id setting_0

# One app's whole suite
python eval.py -c configs/test_macro.yaml -n macro_setting_v1 \
  --task_config evaluation/config/setting.yaml

# Several apps at once
python eval.py -c configs/test_macro.yaml -n macro_v1 \
  --task_config evaluation/config/setting.yaml evaluation/config/clock.yaml

# Everything (omit --task_config → all 9 configs), 4 parallel workers
python eval.py -c configs/test_macro.yaml -n macro_v1 -p 4

# Filter by app name as well
python eval.py -c configs/test_macro.yaml -n macro_v1 --app Settings
```

Notes:

- `--task_config` accepts files **or directories** (`find_all_task_files()`
  expands them). If nothing matches, the run aborts in about a second *before*
  cloning an AVD.
- Re-running with the same `-n <name>` skips tasks that already have traces.
- `-p N` spreads tasks over N worker processes (`evaluation/parallel.py`).
- Set `share_instance: true` under `task.args` to keep the emulator alive across
  tasks of the same app, so dependent tasks accumulate state.
- To point PhoneCLI at another app, copy `configs/test_macro.yaml` and repoint
  `task.args.app_map` at `./app_maps/<app>_android.yaml`.

---

## 5. Agent features

`MacroAgentTask` (`evaluation/macro_agent.py`) implements the hybrid loop:

| Feature | Description |
|---------|-------------|
| **LLM task → operation** | Phase 1 maps the instruction onto one operation of the app map's catalog (`OP:` / `MACRO_VLM:` / `NEED_VLM:` / `FINISH:`); Phase 2 verifies the candidate target page, and falls back to Phase 1's choice if it errors |
| **Deterministic replay** | `force_stop` → `launch` → recorded `tap`/`swipe` steps over ADB — sub-second, zero VLM tokens |
| **Landing verification** | after replay the current screen is identified from the XML dump; a mismatch restarts the app and hands the round to the VLM |
| **VLM fallback** | no map, no matching operation, failed replay or failed verification all fall through to the same cloud-VLM loop the baseline uses — so PhoneCLI cannot score below the baseline |
| **Screen hints for Round 2+** | the target screen's description/elements are injected into the VLM history (`landing_hint_level`) |
| **Stall recovery** | after repeated fruitless rounds it injects search/ad-closing hints, and appends self-correction hints when an action errors |
| **Token accounting** | per-label usage (`macro_map_task`, `agent_vlm`, `judge_*`, …) is accumulated and written to `token_usage.json` |

Optional knobs live under `task.args.llm_config`:

| Key | Default | Effect |
|-----|---------|--------|
| `force_macro_vlm` | `false` | Downgrade every `OP:` to `MACRO_VLM:` (always involve the VLM) |
| `skip_landing_check` | `false` | Ablation: disable the landing-mismatch guard |
| `landing_hint_level` | `2` | `1` = description only, `2` = description + elements (+ optional cold read) |
| `cold_read_warmup` | `false` | Take an extra screenshot before handing over to the VLM |
| `api_key` / `api_base` / `model` | the agent's own | LLM used for routing and verification |

---

## 6. Judging

Traces land in `logs/evaluation/<name>/<task_id>_<timestamp>/` and are
intentionally **not** versioned. Judging is per app: programmatic XML/operation
checks plus an LLM check for query-style tasks (`evaluation/tasks/<app>/…`,
registered through each app's `function_map`).

```bash
python run_eval_judge.py \
  --traces logs/evaluation/macro_setting_v1 \
  --config evaluation/config/setting.yaml \
  --test-config configs/test_macro.yaml

# per-task detail
python run_eval_judge.py --traces logs/evaluation/macro_setting_v1 \
  --config evaluation/config/setting.yaml --test-config configs/test_macro.yaml --detail
```

- `--test-config` is optional: when omitted, `run_eval_judge.py` scans
  `configs/test_*.yaml` for the agent's `api_key`/`api_base`.
- Judge credentials otherwise come from `--api-key/--api-base/--model` or the
  `API_KEY` / `API_BASE` / `MODEL_NAME` environment variables (default base
  `https://openrouter.ai/api/v1`, default model `qwen/qwen3.7-plus`).
- Token totals for a run are written to `logs/evaluation/<name>/token_usage.json`,
  with a per-task copy inside each task directory.

---

## 7. Command reference

### `eval.py` — run tasks

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config` | `config-mllm-0409.yaml` | **Agent** config (agent / task / eval blocks) |
| `-n, --name` | `test` | Run name → `logs/evaluation/<name>/` |
| `--task_config` | every `evaluation/config/*.yaml` | Task config file(s) or directory(ies) |
| `--task_id` | all tasks | Specific task id(s), e.g. `setting_0` |
| `--app` | all apps | Filter by app name |
| `-p, --parallel` | `1` | Number of worker processes |
| `--debug` | off | Debug mode |

### `run_eval_judge.py` — re-score traces

| Flag | Description |
|------|-------------|
| `--traces` | Trace root directory (e.g. `logs/evaluation/macro_v1`) |
| `--config` | Task config YAML that produced the traces |
| `--test-config` | Agent config YAML; optional (auto-discovers `configs/test_*.yaml`) |
| `--api-key` / `--api-base` / `--model` | Judge LLM overrides |
| `--detail` | Print per-task detail |

### `build_android_map.py` — build an app map

See the option table in [Section 3](#options).

---

## 8. Environment variables

| Variable | Used by | Description |
|----------|---------|-------------|
| `OPENROUTER_API_KEY` | agent configs | Expanded into `api_key` at config load; unset → empty string |
| `API_KEY` | LLM judge | Fallback judge credential (`EMPTY` if unset) |
| `API_BASE` | LLM judge | Fallback base URL (default `https://openrouter.ai/api/v1`) |
| `MODEL_NAME` | LLM judge | Fallback judge model (default `qwen/qwen3.7-plus`) |
| `DINO_EXECUTOR_URL` | `page_executor/utils.py` | Grounding service used when an agent sets `relative_bbox: true` (default `http://localhost:24020/v1/executor`) |
| `PHONECLI_ENV` | `phonecli/__init__.py` | Alternative `.env` path |

A `.env` file in this directory (or `$PHONECLI_ENV`) is loaded automatically when
any `phonecli.*` module is imported; variables already present in the environment
always win.

---

## 9. Troubleshooting

### `AttributeError: Class MacroAgentTask_AutoTest not found`

`task.class` is resolved from `eval.py`'s globals, which are filled by
`from evaluation.auto_test import *`. This error means `evaluation` resolved to a
*different* package — almost always because the script was started from the wrong
directory or as a module:

```bash
cd phonecli_android && python eval.py -c configs/test_macro.yaml ...   # ✅
python -m phonecli_android.eval ...                                    # ❌ from repo root
```

### `openai.OpenAIError: The api_key client option must be set …`

`OPENROUTER_API_KEY` is not exported, so `${OPENROUTER_API_KEY}` expanded to an
empty string:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

### `No task config files found. Pass --task_config …`

The path given to `--task_config` does not exist or holds no `*.yaml`. This is
deliberate: the run stops in ~1 s instead of cloning an AVD for zero tasks.

### Emulator / AVD problems

- `Pixel_7_Pro_API_33` missing → create an API 33 AVD with that exact name (or
  override `eval.avd_name` / `--avd-name`).
- `PermissionError: … ~/.android/avd/<avd>_0.avd` → the AVD home is not writable;
  point `eval.avd_base` / `--avd-base` somewhere writable.
- Leftover clones: a run normally deletes its own clone (`<avd>_0.avd` plus the
  matching `.ini`) in `Instance.__del__`, so only a killed run leaves them behind
  — remove `~/.android/avd/Pixel_7_Pro_API_33_0.avd` and
  `~/.android/avd/Pixel_7_Pro_API_33_0.ini` by hand.
- Booting is slow by design: the harness cold-boots once, saves a `clean`
  snapshot and restores it per task.

### Macro replays onto the wrong screen

The map is stale (app UI changed) or was built at another resolution — tap
coordinates are absolute pixels derived from `center × screen_w/screen_h`. Rebuild
it against the AVD you evaluate on:

```bash
python build_android_map.py -p <package> -a <App> -o ./app_maps/<app>_android.yaml
```

### Date- and location-dependent tasks

Before each task the controller forces the device clock to **2024-05-10
12:00:00** (except for Maps.me tasks) and the emulator location to San Francisco
(`adb emu geo fix -122.156 37.438`). Tasks whose instruction names another date —
e.g. the Bluecoins questions, currently worded for 2025 — depend on the app's
seeded data matching that wording; check the fixture data if such tasks fail oddly.

### Missing traces / "Task … already run, skipping"

Re-running with the same `-n <name>` skips tasks that already have traces. Use a
new name, or delete `logs/evaluation/<name>/`.

---

## 10. Architecture

```
phonecli_android/
├── eval.py                  Task runner (agent config + task config → logs/evaluation/<name>)
├── run_eval_judge.py        Judge: re-scores recorded traces (programmatic XML / LLM)
├── build_android_map.py     App-map builder CLI (clone AVD → BFS crawl → YAML)
├── config_env.py            YAML loading with ${ENV_VAR} expansion
├── generate_result.py       Result aggregation helpers
├── adb_client.py            ADB helper injected into Docker containers
├── requirements.txt         Python dependencies (identical to the repository root's)
├── agent/                   LLM/MLLM client wrappers (OpenAI-compatible, Qwen, GLM, Claude)
├── evaluation/              Android-Lab framework
│   ├── macro_agent.py       PhoneCLI macro agent          ← the PhoneCLI implementation
│   ├── evaluation.py        Task base classes incl. ScreenshotCloudTask (baseline)
│   ├── app_map.py           App-map runtime: load / query / ops catalog / nav reference
│   ├── build_map.py         BFS crawler + LLM element classification & enrichment
│   ├── auto_test.py         Per-task orchestration, AVD lifecycle, token accounting
│   ├── configs.py           AppConfig / TaskConfig
│   ├── task.py, definition.py, utils.py, docker_utils.py, parallel.py
│   ├── config/<app>.yaml    Task definitions: 138 tasks / 9 AndroidLab apps
│   └── tasks/<app>/         Per-app judge implementations (`function_map`)
├── phonecli/                Only the PhoneCLI layer the macro agent consumes
│   ├── prompts.py           MACRO_PLAN / MACRO_VERIFY / VLM_VERIFY + map-builder prompts
│   ├── llm_client.py        OpenRouter-compatible text/vision completion
│   └── token_usage.py       Per-label token accounting
├── app_maps/                9 pre-built maps, one per benchmark app
├── configs/                 The two agent configs (macro / baseline)
├── templates/               Cloud VLM system prompts (SYSTEM_PROMPT_ANDROID_MLLM_CLOUD_V0)
├── utils_mobile/            AndroidController (ADB), XML tree tooling
├── page_executor/           VLM action executors
└── recorder/, tools/        Trace recording and maintenance scripts
```

**Scope note.** The benchmark is exactly the upstream THUDM Android-Lab set
(9 apps / 138 tasks). The parent project's three extension apps — Gmail, TikTok
and Reddit — were removed here because they were never used, so neither their task
configs nor their judges exist any more.
