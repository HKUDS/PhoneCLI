English · [中文](map-building.zh.md)

# Map Building: A Measured Pipeline

This document records the current design of map building, the **measured basis** for every change, and the known unresolved problems.
All numbers come from the same device (Redmi K90 / HyperOS 3 / Android 16 / 1280×2772).

## 1. Lineage

The crawler is **not a rewrite**. `phoneagent/build_map.py` loads the upstream
`phonecli_android/evaluation/build_map.py` through `_load_crawler()`, and runs PhoneCLI's original BFS algorithm:

```
1. Launch the app, dump + scroll the home page
2. BFS: for each element, replay the full path → tap → dump the new screen
3. Record edges (leads_to) and macros (full path from screen_0)
4. LLM element classification (STABLE / DYNAMIC)
5. Output YAML
```

We made exactly one invasive change to the upstream file (`_parse_json_items`, see §3). Everything else
lives in the adaptation layer, or is a separately written **verified path** for BFS.

## 2. Pipeline

```
build_map  →  profile cleanup  →  sync-maps.sh  →  bench replay verification
```

| Stage | Command |
|---|---|
| Crawl | `python3 -m phoneagent.build_map --serial <S> --package <P> --app <name> --llm-api-base … --llm-api-key … --output maps/<name>.yaml` |
| Clean | `python3 -m phoneagent.core.profile --map maps/<name>.yaml --out maps/<name>_clean.yaml` |
| Package | `./android-app/sync-maps.sh` (copies into the APK assets) |
| Verify | `python3 -m phoneagent.bench --map maps/<name>.yaml --limit 10 --serial <S>` |

## 3. Every Change and Why

| Change | Real failure that triggered it | Effect |
|---|---|---|
| **Startup validation** `_ensure_home` always runs | Crawling "Clock" recorded the calendar; crawling "Calculator" recorded PhoneAgent's own chat UI | Abort when the foreground package name does not match |
| **Enrichment wired into the verified path** | `description` was always 0 in four maps, because the default is the verified path and that path never calls `_enrich_map` | Screen descriptions 0 → **100%** |
| **Enrichment parsing** `_parse_json_items` (upstream change) | Element enrichment required "one JSON per line"; when the model returned an array or a fenced block the items were **silently dropped**, so aliases stayed empty | Aliases 0 → 1741 (three maps) |
| **Similarity dedup** `_match_screen` | Under exact signatures Calculator had only 17 distinct descriptions across its 50 screens (66% duplicates); the element count on the same panel jittered between 37/38/39 | Duplicate rate 66% → **8%** |
| **Time budget** `--max-minutes` | Two runs "stuck for 25 minutes, 55 taps, zero output" | Wraps up on time at 40 minutes |
| **Per-step watchdog** `--step-timeout` | The hang happened **inside a round** (dump/collect/replay chain); the checks between rounds cannot see it | The timeout branch is skipped; `stats["timeout"]` is visible |
| **Incremental persistence** `_save_partial` | A 3-hour crawl was killed and everything was lost | Persist once per new screen |
| **Final enrichment** | A killed map had no descriptions | Enrich even after an early stop |
| **Macro screen preconditions** | — | ⚠️ see §5 |

## 4. Measurements

Same phone, same set of actions (`--limit 10`), **zero model calls**:

| Map and mechanism | Screens | replay success | Exact landing |
|---|---|---|---|
| Old map (exact dedup, no enrichment) | 42 | 100% | 40% |
| Similarity dedup (interrupted, no enrichment) | 10 | 100% | 60% |
| **Completed within the time budget + enrichment** | 15 | 100% | **70%** |

Output of three deep-crawl maps (old dedup), for scale:

| Map | Screens | Descriptions | Aliases | Actions | NAV |
|---|---|---|---|---|---|
| Clock | 42 | 42 | 373 | 573 | 38 |
| Calendar | 50 | 50 | 261 | 496 | 108 |
| Calculator | 50 | 50 | 1107 | 482 | 42 |

### 4.1 The Four Maps Measured Today (2026-11-08)

Same phone, same set of actions (`--limit 10`), **zero model calls**:

| Map | Screens | Derived actions | replay success | Exact landing | Same app |
|---|---|---|---|---|---|
| **Settings (re-crawled after the fix)** | 14 | 135 | 100% | **90%** | 100% |
| Settings (old map shipped with the APK) | 20 | 330 | 100% | 10% | 40% |
| Clock (new pipeline) | 15 | 244 | 100% | 70% | 100% |
| Calendar (old dedup) | 50 | 496 | 100% | 30% | 100% |
| Calculator (old dedup) | 50 | 482 | 100% | 20% | 20% |

Settings going from 10% to 90% is not tuning; it is the direct result of fixing the two bugs in §5.2 / §5.3.

## 5. Negative Results (Equally Important)

### 5.1 Macro Screen Preconditions: the Aborting Version Was Wrong

```
Aborting version: exact landing  0/10, 9 actions died at step 2
Warning version:  exact landing  7/10, everything replays normally
```

The cause is not the guard logic but **the signal itself being unreliable**: the screen id recognized during
crawling and the id recognized during replay are often inconsistent. The same screen, two recognitions,
two different answers.

So it was downgraded to `MacroExecutor.screen_warnings` — recorded, but no longer aborting.
The metadata is still kept in every tap step of the map (`"screen": parent_id`), and can be
re-enabled once recognition is stable.

### 5.2 The Launch Intent Gets Taken Over by Another App

This is the most expensive bug, because it looks completely normal in the report: the macro "executes successfully" and every step is ok.

```
$ adb shell am start -a android.settings.SETTINGS
Starting: Intent { act=android.settings.SETTINGS }
Warning: Activity not started, its current task has been brought to the front
mCurrentFocus=com.android.phone/com.android.phone.settings.MobileNetworkSettings
```

HyperOS handed `android.settings.SETTINGS` to `com.android.phone`. And
`MacroExecutor` sent the intent **without checking whether the package had actually come up**, and returned success directly.
So every subsequent tap was "coordinates measured on the settings page", slammed into another app.

Same settings map, same set of actions:

| | Exact landing | Same app |
|---|---|---|
| Before the fix | 0/10 | 0/10 |
| After the fix (old map) | 1/10 | 4/10 |
| After the fix + re-crawl (new map) | **9/10** | 100% |

Fix: `device.launch()` already returned "is the target package really in the foreground", so the macro now looks at that return value;
if it did not land there, fall back to launching by package name (through component resolution).

### 5.3 A Tap Went to Another App, and That Screen Is Not This App's Screen

The same root cause in the crawler, with a more insidious consequence: **dirty screens were stored in the map as new screens**.

```
29 / 49 taps left another app in the foreground
4 of the 24 screens in the map were Calculator (screen_13/14/16/22)
```

Recognition scores by text overlap; Calculator screens have many elements and distinctive text, so during replay they **score higher than the real settings screens**.

Two fixes: branch launches also check the return value and fall back; after a tap, a screen whose foreground belongs to an unrelated app is
**not recorded** (`com.android.phone` / `com.miui.securitycenter` / `com.xiaomi.account`
are kept, because they genuinely host settings sub-pages).

After the re-crawl all 14 screens belong to Settings; no Calculator screen remains.

## 6. Known and Unresolved

| Problem | Symptom | Possible direction |
|---|---|---|
| Unstable screen recognition | Two recognitions of the same screen give different ids | Recognize with the structural fingerprint in `_stable_keys` instead of text matching |
| A single screen still duplicates | In the re-crawled 14 screens the main menu appears twice, and there are three Bluetooth-related screens | The similarity threshold does not work for "same page, different scroll position" |
| Crawling is slow | From 2.5 s per branch, in 30 minutes it only got through 14 screens and 67 taps | Do not repeat force_stop+launch before a branch; use the back key instead |
| Maps go stale | The same settings map: 40% in October (month 10), 10% in November (month 11), 90% after re-crawl | Verify the landing + switch paths on failure, instead of trusting a static map |
| The screen cap is a hard truncation | Both Calendar and Calculator hit the 50-screen ceiling | Beyond the cap, also report whether it was truncated |
| Verified on one device only | Android 13 needs CAPTURE_VIDEO_OUTPUT (unsupported); 14/15 untested | Run `probe` on a stock 14+ phone |

## 7. Stability Conclusions

The crawler now has: startup validation, a time budget, a per-step watchdog, incremental persistence, final enrichment, similarity dedup,
cross-app pollution interception, and launch-result validation. In a continuous measured run, the run with a 30-minute budget **ended on time by itself**
(`stopped_by: time_budget`), `timeout: 0`, enrichment finished, every screen has a description.

The landing rate of the settings map went from 10% to 90%, which shows that the earlier judgment "the remaining 30% of landing failures are a recognition problem"
was wrong — **most of them were replays that were simply not inside the target app**.

**This is the first complete output produced entirely without human intervention whose replays are usable.**

## 8. Crawling Inside the App (verified 2026-11-09)

The crawler can now run inside the app and drive a **virtual display**; the physical screen is unaffected.

```
POST /crawl {"package":"com.android.settings","app":"设置","max_screens":6,"max_minutes":6}
  -> {"id":"14fbb5d3","output":"/data/.../files/crawls/设置poc6.yaml"}
GET  /crawl/<id>   -> {"state":"done","lines":[...]}
```

Measured (same machine):

| | Host-side crawler | In-app crawler |
|---|---|---|
| Drives | display 0 (physical screen) | virtual display |
| Can the phone be used meanwhile | ❌ occupied | ✅ usable the whole time |
| Speed | 14 screens / 1600 s ≈ **114 s/screen** | 6 screens / 94 s ≈ **15.7 s/screen** |
| Dependencies | Mac + USB/adb | none |

Why it is 7× faster: on the host side every dump starts a `uiautomator` subprocess, and every tap goes through
`input tap`; inside the app it goes straight through the accessibility bridge (`ACTION_CLICK`), and dumps read the same tree.

During verification the physical screen stayed on PhoneAgent's own UI the whole time:

```
Display #0  topResumedActivity=com.phoneagent.app/.MainActivity
Display #97 topResumedActivity=com.android.settings/.SubSettings   <- the crawler is here
```

### The Four Changes Made for This

| Change | Reason |
|---|---|
| `build_map.main(device=...)` | Reuse the `VirtualDisplayDevice` the app already holds, instead of opening another adb connection to display 0 |
| `VirtualDisplayDevice.launch(intent, wait)` | Macro replay and the crawler both use this signature; on a virtual display only the component is needed |
| `_guarded` catches `ValueError` | `signal` can only be used on the main thread; in-app crawling runs on a worker thread |
| Add `from __future__ import annotations` to three upstream modules | The in-app Python is 3.8; `list[str]` annotations would raise TypeError when evaluated at def time |

### Not Done Yet

- The endpoint is currently fixed at `--no-classify --no-enrich` (no model calls), so the maps it produces have no descriptions or aliases.
  Wiring in the API key already present in the app would let the full pipeline run.
- There is still no "build map" UI in the app; for now it can only be triggered over HTTP.

## 9. Wiring Enrichment into the App (2026-11-09)

`/crawl` now uses the API key already present in the app and runs the **same** pipeline as the host:
element classification → element enrichment (aliases/type) → screen descriptions → app metadata.

One measured run on a real device (Settings, 6 screens, with enrichment):

```
screens=6  elements=81  described=6/6  aliased=81/81  typed=81/81
elapsed 151 s  ->  25 s/screen (with enrichment)
```

Same machine; the host-side crawler is **114 s/screen** — even counting enrichment, in-app is still about 4.5× faster.

### 9.1 Enrichment Once "Ran but Amounted to Nothing"

After the first hookup: all 6 screens had descriptions, and **only 1 of the 81 elements had an alias**. There was no exception in the log;
`[Enrich] Done.` printed as usual.

The cause: this time the model **formatted the object across multiple lines**

```
{
  "text": "返回",
  "aliases": [
    "back",
    "go back"
  ]
}
```

But the last-resort fallback in `_parse_json_items` was "split by line, one JSON per line". A multi-line object cannot be parsed
on any single line, so it **parsed 0 items and dropped them silently**.

Fix: stop splitting by line and scan with `json.JSONDecoder().raw_decode` — find a `{` from the start,
decode one object, jump to its end and continue. One per line, arrays, fences, `{"elements": [...]}`,
`1. {...}`, multi-line pretty-printing, and explanatory text mixed in are all covered by the same loop.

Re-measured after the fix: **81/81 elements got aliases and types**.

### 9.2 The Same Lesson Applies to the Host

The three published maps were crawled with the old parser, and alias coverage is only about half
(Settings 73/135, Calendar 22/84, Clock 86/264). With the parser fixed, host-side re-crawls will benefit too.

### 9.3 One Diagnosable Log Line

Every screen now prints one line, `[Enrich] screen_1: 986 chars -> 10 items`; when parsing yields 0 it also prints
the first 200 characters of the raw reply. A problem like "enrichment quietly failed" will be visible at a glance next time.

## 10. Re-crawling Settings In-App and A/B (2026-11-09)

With the fixed parser, Settings was re-crawled inside the app (16 screens / 89 taps / 28 minutes):

```
screens=16  elements=167  described=16/16  aliased=167/167  typed=167/167
```

### 10.1 A Map Must Be Tested on the Screen It Will Later Run On

Same bench (`--limit 10`), same machine, two settings maps:

| Map | Screens | Aliases | Measured on the **virtual display** | Measured on the **physical display** |
|---|---|---|---|---|
| Old (host-crawled, published) | 14 | 73/135 | **4/10** | 9/10 |
| New (in-app crawl) | 16 | 167/167 | **8/10** | 2/10 |

Both maps are good on the display each was **crawled on**; swap the display and each drops by more than half. The reason is practical:
coordinates, element positions, and even which screens appear all depend on that display's state.

Conclusion: **a map crawled in the app must be tested in the app**. So `POST /bench` was added — it uses the app's own
`VirtualDisplayDevice` to run `bench.run_one`, the same code as the host bench.

### 10.2 The Published Settings Map Switches to the In-App One

`settings.yaml` now comes from `k90_settings_inapp.yaml` (in-app, 16 screens, 100% aliases) instead of
`k90_settings_fresh.yaml` (host, 14 screens). The reason is the table above:
the agent runs on the virtual display, and 8/10 > 4/10.

### 10.3 An Incidental Finding

The host-side bench now **cannot** run while the app is running: the virtual display takes over the
"current focus" slot in `dumpsys window`, so `AdbDevice.current_app()` returns the app on the virtual display,
`launch ... returned false`, and all 10 actions turn into MISS. Stop the app before testing the host path.

## 11. Clock Re-crawl Succeeds, Calendar Blocked by a System App (2026-11-09)

### 11.1 Clock: 2 Screens, 9/9

Re-crawling Clock inside the app yielded only **2 screens / 27 elements** (the home page has very few tappable labels; the rest were all
scroll positions of the same alarm list), yet replay landed exactly **9/9 times** — while the old 15-screen host map
scores **0/10** on the virtual display.

A small map with a high hit rate is more useful than a large map with a low hit rate: when a macro does not work the agent falls back to the VLM,
but when a macro goes to the wrong place it first has to waste a step before finding out.

### 11.2 Calendar: Fails Startup Validation

All three attempts stopped at startup validation:

```
start attempt 1: the dump is com.android.settings, not com.android.calendar
start attempt 2: the dump is com.xiaomi.account,  not com.android.calendar
start attempt 3: the dump is com.android.phone,    not com.android.calendar
```

Four real defects were fixed along the way (none of them Calendar-specific):

| Defect | Symptom | Fix |
|---|---|---|
| The bridge's dump carries no `package` attribute | Startup validation cannot read the package name, so it always decides "not the target app" | `appendNode` emits `package="..."` |
| A permission dialog is treated as "the app did not come up" | All three retries ABORT | Recognize buttons like Allow/Deny and Agree/Exit, and **tap Deny first** (we do not accept privacy agreements on the user's behalf); if that does not work, force-stop the interfering app |
| The tree lags behind the window | A dump within 3 seconds of a cold start is still the previous screen | Retry the dump up to 4 times; an empty dump does not count as "there is a dialog" |
| MIUI dialogs do not use AOSP package names | `SystemAppPermissionDialogActivity` of `com.miui.securitycenter` | Treat "a grant button and a deny button appearing together" as a dialog |

Still unresolved: Calendar is a system app, its process gets started by the system, its task lingers on display 0,
so `am start -S --display N` merely brings the old task forward and the virtual display stays on the previous screen.
The next step is either to clear all tasks of that package before launching, or to relax startup validation for Calendar alone.

## 12. The In-App "Build Map" UI (2026-11-09)

The UI gained a "Maps" button; it opens a table: pick an app (every launchable one is listed, PhoneAgent itself excluded), the maximum number of screens, and the time budget. After "Start" the progress is written straight into the chat log:

```
Build map
Start: Settings (com.android.settings), at most 2 screens / 20 minutes
The map will be written to /data/user/0/com.phoneagent.app/files/crawls/设置.yaml
  radios : before {'wifi': '1', 'bluetooth': '1', 'airplane': '0'}
  llm    : qwen/qwen3.8-flash (reasoning off, max_tokens=4000)
  [Verified] depth=1 tap '搜索系统设置项' (from screen_0)
  [Verified] depth=1 tap '账号、云服务、会员权益等' (from screen_0)
  [Enrich] screen_0: 2943 chars -> 26 items
  [Enrich] screen_1: 1535 chars -> 15 items
Build map
Done.
map    : /data/user/0/com.phoneagent.app/files/crawls/设置.yaml  (80.4s)
```

Details:

- The dropdown remembers the last crawled app (`SharedPreferences`), so repeated jobs do not have to scroll the list every time
- Progress polls `/crawl/<id>` every 2.5 seconds and prints only lines with new content
- Crawling does not tie up the UI: the virtual display runs while the physical screen stays on the chat page, so you can watch progress while it crawls
- Progress lines filter out HTTP access logs and heartbeats — they are the caller's own requests, not crawl progress

### 11.3 Why Calendar Cannot Get onto the Virtual Display (Conclusion)

It is not that "the app does not support multiple displays", but that **the calendar home Activity is pinned to the default display by the system**:

```
am start -S --display 145 -n com.android.calendar/.homepage.AllInOneActivity
  -> Display #0  topResumedActivity=com.android.calendar/.homepage.AllInOneActivity

am start -S --display 145 -n com.android.calendar/.selectcalendars.CalendarAccountManagerActivity
  -> Display #145 topResumedActivity=com.android.calendar/.selectcalendars.CalendarAccountManagerActivity
```

**Other** Activities of the same app land on the virtual display normally; only the home page does not — classic
`singleTask`/`singleInstance` behavior: the system always puts such an Activity back into "its own" task,
and that task is on the default display. `--activity-multiple-task` does not help either; after a force-stop, launching again
is still pulled back to display 0.

The Settings and Clock home pages do not have this problem, which is why they can be crawled.

**Handling**: the Calendar map is **no longer shipped with the APK**. Its old map (host-crawled, 7 screens) scores 0/10 on the virtual display,
so keeping it would only make the agent waste one macro run before falling back to the model. When there is a way to get the home page onto a virtual display
(for example a Launcher entry, or the `setDisplayId` variant of `VirtualDisplay`), we can come back to it.

A related defect was fixed along the way: `extractMaps()` used to only **add** files into `files/maps`, so a map removed from the
APK stayed on the device forever. It now clears old `*.yaml` files before unzipping.

## 13. Apps That Will Not Start: Fail Fast and Say Why (2026-11-09)

Before this, for an app like Calendar whose "home page cannot go onto the virtual display", the agent would: macro misses → VLM launches repeatedly →
planner gives up, and what the user saw was a long spin followed by `failed: planner`, with no idea what had happened.

Now `VirtualDisplayDevice.launch` **verifies**: after `am start` it polls
`current_app()`, and throws `LaunchRefused` if it did not land on the target package.

```
[launch] com.android.calendar -> com.android.calendar/.homepage.AllInOneActivity on display 153
  cannot start com.android.calendar here: singleTask activities stay on the display of their own task
  this app's home activity is pinned to its own task; aborting
```

The agent side likewise:

```
state: failed
answer: cannot open "com.android.calendar": its home screen is declared singleTask,
        so the system only puts it back on the display it is already on, and it will not start on a virtual display.
        Use a different app, or operate the phone by hand.
steps:  vlm | home | ok
        launch | com.android.calendar | refused
```

It ends after 2 steps instead of 11. The key is that `LaunchRefused` **must not be swallowed by `_execute_action`** —
that code turned every `DeviceError` into `False` (equivalent to "this action failed, keep trying"), which is wrong for "this app simply will not start".

### Basis for the Judgement

| App | Entry Activity | launchMode | Can go on a virtual display |
|---|---|---|---|
| Calendar | `homepage.AllInOneActivity` | **singleTask (2)** | ❌ |
| Clock | `DeskClockTabActivity` | not set (standard) | ✅ |
| Settings | `MiuiSettings` | not set (standard) | ✅ |

Other Activities of the same Calendar app (such as `CalendarAccountManagerActivity`) can go up,
so the problem lands precisely on "the launch mode of the home Activity", not on the app failing to support multiple displays.

## 14. Making Crawling Faster: Known Screens Are Not Re-collected (2026-11-09)

In a single crawl the vast majority of taps land on **screens the map already has**, yet the original flow did the same expensive work for every branch:
three swipes up + three dumps + one LLM classification.

Now a **cheap dump** is taken right after the tap and compared against known screens; on a hit nothing is collected and only the edge is recorded:

```
[Verified]   known screen screen_4 (overlap 0.93, no scroll)
```

A duplicate wait was also removed: `launch()` already polls until the target app is in the foreground, so the following
`sleep(3.0)` was paying twice for the same wait; it is now 0.8 seconds.

### Measurements (Settings, same machine, in-app)

| | Taps | Known-screen hits | Elapsed | s/tap | s/screen | Landing |
|---|---|---|---|---|---|---|
| Before | 89 | — | 1673 s | 18.8 | 105 | 8/10 |
| **Now** | 75 | **25** | 1068 s | **14.2** | **82** | 7/10 |

s/tap **−24%**, s/screen **−22%**, landing unchanged (10 samples; 7 vs 8 is within one sample's
error). Enrichment is still 100% (all 152 elements across 13 screens have aliases).

### What Else Can Still Be Saved

The biggest remaining chunk of s/tap is **re-running force-stop + launch for every branch** (about 4.5 s).
If the previous branch ended exactly on the parent screen, it could simply keep tapping. The risk is that the parent screen may need
scrolling for the target element to be visible, so skip only when "the parent screen needs no scrolling" — not done yet.

### 14.1 Negative Result: Using the Back Key to "Return to the Parent Screen" Saves No Time

The idea is straightforward: force-stop + relaunch at the start of every branch costs ~4.5 s, while at the end of the previous branch you are
most likely one level below the parent screen, so one back key press (one dump, about 1 s) should be enough.

Measured (Settings, 16-screen budget, same conditions as the table above):

| | Taps | Known-screen hits | Back-key successes | Elapsed |
|---|---|---|---|---|
| Only "skip collection for known screens" | 75 | 25 | — | 1068 s |
| **Plus "back key to the parent screen"** | 69 | 19 | **2** | 1098 s |

The back key succeeded only **2 times** (out of 69 taps). The likely reason: for most branches the parent screen is not "one level up",
so one back press does not land there; and every failed attempt costs two more dumps, offsetting the saved restart.

Conclusion: **this optimization was reverted**; the code went back to only "skip collection for known screens". Recorded so that
nobody tries it again later.

## 15. App Coverage: Three New Apps Measured (2026-11-09)

| App | Result | Reason |
|---|---|---|
| Browser | ❌ refused to start | `BrowserActivity` is **singleTask**, same as Calendar |
| Notes | ❌ stuck at startup validation | After it came up, a MIUI permission dialog covered it, and the dialog's buttons are not in our "allow/deny" vocabulary |
| File Manager | ⚠️ crawls, but only 1 screen | The home page has very few navigable labels; 9 taps were skipped |

Coverage did not expand, but three real problems surfaced, all of them fixed:

### 15.1 Launch Validation Too Strict: Covered by a Dialog ≠ Did Not Start

Both File Manager and Notes were judged "cannot start on the virtual display", when in fact they **did start** and were merely covered by a permission dialog;
`current_app()` returned the dialog's package name (`com.miui.securitycenter`).

Two fixes:
- In `launch()`, if the foreground is a known dialog host, the launch counts as successful (the caller already knows how to close dialogs)
- If the first attempt does not land on the target package, **hard-stop and try once more** before concluding — because "the app is already running on the
  phone's own display" makes `am start --display N` bring that old task forward

### 15.2 "Legitimate Hosts" Must Be Per-App

`FOREIGN_HOSTS` used to be one global set (`com.android.phone` / `com.miui.securitycenter`
/ `com.xiaomi.account`), which was set up for the settings pages — the mobile network page really is hosted by the phone app.
But for File Manager those packages mean "the crawler left the app": one File Manager crawl recorded the **Xiaomi account page**
as one of its screens.

It is now "when crawling which app, which hosts may take over":

```python
FOREIGN_HOSTS = {
    "com.android.settings": {"com.android.phone", "com.miui.securitycenter", "com.xiaomi.account"},
}
```

Everything else is treated as pollution. After re-crawling File Manager, the account page no longer appears.

### 15.3 Conclusion

Whether app coverage can expand depends on the app itself:

- **singleTask home page** (Browser, Calendar) — no solution on a virtual display; it can now refuse quickly and explain why
- **Strong permission/privacy dialogs** (Notes) — stuck at startup validation; the dialog vocabulary needs extending
- **Simple page structure** (File Manager) — crawlable, but there is not much to navigate, so the value is low

The next time we expand, we should filter by "launchMode + whether there is a first-launch dialog" first, instead of trying apps one by one.

## 16. Filter First, Then Crawl: `GET /crawl/apps` (2026-11-09)

Trying apps one by one is too expensive (one failure costs several minutes). Now tapping "Maps" in the app asks the server first:

```
GET /crawl/apps
[{"package":"com.android.settings","label":"设置","launch_mode":0,
  "state":"crawlable","reason":"标准启动模式，可以爬"},
 {"package":"com.android.browser","label":"浏览器","launch_mode":0,
  "state":"refused","reason":"上次爬取确认起不来（首页最终落在默认屏）"}]
```

In the UI each app carries a marker: **✅ crawlable / 📌 map already exists / ⛔ cannot go on a virtual display**;
selecting a ⛔ one pops up an explanation immediately, instead of wasting a crawl.

The decision has two layers:

| Layer | Basis | Characteristic |
|---|---|---|
| **Static** | the `launchMode` of the launcher activity in `PackageManager` | Available immediately, but misjudges apps whose "entry Activity is not itself singleTask yet hands the task to a singleTask page" |
| **Dynamic** | remember the package after an actual crawl failure | Always accurate, at the cost of one failure |

The browser's SplashActivity is an example of a static misjudgement (standard, but the real page is elsewhere
and is pinned to the default display). So a failure is written to `files/refused-apps.txt`, and next time the app is marked ⛔ right away.

### Current State (this machine, 10 launchable apps)

```
✅ crawlable   Settings  File Manager  Notes  Camera  App Store  Dianping  Meituan  Mi Mover
⛔ will not start   Browser (dynamically confirmed)  Minor Mode (singleTask)
```

### 16.1 A Static Judgement Can Only Be a "Hint", Not a Conclusion

`launchMode` is static information, and it errs in both directions:

| App | Static judgement | Actual |
|---|---|---|
| Browser | standard | ❌ will not start (the entry hands the task to a pinned page) |
| Clock | **singleTask** | ✅ crawlable, and 9/9 landing |
| Calendar | **singleTask** | ❌ will not start |

The first version marked anything "non-standard" as ⛔ and forbade crawling, and the result was that **Clock was marked uncrawlable** — and the map we ship is exactly Clock.

So there are three tiers now:

```
✅ crawlable      static is standard
⚠️ may not start   static is singleTask — allow one attempt
⛔ tried, will not start  actually failed a crawl — remembered, no longer recommended
```

`refused-apps.txt` also changed from append-only to two-way: if an app later crawls successfully, the record is deleted
(it once accumulated 16 entries, half of which were actually crawlable).
