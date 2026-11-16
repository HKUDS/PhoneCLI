"""Build an app map for whatever app is installed on the connected phone.

    python3 -m phoneagent.build_map \
        --serial e3fa9e60 \
        --package com.android.settings --app 设置 \
        --max-screens 50 --max-depth 3 \
        --output phoneagent/maps/k90_settings.yaml

The BFS crawler itself is the upstream one
(``phonecli_android/evaluation/build_map.py``), reused unchanged through a
small adapter so the generated YAML keeps exactly the format that
:class:`~phoneagent.core.app_map.AppMap` and the replay path expect.

The adapter is the interesting part: it proves the device protocol is enough
to drive compilation, not just replay.

Safety rails
------------
* ``--keep-awake`` (default on) pins the screen on and dismisses the keyguard
  for the duration, then restores the setting.
* Wi-Fi / Bluetooth / airplane state is sampled before and after; the script
  warns if crawling flipped anything.
* ``--max-depth 1`` only taps the rows of the first screen, so the crawler opens
  sub-pages but never taps inside them.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import random
import re
import signal
import sys
import time
from pathlib import Path

import yaml
from typing import Optional

from .devices.adb import AdbDevice
from .core.ui import find_label, parse_nodes
from .devices.base import DeviceError, LaunchRefused, NeedsHandover, PhoneDevice

REPO = Path(__file__).resolve().parents[1]
CRAWLER = REPO / "phonecli_android" / "evaluation" / "build_map.py"

#: The app this crawl may be running in (hands-off mode). Never stop it.
AGENT_PACKAGE = "com.phoneagent.app"


# ---------------------------------------------------------------------------
# Adapter: PhoneDevice -> the controller surface the crawler expects
# ---------------------------------------------------------------------------


class DeviceController:
    """Minimal adapter over the 7 methods the upstream crawler calls."""

    def __init__(self, device: PhoneDevice, launch_intent: str = "") -> None:
        self.device = device
        self.launch_intent = launch_intent
        self.width, self.height = device.info().size
        self._last_xml = ""          # most recent dump, used to label a tap
        self.verified_taps = 0
        self.blind_taps = 0

    def get_device_size(self) -> tuple[int, int]:
        return self.width, self.height

    def _publish(self) -> None:
        """Let the app show this screen.

        The crawler works from trees, never from screenshots, so without a hook
        the live view showed an empty box for a whole build. Called on this
        thread only, so it never competes for the adb transport.
        """
        hook = getattr(self, "on_screen", None)
        if hook is None:
            return
        try:
            hook()
        except Exception:  # noqa: BLE001 - a picture is optional
            pass

    def get_xml(self, prefix: str, save_dir: str) -> str:
        xml = self.device.dump_ui()
        self._last_xml = xml
        self._publish()
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, f"{prefix}.xml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(xml)
        return path

    def get_screenshot(self, prefix: str, save_dir: str) -> str:
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, f"{prefix}.png")
        self.device.screenshot(path)
        return path

    def tap(self, x: int, y: int):
        """Tap the element at (x, y), re-locating it by label first.

        The crawler decides where to tap from a dump taken moments earlier; if
        the layout shifts in between (a banner appears, a list scrolls back)
        the blind tap lands on a neighbour and the resulting screen is
        recorded as this element's destination — a silent, invisible
        corruption of the map. Resolving the label against a fresh dump keeps
        the edge consistent with the label the map claims.
        """
        label = self._label_at(x, y)
        if label:
            fresh = self.device.dump_ui()
            node = find_label(fresh, label)
            if node is not None:
                self.device.tap(*node.center)
                self.verified_taps += 1
                self._publish()
                return
        self.device.tap(int(x), int(y))
        self.blind_taps += 1
        self._publish()

    def _label_at(self, x: int, y: int) -> str:
        """Label of the node whose center is (x, y) in the last dump."""
        if not self._last_xml:
            return ""
        for node in parse_nodes(self._last_xml):
            if not node.label:
                continue
            cx, cy = node.center
            if abs(cx - x) <= 3 and abs(cy - y) <= 3:
                return node.label
        return ""

    def swipe(self, x, y, direction: str, dist="medium", quick: bool = False):
        if x is None:
            x = self.width // 2
        if y is None:
            y = self.height // 2
        if isinstance(dist, str):
            unit = int(self.width / 10)
            unit *= 10 if dist == "long" else 2
        else:
            unit = int(dist)
        offsets = {
            "up": (0, -2 * unit),
            "down": (0, 2 * unit),
            "left": (-unit, 0),
            "right": (unit, 0),
        }
        dx, dy = offsets.get(direction, (0, 0))
        if (dx, dy) == (0, 0):
            return "ERROR"
        self.device.swipe(int(x), int(y), int(x + dx), int(y + dy), 100 if quick else 400)
        return "ok"

    def kill_package(self, package: str):
        self.device.force_stop(package)

    def launch_app(self, package: str):
        # Verified launch: the ROM may silently ignore `monkey`, which would
        # make the crawler tap on whatever screen was left in front.
        self.device.launch(package, intent=self.launch_intent)
        self._publish()

    def run_command(self, command: str):
        """The crawler passes full ``adb shell ...`` strings."""
        shell_cmd = command
        for prefix in ("adb shell ", "adb "):
            if shell_cmd.startswith(prefix):
                shell_cmd = shell_cmd[len(prefix):]
                break
        return self.device.shell(shell_cmd)

    def get_current_app(self) -> str:
        return self.device.current_app()


def _load_crawler():
    # The Android tree expects to be its own package root: build_map.py does
    # `from phonecli.llm_client import ...`, which from the repository root
    # resolves to the *iOS* `phonecli/` package instead (whose text_completion
    # has no `label` argument), silently breaking classify/enrich.
    android_root = str(REPO / "phonecli_android")
    for name in [m for m in sys.modules if m == "phonecli" or m.startswith("phonecli.")]:
        del sys.modules[name]
    if android_root in sys.path:
        sys.path.remove(android_root)
    sys.path.insert(0, android_root)

    spec = importlib.util.spec_from_file_location("phonecli_android_build_map", CRAWLER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["phonecli_android_build_map"] = module  # needed for the LLM shim
    spec.loader.exec_module(module)
    return module.build_app_map


# ---------------------------------------------------------------------------
# Screen / radio state helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Verified crawler
# ---------------------------------------------------------------------------


#: Two candidates for one label this close together are the same control.
NEAR_PX = 120

#: Typed into any text field the crawl finds, so the page behind it becomes a
#: screen with content instead of an empty one indistinguishable from its parent.
PROBE_WORD = "test"


def _nearest_label(xml: str, label: str, cx: float, cy: float):
    """The match for ``label`` closest to where it was, when the label repeats.

    ``find_label`` refuses ambiguous labels, and a tab bar makes almost every
    tab ambiguous: 世界时钟 is the page title, the tab and the tab's caption.
    The crawl then reported "not on screen" and skipped it -- which is why the
    clock map held one screen and its four tabs were never opened. Nearest to
    the recorded position picks the one the crawl meant, and a tappable node
    wins a tie because that is the one whose tap does something.
    """
    nodes = [node for node in parse_nodes(xml) if node.label == label]
    if not nodes:
        return None

    def distance(node) -> float:
        return (node.center[0] - cx) ** 2 + (node.center[1] - cy) ** 2

    # Distance decides first: a tappable node on the other side of the screen is
    # not what the crawl meant. Only among candidates at the same place (a tab's
    # caption and its container are ~35 px apart) does tappable win.
    near = [node for node in nodes if distance(node) <= NEAR_PX ** 2]
    return min(near or nodes, key=lambda node: (0 if node.clickable else 1, distance(node)))


def _screen_moved(device, step_timeout: float, parent_sig: str, bm,
                  width: int, height: int) -> bool:
    """Did the screen change since ``parent_sig`` was taken?"""
    try:
        now = _guarded(step_timeout, device.dump_ui)
    except StepTimeout:
        return False
    sig = bm._build_signature(
        bm._parse_elements_android(now, width, height, None), None)
    return sig != parent_sig


def _clickable_points(xml: str, width: int, height: int) -> set:
    """Where a tap does something, as (nx, ny) of each clickable node.

    A tab is usually a clickable container with a caption inside it, and the
    parsed element is the caption -- whose own point is not clickable. So the
    container's rectangle is expanded into a few sampled points: any element
    whose centre falls inside one of them is tappable.
    """
    out = set()
    for node in parse_nodes(xml):
        if not node.clickable:
            continue
        left, top, right, bottom = node.bounds
        if right <= left or bottom <= top:
            continue
        for fx in (0.5,):
            for fy in (0.5, 0.25, 0.75):
                x = left + (right - left) * fx
                y = top + (bottom - top) * fy
                out.add((round(x / width, 4), round(y / height, 4)))
        out.add((round(node.center[0] / width, 4), round(node.center[1] / height, 4)))
    return out


def _editable_at(xml: str, x: float, y: float) -> bool:
    """Is this point a text field (or inside one)?"""
    for node in parse_nodes(xml):
        if not (node.editable or "EditText" in (node.klass or "")):
            continue
        left, top, right, bottom = node.bounds
        if left <= x <= right and top <= y <= bottom:
            return True
    return False


def _inside_clickable(xml: str, x: float, y: float) -> bool:
    """Is this point inside any clickable node? A caption inside its tab is."""
    for node in parse_nodes(xml):
        if not node.clickable:
            continue
        left, top, right, bottom = node.bounds
        if left <= x <= right and top <= y <= bottom:
            return True
    return False


def _collect_screen(controller: DeviceController, bm, scroll_pages: int):
    """Dump the current screen, scrolling a few times to reach folded elements."""
    width, height = controller.get_device_size()
    seen: dict[str, dict] = {}
    positions: dict[str, set] = {}
    # Which of the remembered positions can actually be tapped. A label often
    # appears twice -- the page title and the tab that opens it -- and picking
    # the first left the crawler tapping headings: the clock's four tabs were
    # never opened, so that map held two screens against the original
    # project's thirty-one.
    clickable: dict[str, set] = {}

    for page in range(scroll_pages):
        xml = controller.device.dump_ui()
        tappable = _clickable_points(xml, width, height)
        for element in bm._parse_elements_android(xml, width, height, None):
            text = element["text"]
            if not text:
                continue
            point = (round(element["nx"], 4), round(element["ny"], 4))
            # "inside a clickable container" counts too: a tab's caption is a
            # plain TextView, but tapping it opens the tab.
            is_clickable = point in tappable or _inside_clickable(
                xml, element["cx"], element["cy"])
            if is_clickable:
                clickable.setdefault(text, set()).add(point)
            if text in seen:
                # A tappable occurrence replaces a plain one: same label, but
                # this is the node whose tap does something.
                current = (round(seen[text]["nx"], 4), round(seen[text]["ny"], 4))
                if is_clickable and current not in clickable.get(text, set()):
                    element = dict(element)
                    element["found_at_scroll"] = page
                    seen[text] = element
            else:
                seen[text] = dict(element)
                seen[text]["found_at_scroll"] = page
            positions.setdefault(text, set()).add(point)
        if page < scroll_pages - 1:
            controller.swipe(width // 2, int(height * 0.7), "up", dist=450)
            time.sleep(1.0)

    # an element seen at the same place on several pages is part of the frame
    for text, element in seen.items():
        element["fixed"] = len(positions.get(text, set())) == 1 and element["found_at_scroll"] == 0
    return list(seen.values())


def _replay_path(device, macro: list[dict]) -> None:
    """Replay a parent path, re-locating every tap by its label."""
    for step in macro:
        action = step.get("action")
        if action == "tap":
            label = step.get("text")
            node = find_label(device.dump_ui(), label) if label else None
            if node is not None:
                device.tap(*node.center)
            else:
                device.tap(int(step["x"]), int(step["y"]))
            time.sleep(step.get("wait", 1.0))
        elif action == "swipe":
            device.swipe(int(step["x1"]), int(step["y1"]),
                         int(step["x2"]), int(step["y2"]),
                         int(step.get("duration", 400)))
            time.sleep(step.get("wait", 0.5))


#: Runs of digits are what change between two visits to the same screen: a
#: calculator's result, a clock's countdown, a calendar's day numbers.
_VOLATILE_DIGITS = re.compile(r"\d+")


def _element_key(element) -> str:
    text = (element.get("text") or "").strip()
    key = _VOLATILE_DIGITS.sub("#", text)
    klass = (element.get("class") or element.get("resource_id") or "").split(".")[-1]
    center = element.get("center") or (0.0, 0.0)
    try:
        x, y = round(float(center[0]), 1), round(float(center[1]), 1)
    except (TypeError, ValueError, IndexError):
        x = y = 0.0
    return f"{klass}:{key}:{x}:{y}"


def _stable_keys(elements, preserve_set=None) -> frozenset:
    """The set a screen is matched on; compared by overlap, never by equality."""
    keys = set()
    for element in elements or []:
        text = (element.get("text") or "").strip()
        if element.get("dynamic"):
            continue          # content that changes: not an anchor
        try:
            if text and (_is_dynamic_text(text) or _should_skip_text(text, preserve_set)):
                continue
        except Exception:  # noqa: BLE001
            pass
        keys.add(_element_key(element))
    return frozenset(keys)


def _similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


#: Below this many shared labels, containment is rounding noise rather than
#: evidence that two captures are the same page.
CONTAINMENT_FLOOR = 4


def _screen_texts(elements, preserve_set=None) -> frozenset:
    """The labels on a screen, ignoring where on the screen they sat."""
    texts = set()
    for element in elements or []:
        text = (element.get("text") or "").strip()
        if element.get("dynamic"):
            continue
        try:
            if text and (_is_dynamic_text(text) or _should_skip_text(text, preserve_set)):
                continue
        except Exception:  # noqa: BLE001
            pass
        if text:
            texts.add(_VOLATILE_DIGITS.sub("#", text))
    return frozenset(texts)


def _fingerprint(elements, preserve_set=None) -> tuple:
    return _stable_keys(elements, preserve_set), _screen_texts(elements, preserve_set)


def _print_similarity(a: tuple, b: tuple) -> float:
    """How much two captures are the same screen.

    Position-bearing keys are the precise test, but scrolling moves every row,
    so a page captured at two offsets shares its labels and not its keys. That
    is how one Settings page ended up stored as two screens -- and two screens
    that are one page make identification a coin toss at replay time.
    """
    keys_a, texts_a = a
    keys_b, texts_b = b
    if not keys_a or not keys_b:
        return 0.0
    jaccard = _similarity(keys_a, keys_b)
    shared = len(texts_a & texts_b)
    if shared < CONTAINMENT_FLOOR:
        return jaccard
    containment = shared / min(len(texts_a), len(texts_b))
    return max(jaccard, containment)


def _selected_labels(xml: str) -> list:
    """Labels the app marks as selected in this dump (the highlighted tab)."""
    import re as _re

    out = []
    for tag in _re.finditer(r"<node\b([^>]*)>", xml or ""):
        attrs = tag.group(0)
        if 'selected="true"' not in attrs:
            continue
        match = _re.search(r'(?:text|content-desc)="([^"]+)"', attrs)
        if match:
            label = match.group(1).strip()
            if label and label not in out:      # the tab and its label node both match
                out.append(label)
    return out


def _anchor_texts(elements) -> set:
    """The labels a screen is identified by: its chrome, not its content."""
    texts = {(e.get("text") or "").strip() for e in elements or []}
    texts.discard("")
    stable = {(e.get("text") or "").strip() for e in elements or []
              if not e.get("dynamic")}
    stable.discard("")
    return stable or texts


def _same_chrome(keys: frozenset, screens: list, threshold: float = 0.6):
    """The screen this one is a content refresh of, if any."""
    if not keys:
        return None
    for screen in screens:
        other = _stable_keys(screen.get("elements") or [])
        if not other:
            continue
        overlap = len(keys & other)
        if overlap and overlap / len(keys | other) >= threshold:
            return screen["id"]
    return None


def _content_heavy(elements) -> bool:
    """Is most of this screen variable content rather than chrome?"""
    total = len(elements or [])
    if not total:
        return False
    dynamic = sum(1 for e in elements if e.get("dynamic"))
    return dynamic / total > 0.5


def _has_text_field(xml: str) -> bool:
    """Does this screen carry an input field?

    A search page looks exactly like the feed it was opened from -- same top
    bar, same bottom tabs, same category chips -- so containment merged 小红书's
    search page into the feed and the crawl never recorded it. An editable node
    is the one thing a search page has and a feed does not.
    """
    return 'editable="true"' in (xml or "") or "EditText" in (xml or "")


def _match_unified(screen_keys: dict, screen_texts: dict, elements, selected=None,
                   has_input=None, screen_inputs=None, screen_selected=None,
                   threshold: float = 0.85):
    """Is this capture a screen we already have?

    Two rules, and it is match if either says so. The strict positional one is
    precise; the replay-style containment one is what the map will use later, and
    agreeing with it here is the point -- a screen that replay would call "the
    same page" must not be stored as a second copy now.
    """
    matched, score = _match_screen(screen_keys, _fingerprint(elements))
    if matched is not None:
        # The tab check has to come before this return, not only inside the
        # containment loop below: the four tabs of a clock share their whole
        # chrome, so the positional fingerprint matches them perfectly and the
        # strict path never reached the tab comparison at all.
        if screen_selected is not None and selected:
            known_tab = set(screen_selected.get(matched) or [])
            if known_tab and set(selected) != known_tab:
                return None, 0.0
        return matched, score

    # Loose merging is for content-heavy screens only. A feed's "same page with
    # different posts" is a duplicate worth folding; a settings sub-page that
    # merely shares 返回 with its menu is not, and merging those turned nineteen
    # settings pages into thirteen with a worse landing rate (6/6 -> 3/6).
    if not _content_heavy(elements):
        return None, 0.0

    texts = _anchor_texts(elements) | set(selected or [])
    best_id, best_cov = None, 0.0
    if texts:
        for screen_id, known in screen_texts.items():
            if not known:
                continue
            if (has_input is not None and screen_inputs is not None
                    and screen_inputs.get(screen_id, False) != has_input):
                # "One has a text field and the other does not" is never the
                # same page, however much of the chrome they share.
                continue
            if screen_selected is not None and selected:
                # A different highlighted tab means a different page even when
                # every other label is shared: 闹钟 / 世界时钟 / 秒表 / 计时 are one
                # screen by overlap and four by behaviour. Measured: the clock
                # map held two screens against the original project's thirty-one.
                known_tab = set(screen_selected.get(screen_id) or [])
                if known_tab and set(selected) != known_tab:
                    continue
            shared = len(texts & known)
            if not shared:
                continue
            coverage = shared / len(known)
            # Containment alone is not enough: a settings sub-page shares 返回
            # and the app's tab names with the menu it came from, so 0.5 on that
            # measure merged nineteen pages into five. Demanding general
            # similarity as well keeps "the same page seen twice" apart from
            # "a small page inside a big one".
            jaccard = shared / len(texts | known)
            if coverage >= 0.5 and jaccard >= 0.35 and coverage > best_cov:
                best_id, best_cov = screen_id, coverage
    return best_id, best_cov


def _match_screen(screen_keys: dict, keys: tuple, threshold: float = 0.85):
    """The known screen this dump is a view of, or None.

    Exact signatures cannot merge two visits whose element set jitters by an
    element or two -- a calculator's panel comes back with 37, 38 and 39
    elements -- so screens are matched by overlap instead.
    """
    best_id, best_score = None, 0.0
    for screen_id, known in screen_keys.items():
        score = _print_similarity(keys, known)
        if score > best_score:
            best_id, best_score = screen_id, score
    if best_id is not None and best_score >= threshold:
        return best_id, best_score
    return None, best_score


class StepTimeout(Exception):
    """One crawl step took longer than its budget."""


def _guarded(seconds: float, fn, *args, **kwargs):
    """Run one device step under a wall-clock limit.

    A crawl step is a chain of adb calls -- dump, scroll, dump, replay, tap --
    and any of them can stall long enough that the crawler never returns to its
    loop. The time budget only checked between steps, so a stall inside one step
    hung the whole run for as long as the shell stayed alive. SIGALRM interrupts
    the blocking call, so the step is abandoned and the crawl moves on.
    """
    if not seconds or not hasattr(signal, "SIGALRM"):
        return fn(*args, **kwargs)

    def _fire(_signum, _frame):
        raise StepTimeout(f"step exceeded {seconds:.0f}s")

    try:
        previous = signal.signal(signal.SIGALRM, _fire)
    except ValueError:
        # "signal only works in main thread": in the app the crawl runs on a
        # worker so the request thread stays free. The step watchdog is simply
        # unavailable there -- the wall-clock budget between steps still is.
        return fn(*args, **kwargs)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*args, **kwargs)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _classify_stable_dynamic(elements: list, app_name: str, api_key: str,
                             api_base: str, model: str):
    """Ask the model which labels will still be here next time.

    Uses phoneagent's own prompt rather than phonecli.prompts: that file is
    the repo's own CLI, and the crawl here keeps the dynamic half as tags
    instead of dropping it. Editing the upstream prompt would have changed
    the original implementation too.
    """
    if not elements:
        return [], []
    from phonecli.llm_client import text_completion

    from .core.prompts import ELEMENT_CLASSIFY_PROMPT

    text_list = "\n".join(e["text"] for e in elements)
    system_prompt = ELEMENT_CLASSIFY_PROMPT.format(app_name=app_name)
    rsp = text_completion(system_prompt, f"Elements:\n{text_list}",
                          api_key=api_key, api_base=api_base, model=model,
                          max_tokens=2048, temperature=0.0, label="classify")
    stable_texts, dynamic_texts = set(), set()
    for line in (rsp or "").strip().splitlines():
        line = line.strip()
        upper = line.upper()
        if "|" not in line:
            continue
        label, _, text = line.partition("|")
        if label.strip().upper() == "DYNAMIC":
            dynamic_texts.add(text.strip())
        elif label.strip().upper() == "STABLE":
            stable_texts.add(text.strip())
    stable = [e for e in elements if e["text"] in stable_texts]
    dynamic = [e for e in elements if e["text"] in dynamic_texts]
    return stable, dynamic


def _tag_dynamic(bm, elements: list, app_name: str, api_key: str, api_base: str,
                 model: str) -> list:
    """Mark the volatile half of a screen instead of deleting it.

    The classifier answers "will this text be here next time", not "can this be
    tapped". Keeping everything and carrying the answer as a flag lets the crawl
    explore content and the matcher ignore it -- the two needs that one filter
    used to serve badly.
    """
    try:
        result = _classify_stable_dynamic(
            elements, app_name, api_key, api_base, model)
    except Exception as exc:  # noqa: BLE001 - classification is optional
        print(f"[Verified]   classification failed ({exc}); keeping all")
        return elements
    # Two shapes exist: the upstream returns (stable, dynamic), the Android
    # port returns only the stable list. Guessing wrong made every element look
    # stable, which is how a "tagged" crawl ended up with zero tags.
    if isinstance(result, tuple) and len(result) == 2:
        stable, dynamic = result
        dynamic_texts = {e["text"] for e in dynamic}
    else:
        kept_texts = {e["text"] for e in (result or [])}
        dynamic_texts = {e["text"] for e in elements if e["text"] not in kept_texts}
    print(f"[Verified]   tagged {len(dynamic_texts)} dynamic element(s)")
    for element in elements:
        element["dynamic"] = element["text"] in dynamic_texts
    return elements


def _element_point(element: dict, width: int, height: int):
    """Where the element sat, in pixels, from its normalised centre."""
    try:
        nx, ny = element.get("nx"), element.get("ny")
        if nx is None or ny is None:
            center = element.get("center") or []
            if len(center) == 2:
                nx, ny = center
        if nx is None or ny is None:
            return None
        return int(float(nx) * width), int(float(ny) * height)
    except (TypeError, ValueError):
        return None


def _record_edge(screens, parent_id: str, label: str, target_id: str) -> None:
    """Point the parent's element at the screen the tap actually reached.

    An edge back to the screen it started from is not navigation: either the tap
    missed, or the page had not changed yet when the dump was taken. Recording it
    taught the map that "显示与亮度" leads to the settings menu (measured: five of
    the settings home screen's entries were like that), so it is dropped.
    """
    if target_id == parent_id:
        return
    for screen in screens:
        if screen["id"] != parent_id:
            continue
        for entry in screen["elements"]:
            if entry["text"] == label:
                entry["leads_to"] = target_id
        return


def _map_health(output_path: str, device, package: str, ops: int = 5,
                settle: float = 1.5) -> dict:
    """Replay a few of the map's own operations and count the landings.

    Returns ``{ops, exact, same_app, at}``. Sampling is deliberately small: the
    point is a cheap honest number, not a full benchmark.
    """
    import time as _time

    from . import bench as bench_mod
    from .core.app_map import AppMap

    app = AppMap(output_path)
    sampled = bench_mod._sample_nav_ops(app, ops, 0)
    exact = same_app = 0
    for op in sampled:
        row = bench_mod.run_one(device, app, op, settle=settle)
        if row.landed:
            same_app += 1
            if row.landed == row.expected:
                exact += 1
    return {
        "ops": len(sampled),
        "exact": exact,
        "same_app": same_app,
        "at": _time.strftime("%Y-%m-%dT%H:%M"),
    }


def _record_map_health(output_path: str, device, package: str, ops: int = 5,
                       settle: float = 1.5) -> dict:
    """Measure the map and write the result into its header.

    The header is rewritten in place: maps are plain YAML, and a portable score
    next to the screens is worth more than a log line nobody keeps.
    """
    health = _map_health(output_path, device, package, ops=ops, settle=settle)
    _set_header(output_path, "crawled_at", health["at"])
    _set_header(output_path, "bench", health)
    print(f"bench  : {health['exact']}/{health['ops']} exact landings "
          f"({health['same_app']}/{health['ops']} in the right app)")
    return health


def _set_header(path: str, key: str, value) -> None:
    """Add ``key: value`` to a map's header, replacing an existing one."""
    import yaml as _yaml

    with open(path, encoding="utf-8") as fh:
        data = _yaml.safe_load(fh) or {}
    data[key] = value
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        _yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


def _save_partial(output_path, app_name, package, width, height, screens, macros,
                  launch_intent="", display=0) -> None:
    output = {
        "app": app_name,
        "package": package,
        "screen_w": width,
        "screen_h": height,
        # Which display these coordinates belong to. 0 means the phone's own
        # screen (hands-off): an app driven there can only use macros recorded
        # there, and until this was stored the agent could not tell them apart.
        # The caller passes the display: this helper has no device in scope, and
        # reading one here crashed every partial save ("name 'device' is not
        # defined"), which is exactly the moment the map matters most.
        "display": int(display),
        "screens": screens,
        "screen_macros": macros,
    }
    if launch_intent:
        output["launch_intent"] = launch_intent
    # A partial save must NOT take the place of a finished map: a crawl that
    # dies in its first minute used to replace a 7-screen map with a 1-screen
    # one, because this wrote to output_path. Keep it beside the real file; a
    # successful crawl overwrites that one at the end.
    tmp = f"{output_path}.partial"
    with open(tmp, "w", encoding="utf-8") as fh:
        yaml.safe_dump(output, fh, allow_unicode=True, sort_keys=False)


def _stable_signature(elements, preserve_set=None) -> str:
    """A screen signature built from what does not change between visits.

    The upstream signature keys on element *texts* only, taking the first ten:
    a calculator's result line turns every keypress into a new screen, while two
    genuinely different screens that share ten labels collapse into one. This
    one keys on structure instead -- class, text with digits normalised, and a
    coarse position -- so a changed value stops counting as a new screen and a
    different layout still counts.
    """
    parts = []
    for element in elements or []:
        text = (element.get("text") or "").strip()
        try:
            if text and (_is_dynamic_text(text) or _should_skip_text(text, preserve_set)):
                continue
        except Exception:  # noqa: BLE001 - a signature must never raise
            pass
        key = _VOLATILE_DIGITS.sub("#", text)
        klass = (element.get("class") or element.get("resource_id") or "").split(".")[-1]
        center = element.get("center") or (0.0, 0.0)
        try:
            x, y = round(float(center[0]), 1), round(float(center[1]), 1)
        except (TypeError, ValueError, IndexError):
            x = y = 0.0
        parts.append(f"{klass}:{key}:{x}:{y}")
    if not parts:
        # an almost empty transient (a lone back button) must not merge with
        # every other empty screen
        return f"#{len(elements or [])}"
    return "|".join(sorted(parts))


#: Per app, the packages that legitimately host a page the crawler may reach.
#: HyperOS implements the mobile-network page inside com.android.phone and
#: hands several Settings pages to the security centre, so for Settings those
#: are real destinations. For any other app the same package is just "the crawl
#: left the app": a file-manager crawl recorded the Xiaomi account page as one
#: of its screens exactly that way.
FOREIGN_HOSTS = {
    "com.android.settings": frozenset({
        "com.android.phone",
        "com.miui.securitycenter",
        "com.xiaomi.account",
    }),
}


def _foreign(foreground: str, package: str) -> bool:
    """True when whatever is on screen belongs to an unrelated app.

    A tap that opens the calculator means the coordinates were stale, not that
    the app has a calculator page. Recording that dump puts a foreign screen --
    and its labels -- into the map, where it out-scores the real one at replay
    time. This is how a "settings" map ended up with four calculator screens.
    """
    if not foreground or foreground == package:
        return False
    return foreground not in FOREIGN_HOSTS.get(package, frozenset())


#: Seconds between taps in a crawl (low, high). A person does not tap twice a
#: second, and neither should the crawler.
TAP_GAP = (0.7, 1.6)

#: Fewer elements than this cannot identify a screen at replay time.
MIN_SCREEN_ELEMENTS = 4

#: Labels whose tap changes the world instead of opening a page.
#:
#: The crawl is click-driven: it taps every row it can see, including the one
#: that says "add alarm". One afternoon of crawling the clock left a dozen
#: alarms behind, all of which the user had to delete by hand. Anything that
#: creates, deletes, confirms, sends or buys is skipped; --allow-consequential
#: turns the guard off for an app whose every screen is behind such a button.
CONSEQUENTIAL = re.compile(
    r"(新建|创建|添加|删除|移除|清除|清空|重置|格式化|卸载|恢复出厂"
    r"|安装|更新全部|立即更新|升级"
    r"|确定|保存|提交|发送|拨号|呼叫|支付|购买|下单|充值"
    r"|拍照|录像|录音"
    r"|开始|启动|关机|重启|注销|退出登录|解绑|取消配对"
    # Social actions. A crawl tapping these edits the user's own account:
    # 微博 was liking posts, 点评 was opening its "不喜欢" feedback menu, and
    # neither shows up as a screen change, so nothing else would notice.
    r"|^赞$|^点赞|^转发|^评论$|^关注$|^已关注$|^收藏$|^打赏|^投币|^三连"
    r"|不喜欢|不感兴趣|屏蔽|举报|投诉|拉黑|减少推荐|踩)"
)


def _consequential(text: str) -> bool:
    """Whether tapping this label would change something rather than open it."""
    return bool(CONSEQUENTIAL.search(text or ""))


# bound to the upstream helpers once the crawler module is loaded
_is_dynamic_text = lambda _text: False          # noqa: E731
_should_skip_text = lambda _text, _preserve=None: False  # noqa: E731


def _crawl_verified(bm, controller, device, package, app_name, output_path,
                    max_screens, max_depth, scroll_pages, classify,
                    llm_api_key, llm_api_base, llm_model, enrich=True,
                    max_minutes: float = 0.0, step_timeout: float = 90.0,
                    allow_consequential: bool = False, should_stop=None) -> str:
    """BFS crawl that never taps without knowing what it is tapping.

    Differences from the upstream loop, each one prompted by a real-device
    failure:

    * every tap is resolved against a fresh dump by label; a label that is not
      on screen aborts that branch instead of pressing stale coordinates
    * the screen reached after a tap is compared with the parent's signature;
      an unchanged screen is recorded as "this element does not navigate"
      instead of becoming a phantom screen
    * the foreground package is logged when it changes (HyperOS hands many
      settings pages over to com.miui.securitycenter)
    """
    width, height = controller.get_device_size()
    boot = [
        {"action": "force_stop", "package": package, "wait": 0.5},
        {"action": "launch", "package": package, "wait": 3.0},
    ]

    try:
        elements_0 = _guarded(step_timeout, _collect_screen, controller, bm, scroll_pages)
    except StepTimeout as exc:
        print(f"[Verified] start screen timed out ({exc}); aborting")
        return output_path
    if classify:
        # Tagged, not filtered. Dropping the "dynamic" half of a feed left
        # nothing to tap -- 小红书 produced one screen for exactly this reason
        # (18 of 26 elements discarded as content). They are still useful for
        # exploration; they are only unfit as anchors for matching.
        elements_0 = _tag_dynamic(bm, elements_0, app_name,
                                  llm_api_key, llm_api_base, llm_model)
    if not elements_0:
        # An empty first screen makes a one-screen map with nothing in it, and
        # it would overwrite a good map of the same app. 微博's splash renders
        # nothing for a few seconds; that is a failed start, not a map.
        for attempt in range(2):
            print(f"[Verified] screen_0 came back empty; relaunching "
                  f"({attempt + 1}/2)")
            try:
                device.force_stop(package)
                time.sleep(1.0)
                device.launch(package)
                time.sleep(4.0)
                elements_0 = _collect_screen(controller, bm, scroll_pages=0)
            except (DeviceError, StepTimeout) as exc:
                print(f"[Verified]   relaunch failed: {exc}")
                elements_0 = []
            if elements_0:
                break
        if classify:
            elements_0 = _tag_dynamic(bm, elements_0, app_name,
                                      llm_api_key, llm_api_base, llm_model)
        if not elements_0:
            raise DeviceError(
                "the app shows nothing an accessibility tree can read; "
                "no map written")
    signature_0 = bm._build_signature(elements_0, None)
    print(f"[Verified] screen_0: {len(elements_0)} elements")

    try:
        start_xml = _guarded(step_timeout, device.dump_ui)
        selected_0 = _selected_labels(start_xml)
    except StepTimeout:
        start_xml = ""
        selected_0 = []
    screens = [{
        "id": "screen_0",
        "description": "",
        **({"selected": selected_0} if selected_0 else {}),
        "elements": [{
            "text": e["text"],
            "center": [round(e["nx"], 4), round(e["ny"], 4)],
            "fixed": e.get("fixed", False),
            "found_at_scroll": e.get("found_at_scroll", 0),
            **({"dynamic": True} if e.get("dynamic") else {}),
        } for e in elements_0],
    }]
    signatures = {signature_0: "screen_0"}
    screen_keys = {"screen_0": _fingerprint(elements_0)}
    #: The labels each screen will carry in the map, used to compare screens the
    #: way replay does. Without this the crawl merged on 0.85 similarity while
    #: replay matched on 0.5 coverage, so a page could be stored twice and the
    #: two copies then fought each other at replay time.
    screen_texts = {"screen_0": _anchor_texts(elements_0)}
    #: screen id -> whether it carries an input field (see _has_text_field)
    screen_inputs = {"screen_0": _has_text_field(start_xml)}
    #: screen id -> the tab labels the app marked selected there
    screen_selected = {"screen_0": list(selected_0)}
    macros = {"screen_0": boot}
    queue = [
        (e, ["screen_0"], boot)
        for e in elements_0
        if not bm._is_dynamic(e["text"]) and not bm._should_skip(e["text"], None)
        and (allow_consequential or not _consequential(e["text"]))
    ]

    stats = {"taps": 0, "skipped": 0, "no_op": 0, "foreign": 0, "timeout": 0,
             "blocked": 0, "known": 0, "thin": 0}
    started_at = time.time()

    deadline = (started_at + max_minutes * 60.0) if max_minutes else 0.0
    stopped_early = False
    cancelled = False

    while queue and len(screens) < max_screens:
        if should_stop is not None and should_stop():
            # The user asked to stop. Keep what was built and enrich it: a map
            # with fewer screens is still useful, and losing the whole run
            # because someone changed their mind is not.
            print(f"[Verified] cancelled by the user with {len(screens)} screens")
            cancelled = True
            break
        if deadline and time.time() > deadline:
            # max_screens no longer bounds the work: with overlap matching one
            # screen can take many taps. Stop on the clock, keep what we have,
            # and still enrich it -- an interrupted crawl with descriptions is
            # worth far more than a bare one.
            print(f"[Verified] time budget reached ({max_minutes:.0f} min), "
                  f"stopping with {len(screens)} screens")
            stopped_early = True
            break
        # Touch the app at a human rate: back-to-back taps are the clearest
        # sign a script is driving, and a crawl is already intrusive enough.
        time.sleep(random.uniform(*TAP_GAP))
        element, path, parent_macro = queue.pop(0)
        if len(path) > max_depth:
            continue
        label = element["text"]
        parent_id = path[-1]
        print(f"[Verified] depth={len(path)} tap {label!r} (from {parent_id})")

        # An interposing host window blocks the launch: the crawl tapped into
        # com.xiaomi.account and every later attempt to start Settings found
        # the account page still in front, so the branch aborted. A host that
        # this app is allowed to hand over to is safe to stop.
        try:
            front = device.current_app()
        except DeviceError:
            front = ""
        if front and front != package and front in FOREIGN_HOSTS.get(package, frozenset()):
            print(f"[Verified]   stopping the host {front} that is in the way")
            try:
                device.force_stop(front)
            except DeviceError:
                pass
            time.sleep(0.4)
        device.force_stop(package)
        time.sleep(0.3)
        # Whatever else is in front goes too: a crawl that starts on top of a
        # previous app spends its first branches unwinding it.
        try:
            front = device.current_app()
        except DeviceError:
            front = ""
        if front == AGENT_PACKAGE:
            # In hands-off mode the crawl drives the phone's own display, and
            # the app it is running in is what sits in front. Force-stopping it
            # stops the crawl (measured: the process died one second after the
            # first tap), so it is sent to the background instead -- the
            # foreground service keeps it alive.
            try:
                device.press_home()
                print("[Verified]   sent the agent's own app to the background")
            except DeviceError:
                pass
        elif front and front != package and front not in (
                "com.android.systemui", "com.miui.home", "com.android.launcher3"):
            try:
                device.force_stop(front)
                print(f"[Verified]   cleared {front} from the display")
            except DeviceError:
                pass
        # Honour the verdict: HyperOS answers android.settings.SETTINGS with
        # com.android.phone's mobile-network page, so an unchecked intent launch
        # starts every branch in the wrong app -- which is exactly how the
        # calculator got into this map. Falling back to the package resolves the
        # activity by component.
        landed = device.launch(package, intent=controller.launch_intent)
        if not landed:
            landed = device.launch(package)
        if not landed:
            stats["foreign"] += 1
            print(f"[Verified]   cannot bring {package} up; skipping this branch")
            continue
        # launch() already polled until the app was in front, so the old
        # three-second "give it time" was paying twice for the same wait.
        time.sleep(0.8)
        try:
            _guarded(step_timeout, _replay_path, device, parent_macro)
            xml = _guarded(step_timeout, device.dump_ui)
        except StepTimeout as exc:
            stats["timeout"] += 1
            print(f"[Verified]   {exc}; skipping this branch")
            continue
        parent_sig = bm._build_signature(
            bm._parse_elements_android(xml, width, height, None), None)
        # Act first, verify after -- the way the original crawler does.
        #
        # This used to look the label up and skip the branch when it was not
        # found. Ambiguous labels (a tab's name is also the page title) were
        # therefore never tapped, and whole tab pages went missing without a
        # single error: the log said "skipped", the map said one screen. The
        # coordinate the element was recorded at is the better first attempt.
        # The element may have been collected on a scrolled page: the app was
        # restarted for this branch, so the list is back at the top and the
        # recorded point is now empty space. The upstream builder scrolls to
        # `found_at_scroll` first for exactly this reason; without it the tap
        # lands on nothing, which is most of the "no_op" count.
        pages = int(element.get("found_at_scroll", 0) or 0)
        for _ in range(pages):
            controller.swipe(width // 2, int(height * 0.7), "up", dist=450)
            time.sleep(0.5)
        if pages:
            print(f"[Verified]   scrolled {pages} page(s) back to {label!r}")
            try:
                xml = _guarded(step_timeout, device.dump_ui)
            except StepTimeout:
                pass
        point = (int(element["cx"]), int(element["cy"]))
        tapped = point
        device.tap(*point)
        stats["taps"] += 1
        time.sleep(1.5)
        # A search page usually appears *after* the tap: on 小红书 the thing you
        # tap is a magnifier icon, and the text field only exists on the page it
        # opens. So look again, and type into whatever field is there now.
        after = ""
        try:
            after = _guarded(step_timeout, device.dump_ui)
        except StepTimeout:
            pass
        reached_field = bool(after) and any(
            node.editable or "EditText" in (node.klass or "")
            for node in parse_nodes(after)
        ) and not _editable_at(xml, point[0], point[1])
        if reached_field:
            try:
                device.input_text(PROBE_WORD)
                stats["typed"] = stats.get("typed", 0) + 1
                print(f"[Verified]   typed {PROBE_WORD!r} on the page {label!r} opened")
                time.sleep(1.8)
            except Exception as exc:  # noqa: BLE001 - typing is best effort
                print(f"[Verified]   could not type after {label!r}: {exc}")
        if _editable_at(xml, point[0], point[1]):
            # A search box opens an empty page with a keyboard, which is the
            # same screen as the one behind it by every measure the crawl uses.
            # Typing a word turns it into a results page -- a real, distinct
            # screen -- and that is the page an operation should land on.
            try:
                device.input_text(PROBE_WORD)
                stats["typed"] = stats.get("typed", 0) + 1
                print(f"[Verified]   typed {PROBE_WORD!r} into {label!r}")
                time.sleep(1.5)
            except Exception as exc:  # noqa: BLE001 - typing is best effort
                print(f"[Verified]   could not type into {label!r}: {exc}")
        if not _screen_moved(device, step_timeout, parent_sig, bm, width, height):
            # Nothing happened: the layout drifted, or the label sits on a
            # heading. Ask where it is now and tap that instead -- once.
            try:
                xml = _guarded(step_timeout, device.dump_ui)
            except StepTimeout:
                xml = ""
            node = (find_label(xml, label) or _nearest_label(
                xml, label, element["cx"], element["cy"])) if xml else None
            if node is not None and abs(node.center[0] - point[0]) + \
                    abs(node.center[1] - point[1]) > 8:
                print(f"[Verified]   {label!r} was at {point}, tapping {node.center}")
                device.tap(*node.center)
                tapped = (int(node.center[0]), int(node.center[1]))
                stats["taps"] += 1
                time.sleep(1.5)
            else:
                stats["no_op"] = stats.get("no_op", 0) + 1
                print(f"[Verified]   tapping {label!r} at {point} changed nothing")
        # The verified crawler taps the device directly, so the controller's
        # own hooks never fire here -- this is the only place a picture of the
        # new screen can be published.
        controller._publish()

        foreground = device.current_app()
        if _foreign(foreground, package):
            stats["foreign"] += 1
            print(f"[Verified]   left the app for {foreground}; not recording this screen")
            continue
        if foreground and foreground != package:
            print(f"[Verified]   note: foreground is {foreground}")

        # A cheap look first. Most branches land on a screen the map already
        # has, and collecting that screen again -- three swipes, three dumps,
        # an LLM classification -- was the single largest cost of a crawl.
        quick_xml_package = ""
        try:
            quick_xml = _guarded(step_timeout, device.dump_ui)
            quick_xml_package = _dump_package(quick_xml)
            quick_elements = bm._parse_elements_android(quick_xml, width, height, None)
        except StepTimeout:
            quick_elements = None
        # The foreground can change between the tap and the dump: an account
        # page opening in a webview looks like the same app for a moment. The map
        # then keeps a screen from another package, and at replay time that
        # screen out-scores the real one -- this is how a file-manager map ended
        # up with a Xiaomi account page as its second screen.
        if quick_xml_package and quick_xml_package != package \
                and quick_xml_package not in FOREIGN_HOSTS.get(package, frozenset()) \
                and quick_xml_package not in DIALOG_PACKAGES:
            stats["foreign"] += 1
            print(f"[Verified]   the dump is {quick_xml_package}, not {package}; "
                  "not recording this screen")
            continue
        if quick_elements:
            matched, score = _match_unified(screen_keys, screen_texts,
                                            quick_elements,
                                            _selected_labels(quick_xml),
                                            _has_text_field(quick_xml),
                                            screen_inputs, screen_selected)
            if matched is not None:
                stats["known"] += 1
                print(f"[Verified]   known screen {matched} (overlap {score:.2f}, no scroll)")
                _record_edge(screens, parent_id, label, matched)
                continue

        # Collect with scrolling so folded elements are recorded with the
        # number of swipes needed to reach them.
        try:
            new_elements = _guarded(step_timeout, _collect_screen, controller, bm, scroll_pages)
        except StepTimeout as exc:
            stats["timeout"] += 1
            print(f"[Verified]   {exc}; skipping this branch")
            continue
        # dedupe by text, same as upstream
        seen, deduped = set(), []
        for e in new_elements:
            if e["text"] in seen:
                continue
            seen.add(e["text"])
            deduped.append(e)
        new_elements = deduped
        if not new_elements:
            continue
        # A screen with one or two elements is not a screen: it is a page
        # caught mid-load, or a dialog's remains. Recorded, it can never be
        # matched again (nothing to match on) and it eats one of the budget's
        # slots -- measured: a settings map carried two such screens, one of
        # them literally "a minimal screen containing only a back button".
        if len(new_elements) < MIN_SCREEN_ELEMENTS:
            stats["thin"] = stats.get("thin", 0) + 1
            print(f"[Verified]   only {len(new_elements)} element(s); not a screen, skipping")
            continue

        # A screen is recorded from one dump, and a dump catches whatever is
        # passing through: a "刷新完成" toast, a spinner label. Those texts are
        # gone a second later, so a map built from them can never match again
        # (a file-manager map kept "刷新完成" and then identified nothing).
        # Take a second dump and keep only what is on both.
        selected_new: list = []
        try:
            again = _guarded(step_timeout, device.dump_ui)
            selected_new = _selected_labels(again)
            again_texts = {
                e["text"] for e in bm._parse_elements_android(again, width, height, None)
            }
            stable = [e for e in new_elements if e["text"] in again_texts]
            # only trust the filter when it leaves enough to identify a screen
            if len(stable) >= max(3, len(new_elements) // 3):
                if len(stable) != len(new_elements):
                    print(f"[Verified]   dropped {len(new_elements) - len(stable)} "
                          "transient element(s)")
                new_elements = stable
        except StepTimeout:
            pass
        # Checked again after the stability filter: a five-element page can lose
        # two transient ones and become a screen nothing can match (measured:
        # six such screens survived a crawl whose check ran only before it).
        if len(new_elements) < MIN_SCREEN_ELEMENTS:
            stats["thin"] = stats.get("thin", 0) + 1
            print(f"[Verified]   only {len(new_elements)} element(s) after filtering; "
                  "not a screen, skipping")
            continue

        signature = bm._build_signature(new_elements, None)
        if signature == parent_sig:
            print("[Verified]   no-op: the screen did not change")
            stats["no_op"] += 1
            continue

        if classify:
            new_elements = _tag_dynamic(bm, new_elements, app_name,
                                        llm_api_key, llm_api_base, llm_model)

        matched_id, score = _match_unified(screen_keys, screen_texts, new_elements,
                                           selected_new, _has_text_field(again),
                                           screen_inputs, screen_selected)
        if matched_id is None and _content_heavy(new_elements):
            # A feed that reloads with different posts is the same screen, not a
            # new one. Positional fingerprints cannot see that -- every card
            # moved -- but the chrome did not, and the chrome is what a screen
            # is identified by at replay time. Measured: 21 of 点评's 27 screens
            # and 36 of 小红书's 45 were the same page with different content.
            mine = _stable_keys(new_elements)
            twin = _same_chrome(mine, screens)
            if twin is not None:
                print(f"[Verified]   content-only page; counted as {twin}")
                stats["merged"] = stats.get("merged", 0) + 1
                matched_id = twin
        if matched_id is not None:
            target_id = matched_id
            print(f"[Verified]   known screen {target_id} (overlap {score:.2f})")
        else:
            target_id = f"screen_{len(screens)}"
            signatures[signature] = target_id
            screen_keys[target_id] = _fingerprint(new_elements)
            screen_texts[target_id] = _anchor_texts(new_elements) | set(selected_new)
            screen_inputs[target_id] = _has_text_field(again)
            screen_selected[target_id] = list(selected_new)
            # Keep what we have: a crawl that dies three hours in should not lose
            # every screen it already found.
            try:
                _save_partial(output_path, app_name, package, width, height,
                              screens, macros, controller.launch_intent,
                              display=0 if controller.display_id is None
                              else int(controller.display_id))
                print(f"[Verified]   partial map saved ({len(screens)} screens)")
            except Exception as exc:  # noqa: BLE001
                print(f"[Verified]   partial save failed: {exc}")
            scroll_steps = [
                {"action": "swipe", "x1": width // 2, "y1": int(height * 0.7),
                 "x2": width // 2, "y2": int(height * 0.2), "duration": 400, "wait": 0.5}
                for _ in range(max(0, int(element.get("found_at_scroll", 0))))
            ]
            # The macro records the point that was actually tapped: the first
            # attempt, or the retry when the first one did nothing.
            new_macro = list(parent_macro) + scroll_steps + [{
                "action": "tap", "x": int(tapped[0]), "y": int(tapped[1]),
                "text": label, "wait": 1.5,
                # which screen this tap was recorded on: replay checks it before
                # pressing, because the same label exists on other screens and
                # calling up the wrong one is how a correct-looking macro lands
                # somewhere else
                "screen": parent_id,
            }]
            macros[target_id] = new_macro
            screens.append({
                "id": target_id,
                "description": "",
                **({"selected": selected_new} if selected_new else {}),
                "package": foreground if foreground and foreground != package else "",
                "elements": [{
                    "text": e["text"],
                    "center": [round(e["nx"], 4), round(e["ny"], 4)],
                    "fixed": e.get("fixed", False),
                    **({"dynamic": True} if e.get("dynamic") else {}),
                    "found_at_scroll": e.get("found_at_scroll", 0),
                } for e in new_elements],
            })
            print(f"[Verified]   new screen {target_id} ({len(new_elements)} elements)")
            # Explore the chrome, not the content. The classifier already
            # answered "will this still be here next time"; tapping the ones it
            # called dynamic is how a crawl spends its budget on review text and
            # promo cards and ends up with nine variants of the same feed. The
            # elements stay in the map -- a task may still want to tap a card.
            explorable = [e for e in new_elements
                          if not e.get("dynamic")
                          and not bm._is_dynamic(e["text"])
                          and not bm._should_skip(e["text"], None)]
            if not explorable:
                # Nothing but content: this screen is a dead end unless a card
                # can be opened, so allow a couple.
                explorable = [e for e in new_elements
                              if not bm._should_skip(e["text"], None)][:2]
            for e in explorable:
                if not allow_consequential and _consequential(e["text"]):
                    # Counted apart from "not on screen": these are the taps
                    # the guard refused, and they are the reason a map of a
                    # create-something app comes out thin.
                    stats["blocked"] += 1
                    continue
                queue.append((e, path + [target_id], new_macro))

        # same rule: an edge to the screen we started from is not navigation
        if target_id != parent_id:
            for screen in screens:
                if screen["id"] == parent_id:
                    for entry in screen["elements"]:
                        if entry["text"] == label:
                            entry["leads_to"] = target_id
                    break

    # How much of the app is left. "12 screens" says nothing on its own: the
    # same number is "that is everything" for a clock and "a third of it" for
    # settings, and only the queue knows which.
    remaining = len(queue)
    reason = ("cancelled" if cancelled
              else "time_budget" if stopped_early
              else "screen_limit" if len(screens) >= max_screens
              else "queue_empty")
    coverage = {
        "screens": len(screens),
        "queued": remaining,
        "depth_limit": max_depth,
        "stopped": reason,
        "complete": reason == "queue_empty",
    }
    print(f"[Verified] coverage {len(screens)} screens, {remaining} unvisited "
          f"(stopped by {reason}, depth limit {max_depth})")
    for element, path, _macro in queue[:5]:
        print(f"[Verified]   unvisited: depth={len(path)} {element['text']!r}")

    output = {
        "app": app_name,
        "package": package,
        "screen_w": width,
        "screen_h": height,
        "screens": screens,
        "screen_macros": macros,
        "coverage": coverage,
    }
    if controller.launch_intent:
        output["launch_intent"] = controller.launch_intent

    # The verified path used to stop here, which meant every map it produced had
    # no descriptions, aliases or semantic types -- the same upstream enrichment
    # that makes build_app_map useful was simply never called. Reuse it rather
    # than trade verified edges for descriptions.
    if enrich and screens:
        try:
            bm._enrich_map(
                output,
                app_name,
                api_key=llm_api_key,
                api_base=llm_api_base,
                model=llm_model,
            )
        except Exception as exc:  # noqa: BLE001 - a map without descriptions still works
            print(f"[Verified] enrichment failed ({exc}); saving without it")

    # A cancelled or truncated run writes beside the real map instead of over
    # it: the file in use may be a better one, and losing it because someone
    # pressed 取消 is not a trade anyone agreed to.
    target = output_path
    if cancelled:
        target = output_path + ".partial"
        print(f"[Verified] cancelled: keeping the map in use, writing {target}")
    with open(target, "w", encoding="utf-8") as fh:
        yaml.safe_dump(output, fh, allow_unicode=True, sort_keys=False)

    if stopped_early:
        stats["stopped_by"] = "time_budget"
    if cancelled:
        stats["stopped_by"] = "cancelled"
    print(f"[Verified] {stats} -> {output_path}")
    return output_path


def bm_yaml_screens(path: str):
    """Load just the screens list of a generated map."""
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("screens", [])


def _radio_state(device: PhoneDevice) -> dict[str, str]:
    keys = {
        "wifi": "settings get global wifi_on",
        "bluetooth": "settings get global bluetooth_on",
        "airplane": "settings get global airplane_mode_on",
    }
    out = {}
    for name, command in keys.items():
        try:
            out[name] = device.shell(command).strip()
        except DeviceError:
            out[name] = "?"
    return out


#: Windows that sit on top of the app without being another app. A search box
#: raises the keyboard, and the keyboard's own window is the first node in the
#: dump -- which made every search page look like "left the app for unknown"
#: and kept the crawl from ever recording one.
CHROME_PACKAGES = ("com.android.systemui", "inputmethod", "com.google.android.inputmethod",
                   "com.sohu.inputmethod", "com.baidu.input", "com.iflytek.inputmethod",
                   "com.miui.inputmethod")


def _dump_package(xml: str) -> str:
    """The package of the window a dump actually captured.

    dumpsys can report the target app in front while uiautomator returns
    another window -- an app on a second display wins the accessibility focus,
    and the crawler then records screens it cannot even see. The dump is what
    the crawl reads, so the dump is what has to be checked.

    A keyboard counts as the app it is typing into: its window comes first, and
    treating it as a foreign app hid every search page from the crawl.
    """
    seen = re.findall(r'package="([^"]+)"', xml or "")
    if not seen:
        return ""
    for name in seen:
        if not any(chrome in name for chrome in CHROME_PACKAGES):
            return name
    return seen[0]


#: Packages that put a system dialog in front of the app being crawled. The
#: dump then belongs to the dialog, so "the foreground is not the app" is the
#: wrong conclusion -- and force-stopping again just shows the dialog again,
#: which is how a calendar crawl aborted three times in a row.
DIALOG_PACKAGES = frozenset({
    "com.android.permissioncontroller",
    "com.google.android.permissioncontroller",
    "android",
})

#: Labels that dismiss such a dialog in favour of the app.
GRANT_LABELS = ("始终允许", "本次允许", "使用时允许", "允许", "启动应用", "同意")


#: Labels that appear next to a grant button on a permission prompt.
DENY_LABELS = ("拒绝", "取消", "不允许", "禁止", "退出", "以后再说")


def _looks_like_dialog(xml: str) -> bool:
    """A prompt, whichever package is hosting it.

    MIUI asks for permissions through its own security-centre dialog, so the
    package name alone does not identify a prompt -- but the pair of a grant
    button and a deny button does.
    """
    if not xml or 'package="' not in xml:
        return False
    has_grant = any(label in xml for label in GRANT_LABELS)
    has_deny = any(label in xml for label in DENY_LABELS)
    return has_grant and has_deny


def _dismiss_dialog(device: PhoneDevice, xml: str) -> bool:
    """Get rid of an interposing dialog; True if a button was pressed.

    Deny first. A prompt in front of a crawl is usually a third-party app
    asking for consent (Xiaomi's wallpaper carousel greets every fresh device
    that way), and agreeing to someone's privacy policy is not something a
    crawler should do on the user's behalf.
    """
    for label in DENY_LABELS + GRANT_LABELS:
        node = find_label(xml, label)
        if node is None:
            continue
        try:
            device.tap(*node.center)
        except DeviceError:
            return False
        print(f"  start: dismissed a system dialog by tapping {label!r}")
        return True
    return False


def _ensure_home(device: PhoneDevice, package: str, launch_intent: str,
                 expect: str, attempts: int = 3) -> bool:
    """Bring the app to a screen that actually contains `expect`.

    A promo dialog or a resumed sub-page means the crawl would start from the
    wrong screen and every edge after it inherits the mistake.
    """
    for attempt in range(1, attempts + 1):
        try:
            device.force_stop(package)
            time.sleep(0.5)
            device.launch(package, intent=launch_intent)
            time.sleep(3.5)
            # The accessibility tree lags the window: right after a cold start
            # the dump is empty or still belongs to the previous screen, and an
            # empty dump must not be read as "a dialog is blocking".
            xml = ""
            for _ in range(4):
                xml = device.dump_ui()
                if _dump_package(xml):
                    break
                time.sleep(1.0)
        except NeedsHandover as exc:
            # The app put itself on the user's display. That is not a reason to
            # throw the whole crawl away: pull it back and let the caller try
            # the next branch. Measured on 小红书, which had reached seven
            # screens when the first handover aborted a twelve-minute run.
            print(f"  cannot start {exc.package} here: {exc.reason}")
            print("  bringing it back to the agent's display and continuing")
            try:
                device.force_stop(exc.package)
                time.sleep(0.5)
                back = getattr(device, "return_to_own_display", None)
                if back is not None:
                    back()
                device.launch(package, intent=launch_intent)
                time.sleep(2.0)
            except Exception:  # noqa: BLE001 - housekeeping
                pass
            return False
        except LaunchRefused as exc:
            print(f"  cannot start {exc.package} here: {exc.reason}")
            print("  this app's home activity is pinned to its own task; aborting")
            # Leave the phone as we found it: some launch attempts end with the
            # app sitting on the user's own screen (短信 did), and the crawl
            # should not hand back a phone with a stranger's app in front.
            try:
                device.force_stop(exc.package)
            except Exception:  # noqa: BLE001 - housekeeping, not part of the crawl
                pass
            return False
        except DeviceError as exc:
            print(f"  start attempt {attempt} failed: {exc}")
            continue
        dumped = _dump_package(xml)
        if not dumped:
            print(f"  start attempt {attempt}: no package in the dump: {xml[:160]!r}")
        if dumped in DIALOG_PACKAGES or _looks_like_dialog(xml):
            # A permission prompt is in front. Grant it and look again without
            # relaunching: a relaunch only brings the dialog back.
            if _dismiss_dialog(device, xml):
                time.sleep(1.5)
                try:
                    xml = device.dump_ui()
                except DeviceError:
                    xml = ""
                dumped = _dump_package(xml)
            if dumped in DIALOG_PACKAGES or _looks_like_dialog(xml):
                print(f"  start attempt {attempt}: a system dialog ({dumped}) blocks the app")
                started = False
                continue
        if dumped and dumped != package:
            print(f"  start attempt {attempt}: the dump is {dumped}, not {package}")
            # Something else is in front and pressing back did not move it.
            # Stop it -- but never the shell of the phone itself.
            if dumped not in ("android", "com.android.systemui", "com.miui.home"):
                try:
                    device.force_stop(dumped)
                    time.sleep(1.0)
                except DeviceError:
                    pass
            started = False
        elif expect:
            started = expect in xml
        else:
            # With no expected text to look for, require the target app itself
            # to be in front. Without this the crawl happily records whatever
            # happened to be on screen (it once recorded our own chat UI as the
            # calculator).
            try:
                started = device.current_app() == package
            except DeviceError:
                started = False
        if started:
            return True
        print(f"  start attempt {attempt}: '{expect}' not on screen, pressing back")
        try:
            device.press_back()
        except DeviceError:
            pass
        time.sleep(1.0)
    return False


def _keep_awake(device: PhoneDevice, enabled: bool) -> Optional[str]:
    if not enabled:
        return None
    try:
        previous = device.shell("settings get global stay_on_while_plugged_in").strip()
        device.shell("svc power stayon true")
        device.shell("input keyevent 224")  # WAKEUP
        time.sleep(0.8)
        device.shell("wm dismiss-keyguard")
        time.sleep(1.0)
        return previous
    except DeviceError as exc:
        print(f"  warning: could not keep the screen awake: {exc}")
        return None


def _restore_awake(device: PhoneDevice, previous: Optional[str]) -> None:
    if previous is None:
        return
    try:
        if previous.isdigit():
            device.shell(f"settings put global stay_on_while_plugged_in {previous}")
        else:
            device.shell("svc power stayon false")
    except DeviceError:
        pass


# ---------------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    device: Optional[PhoneDevice] = None,
    on_screen=None,
    should_stop=None,
) -> int:
    """Crawl one app into a map.

    ``device`` lets the app pass the virtual display it already owns instead of
    opening a fresh adb connection to display 0: the same crawl code then works
    on a screen the user is not looking at.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--adb", default=None)
    parser.add_argument("--package", "-p", required=True)
    parser.add_argument("--app", "-a", default="")
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--max-screens", type=int, default=50)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--no-bench", action="store_true",
                        help="skip the self-measurement at the end of a crawl")
    parser.add_argument("--bench-ops", type=int, default=5,
                        help="how many operations to replay to score the map")
    parser.add_argument("--scroll-pages", type=int, default=3)
    parser.add_argument("--step-timeout", type=float, default=90.0,
                        help="abandon one crawl step after this long (seconds)")
    parser.add_argument("--max-minutes", type=float, default=0.0,
                        help="stop the crawl after this long (0 = no limit); "
                             "what was found is kept and still enriched")
    parser.add_argument("--no-classify", action="store_true")
    parser.add_argument("--verified", dest="verified", action="store_true", default=True,
                        help="label-anchored taps with post-tap verification (default)")
    parser.add_argument("--no-verified", dest="verified", action="store_false",
                        help="use the upstream open-loop crawl")
    parser.add_argument("--no-enrich", action="store_true")
    parser.add_argument("--llm-api-key", default="EMPTY")
    parser.add_argument("--llm-api-base", default="http://localhost:8002/v1")
    parser.add_argument("--llm-model", default="Qwen/Qwen2.5-3B-Instruct")
    parser.add_argument("--llm-max-tokens", type=int, default=4000)
    parser.add_argument("--no-disable-reasoning", action="store_true")
    parser.add_argument("--expect-home", default="", help="text that must be on the start screen")
    parser.add_argument("--allow-consequential", action="store_true",
                        help="also tap labels like 添加/删除/确定 (the crawl will "
                             "then change the device, e.g. leave alarms behind)")
    parser.add_argument("--redact", dest="redact", action="store_true", default=True,
                        help="strip device-specific data from the map (default)")
    parser.add_argument("--no-redact", dest="redact", action="store_false")
    parser.add_argument("--redact-term", action="append", default=[],
                        help="extra literal string to redact (repeatable)")
    parser.add_argument("--launch-intent", default="", help="intent action that opens the app home")
    parser.add_argument("--keep-awake", dest="keep_awake", action="store_true", default=True)
    parser.add_argument("--no-keep-awake", dest="keep_awake", action="store_false")
    args = parser.parse_args(argv)

    device = device or AdbDevice(serial=args.serial, adb=args.adb)
    info = device.info()
    print(f"device : {info.model} / Android {info.os_version} / {info.screen_width}x{info.screen_height}")

    before = _radio_state(device)
    print(f"radios : before {before}")

    previous_awake = _keep_awake(device, args.keep_awake)
    # Always verify the start, not only when --expect-home is given: without a
    # check the crawl records whatever happens to be on screen, which is how a
    # 时钟 crawl once recorded the calendar, and an earlier one recorded our own
    # chat UI.
    check = args.expect_home or "the foreground app"
    ok = _ensure_home(device, args.package, args.launch_intent, args.expect_home)
    print(f"start  : home check {'PASS' if ok else 'FAIL'} ({check})")
    if not ok:
        print("  aborting: the target app never came to the front")
        _restore_awake(device, previous_awake)
        return 2
    if args.keep_awake:
        print("screen : stay-on enabled, keyguard dismissed")

    build_app_map = _load_crawler()
    bm = sys.modules["phonecli_android_build_map"]

    # Screen dedup is the difference between a useful map and 30 copies of the
    # same calculator, so replace the upstream signature with the structural one
    # for both crawl paths.
    global _is_dynamic_text, _should_skip_text
    _is_dynamic_text = bm._is_dynamic
    _should_skip_text = bm._should_skip
    bm._build_signature = _stable_signature
    print("dedup  : structural signature (class + normalised text + position)")

    # Route the crawler's LLM calls through our client so reasoning can be
    # turned off: with reasoning on, qwen3.8-flash spends the whole budget
    # thinking and returns empty content, which silently collapses the map
    # (every element classified as dynamic).
    if args.llm_api_key and not args.no_disable_reasoning:
        # make_llm, not OpenAICompatClient: the protocol follows the base URL,
        # and on DeepSeek's Anthropic endpoint the direct client posted to
        # /chat/completions and got a 404 -- the enrichment silently produced a
        # map with no descriptions at all.
        from .server import make_llm

        client = make_llm(args.llm_api_key, args.llm_api_base, args.llm_model)

        def _shim(system_prompt, user_prompt, **kwargs):
            return client.text(system_prompt, user_prompt,
                               temperature=kwargs.get("temperature", 0.0))

        crawler_module = sys.modules["phonecli_android_build_map"]
        crawler_module.text_completion = _shim
        print(f"llm    : {args.llm_model} (reasoning off, max_tokens={args.llm_max_tokens})")
    controller = DeviceController(device, launch_intent=args.launch_intent)
    controller.on_screen = on_screen      # lets the app show each screen it reaches
    started = time.time()
    try:
        if args.verified:
            output = _crawl_verified(
                bm, controller, device, args.package, args.app or args.package,
                args.output, args.max_screens, args.max_depth, args.scroll_pages,
                not args.no_classify, args.llm_api_key, args.llm_api_base,
                args.llm_model, enrich=not args.no_enrich,
                max_minutes=args.max_minutes, step_timeout=args.step_timeout,
                allow_consequential=args.allow_consequential,
                should_stop=should_stop,
            )
        else:
            output = build_app_map(
            controller=controller,
            package=args.package,
            app_name=args.app or args.package,
            output_path=args.output,
            max_screens=args.max_screens,
            max_depth=args.max_depth,
            scroll_pages=args.scroll_pages,
            classify=not args.no_classify,
            enrich=not args.no_enrich,
            llm_api_key=args.llm_api_key,
            llm_api_base=args.llm_api_base,
                llm_model=args.llm_model,
            )
    finally:
        _restore_awake(device, previous_awake)

    # record the launch intent so replay can use it too
    if args.launch_intent and os.path.exists(output):
        with open(output, "r", encoding="utf-8") as fh:
            text = fh.read()
        if "launch_intent:" not in text:
            text = text.replace(
                f"package: {args.package}\n",
                f"package: {args.package}\nlaunch_intent: {args.launch_intent}\n",
                1,
            )
            with open(output, "w", encoding="utf-8") as fh:
                fh.write(text)

    # A map that has never been replayed is a guess. Measure a handful of its
    # own operations now, while the app is still on the display, and record the
    # score with the map: "7 screens, 4/5 landings" is a fact the next person
    # can act on, where "7 screens" alone is not.
    if os.path.exists(output) and not args.no_bench:
        try:
            _record_map_health(output, device, args.package,
                               ops=int(args.bench_ops or 5),
                               settle=1.5)
        except Exception as exc:  # noqa: BLE001 - a score must not fail a crawl
            print(f"bench  : skipped ({type(exc).__name__}: {exc})")

    elapsed = time.time() - started
    after = _radio_state(device)
    print(f"radios : after  {after}")
    if after != before:
        changed = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
        print(f"  WARNING radios changed during the crawl: {changed}")

    if args.redact:
        from .redact import auto_terms, name_candidates, redact_map

        terms = list(args.redact_term) + auto_terms(device)
        try:
            texts = [e["text"] for screen in bm_yaml_screens(output)
                     for e in screen.get("elements", [])]
            for candidate in name_candidates(texts, terms):
                if candidate not in terms:
                    terms.append(candidate)
        except Exception:
            pass
        public = str(Path(output).with_suffix(".public.yaml"))
        report = redact_map(output, terms, out=public)
        print(f"redact : {report['elements']} elements -> {public} "
              f"(working map kept unredacted: it is what replay needs)")

    print(f"taps   : {controller.verified_taps} verified by label, {controller.blind_taps} blind")
    print(f"map    : {output}  ({elapsed:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
