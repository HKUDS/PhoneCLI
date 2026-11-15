"""How often the phone may be pointed at the same app.

A crawl is the least human thing this agent does: dozens of taps on one app in a
few minutes. Commercial apps notice, and the answer to "stop touching me that
often" is to touch less often -- both between crawls of the same app and between
the taps inside one.

The record is a small JSON file so it survives restarts; the app is the only
caller, and a lost record costs nothing but an early crawl.
"""

from __future__ import annotations

import json
import os
import time

#: How long a commercial app is left alone after a crawl of it finished.
CRAWL_COOLDOWN = 900.0        # 15 minutes
#: The same for a system app: it has no risk control to provoke, and a crawl of
#: Settings is five minutes of work, not a threat to anybody. This is only here
#: so a loop cannot re-crawl the same app back to back.
UTILITY_COOLDOWN = 120.0

#: Packages that belong to the phone itself, or ship with it. These are not the
#: apps that answer automation with a captcha.
UTILITY_PREFIXES = ("com.android.", "com.miui.", "android", "com.google.android.")


def is_commercial(package: str) -> bool:
    """Does this app have an account, a payment, or a reason to watch for bots?

    Guessed from the package name, which is a heuristic: a system app shipped by
    a vendor is treated as safe, everything else is treated as a shop until it
    proves otherwise. Being wrong in this direction only costs a cooldown.
    """
    name = (package or "").strip()
    if not name:
        return False
    return not any(name.startswith(prefix) for prefix in UTILITY_PREFIXES)
#: Gap between taps inside a crawl: a person does not tap twice a second either.
TAP_GAP = (0.7, 1.6)


def load(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(path: str, data: dict) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        from .atomic import write_json

        write_json(path, data)
    except OSError as exc:
        print(f"[pacing] could not save: {exc}")


def mark(path: str, package: str, now: float = 0.0) -> None:
    """Remember that we just started working on this app."""
    if not path or not package:
        return
    data = load(path)
    data[package] = (now or time.time())
    save(path, data)


def remaining(path: str, package: str, cooldown: float = 0.0,
              now: float = 0.0) -> float:
    """Seconds left before this app may be crawled again; 0 means now.

    ``cooldown`` defaults to the app's own class: commercial apps wait a quarter
    of an hour, system apps two minutes.
    """
    if not path or not package:
        return 0.0
    last = float(load(path).get(package, 0) or 0)
    if not last:
        return 0.0
    if not cooldown:
        cooldown = CRAWL_COOLDOWN if is_commercial(package) else UTILITY_COOLDOWN
    left = cooldown - ((now or time.time()) - last)
    return max(0.0, left)


def _owner_only(path: str) -> None:
    """These files hold what the phone has seen; only the app may read them."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
