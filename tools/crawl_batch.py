#!/usr/bin/env python3
"""Crawl a list of apps in one run, one after another, and report what came out.

Sequential on purpose: every crawl takes the phone's virtual display, so two at
once would fight over it.

    python3 tools/crawl_batch.py com.miui.calculator:计算器 com.miui.notes:笔记

Each entry is ``package:name[:screens[:minutes]]``. Requires the app running and
its port forwarded (``adb forward tcp:18765 tcp:8765``).

The report keeps the error text: "failed" alone sends you back to logcat, which
is exactly what happened the first time this ran.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:18765"


def post(path: str, payload: dict) -> dict:
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=15) as response:
        return json.loads(response.read())


def parse(spec: str) -> tuple:
    parts = spec.split(":")
    package, name = parts[0], parts[1]
    screens = int(parts[2]) if len(parts) > 2 else 12
    minutes = int(parts[3]) if len(parts) > 3 else 15
    return package, name, screens, minutes


def crawl_one(package: str, name: str, screens: int, minutes: int) -> dict:
    print(f"\n=== {name} ({package}) {screens} 屏 / {minutes} 分钟", flush=True)
    try:
        crawl_id = post("/crawl", {
            "package": package, "app": name,
            "max_screens": screens, "max_minutes": minutes, "max_depth": 3,
        })["id"]
    except Exception as exc:                      # noqa: BLE001 - report, do not die
        print(f"  开始失败: {exc}", flush=True)
        return {"app": name, "state": "start-failed", "detail": str(exc)[:80]}

    started = time.time()
    deadline = started + minutes * 60 + 900       # slack for enrichment and bench
    while time.time() < deadline:
        time.sleep(10)
        try:
            state = get(f"/crawl/{crawl_id}")
        except Exception:                         # the agent may be restarting
            time.sleep(20)
            continue
        if state.get("state") not in ("done", "failed"):
            continue

        elapsed = time.time() - started
        error = (state.get("error") or "").strip()
        bench = coverage = ""
        try:
            row = next((m for m in get("/maps")["maps"]
                        if m.get("file") == f"{name}.yaml"), None)
            if row:
                b = row.get("bench") or {}
                c = row.get("coverage") or {}
                bench = f"{b.get('exact')}/{b.get('ops')}"
                coverage = f"{c.get('queued')} 未访问"
        except Exception:                         # noqa: BLE001 - cosmetic
            pass
        print(f"  {state.get('state')} {elapsed:.0f}s | bench {bench} | {coverage}"
              + (f"\n  原因: {error[:160]}" if error else ""), flush=True)
        return {"app": name, "state": state.get("state"), "seconds": f"{elapsed:.0f}s",
                "bench": bench, "coverage": coverage, "detail": error[:160]}

    print("  超时", flush=True)
    return {"app": name, "state": "timeout", "detail": "no completion within budget"}


def main(argv: list) -> int:
    if not argv:
        print(__doc__)
        return 2
    rows = [crawl_one(*parse(spec)) for spec in argv]
    print("\n=== 汇总")
    print(f"{'app':12s} {'state':10s} {'bench':8s} {'coverage':10s} detail")
    for row in rows:
        print(f"{row.get('app', ''):12s} {row.get('state', ''):10s} "
              f"{row.get('bench', ''):8s} {row.get('coverage', ''):10s} "
              f"{row.get('detail', '')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
