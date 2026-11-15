"""Device probe — check that a real (or emulated) Android device works.

    python3 -m phoneagent.probe
    python3 -m phoneagent.probe --tap          # also replay one NAV macro
    python3 -m phoneagent.probe --map phonecli_android/app_maps/settings_android.yaml

What it does, in order:

    1. adb reachability + device info (model, Android version, screen size)
    2. screenshot -> .probe/shot.png
    3. UI hierarchy dump -> .probe/ui.xml
    4. match the dump against every app map whose package is installed
    5. optionally replay the first NAV operation and re-identify the screen

Every step prints PASS/FAIL with the concrete evidence, so the output can be
pasted into an issue or a paper appendix.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from .core.app_map import AppMap
from .core.macro import MacroExecutor
from .devices.adb import AdbDevice
from .devices.base import DeviceError

REPO = Path(__file__).resolve().parents[1]
MAP_DIRS = (REPO / "phonecli_android" / "app_maps", REPO / "phonecli" / "app_maps")


def _ok(label: str, detail: str = "") -> None:
    print(f"  PASS  {label}" + (f" — {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))


def _installed_packages(device: AdbDevice) -> set[str]:
    try:
        out = device.shell("pm list packages")
    except DeviceError:
        return set()
    return {
        line.strip().removeprefix("package:")
        for line in out.splitlines()
        if line.strip().startswith("package:")
    }


def _candidate_maps(installed: set[str], platform: str) -> list[AppMap]:
    maps: list[AppMap] = []
    for directory in MAP_DIRS:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            try:
                app = AppMap(path)
            except Exception:
                continue
            # A package id can exist on both platforms (e.g. com.sina.weibo);
            # only offer maps built for the platform we are talking to.
            if app.package in installed and app.platform == platform:
                maps.append(app)
    return maps



_NODE_RE = re.compile(r"<node\b([^>]*)>")
_BOUNDS_RE = re.compile(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')


def _node_texts(xml: str) -> set[str]:
    texts: set[str] = set()
    for match in _NODE_RE.finditer(xml):
        tag = match.group(0)
        for attr in ("text", "content-desc"):
            value = re.search(rf'{attr}="([^"]*)"', tag)
            if value and value.group(1).strip():
                texts.add(value.group(1).strip())
    return texts


def _first_tappable(xml: str):
    """First clickable container that actually holds a labelled child.

    Android list rows are a clickable LinearLayout whose text lives in a
    non-clickable child TextView, so picking the clickable node blindly lands
    on an unlabelled area (and a tap there does nothing).
    """
    nodes = []
    for match in _NODE_RE.finditer(xml):
        tag = match.group(0)
        bounds = _BOUNDS_RE.search(tag)
        if not bounds:
            continue
        box = tuple(int(v) for v in bounds.groups())  # x1, y1, x2, y2
        label = ""
        for attr in ("text", "content-desc"):
            value = re.search(rf'{attr}="([^"]*)"', tag)
            if value and value.group(1).strip():
                label = value.group(1).strip()
                break
        nodes.append((label, box, 'clickable="true"' in tag))

    labels = [(label, box) for label, box, clickable in nodes if label and not clickable]
    for label, (x1, y1, x2, y2), clickable in nodes:
        if not clickable:
            continue
        if x2 - x1 < 40 or y2 - y1 < 40:
            continue
        for text, (tx1, ty1, tx2, ty2) in labels:
            cx, cy = (tx1 + tx2) // 2, (ty1 + ty2) // 2
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return text, ((x1 + x2) // 2, (y1 + y2) // 2)
    return None


def input_check(device: AdbDevice) -> int:
    """Prove that adb input injection actually takes effect on this ROM."""
    print("[I] input injection")
    try:
        device.press_home()
        time.sleep(1.0)
        before_home = device.current_app()

        device.launch("com.android.settings")
        time.sleep(2.5)
        before = device.dump_ui()
    except DeviceError as exc:
        _fail("input setup", str(exc))
        return 1

    target = _first_tappable(before)
    if target is None:
        _fail("input check", "no clickable node found on the Settings screen")
        return 1
    label, (cx, cy) = target
    print(f"      target: {label!r} at ({cx}, {cy})")

    try:
        device.tap(cx, cy)
    except DeviceError as exc:
        _fail("tap", str(exc))
        return 1
    time.sleep(1.5)

    try:
        after = device.dump_ui()
    except DeviceError as exc:
        _fail("dump after tap", str(exc))
        return 1

    changed = _node_texts(before) != _node_texts(after)
    if changed:
        _ok("input", "tap changed the screen — adb injection works")
        _ok("home", f"foreground after HOME was {before_home}")
        return 0

    _fail("input", "tap produced NO screen change")
    print("      likely cause on Xiaomi/HyperOS: Settings > Developer options")
    print("      > 'USB debugging (Security settings)' is off. It needs a")
    print("      Mi account + an inserted SIM card, and may need a reboot.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=None, help="adb serial (default: the only device)")
    parser.add_argument("--adb", default=None, help="path to the adb binary")
    parser.add_argument(
        "--map", action="append", default=None, help="explicit app map(s) to test"
    )
    parser.add_argument("--out", default=".probe", help="directory for shot.png / ui.xml")
    parser.add_argument("--tap", action="store_true", help="replay the first NAV macro")
    parser.add_argument(
        "--input-check",
        action="store_true",
        help="verify that adb input injection actually works (HyperOS gate)",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("[1] adb")
    try:
        device = AdbDevice(serial=args.serial, adb=args.adb)
    except DeviceError as exc:
        _fail("adb device", str(exc))
        return 1
    if not device.is_ready():
        _fail("adb device", "state is not 'device'")
        return 1
    _ok("adb device", f"serial={device.serial}")

    print("[2] device info")
    info = device.info()
    _ok(
        "info",
        f"{info.model} / Android {info.os_version} / "
        f"{info.screen_width}x{info.screen_height} / {info.platform}",
    )

    if args.input_check:
        code = input_check(device)
        if code != 0:
            return code
        print()

    print("[3] screenshot")
    shot_path = out_dir / "shot.png"
    try:
        png = device.screenshot(str(shot_path))
    except DeviceError as exc:
        _fail("screenshot", str(exc))
        return 1
    _ok("screenshot", f"{len(png)} bytes -> {shot_path}")

    print("[4] UI dump")
    try:
        xml = device.dump_ui()
    except DeviceError as exc:
        _fail("dump_ui", str(exc))
        return 1
    xml_path = out_dir / "ui.xml"
    xml_path.write_text(xml, encoding="utf-8")
    nodes = xml.count("<node")
    _ok("dump_ui", f"{nodes} nodes, {len(xml)} chars -> {xml_path}")
    if nodes == 0:
        _fail("dump_ui", "hierarchy has no nodes")

    print("[5] app maps")
    installed = _installed_packages(device)
    maps = (
        [AppMap(p) for p in args.map]
        if args.map
        else _candidate_maps(installed, info.platform)
    )
    if not maps:
        _fail("maps", "no shipped map matches an installed package")
    else:
        _ok("maps", f"{len(maps)} map(s) match installed apps")
        for app in maps:
            ops = app.build_operations()
            screen_id, confidence = app.identify_current_screen(xml)
            mark = "match" if screen_id else "no match"
            print(
                f"        {app.app_name:<16} {app.package:<34} "
                f"{len(app.screens):>3} screens {len(ops):>4} ops  -> {mark}"
                + (f" {screen_id} ({confidence:.0%})" if screen_id else "")
            )

    if not args.tap:
        print("\ndone (add --tap to replay a macro)")
        return 0

    print("[6] macro replay")
    target = next(
        (
            (app, op)
            for app in maps
            for op in app.build_operations().values()
            if op.type == "NAV"
        ),
        None,
    )
    if target is None:
        _fail("macro", "no NAV operation to replay")
        return 1
    app, op = target
    print(f"      {app.app_name}: {op.id} ({len(op.macro)} steps)")
    started = time.time()
    result = MacroExecutor(device).run(op.macro)
    elapsed = time.time() - started
    if not result.ok:
        _fail("macro", result.error)
        return 1
    _ok("macro", f"{result.executed} steps in {elapsed:.2f}s")

    time.sleep(1.0)
    after = device.dump_ui()
    screen_id, confidence = app.identify_current_screen(after)
    (out_dir / "ui_after.xml").write_text(after, encoding="utf-8")
    if screen_id:
        _ok("landed on", f"{screen_id} ({confidence:.0%})")
    else:
        _fail("landed on", "no screen matched after replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
