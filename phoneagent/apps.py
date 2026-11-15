"""Which installed apps can be crawled on a virtual display.

The answer is mostly static: an activity declared ``launchMode="singleTask"``
is pinned to its own task, so ``am start --display N`` delivers the request to
that task, wherever it lives. On a secondary display the app then never
appears, and no amount of retrying changes it -- measured on MIUI's calendar
and browser.

Knowing this before the crawl starts turns a fifteen-minute dead end into one
line in a list.
"""

from __future__ import annotations

import os

#: ``android.content.pm.ActivityInfo`` launch modes.
LAUNCH_STANDARD = 0
LAUNCH_SINGLE_TOP = 1
LAUNCH_SINGLE_TASK = 2
LAUNCH_SINGLE_INSTANCE = 3

LAUNCH_MODE_NAMES = {
    LAUNCH_STANDARD: "standard",
    LAUNCH_SINGLE_TOP: "singleTop",
    LAUNCH_SINGLE_TASK: "singleTask",
    LAUNCH_SINGLE_INSTANCE: "singleInstance",
}

#: Modes that can be placed on a secondary display.
CRAWLABLE_MODES = frozenset({LAUNCH_STANDARD, LAUNCH_SINGLE_TOP})


def verdict(package: str, launch_mode: int, mapped: bool = False) -> dict:
    """What to expect from crawling this app, in one word plus a reason."""
    if launch_mode in CRAWLABLE_MODES:
        reason = "标准启动模式，可以爬"
        state = "mapped" if mapped else "crawlable"
    else:
        # A hint, not a verdict: the clock's launcher activity is singleTask and
        # it crawls fine, so the static answer has false alarms in both
        # directions. Only a failed attempt is proof.
        name = LAUNCH_MODE_NAMES.get(launch_mode, str(launch_mode))
        reason = f"首页声明为 {name}，可能起不来（可以试一次，失败会记住）"
        state = "risky"
    return {"package": package, "launch_mode": launch_mode, "state": state,
            "reason": reason}


def installed_apps() -> list:
    """Launchable apps with their launcher activity's launch mode.

    Returns [] outside the app (no JVM), which keeps the host honest: the caller
    must not assume this list exists.
    """
    try:
        from java import jclass  # Chaquopy only
        from com.chaquo.python import Python
    except Exception:  # noqa: BLE001 - not running inside the app
        return []

    try:
        context = Python.getPlatform().getApplication()
        if context is None:
            return []
        pm = context.getPackageManager()
        intent = jclass("android.content.Intent")("android.intent.action.MAIN")
        intent.addCategory(jclass("android.content.Intent").CATEGORY_LAUNCHER)
        resolved = pm.queryIntentActivities(intent, 0)
        own = context.getPackageName()

        out = []
        seen = set()
        for index in range(resolved.size()):
            info = resolved.get(index)
            activity = info.activityInfo
            package = str(activity.packageName)
            if package == own or package in seen:
                continue
            seen.add(package)
            try:
                label = str(info.loadLabel(pm))
            except Exception:  # noqa: BLE001 - a nameless app is still an app
                label = package
            mode = int(activity.launchMode)
            entry = verdict(package, mode)
            entry["label"] = label
            out.append(entry)
        out.sort(key=lambda item: item["label"])
        return out
    except Exception as exc:  # noqa: BLE001 - never break the server over a list
        print(f"[apps] could not list installed apps: {exc}")
        return []


def mapped_packages(map_dir) -> set:
    """Packages that already have a map in ``map_dir``.

    Takes one directory or several: a phone keeps shipped maps and crawled maps
    apart, and an app with either one has a map.
    """
    dirs = [map_dir] if isinstance(map_dir, str) else list(map_dir or [])
    packages = set()
    for directory in dirs:
        if not directory or not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if not name.endswith(".yaml") or name.endswith(".public.yaml"):
                continue
            path = os.path.join(directory, name)
            try:
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        if line.startswith("package:"):
                            packages.add(line.split(":", 1)[1].strip().strip("'\""))
                            break
            except OSError:
                continue
    return packages


def record_refusal(path: str, package: str) -> None:
    """Remember an app that a real crawl could not start.

    The launcher activity's launch mode is only a hint: the browser's
    SplashActivity is standard and still ends up on the phone's own display,
    because it hands over to an activity that is pinned. Recording the ones a
    crawl actually failed on turns the list into something the user can trust.
    """
    if not path or not package:
        return
    known = load_refusals(path)
    if package in known:
        return
    known.add(package)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(sorted(known)) + "\n")
    except OSError as exc:
        print(f"[apps] could not record refusal for {package}: {exc}")


def forgive_refusal(path: str, package: str) -> None:
    """Forget a refusal after a crawl of that app succeeded.

    The list is a hint learned from failures, and failures happen for reasons
    that get fixed (a prompt we now dismiss, a display that was not up). Left
    alone it grows until half the phone is marked uncrawlable -- it had 16
    entries, including apps that crawl fine.
    """
    if not path or not package:
        return
    known = load_refusals(path)
    if package not in known:
        return
    known.discard(package)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(sorted(known)) + ("\n" if known else ""))
    except OSError as exc:
        print(f"[apps] could not clear refusal for {package}: {exc}")


def load_refusals(path: str) -> set:
    if not path or not os.path.exists(path):
        return set()
    try:
        with open(path, encoding="utf-8") as handle:
            return {line.strip() for line in handle if line.strip()}
    except OSError:
        return set()


def all_app_labels() -> dict:
    """Label -> package for *every* installed app, launcher entry or not.

    MIUI's calendar has no launcher entry this phone exposes, so a
    launcher-only list answers "no app called 日历" -- and the agent then
    retries a launch it cannot resolve instead of reporting why. Knowing the
    package lets the launch attempt fail with the real reason (singleTask).
    """
    try:
        from com.chaquo.python import Python
    except Exception:  # noqa: BLE001 - not running inside the app
        return {}

    try:
        context = Python.getPlatform().getApplication()
        if context is None:
            return {}
        pm = context.getPackageManager()
        installed = pm.getInstalledApplications(0)
        out = {}
        for index in range(installed.size()):
            info = installed.get(index)
            try:
                label = str(pm.getApplicationLabel(info))
            except Exception:  # noqa: BLE001 - an unlabelled app has no name
                continue
            if label and label not in out:
                out[label] = str(info.packageName)
        return out
    except Exception as exc:  # noqa: BLE001
        print(f"[apps] could not list installed applications: {exc}")
        return {}


def known_apps() -> dict:
    """Label -> package for the agent's ``launch`` tool.

    Launcher entries win over the full list: their labels are the ones a user
    (and the model) writes.
    """
    mapping = {}
    for entry in installed_apps():
        label = entry.get("label") or ""
        package = entry.get("package") or ""
        if label and package:
            mapping[label] = package
    for label, package in all_app_labels().items():
        mapping.setdefault(label, package)
    return mapping


def catalog(map_dir: str = "", refusal_path: str = "") -> list:
    """Installed apps with a crawl verdict, and whether a map already exists."""
    mapped = mapped_packages(map_dir)
    refused = load_refusals(refusal_path)
    apps = installed_apps()
    for entry in apps:
        entry.update(verdict(entry["package"], entry["launch_mode"],
                             mapped=entry["package"] in mapped))
        if entry["package"] in refused:
            entry["state"] = "refused"
            entry["reason"] = "上次爬取确认起不来（首页最终落在默认屏）"
    return apps
