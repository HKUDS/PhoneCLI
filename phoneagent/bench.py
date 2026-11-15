"""Macro replay benchmark — measure landing accuracy on a real device.

    python3 -m phoneagent.bench --map phonecli_android/app_maps/calendar_android.yaml --limit 8
    python3 -m phoneagent.bench --all --limit 5          # every installed map
    python3 -m phoneagent.bench --map ... --out bench.json

For each sampled NAV operation:

    1. replay the macro (force_stop -> launch -> taps)
    2. dump the UI hierarchy
    3. identify the current screen against the same map
    4. compare it with the operation's expected ``target_screen``

Reported metrics: replay success, exact landing rate, same-app match rate,
and wall-clock time per operation. This is the number that says whether
"compile once, replay deterministically" actually holds on Android.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .core.app_map import AppMap, Operation
from .core.macro import MacroExecutor
from .devices.adb import AdbDevice
from .devices.base import DeviceError

REPO = Path(__file__).resolve().parents[1]
MAP_DIRS = (REPO / "phonecli_android" / "app_maps", REPO / "phonecli" / "app_maps")


@dataclass
class Row:
    app: str
    op_id: str
    expected: str
    landed: str
    confidence: float
    replay_ok: bool
    seconds: float
    error: str = ""

    @property
    def exact(self) -> bool:
        return bool(self.expected) and self.landed == self.expected

    @property
    def same_app(self) -> bool:
        return bool(self.landed)


def _sample_nav_ops(app: AppMap, limit: int, seed: int) -> list[Operation]:
    nav = [op for op in app.build_operations().values() if op.type == "NAV"]
    nav.sort(key=lambda op: op.id)
    if limit and len(nav) > limit:
        return random.Random(seed).sample(nav, limit)
    return nav


def run_one(
    device: AdbDevice,
    app: AppMap,
    op: Operation,
    settle: float,
    dump_dir: Path | None = None,
    scale: bool = True,
    strict_screen: bool = False,
) -> Row:
    started = time.time()
    source = app.size if scale else None
    def _verify(expected: str) -> bool:
        # replay the same guard the agent uses, so the benchmark measures the
        # behaviour the phone actually gets
        try:
            screen_id, _confidence = app.identify_current_screen(device.dump_ui())
        except Exception:  # noqa: BLE001
            return True
        return screen_id is None or screen_id == expected

    result = MacroExecutor(
        device,
        source_size=source,
        launch_intent=app.launch_intent,
        verify_screen=_verify,
        strict_screen=strict_screen,
    ).run(op.macro)
    elapsed = time.time() - started
    if not result.ok:
        return Row(app.app_name, op.id, op.target_screen, "", 0.0, False, elapsed, result.error)

    time.sleep(settle)
    try:
        xml = device.dump_ui()
    except DeviceError as exc:
        return Row(app.app_name, op.id, op.target_screen, "", 0.0, True, time.time() - started, str(exc))

    if dump_dir is not None:
        dump_dir.mkdir(parents=True, exist_ok=True)
        (dump_dir / f"{app.app_name.lower()}__{op.id}.xml").write_text(xml, encoding="utf-8")

    try:
        foreground = device.current_app()
    except DeviceError:
        foreground = ""
    screen_id, confidence = app.identify_current_screen(xml, package=foreground or "")
    return Row(
        app.app_name,
        op.id,
        op.target_screen,
        screen_id or "",
        round(confidence, 3),
        True,
        round(time.time() - started, 2),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", action="append", default=None, help="app map(s) to benchmark")
    parser.add_argument("--all", action="store_true", help="benchmark every map whose package is installed")
    parser.add_argument("--limit", type=int, default=8, help="operations sampled per map")
    parser.add_argument("--strict-screen", action="store_true",
                        help="stop a replay when the screen it expects is not up")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--settle", type=float, default=1.5, help="seconds between replay and dump")
    parser.add_argument("--serial", default=None)
    parser.add_argument("--adb", default=None)
    parser.add_argument("--out", default=None, help="write the raw rows to a JSON file")
    parser.add_argument("--save-dumps", default=None, help="keep every post-replay UI dump here")
    parser.add_argument(
        "--no-scale",
        action="store_true",
        help="replay raw map coordinates (default: scale map -> device resolution)",
    )
    args = parser.parse_args(argv)

    device = AdbDevice(serial=args.serial, adb=args.adb)
    apps: list[AppMap] = []
    if args.map:
        apps = [AppMap(p) for p in args.map]
    elif args.all:
        installed = _installed(device)
        for directory in MAP_DIRS:
            for path in sorted(directory.glob("*.yaml")):
                app = AppMap(path)
                if app.package in installed:
                    apps.append(app)
    else:
        parser.error("pass --map or --all")

    rows: list[Row] = []
    for app in apps:
        ops = _sample_nav_ops(app, args.limit, args.seed)
        if not ops:
            print(f"{app.app_name}: no NAV operations, skipped")
            continue
        print(f"\n{app.app_name} ({app.package}) — {len(ops)} operations")
        print(f"  {'op':<44}{'expected':<12}{'landed':<12}{'conf':>6}{'secs':>7}  result")
        for op in ops:
            row = run_one(
                device, app, op, args.settle,
                Path(args.save_dumps) if args.save_dumps else None,
                scale=not args.no_scale, strict_screen=args.strict_screen,
            )
            rows.append(row)
            flag = "OK" if row.exact else ("app" if row.same_app else "MISS")
            print(
                f"  {row.op_id[:43]:<44}{row.expected:<12}{row.landed or '-':<12}"
                f"{row.confidence:>6.2f}{row.seconds:>7.2f}  {flag}"
                + (f"  {row.error[:40]}" if row.error else "")
            )

    if not rows:
        return 1

    total = len(rows)
    replayed = sum(1 for r in rows if r.replay_ok)
    exact = sum(1 for r in rows if r.exact)
    same_app = sum(1 for r in rows if r.same_app)
    seconds = sum(r.seconds for r in rows)
    print("\nsummary")
    print(f"  replay success : {replayed}/{total} ({replayed / total:.0%})")
    print(f"  exact landing  : {exact}/{total} ({exact / total:.0%})")
    print(f"  same-app match : {same_app}/{total} ({same_app / total:.0%})")
    print(f"  time per op    : {seconds / total:.2f}s average, 0 model calls")

    if args.out:
        Path(args.out).write_text(json.dumps([asdict(r) for r in rows], indent=2))
        print(f"  raw rows       : {args.out}")
    return 0


def _installed(device: AdbDevice) -> set[str]:
    try:
        out = device.shell("pm list packages")
    except DeviceError:
        return set()
    return {
        line.strip().removeprefix("package:")
        for line in out.splitlines()
        if line.strip().startswith("package:")
    }


if __name__ == "__main__":
    sys.exit(main())
