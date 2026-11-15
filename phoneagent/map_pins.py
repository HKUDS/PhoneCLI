"""Which map represents a package.

A phone accumulates maps: one per app shipped in the APK, one per crawl, and
usually several per app after a few experiments. Routing across all of them is
how a task lands on a screen from a map that no longer describes the phone, so
exactly one map per package is in use at a time.

The default rule is "more screens wins, newest breaks the tie" -- a three-minute
crawl that captured one screen should not displace a map that knows the app.
When that is not what you want, pin a file: the pin wins over the rule until it
is removed or the file goes away.
"""

from __future__ import annotations

import json
import os


def load_pins(path: str) -> dict:
    """package -> file name. A missing or broken file means no pins."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def save_pins(path: str, pins: dict) -> None:
    if not path:
        return
    try:
        from .atomic import write_json

        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_json(path, pins)
    except OSError as exc:
        print(f"[maps] could not save pins: {exc}")


def better(path: str, candidate, previous_path: str, previous,
           shipped_dirs=()) -> bool:
    """Should ``candidate`` represent the package instead of ``previous``?"""
    if len(candidate.screens) != len(previous.screens):
        return len(candidate.screens) > len(previous.screens)
    # On a tie, a crawled map wins over a shipped one. The shipped maps are
    # extracted afresh on every start, so their modification time is always the
    # newest -- which meant a crawl could never take over from one of them.
    shipped = {os.path.abspath(d) for d in shipped_dirs if d}
    candidate_shipped = os.path.abspath(os.path.dirname(path)) in shipped
    previous_shipped = os.path.abspath(os.path.dirname(previous_path)) in shipped
    if candidate_shipped != previous_shipped:
        return previous_shipped
    try:
        return os.path.getmtime(path) >= os.path.getmtime(previous_path)
    except OSError:
        return False


def choose(entries: list, pins: dict, shipped_dirs=()) -> list:
    """One map per package.

    ``entries`` is a list of ``(path, app_map)``. A pinned file name wins even
    if it is smaller or older than its siblings; if the pinned file is not
    among the entries (deleted, or the pin points at nothing) the rule decides.
    """
    by_package: dict = {}
    pinned_packages = set()

    for path, app_map in entries:
        package = app_map.package or os.path.basename(path)
        wanted = pins.get(package)
        if wanted and os.path.basename(path) == wanted:
            by_package[package] = (path, app_map)
            pinned_packages.add(package)

    for path, app_map in entries:
        package = app_map.package or os.path.basename(path)
        if package in pinned_packages:
            continue
        previous = by_package.get(package)
        if previous is None or better(path, app_map, previous[0], previous[1], shipped_dirs):
            by_package[package] = (path, app_map)

    return list(by_package.values())


def pinned_name(pins: dict, app_map, path: str = "") -> bool:
    """Is this map the pinned one for its package?"""
    package = getattr(app_map, "package", "") if app_map is not None else ""
    wanted = pins.get(package)
    if not wanted:
        return False
    return os.path.basename(path) == wanted
