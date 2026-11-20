English · [中文](README.zh.md)

# Documentation index

Two things live here: **what this app is**, and **how the map pipeline works**.
The development log, the milestone write-ups and the comparison notes are not
published with the repository (see the last section), so old links to them may
no longer resolve.

## Product and maps

| File | Contents |
|---|---|
| [errors.md](errors.md) | Error catalogue: look up the sentence on screen, get the cause and the fix |
| [manual.md](manual.md) | The manual: every screen, every setting, what it will not do, and the troubleshooting table |
| [../CHANGELOG.md](../CHANGELOG.md) | Version history: what changed in each release, and which APK carries it |
| [getting-started.md](getting-started.md) | From the APK to a finished task: the first-run checks, the model settings, and the five things that usually go wrong |
| [product-app.md](product-app.md) | What PhoneGo is: one APK, a chat interface, execution on a virtual display; how the package name, the project name and the three code trees relate |
| [map-building.md](map-building.md) | The crawl pipeline: the real failure behind every change, the measured numbers, and the negative results |

## Showcase

| File | Contents |
|---|---|
| [showcase/app-xhs-hku-compare-3x.mp4](showcase/app-xhs-hku-compare-3x.mp4) | One instruction, one phone, both modes side by side at 3× (55 s, with a live step counter) |
| [showcase/app-xhs-ctrip.mp4](showcase/app-xhs-ctrip.mp4) | One instruction across two apps: a note in 小红书, the hotel it names, tonight's price in 携程 (3:40) |
| [showcase/app-xhs-ctrip-4x.mp4](showcase/app-xhs-ctrip-4x.mp4) | The same task at 4× with music (0:43) |
| [showcase/app-xhs-ctrip-en.mp4](showcase/app-xhs-ctrip-en.mp4) | The same kind of task in the English interface (2:54) |
| [showcase/app-xhs-ctrip-en-4x.mp4](showcase/app-xhs-ctrip-en-4x.mp4) | The English take at 4× with music (0:43) |
| [showcase/app-xhs-ctrip.gif](showcase/app-xhs-ctrip.gif) | Three panels of the same run, embedded in the README |
| [showcase/lightagent-demo.gif](showcase/lightagent-demo.gif) | The harness on the AndroidLab benchmark, embedded in the README |

The left half is the **map mode** (app map + macro replay): 17 steps, 64 s,
92.3k tokens. The right half is **vision only** (every step decided from a
screenshot): 32 steps, 146 s, 207.1k tokens. Both finished the task.

## Reference source

| Path | Contents |
|---|---|
| [reference/aosp-pairing/](reference/aosp-pairing/) | AOSP's wireless-debugging pairing implementation (SPAKE2 / TLS / AES-GCM), kept as the reference `phoneagent/devices/pairing` was ported against |

## Upstream pages (from the original PhoneCLI repository)

| File | Contents |
|---|---|
| [modify_androidlab.md](modify_androidlab.md) | Changes made to the AndroidLab evaluation environment |
| [new_apps.md](new_apps.md) | How upstream adds a new app |
| [prepare_for_linux.md](prepare_for_linux.md) / [prepare_for_mac.md](prepare_for_mac.md) | Upstream environment setup |

## What each command-line tool does (the names are easy to mix up)

| Command | Purpose | When to run it |
|---|---|---|
| `python -m phoneagent.probe` | Device connectivity probe (can replay one macro) | First thing after changing device or ROM |
| `python -m phoneagent.build_map` | Crawl one app into a map | When you need a new map |
| `python -m phoneagent.bench` | Replay sampled operations and measure landing rate | After changing a map |
| `python -m phoneagent.check` | Walk a map's screens and operations offline, no device | Smoke test before committing |
| `python -m phoneagent.validate` | Check structure, macros and privacy offline | Before publishing a map |
| `python -m phoneagent.redact` | Produce a shareable copy with personal data removed | When a map leaves the machine |
| `python -m phoneagent.core.profile` | Mark an app's dynamic text per profile so it stays out of fingerprints | When crawling your own phone |
| `python -m phoneagent.run` | One task end to end (route → replay → verify) | When working on the algorithm |
| `python -m phoneagent.demo` | Fully offline demonstration | When there is no device |

`check` and `validate` do not overlap: the first walks the graph and answers
"can every operation be reached", the second inspects structure, macro
definitions and privacy fields. `redact` and `profile` differ the same way: one
produces a copy for sharing, the other decides which text enters the
fingerprint during a crawl.

## Notes in the code

Two comments are more reliable than documentation, because they sit next to the
line they describe:

- Top of `phoneagent/build_map.py`: how this crawl relates to the upstream BFS,
  and where each verification step came from
- `android-app/app/src/main/java/com/phoneagent/app/AgentService.kt`: the
  trade-offs around the service, the virtual display and the wake lock

## What is not in this index

The development log (one entry per problem and decision), the milestone
write-ups (pairing, first device, agent inside the APK), the measurement
reports and the design reviews were written while deciding things. Their
numbers and conclusions now live in the two documents above and in the code
comments; the date-by-date detail does not help anyone reading the code, so it
is not published with the repository. It can still be found in the commit
history.
