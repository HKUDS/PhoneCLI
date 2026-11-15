"""Android app map builder — crawl an Android app via ADB and generate a YAML app map.

Algorithm: BFS crawl from screen_0.
  1. Launch app, dump + scroll screen_0.
  2. BFS: for each element, navigate via FULL path replay, tap, dump new screen.
  3. Record edges (leads_to) and macros (full paths from screen_0).
  4. Optional LLM classification to filter dynamic content (STABLE vs DYNAMIC).
  5. Output compact YAML (platform-agnostic action format).

Usage:
    from evaluation.build_map import build_app_map
    from utils_mobile.and_controller import AndroidController

    controller = AndroidController("emulator-5554")
    build_app_map(
        controller=controller,
        package="com.android.settings",
        app_name="Settings",
        output_path="./app_maps/settings_android.yaml",
    )
"""

# The app runs this file on Python 3.8 (Chaquopy), where `list[str]` in an
# annotation is evaluated at def time and fails; the future import makes
# annotations strings on every version.
from __future__ import annotations

import json
import os
import re
import time
from typing import Optional

import yaml

from phonecli.llm_client import text_completion
from phonecli.prompts import (
    ELEMENT_CLASSIFY_PROMPT,
    ELEMENT_ENRICH_PROMPT,
    SCREEN_ENRICH_PROMPT,
    APP_ENRICH_PROMPT,
)


# ---------------------------------------------------------------------------
# Dynamic content detection patterns
# ---------------------------------------------------------------------------

_DYNAMIC_PATTERNS = [
    (re.compile(p, re.IGNORECASE), label)
    for p, label in [
        (r'^\d{1,2}:\d{2}(:\d{2})?\s*(AM|PM)?$', "time"),
        (r'^\d{1,2}:\d{2}\s*[-–]\s*\d{1,2}:\d{2}$', "duration"),
        (r'^\d{1,2}/\d{1,2}/\d{2,4}$', "date"),
        (r'^\d+%$', "percentage"),
        (r'^\d{1,3}(,\d{3})*$', "number"),
        (r'^\d+\s*(seconds|minutes|hours|days|weeks|months|years)\s*ago$', "relative_time"),
        (r'^\d+(\.\d+)?\s*(GB|MB|KB|TB|B)$', "size"),
        (r'^\d+[\.\d]*\s*(mi|km|m|ft)\b', "distance"),
        (r'^[0-9a-f]{8}-[0-9a-f]{4}', "uuid"),
        (r'^\d{4}[-/]\d{2}[-/]\d{2}$', "date_iso"),
        (r'^\d{1,3}(,\d{3})*(\.\d+)?\s*(元|¥|USD|EUR|£)', "price"),
        (r'^[0-9a-f]{6,}$', "hash"),
    ]
]

SKIP_TEXTS = {
    "", ".", "..", "...", "•", "-", "—", "/", "|", "*",
    "null", "none", "undefined", "true", "false",
}


def _is_dynamic(text: str) -> bool:
    """Check if text matches any dynamic content pattern."""
    for pattern, _ in _DYNAMIC_PATTERNS:
        if pattern.match(text):
            return True
    return False


def _should_skip(text: str, preserve_set: set = None) -> bool:
    """Determine if text should be skipped during crawling."""
    if not text or text.strip() in SKIP_TEXTS:
        return True
    text = text.strip()
    if preserve_set and text in preserve_set:
        return False
    if len(text) > 100:
        return True
    return False


# ---------------------------------------------------------------------------
# Android UIAutomator XML parsing
# ---------------------------------------------------------------------------

def _parse_elements_android(xml_str: str, screen_w: int, screen_h: int,
                             preserve_set: set = None) -> list[dict]:
    """Parse Android UIAutomator XML and extract interactive elements.

    Returns list of dicts with keys: text, cx, cy, bounds.
    Coordinates are normalized to [0, 1].
    """
    elements = []
    seen = set()

    # Match self-closing and non-self-closing node elements
    for match in re.finditer(r'<node\b([^>]*?)(?:/>|>)', xml_str):
        attrs = match.group(1)
        klass = re.search(r'class="([^"]*)"', attrs)
        klass = klass.group(1) if klass else ""

        # Only extract interactive or display elements
        interactive_classes = [
            'Button', 'TextView', 'EditText', 'ImageView', 'ImageButton',
            'CheckBox', 'RadioButton', 'ToggleButton', 'Switch',
            'CheckedTextView', 'CompoundButton',
        ]
        is_interactive = any(ic in klass for ic in interactive_classes)

        # Also check clickable/focusable
        clickable = re.search(r'clickable="([^"]*)"', attrs)
        clickable = clickable and clickable.group(1) == "true"
        focusable = re.search(r'focusable="([^"]*)"', attrs)
        focusable = focusable and focusable.group(1) == "true"

        if not (is_interactive or clickable or focusable):
            continue

        # Extract text
        text = ""
        for attr_name in ["text", "content-desc"]:
            m = re.search(rf'{attr_name}="([^"]*)"', attrs)
            if m and m.group(1).strip():
                text = m.group(1).strip()
                break

        if _should_skip(text, preserve_set):
            continue

        # Extract bounds
        bounds_match = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', attrs)
        if not bounds_match:
            continue
        x1, y1, x2, y2 = map(int, bounds_match.groups())
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        # Normalize
        nx = cx / screen_w
        ny = cy / screen_h

        # Deduplicate (5px grid)
        key = (round(nx, 4), round(ny, 4))
        if key in seen:
            continue
        seen.add(key)

        elements.append({
            "text": text,
            "cx": cx, "cy": cy,
            "nx": nx, "ny": ny,
            "bounds": (x1, y1, x2, y2),
        })

    return elements


def _build_signature(elements: list[dict], preserve_set: set = None) -> str:
    """Build a stable signature string from element texts for screen dedup."""
    texts = []
    for e in elements:
        t = e["text"].strip()
        if _should_skip(t, preserve_set) or _is_dynamic(t):
            continue
        texts.append(t)
    texts = sorted(set(texts))[:10]
    return "|".join(texts)


# ---------------------------------------------------------------------------
# LLM element classification — distinguish fixed UI from dynamic content
# ---------------------------------------------------------------------------

def _classify_elements_with_llm(
    elements: list[dict],
    app_name: str,
    api_key: str = "EMPTY",
    api_base: str = "http://localhost:8002/v1",
    model: str = "Qwen/Qwen2.5-3B-Instruct",
) -> list[dict]:
    """Use LLM to classify elements as STABLE (fixed UI) vs DYNAMIC (content).

    Returns only STABLE elements. Falls back to all elements on LLM error.
    Ported from phonecli/build_map.py.
    """
    if not elements:
        return elements

    text_list = "\n".join(e["text"] for e in elements)
    system_prompt = ELEMENT_CLASSIFY_PROMPT.format(app_name=app_name)
    user_prompt = f"Elements:\n{text_list}"

    try:
        time.sleep(0.3)
        rsp = text_completion(system_prompt, user_prompt,
                              api_key=api_key, api_base=api_base, model=model,
                              max_tokens=2048, temperature=0.0, label="classify")
        stable_set = set()
        for line in rsp.strip().splitlines():
            line = line.strip()
            if line.upper().startswith("STABLE|"):
                stable_set.add(line.split("|", 1)[1].strip())
    except Exception as e:
        print(f"[Build] LLM classification failed: {e}")
        return elements

    stable = [e for e in elements if e["text"] in stable_set]
    print(f"[Build]   classified: {len(stable)} stable, {len(elements) - len(stable)} dynamic filtered")
    return stable


# ---------------------------------------------------------------------------
# LLM enrichment — add aliases, descriptions, and app metadata to the map
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Extract the first JSON object from LLM response with bracket counting."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        start = text.index("{")
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    except (ValueError, json.JSONDecodeError):
        pass
    return {}


def _parse_json_items(rsp: str):
    """Pull JSON objects out of a model reply, whatever shape it chose.

    Expecting one object per line silently dropped everything when the model
    answered with a JSON array, a fenced block, a numbered list, or an object
    wrapping the list ("{"elements": [...]}"). None of those raise: the map
    saves, it just has no aliases and no semantic types, and the only evidence
    is a reply that parsed to zero items.
    """
    text = (rsp or "").strip()
    if not text:
        return []

    def as_list(value):
        if isinstance(value, dict):
            # {"elements": [...]} -- unwrap a single list of objects
            for inner in value.values():
                if (isinstance(inner, list) and inner
                        and all(isinstance(i, dict) for i in inner)):
                    return inner
            return [value]
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
        return []

    def parse_chunk(chunk: str):
        chunk = (chunk or "").strip()
        if not chunk:
            return []
        try:
            items = as_list(json.loads(chunk))
            if items:
                return items
        except (json.JSONDecodeError, TypeError):
            pass

        # Scan for objects instead of splitting on lines: the model often
        # pretty-prints, and one object then spans twenty lines. raw_decode
        # reads exactly one object and reports where it ended, so the next
        # scan starts after it -- this handles one-per-line, numbered lists,
        # pretty-printed blocks and stray prose with the same code.
        decoder = json.JSONDecoder()
        out = []
        index = 0
        while index < len(chunk):
            start = chunk.find("{", index)
            if start < 0:
                break
            try:
                value, end = decoder.raw_decode(chunk[start:])
            except json.JSONDecodeError:
                index = start + 1
                continue
            if isinstance(value, dict):
                out.append(value)
            index = start + end
        return out

    items = parse_chunk(text)
    if items:
        return items

    # a fenced block, which may itself hold one object per line
    for block in text.split("```")[1::2]:
        body = block.strip()
        if body.startswith("json"):
            body = body[4:].strip()
        items = parse_chunk(body)
        if items:
            return items
    return []


def _enrich_map(
    output_data: dict,
    app_name: str,
    api_key: str = "EMPTY",
    api_base: str = "http://localhost:8002/v1",
    model: str = "Qwen/Qwen2.5-3B-Instruct",
):
    """Use LLM to add aliases, semantic types, screen descriptions, and app metadata.

    Modifies *output_data* in-place. Best-effort — LLM failures are caught
    and the map is saved without enrichment. Ported from phonecli/build_map.py.
    """
    screens = output_data.get("screens", [])
    if not screens:
        return

    print("[Enrich] Adding aliases, types, and descriptions...")

    # --- 1. Per-screen: element aliases + semantic types ---
    for screen in screens:
        elements = screen.get("elements", [])
        if not elements:
            continue

        text_list = "\n".join(
            f"{i}: {e['text']}" for i, e in enumerate(elements)
        )
        try:
            rsp = text_completion(
                ELEMENT_ENRICH_PROMPT.format(app_name=app_name, element_list=text_list),
                "Output one JSON object per line.",
                api_key=api_key, api_base=api_base, model=model,
                max_tokens=2048, temperature=0.0, label="enrich_elem",
            )
            items = _parse_json_items(rsp)
            # A reply that parses to nothing is a silent quality loss: the map
            # still builds, it just has no aliases, and nothing in the log says
            # why. One line per screen makes that visible.
            print(f"[Enrich] {screen.get('id', '?')}: "
                  f"{len(rsp or '')} chars -> {len(items)} items")
            if not items and (rsp or "").strip():
                print(f"[Enrich] unparsed reply: {rsp.strip()[:200]!r}")
            for item in items:
                orig_text = item.get("text", "")
                aliases = item.get("aliases", [])
                stype = item.get("semantic_type", "")
                for e in elements:
                    if e["text"] == orig_text:
                        if aliases:
                            e.setdefault("aliases", []).extend(
                                a for a in aliases if a not in e.get("aliases", [])
                            )
                        if stype:
                            e["semantic_type"] = stype
                        break
        except Exception as exc:
            print(f"[Enrich] Element enrichment failed for {screen.get('id', '?')}: {exc}")
            continue

    # --- 2. Per-screen: description, scrollable, scroll_direction ---
    for screen in screens:
        elements = screen.get("elements", [])
        text_list = "\n".join(e["text"] for e in elements[:30])
        try:
            rsp = text_completion(
                SCREEN_ENRICH_PROMPT.format(
                    app_name=app_name,
                    screen_id=screen.get("id", "?"),
                    element_list=text_list,
                ),
                "Output JSON only.",
                api_key=api_key, api_base=api_base, model=model,
                max_tokens=256, temperature=0.0, label="enrich_screen",
            )
            data = _extract_json(rsp)
            if data.get("description"):
                screen["description"] = data["description"]
            screen["scrollable"] = data.get("scrollable", False)
            screen["scroll_direction"] = data.get("scroll_direction", "")
        except Exception as exc:
            print(f"[Enrich] Screen description failed for {screen.get('id', '?')}: {exc}")
            continue

    # --- 3. App-level metadata ---
    screen_summary_parts = []
    for s in screens:
        desc = s.get("description", "") or f"{len(s.get('elements', []))} elements"
        screen_summary_parts.append(f"  {s['id']}: {desc}")
    screen_summary = "\n".join(screen_summary_parts[:30])

    try:
        rsp = text_completion(
            APP_ENRICH_PROMPT.format(app_name=app_name, screen_summary=screen_summary),
            "Output JSON only.",
            api_key=api_key, api_base=api_base, model=model,
            max_tokens=512, temperature=0.0, label="enrich_app",
        )
        data = _extract_json(rsp)
        if data.get("launch_behavior"):
            output_data["launch_behavior"] = data["launch_behavior"]
        if data.get("common_tasks"):
            output_data["common_tasks"] = data["common_tasks"]
        if data.get("known_limitations"):
            output_data["known_limitations"] = data["known_limitations"]
        print(f"[Enrich] App metadata: launch={output_data.get('launch_behavior')}, "
              f"tasks={len(output_data.get('common_tasks', []))}")
    except Exception as exc:
        print(f"[Enrich] App metadata failed: {exc}")

    print("[Enrich] Done.")


# ---------------------------------------------------------------------------
# Main BFS crawler
# ---------------------------------------------------------------------------

def build_app_map(
    controller,
    package: str,
    app_name: str = "",
    output_path: str = "./app_map.yaml",
    max_screens: int = 50,
    max_depth: int = 3,
    scroll_pages: int = 3,
    classify: bool = True,
    enrich: bool = True,
    llm_api_key: str = "EMPTY",
    llm_api_base: str = "http://localhost:8002/v1",
    llm_model: str = "Qwen/Qwen2.5-3B-Instruct",
    preserve_set: set = None,
) -> str:
    """Build an Android app map via BFS crawling.

    Args:
        controller: AndroidController instance connected to a device.
        package: Android package name (e.g., 'com.android.settings').
        app_name: Human-readable app name.
        output_path: Path for the output YAML file.
        max_screens: Maximum number of screens to discover.
        max_depth: Maximum BFS depth.
        scroll_pages: Pages to scroll per screen.
        classify: If True, use LLM to filter out dynamic content.
        enrich: If True, use LLM to add aliases, descriptions, and metadata.
        llm_api_key / llm_api_base / llm_model: LLM config.
        preserve_set: Set of text strings to never skip.

    Returns:
        Path to the generated YAML file.
    """
    screen_w, screen_h = controller.get_device_size()
    print(f"[BuildMap] Device: {screen_w}x{screen_h}, package: {package}")

    if not app_name:
        app_name = package

    screens_data = []        # list of screen dicts for YAML output
    screen_macros = {}       # screen_id -> full path macro from screen_0
    screen_signatures = {}   # signature -> screen_id
    screen_elements = {}     # screen_id -> list of parsed elements

    # BFS queue: (screen_id, macro_path_from_screen_0)
    queue = []

    # -----------------------------------------------------------------------
    # Scroll exploration helper
    # -----------------------------------------------------------------------
    def _scroll_explore(prefix: str, tmp_dir: str) -> list[dict]:
        """Scroll and capture elements across multiple pages."""
        all_elements = []
        found_texts = {}
        seen_keys = set()

        for page in range(scroll_pages + 1):
            xml_path = os.path.join(tmp_dir, f"{prefix}_scroll_{page}.xml")
            controller.get_xml(f"{prefix}_scroll_{page}", tmp_dir)

            if not os.path.exists(xml_path):
                continue

            with open(xml_path, "r") as f:
                xml_str = f.read()

            page_elements = _parse_elements_android(xml_str, screen_w, screen_h, preserve_set)
            for e in page_elements:
                key = (e["nx"], e["ny"])
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                e_copy = {k: v for k, v in e.items()}
                e_copy["found_at_scroll"] = page
                all_elements.append(e_copy)

                # Track which texts appear at the same position across pages
                text = e["text"]
                if text not in found_texts:
                    found_texts[text] = {}
                if key not in found_texts[text]:
                    found_texts[text][key] = 0
                found_texts[text][key] += 1

            # Scroll if not last page
            if page < scroll_pages:
                controller.swipe(
                    screen_w // 2, int(screen_h * 0.7),
                    "up", dist=450,
                )
                time.sleep(1.0)

        # Mark fixed elements (same text+position across >=2 pages)
        for e in all_elements:
            text = e["text"]
            key = (e["nx"], e["ny"])
            if text in found_texts and key in found_texts[text]:
                e["fixed"] = found_texts[text][key] >= 2

        return all_elements

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------
    tmp_dir = os.path.join(os.path.dirname(output_path) or ".", ".build_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    # Kill and launch
    controller.kill_package(package)
    time.sleep(0.5)
    controller.launch_app(package)
    time.sleep(3.0)

    # -----------------------------------------------------------------------
    # Crawl screen_0
    # -----------------------------------------------------------------------
    print("[BuildMap] Crawling screen_0 ...")
    elements_0 = _scroll_explore("screen0", tmp_dir)

    # Deduplicate by text (keep first occurrence)
    seen_texts = set()
    deduped = []
    for e in elements_0:
        text = e["text"]
        if text in seen_texts:
            continue
        seen_texts.add(text)
        deduped.append(e)
    elements_0 = deduped

    # Build signature from raw elements (before LLM classification)
    # so that content differences across tabs/screens are preserved.
    sig = _build_signature(elements_0, preserve_set)

    # Optional LLM classification to filter dynamic content
    if classify:
        elements_0 = _classify_elements_with_llm(
            elements_0, app_name, llm_api_key, llm_api_base, llm_model)

    print(f"[BuildMap] screen_0: {len(elements_0)} elements")

    screen_id = "screen_0"
    screens_data.append({
        "id": screen_id,
        "description": f"{app_name} main screen",
        "elements": [{
            "text": e["text"],
            "center": [round(e["nx"], 4), round(e["ny"], 4)],
            "fixed": e.get("fixed", False),
            "found_at_scroll": e.get("found_at_scroll", 0),
        } for e in elements_0],
    })
    screen_macros[screen_id] = [
        {"action": "force_stop", "package": package, "wait": 0.5},
        {"action": "launch", "package": package, "wait": 3.0},
    ]
    screen_signatures[sig] = screen_id
    screen_elements[screen_id] = elements_0

    # Enqueue elements that can lead to new screens
    for e in elements_0:
        if not _is_dynamic(e["text"]):
            queue.append((e, [screen_id], screen_macros[screen_id]))

    # -----------------------------------------------------------------------
    # BFS loop
    # -----------------------------------------------------------------------
    print(f"[BuildMap] Starting BFS (max_screens={max_screens}, max_depth={max_depth}) ...")
    while queue and len(screens_data) < max_screens:
        element, path_screens, parent_macro = queue.pop(0)
        depth = len(path_screens)

        if depth > max_depth:
            continue

        # Navigate: replay full parent macro + tap
        print(f"[BuildMap] BFS depth={depth}, tapping '{element['text']}' ...")

        controller.kill_package(package)
        time.sleep(0.3)
        controller.launch_app(package)

        # Extract launch wait from parent macro (default 3s)
        launch_wait = 3.0
        for step in parent_macro:
            if step.get("action") == "launch":
                launch_wait = step.get("wait", 3.0)
                break
        time.sleep(launch_wait)

        # Replay parent macro
        for step in parent_macro:
            action = step.get("action", "")
            if action == "tap":
                controller.tap(step["x"], step["y"])
                time.sleep(step.get("wait", 1.0))
            elif action == "swipe":
                # Use raw ADB — controller.swipe_precise() has a coord bug
                dur = step.get("duration", 400)
                controller.run_command(
                    f"adb shell input swipe {step['x1']} {step['y1']} "
                    f"{step['x2']} {step['y2']} {dur}"
                )
                time.sleep(step.get("wait", 0.5))
            elif action in ("launch", "force_stop"):
                pass  # Already handled above

        # Tap the target element
        ex = round(element["cx"])
        ey = round(element["cy"])
        controller.tap(ex, ey)
        time.sleep(1.5)

        # Dump new screen
        prefix = f"screen_{len(screens_data)}"
        xml_path = os.path.join(tmp_dir, f"{prefix}.xml")
        controller.get_xml(prefix, tmp_dir)

        if not os.path.exists(xml_path):
            continue

        with open(xml_path, "r") as f:
            xml_str = f.read()

        new_elements = _parse_elements_android(xml_str, screen_w, screen_h, preserve_set)

        # Deduplicate by text
        seen_texts = set()
        deduped = []
        for e in new_elements:
            text = e["text"]
            if text in seen_texts:
                continue
            seen_texts.add(text)
            deduped.append(e)
        new_elements = deduped

        if not new_elements:
            continue

        # Build signature from raw elements (before LLM classification)
        # so that content differences across tabs/screens are preserved.
        # LLM classification filters too aggressively for apps with
        # persistent nav bars (e.g., bottom tabs), making all screens
        # look identical.
        new_sig = _build_signature(new_elements, preserve_set)

        # Optional LLM classification
        if classify:
            new_elements = _classify_elements_with_llm(
                new_elements, app_name, llm_api_key, llm_api_base, llm_model)
            if not new_elements:
                print(f"[BuildMap]   → no stable elements, skip")
                continue

        # Check if this screen already exists
        if new_sig in screen_signatures:
            existing_id = screen_signatures[new_sig]
            # Record edge from parent to existing
            parent_screen = path_screens[-1]
            for s in screens_data:
                if s["id"] == parent_screen:
                    for e_data in s["elements"]:
                        if e_data["text"] == element["text"]:
                            e_data["leads_to"] = existing_id
                            break
                    break
            continue

        # New screen discovered
        new_id = f"screen_{len(screens_data)}"
        screen_signatures[new_sig] = new_id

        # Build full path macro
        tap_x = round(element["nx"] * screen_w)
        tap_y = round(element["ny"] * screen_h)
        new_macro = list(parent_macro) + [
            {"action": "tap", "x": tap_x, "y": tap_y, "wait": 1.5},
        ]

        screen_macros[new_id] = new_macro
        screen_elements[new_id] = new_elements

        screens_data.append({
            "id": new_id,
            "elements": [{
                "text": e["text"],
                "center": [round(e["nx"], 4), round(e["ny"], 4)],
                "fixed": e.get("fixed", False),
                "found_at_scroll": e.get("found_at_scroll", 0),
            } for e in new_elements],
        })

        # Record edge from parent to new screen
        parent_screen = path_screens[-1]
        for s in screens_data:
            if s["id"] == parent_screen:
                for e_data in s["elements"]:
                    if e_data["text"] == element["text"]:
                        e_data["leads_to"] = new_id
                        break
                break

        print(f"[BuildMap] New screen: {new_id} ({len(new_elements)} elements) — total: {len(screens_data)}")

        # Enqueue new elements
        for e in new_elements:
            if not _is_dynamic(e["text"]) and len(path_screens) < max_depth:
                queue.append((e, path_screens + [new_id], new_macro))

    # -----------------------------------------------------------------------
    # Convert macros to platform-agnostic format (absolute pixel coords)
    # -----------------------------------------------------------------------
    formatted_macros = {}
    for sid, macro in screen_macros.items():
        formatted = []
        for step in macro:
            formatted.append({
                k: (round(v) if k in ("x", "y", "x1", "y1", "x2", "y2") else v)
                for k, v in step.items()
            })
        formatted_macros[sid] = formatted

    # -----------------------------------------------------------------------
    # Build YAML output
    # -----------------------------------------------------------------------
    output = {
        "app": app_name,
        "package": package,
        "screen_w": screen_w,
        "screen_h": screen_h,
        "screens": screens_data,
        "screen_macros": formatted_macros,
    }

    # -----------------------------------------------------------------------
    # Optional: LLM enrichment (aliases, descriptions, app metadata)
    # -----------------------------------------------------------------------
    if enrich:
        try:
            _enrich_map(output, app_name,
                        api_key=llm_api_key, api_base=llm_api_base, model=llm_model)
        except Exception as e:
            print(f"[BuildMap] Enrichment failed ({e}) — saving map without enrichment")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w") as f:
        yaml.dump(output, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"[BuildMap] Done: {len(screens_data)} screens → {output_path}")

    # Cleanup temp files
    for fn in os.listdir(tmp_dir):
        os.remove(os.path.join(tmp_dir, fn))

    from phonecli.token_usage import token_usage
    token_usage.print_report()

    return output_path
