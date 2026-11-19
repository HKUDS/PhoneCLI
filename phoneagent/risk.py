"""Which apps have asked for a captcha, and what that means for the next task.

An app that has answered automation with a security check is not a good place
for deterministic replay: the macro taps the same pixels in the same rhythm, and
that is exactly what the app was objecting to. Replay is therefore skipped for
that app and the GUI loop takes over, which is slower, more varied, and less
likely to be read as a script.

The record is per package and survives restarts; it decays, because an app that
challenged once a month ago is not the same as one that challenged an hour ago.
"""

from __future__ import annotations

import json
import os
import time

#: How long a challenge keeps an app on the "do not replay" list.
RISK_DAYS = 7
#: A mark the user made by hand does not expire: they know the app better than a
#: timer does.
#: Challenged this many times, an app is not replayed again until the record ages
#: out -- the second check was not bad luck.
SERIOUS_COUNT = 3


def _path(path: str) -> str:
    return path or ""


def load(path: str) -> dict:
    """{package: {"count": n, "last": epoch, "until": epoch}}"""
    if not _path(path) or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: str, data: dict) -> None:
    if not _path(path):
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        from .atomic import write_json

        write_json(path, data)
    except OSError as exc:
        print(f"[risk] could not save: {exc}")


def record(path: str, package: str, now: float = 0.0) -> dict:
    """Note that ``package`` asked for a captcha, and return the new record."""
    if not package:
        return {}
    data = load(path)
    entry = dict(data.get(package) or {})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last"] = now or time.time()
    entry["until"] = entry["last"] + RISK_DAYS * 86400
    data[package] = entry
    save(path, data)
    return entry


def mark(path: str, package: str, note: str = "", now: float = 0.0) -> dict:
    """Put an app on the list by hand; this one does not expire."""
    if not path or not package:
        return {}
    data = load(path)
    entry = dict(data.get(package) or {})
    entry["permanent"] = True
    entry["last"] = now or time.time()
    if note:
        entry["note"] = note
    data[package] = entry
    save(path, data)
    return entry


def avoid(path: str, package: str, now: float = 0.0) -> bool:
    """Should replay be skipped for this package?"""
    entry = load(path).get(package or "")
    if not entry:
        return False
    if entry.get("permanent"):
        return True
    moment = now or time.time()
    last = float(entry.get("last", 0) or 0)
    if not last or moment - last > RISK_DAYS * 86400:
        return False                       # old news
    return int(entry.get("count", 0)) >= 1


def describe(path: str, package: str) -> str:
    entry = load(path).get(package or "")
    if not entry:
        return ""
    note = str(entry.get("note", "") or "")
    if note:
        return f"这个 App 被标记为有风控（{note}）"
    count = int(entry.get("count", 0))
    last = float(entry.get("last", 0) or 0)
    if not last:
        return ""
    minutes = int((time.time() - last) // 60)
    when = f"{minutes} 分钟前" if minutes < 120 else f"{minutes // 60} 小时前"
    return f"这个 App {when}弹过安全验证（第 {count} 次）"


def _owner_only(path: str) -> None:
    """These files hold what the phone has seen; only the app may read them."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
