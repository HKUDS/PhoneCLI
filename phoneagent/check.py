"""Offline map checker — validate every app map without a phone.

    python3 -m phoneagent.check
    python3 -m phoneagent.check --maps phonecli/app_maps --maps phonecli_android/app_maps

For each YAML map it loads the graph, builds the operation catalog, runs the
static macro validator over every stored and generated macro, and prints a
one-line summary. Exit code is 1 when any map has problems.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.app_map import AppMap
from .core.macro import validate_macro

DEFAULT_DIRS = ("phonecli/app_maps", "phonecli_android/app_maps")

_HEADER = f"{'map':<34}{'platform':<10}{'screens':>8}{'ops':>6}{'NAV':>6}{'ACT':>6}{'flat':>7}{'tree':>7}  problems"


def check_map(path: Path) -> tuple[list[str], dict]:
    problems: list[str] = []
    try:
        app = AppMap(path)
    except Exception as exc:  # malformed YAML must be reported, not crash
        return [f"{path.name}: load failed: {exc}"], {}

    ops = app.build_operations()
    for screen_id, macro in app._macros.items():
        for issue in validate_macro(macro):
            problems.append(f"{path.name}:{screen_id}: {issue}")
    for op in ops.values():
        for issue in validate_macro(op.macro):
            problems.append(f"{path.name}:{op.id}: {issue}")

    flat = app.format_ops_catalog(ops, style="flat")
    tree = app.format_ops_catalog(ops, style="tree")
    stats = {
        "platform": app.platform,
        "screens": len(app.screens),
        "ops": len(ops),
        "nav": sum(1 for op in ops.values() if op.type == "NAV"),
        "act": sum(1 for op in ops.values() if op.type == "ACT"),
        "flat": len(flat),
        "tree": len(tree),
    }
    return problems, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--maps",
        action="append",
        default=None,
        help="directory of *.yaml app maps (repeatable)",
    )
    args = parser.parse_args(argv)

    dirs = [Path(d) for d in (args.maps or DEFAULT_DIRS)]
    files: list[Path] = []
    for directory in dirs:
        if not directory.is_dir():
            print(f"warning: {directory} is not a directory", file=sys.stderr)
            continue
        files.extend(sorted(directory.glob("*.yaml")))

    if not files:
        print("no maps found", file=sys.stderr)
        return 1

    print(_HEADER)
    print("-" * len(_HEADER))
    total_problems = 0
    for path in files:
        problems, stats = check_map(path)
        total_problems += len(problems)
        if stats:
            print(
                f"{path.name:<34}{stats['platform']:<10}{stats['screens']:>8}"
                f"{stats['ops']:>6}{stats['nav']:>6}{stats['act']:>6}"
                f"{stats['flat']:>7}{stats['tree']:>7}  {'OK' if not problems else str(len(problems))}"
            )
        for problem in problems:
            print(f"  ! {problem}")

    print("-" * len(_HEADER))
    print(f"{len(files)} maps, {total_problems} problems")
    return 1 if total_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
