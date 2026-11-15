"""App map: load YAML, identify the current screen, build the operation catalog.

This is the merge of the two existing copies:

* ``phonecli/app_map.py``               (iOS, XCUITest XML)
* ``phonecli_android/evaluation/app_map.py`` (Android, UIAutomator XML)

They had drifted only in the XML parser, the catalog formatting and the
``Operation.target_screen`` field; everything else was byte-identical. This
module keeps both parsers and both catalog styles behind one API so the
algorithm layer stays single-sourced.

Macro format (platform-agnostic; ``bundle_id`` and ``package`` are both
accepted when replaying)::

    {"action": "force_stop", "package": "com.android.settings", "wait": 0.5}
    {"action": "launch",     "package": "com.android.settings", "wait": 3.0}
    {"action": "tap",        "x": 540, "y": 960, "wait": 1.0}
    {"action": "swipe",      "x1": 540, "y1": 1500, "x2": 540, "y2": 500,
                             "duration": 400, "wait": 0.5}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import yaml

# Interactive XCUITest element types worth reading text from.
_IOS_XML_TYPES = (
    "XCUIElementTypeButton",
    "XCUIElementTypeCell",
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeSearchField",
    "XCUIElementTypeSwitch",
    "XCUIElementTypeTab",
    "XCUIElementTypeLink",
    "XCUIElementTypeStaticText",
    "XCUIElementTypeImage",
    "XCUIElementTypeIcon",
)

_DEFAULT_SCREEN = {"ios": (390, 844), "android": (1080, 1920)}


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def texts_from_ios_xml(xml_str: str) -> set[str]:
    """Labels/names of interactive elements in an XCUITest page source."""
    texts: set[str] = set()
    for itype in _IOS_XML_TYPES:
        for tag in re.finditer(rf"<{itype}\b([^>]*?)/>", xml_str):
            attrs = tag.group(0)
            label = re.search(r'label="([^"]*)"', attrs)
            name = re.search(r'name="([^"]*)"', attrs)
            text = (label.group(1) if label else "") or (name.group(1) if name else "")
            text = text.strip()
            if text and len(text) > 1:
                texts.add(text)
    return texts


def texts_from_android_xml(xml_str: str) -> set[str]:
    """Text/content-desc of UIAutomator ``<node>`` elements."""
    texts: set[str] = set()
    for tag in re.finditer(r"<node\b([^>]*)>", xml_str):
        attrs = tag.group(0)
        for attr in ("text", "content-desc"):
            match = re.search(rf'{attr}="([^"]*)"', attrs)
            if match and match.group(1).strip():
                text = match.group(1).strip()
                if len(text) > 1:
                    texts.add(text)
    return texts


def selected_texts_from_android_xml(xml_str: str) -> set[str]:
    """Labels of the nodes the app marks as selected -- a highlighted tab.

    A tab bar looks the same on every tab; which entry is selected is the only
    thing that tells 推荐 from 消息, and it is not in ``text``/``content-desc``.
    """
    texts: set[str] = set()
    for tag in re.finditer(r"<node\b([^>]*)>", xml_str):
        attrs = tag.group(0)
        if 'selected="true"' not in attrs:
            continue
        for attr in ("text", "content-desc"):
            match = re.search(rf'{attr}="([^"]*)"', attrs)
            if match and match.group(1).strip():
                texts.add(match.group(1).strip())
    return texts


def detect_xml_platform(xml_str: str) -> Optional[str]:
    """Guess the dump format from its content."""
    if "XCUIElementType" in xml_str:
        return "ios"
    if "<node" in xml_str:
        return "android"
    return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ScreenElement:
    text: str
    center: tuple[float, float]  # normalised [0, 1]
    leads_to: Optional[str] = None
    found_at_scroll: int = 0
    fixed: bool = False
    aliases: list[str] = field(default_factory=list)  # ["WiFi", "wireless", "无线"]
    semantic_type: str = ""  # toggle/button/network/input/tab/label/link
    redacted: bool = False  # text was replaced by a placeholder at export time
    #: Content, not chrome: it changes between launches, so it identifies a
    #: screen only weakly and must not be the evidence a match rests on.
    dynamic: bool = False


@dataclass
class Screen:
    id: str
    elements: list[ScreenElement] = field(default_factory=list)
    #: Labels that were marked selected when this screen was captured (the
    #: highlighted tab). Two tabs of a feed differ by nothing else.
    selected: list[str] = field(default_factory=list)
    description: str = ""
    package: str = ""  # empty = the map's own package
    scrollable: bool = False
    scroll_direction: str = ""  # "vertical" | "horizontal"


@dataclass
class Operation:
    id: str
    description: str
    macro: list[dict]
    type: str  # "NAV" or "ACT"
    target_screen: str = ""  # screen reached by a NAV operation


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------


class AppMap:
    """Loads and queries a YAML app map."""

    MIN_COVERAGE = 0.5

    def __init__(self, map_path: str, platform: Optional[str] = None):
        try:
            with open(map_path, "r", encoding="utf-8") as fh:
                self.data = yaml.safe_load(fh)
        except (FileNotFoundError, yaml.YAMLError) as exc:
            raise ValueError(f"Failed to load app map '{map_path}': {exc}") from exc

        self.map_path = map_path
        self.app_name = self.data.get("app", "Unknown")
        self.package = self.data.get("package", "")
        # Macros are needed by _infer_platform(), so load them first.
        self._macros: dict[str, list[dict]] = self.data.get("screen_macros", {})
        self.platform = platform or self._infer_platform()

        default_w, default_h = _DEFAULT_SCREEN.get(self.platform, _DEFAULT_SCREEN["android"])
        self.screen_w = int(self.data.get("screen_w", default_w))
        self.screen_h = int(self.data.get("screen_h", default_h))
        #: Display the coordinates were measured on (0 = the phone's own screen).
        #: Missing means "unknown", which is treated as a virtual display.
        self.display = self.data.get("display", None)

        self.launch_behavior = self.data.get("launch_behavior", "always_home")
        # Optional: an intent action that reliably opens this app's home page
        # (e.g. android.settings.SETTINGS). Used instead of a package launch.
        self.launch_intent: str = self.data.get("launch_intent", "") or ""
        self.common_tasks: list[str] = self.data.get("common_tasks", [])
        #: When this map was crawled, and how its own operations scored when
        #: replayed: {ops, exact, same_app, at}. Empty for older maps.
        self.crawled_at: str = str(self.data.get("crawled_at", "") or "")
        self.bench: dict = dict(self.data.get("bench") or {})
        #: What the crawl reached and what it left behind:
        #: {screens, queued, depth_limit, stopped, complete}
        self.coverage: dict = dict(self.data.get("coverage") or {})
        self.known_limitations: list[str] = self.data.get("known_limitations", [])

        self.screens: list[Screen] = []
        for raw in self.data.get("screens", []):
            elements = [
                ScreenElement(
                    text=e["text"],
                    center=tuple(e["center"]),
                    leads_to=e.get("leads_to"),
                    found_at_scroll=e.get("found_at_scroll", 0),
                    fixed=e.get("fixed", False),
                    aliases=e.get("aliases", []),
                    semantic_type=e.get("semantic_type", ""),
                    redacted=bool(e.get("redacted", False)),
                    dynamic=bool(e.get("dynamic", False)),
                )
                for e in raw.get("elements", [])
            ]
            self.screens.append(
                Screen(
                    id=raw["id"],
                    elements=elements,
                    description=raw.get("description", ""),
                    package=raw.get("package", ""),
                    scrollable=raw.get("scrollable", False),
                    scroll_direction=raw.get("scroll_direction", ""),
                    selected=[str(x) for x in (raw.get("selected") or [])],
                )
            )

    # -- basics -----------------------------------------------------------

    def _infer_platform(self) -> str:
        """Best-effort platform guess, strongest signal first.

        The iOS tree also ships maps for third-party apps (``com.sina.weibo``),
        so the bundle id alone is not enough. The macro steps are the reliable
        tell: iOS maps store ``bundle_id``, Android maps store ``package``.
        """
        for macro in self._macros.values():
            for step in macro:
                if "bundle_id" in step:
                    return "ios"
                if "package" in step:
                    return "android"

        package = (self.package or "").lower()
        if package.startswith("com.apple."):
            return "ios"

        path = str(self.map_path).lower().replace("\\", "/")
        if "phonecli_android" in path:
            return "android"
        if path.startswith("phonecli/") or "/phonecli/" in path:
            return "ios"

        # Last resort: iPhone layouts are narrow, Android dumps are wide.
        raw_width = self.data.get("screen_w") or 0
        return "ios" if 0 < raw_width <= 600 else "android"

    @property
    def size(self) -> tuple[int, int]:
        return self.screen_w, self.screen_h

    def get_screen(self, screen_id: str) -> Optional[Screen]:
        for screen in self.screens:
            if screen.id == screen_id:
                return screen
        return None

    def get_macro(self, screen_id: str) -> list[dict]:
        """Full replay path to reach ``screen_id`` from a cold start."""
        return list(self._macros.get(screen_id, []))

    # -- screen identification -------------------------------------------

    def identify_current_screen(
        self, xml_str: str, platform: str = "auto", package: str = ""
    ) -> tuple:
        """Match a UI dump against the map's screens.

        ``package`` restricts the search to screens owned by that foreground
        package: HyperOS hands many settings pages over to
        ``com.miui.securitycenter`` / ``com.xiaomi.account``, and without this
        filter a page of another app can out-score the real one.

        Returns ``(screen_id, confidence)``; ``(None, 0)`` when nothing fits.
        """
        plat = platform
        if plat == "auto":
            plat = detect_xml_platform(xml_str) or self.platform

        texts = self._texts(xml_str, plat)
        # Which tab is highlighted is part of the screen, not decoration.
        dump_tabs: set = set()
        if plat == "android":
            dump_tabs = set(selected_texts_from_android_xml(xml_str))
            texts = set(texts) | dump_tabs
        if not texts:
            # Wrong guess is cheap to recover from: try the other format once.
            other = "android" if plat == "ios" else "ios"
            texts = self._texts(xml_str, other)

        if not texts:
            return None, 0.0

        best_id, best_overlap, best_coverage, best_rarity = None, 0, 0.0, 0.0
        for screen in self.screens:
            if package and (screen.package or self.package) != package:
                continue
            # A different highlighted tab is a different screen, whatever else
            # the two share. The four tabs of a clock carry the same tab bar and
            # the same header, so text overlap alone cannot tell them apart and
            # identification fell back to the first one -- measured as "landed
            # screen_0, expected screen_1" after a correct macro.
            known_tabs = set(getattr(screen, "selected", []) or [])
            if dump_tabs and known_tabs and not (dump_tabs & known_tabs):
                continue
            # Compare against what is visible *without* scrolling: a dump after
            # replay shows page 0 only, while the map also stores the folded
            # elements reached with swipes.
            screen_texts = {
                e.text
                for e in screen.elements
                if e.found_at_scroll == 0 and not e.redacted and not e.dynamic
            }
            screen_texts = screen_texts | set(getattr(screen, "selected", []) or [])
            if not screen_texts:
                # A screen made only of content still has to be identifiable, or
                # it becomes unmatchable; fall back rather than lose it.
                screen_texts = {
                    e.text for e in screen.elements if not e.redacted and not e.dynamic
                }
            if not screen_texts:
                screen_texts = {e.text for e in screen.elements if not e.redacted}
            if not screen_texts:
                screen_texts = {e.text for e in screen.elements}
            if not screen_texts:
                continue
            matched = texts & screen_texts
            # Weight by rarity across the map's screens. A tab label ("闹钟",
            # "计时") lives on every screen of the app, so matching it says
            # nothing; a label unique to one screen is the evidence. Without
            # this the scores of near-identical screens differ by a rounding
            # error and identification flips between runs -- which is what made
            # the macro screen guard unusable.
            overlap = len(matched)
            coverage = overlap / len(screen_texts)
            # Rarity only breaks ties. Changing the primary rule broke a
            # package-scoped case whose match is one shared label out of two;
            # the instability that mattered was two screens scoring the same,
            # and that is exactly a tie.
            rarity = sum(self._weight(t) for t in matched)
            # Coverage breaks a tie on overlap before rarity does: when two
            # screens share the same labels, the one those labels fill more
            # completely is the better answer. Without it a duplicate screen
            # could win on rarity alone, and the choice flipped between runs.
            rank = (overlap, round(coverage, 6), rarity)
            best_rank = (best_overlap, round(best_coverage, 6), best_rarity)
            if coverage >= self.MIN_COVERAGE and rank > best_rank:
                best_overlap, best_id, best_coverage = overlap, screen.id, coverage
                best_rarity = rarity

        if best_id is None:
            return None, best_coverage
        return best_id, best_coverage

    def _weight(self, text: str) -> float:
        """How much a text distinguishes one screen from the others."""
        counts = getattr(self, "_text_df", None)
        if counts is None:
            counts = {}
            for screen in self.screens:
                for text in {e.text for e in screen.elements if not e.redacted}:
                    counts[text] = counts.get(text, 0) + 1
            self._text_df = counts
        seen = counts.get(text, 0)
        # 1.0 for a text on a single screen, down to a small floor for one that
        # appears everywhere (the floor keeps a shared-only screen matchable).
        return 1.0 / max(1, seen) if seen else 0.0

    @staticmethod
    def _texts(xml_str: str, platform: str) -> set[str]:
        if platform == "ios":
            return texts_from_ios_xml(xml_str)
        return texts_from_android_xml(xml_str)

    # -- navigation -------------------------------------------------------

    def find_relative_macro(self, from_id: str, to_id: str) -> list:
        """Steps needed to go from ``from_id`` to ``to_id``.

        ``screen_macros`` stores full paths from a cold start, so the common
        prefix is stripped. When the two screens share no prefix (e.g. the
        current screen was reached by the VLM), the full path is returned.
        """
        from_path = self._macros.get(from_id, [])
        to_path = self._macros.get(to_id, [])
        if not to_path:
            return []
        if not from_path:
            return list(to_path)

        i = 0
        while i < min(len(from_path), len(to_path)) and from_path[i] == to_path[i]:
            i += 1
        return list(to_path[i:])

    def get_nav_targets(self, from_screen_id: str) -> list[str]:
        """Navigation labels reachable from a screen (text + first alias)."""
        targets = []
        screen = self.get_screen(from_screen_id)
        if screen:
            for e in screen.elements:
                if e.leads_to:
                    label = e.text
                    if e.aliases:
                        label += f" ({e.aliases[0]})"
                    targets.append(label)
        return targets

    # -- operations -------------------------------------------------------

    def build_operations(self) -> dict[str, Operation]:
        """Traverse the screen graph from ``screen_0`` and emit operations."""
        ops: dict[str, Operation] = {}
        visited_screens: set[str] = set()
        visited_ops: set[str] = set()

        def _skip(text: str) -> bool:
            lowered = text.lower()
            skips = (
                "profile picture",
                "navigate up",
                "more options",
                "dismiss",
                "clear",
                "learn more",
                "back",
                "cancel",
                "no unused",
                "no recent",
                "no nearby",
                "google account",
                "manage your google",
            )
            stripped = text.strip()
            # A single character is noise in Latin script ("x", "-") but a whole
            # word in Chinese: 我, 赞, 转 are the profile tab and the two buttons
            # a feed is full of. Dropping them left 小红书's map with no 我
            # operation at all, so "看我的页面" could never be routed.
            if len(stripped) <= 1 and stripped.isascii():
                return True
            return any(s in lowered for s in skips)

        def _op_id(texts: list[str]) -> str:
            # Consecutive repeats are the same word twice, not a path: a screen
            # whose tab is also the way in produced 世界时钟_世界时钟, which reads
            # like a mistake in the app.
            collapsed: list = []
            for text in texts:
                if not collapsed or collapsed[-1] != text:
                    collapsed.append(text)
            clean = "".join(
                c if c.isalnum() else "_" for c in "_".join(collapsed).lower()
            )
            return clean.strip("_")[:60]

        def _explore(screen_id: str, prefix_texts: list[str], depth: int, max_depth: int = 6):
            if depth > max_depth or screen_id in visited_screens:
                return
            visited_screens.add(screen_id)
            screen = self.get_screen(screen_id)
            if not screen:
                return

            base_macro = self.get_macro(screen_id)
            for element in screen.elements:
                if element.redacted or _skip(element.text) or not element.text:
                    continue

                x = round(element.center[0] * self.screen_w)
                y = round(element.center[1] * self.screen_h)
                mid_x = self.screen_w // 2
                from_y = int(self.screen_h * 0.7)
                to_y = int(self.screen_h * 0.2)

                op_macro = list(base_macro)
                for _ in range(element.found_at_scroll):
                    op_macro.append(
                        {
                            "action": "swipe",
                            "x1": mid_x,
                            "y1": from_y,
                            "x2": mid_x,
                            "y2": to_y,
                            "duration": 400,
                            "wait": 0.5,
                        }
                    )
                # `text` is a hint for the replay layer: when present it
                # resolves the label against a live dump instead of trusting
                # coordinates that may have drifted since compilation.
                op_macro.append(
                    {"action": "tap", "x": x, "y": y, "wait": 1.0, "text": element.text}
                )

                texts = prefix_texts + [element.text]
                op_id = _op_id(texts)
                if op_id in visited_ops:
                    continue
                visited_ops.add(op_id)

                ops[op_id] = Operation(
                    id=op_id,
                    description=" → ".join(texts),
                    macro=op_macro,
                    type="NAV" if element.leads_to else "ACT",
                    target_screen=element.leads_to or "",
                )

                if element.leads_to:
                    _explore(element.leads_to, texts, depth + 1)

        _explore("screen_0", [], 0)
        return ops

    def get_target_screen_info(self, ops: dict[str, Operation], op_id: str) -> str:
        """Description of the screen an operation lands on (for verification)."""
        op = ops.get(op_id) if ops else None
        if not op or not op.target_screen:
            return ""
        screen = self.get_screen(op.target_screen)
        return getattr(screen, "description", "") if screen else ""

    # -- formatting for the model ----------------------------------------

    def format_ops_catalog(
        self,
        ops: dict[str, Operation],
        style: str = "flat",
        nav_only: bool = False,
    ) -> str:
        """Render the operation catalog for an LLM prompt.

        ``style="flat"`` keeps the original iOS layout (``[NAV] id — path``
        plus aliases); ``style="tree"`` renders the indent tree with semantic
        tags that the Android side introduced, which is ~60% shorter.
        """
        if style == "tree":
            return self._format_tree(ops, nav_only=nav_only)
        return self._format_flat(ops, nav_only=nav_only)

    def _format_flat(self, ops: dict[str, Operation], nav_only: bool = False) -> str:
        lines = []
        for op_id, op in ops.items():
            if nav_only and op.type != "NAV":
                continue
            tag = "[ACT]" if op.type == "ACT" else "[NAV]"
            line = f"  {tag} {op_id} — {op.description}"
            last_text = op.description.split(" → ")[-1] if " → " in op.description else op.description
            element = self._find_element(last_text)
            if element and element.aliases:
                line += f"  [aliases: {', '.join(element.aliases)}]"
            lines.append(line)
        return "\n".join(lines)

    def _format_tree(self, ops: dict[str, Operation], nav_only: bool = False) -> str:
        filtered = {k: v for k, v in ops.items() if not nav_only or v.type == "NAV"}
        if not filtered:
            return ""

        tree: dict[str, list[tuple[str, str, Operation]]] = {}
        for op_id, op in filtered.items():
            parts = op.description.split(" → ")
            prefix = " → ".join(parts[:-1]) if len(parts) > 1 else ""
            tree.setdefault(prefix, []).append((parts[-1], op_id, op))

        hint = " (NAV only)" if nav_only else ""
        lines = [
            f"{self.app_name} ({self.package}) — {len(filtered)} operations "
            f"in {len(tree)} groups{hint}"
        ]
        self._format_tree_level(tree, "", 0, lines)
        return "\n".join(lines)

    def _format_tree_level(
        self, tree: dict, prefix: str, depth: int, lines: list[str]
    ) -> None:
        items = tree.get(prefix, [])
        if not items:
            return
        items.sort(key=lambda item: (item[2].type != "NAV", item[0].lower()))

        indent = "  " * depth
        for suffix, op_id, op in items:
            element = self._find_element(suffix)
            tags = []
            if element:
                type_tag = self._type_tag(element)
                if type_tag:
                    tags.append(type_tag)
                if element.aliases:
                    tags.append(element.aliases[0])
            tag_str = f" [{'] ['.join(tags)}]" if tags else ""
            lines.append(f"{indent}{suffix}{tag_str}  # {op_id}")

            child_prefix = f"{prefix} → {suffix}" if prefix else suffix
            if child_prefix in tree:
                self._format_tree_level(tree, child_prefix, depth + 1, lines)

    def _find_element(self, text: str) -> Optional[ScreenElement]:
        for screen in self.screens:
            for element in screen.elements:
                if element.text == text:
                    return element
        return None

    @staticmethod
    def _type_tag(element: ScreenElement) -> str:
        mapping = {
            "toggle": "TOGGLE",
            "switch": "TOGGLE",
            "input": "INPUT",
            "textfield": "INPUT",
            "slider": "SLIDER",
            "button": "BUTTON",
            "checkbox": "CHECKBOX",
            "label": "LABEL",
            "link": "LINK",
            "network": "NETWORK",
            "setting": "SETTING",
            "tab": "TAB",
            "menu_item": "MENU",
        }
        return mapping.get(element.semantic_type, "")

    def format_nav_reference(self) -> str:
        """A short markdown navigation reference for VLM system prompts."""
        if not self.screens:
            return ""

        noise = {
            "navigate up",
            "more options",
            "back",
            "cancel",
            "dismiss",
            "clear",
            "learn more",
            "profile picture",
        }

        def clean(text: str) -> str:
            return text.replace("&amp;", "&")

        def is_noise(text: str) -> bool:
            return text.lower() in noise or len(text.strip()) <= 1

        children_of: dict[str, list[tuple[str, str, str]]] = {}
        for screen in self.screens:
            for element in screen.elements:
                if element.leads_to and not is_noise(element.text):
                    children_of.setdefault(screen.id, []).append(
                        (element.text, element.leads_to, element.semantic_type)
                    )

        if "screen_0" not in children_of:
            return f"App: {self.app_name} ({self.package})"

        roots: dict[str, tuple[str, str]] = {}
        for text, child_id, semantic in children_of["screen_0"]:
            previous = roots.get(child_id)
            if previous is None or len(text) < len(previous[0]):
                roots[child_id] = (text, semantic)

        lines = [
            "## App Navigation Reference",
            f'This app ("{self.app_name}") has these top-level pages:',
            "",
        ]
        for child_id, (label, semantic) in sorted(roots.items(), key=lambda kv: kv[1][0].lower()):
            sub_items = children_of.get(child_id, [])
            sub_navs = [(t, s) for t, _sid, s in sub_items if not is_noise(t)]
            if len(sub_navs) > 8:
                sub_navs = [(t, s) for t, s in sub_navs if s not in ("label", "")]
                sub_navs = sub_navs[:8]

            tag = " [search]" if semantic == "input" else (" [toggle]" if semantic in ("toggle", "switch") else "")
            lines.append(f"- {clean(label)}{tag}")
            for sub_text, sub_semantic in sub_navs:
                sub_tag = " [text]" if sub_semantic == "input" else (
                    " [toggle]" if sub_semantic in ("toggle", "switch") else ""
                )
                lines.append(f"  - {clean(sub_text)}{sub_tag}")

        return "\n".join(lines)

    def build_enriched_screen_hint(self, screen_id: str) -> str:
        """A compact VLM hint about what a screen is and where it leads."""
        screen = self.get_screen(screen_id)
        if not screen:
            return ""

        parts = [f'You are on "{screen_id}"']
        if screen.description:
            parts[0] += f": {screen.description}"
        parts[0] += "."

        nav_targets = self.get_nav_targets(screen_id)
        if nav_targets:
            parts.append(f"Navigation targets: {', '.join(nav_targets)}")
        else:
            refs = [e.text for e in screen.elements if e.fixed and e.text]
            if refs:
                parts.append(f"Reference elements: {', '.join(refs[:8])}")

        if screen.scrollable:
            parts.append(f"This screen is {screen.scroll_direction or 'vertical'}-scrollable.")

        return " ".join(parts)
