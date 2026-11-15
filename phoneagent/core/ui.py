"""Small shared helpers for reading a UIAutomator hierarchy.

Both the macro executor (text-anchored tap) and the agent (on-screen label
list) need the same thing: turn the XML dump into a flat list of
``(label, center, clickable)`` records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

_NODE_RE = re.compile(r"<node\b([^>]*)>")
_BOUNDS_RE = re.compile(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')


@dataclass(frozen=True)
class UiNode:
    label: str
    center: tuple[int, int]
    bounds: tuple[int, int, int, int]
    clickable: bool
    editable: bool = False
    klass: str = ""

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]


def parse_nodes(xml: str) -> list[UiNode]:
    """Every ``<node>`` with a screen box, labelled or not."""
    nodes: list[UiNode] = []
    for match in _NODE_RE.finditer(xml or ""):
        tag = match.group(0)
        box = _BOUNDS_RE.search(tag)
        if not box:
            continue
        x1, y1, x2, y2 = (int(v) for v in box.groups())
        label = ""
        for attr in ("text", "content-desc"):
            value = re.search(rf'{attr}="([^"]*)"', tag)
            if value and value.group(1).strip():
                label = value.group(1).strip()
                break
        nodes.append(
            UiNode(
                label=label,
                center=((x1 + x2) // 2, (y1 + y2) // 2),
                bounds=(x1, y1, x2, y2),
                clickable='clickable="true"' in tag,
                editable='editable="true"' in tag,
                klass=(re.search(r'class="([^"]*)"', tag) or [None, ""])[1]
                if re.search(r'class="([^"]*)"', tag) else "",
            )
        )
    return nodes


def labelled(nodes: Iterable[UiNode], limit: int = 0) -> list[UiNode]:
    """Labelled nodes in screen order, deduplicated by label."""
    seen: set[str] = set()
    out: list[UiNode] = []
    for node in nodes:
        if not node.label or node.label in seen:
            continue
        if node.width < 20 or node.height < 20:
            continue
        seen.add(node.label)
        out.append(node)
        if limit and len(out) >= limit:
            break
    return out


def find_label(xml: str, label: str, require_unique: bool = True) -> Optional[UiNode]:
    """Locate a label, refusing ambiguous matches.

    Generic labels ("设置", "返回", "同意") appear several times on a screen; a
    text anchor that silently picks the first one is worse than the compiled
    coordinate, so when ``require_unique`` is set an ambiguous label is
    reported as "not found" and the caller falls back to the coordinates.
    """
    nodes = parse_nodes(xml)
    target = label.strip()
    if not target:
        return None

    exact = [n for n in nodes if n.label == target]
    if len(exact) == 1 or (exact and not require_unique):
        return _tappable(exact, nodes)

    lowered = target.lower()
    partial = [n for n in nodes if lowered in n.label.lower()]
    if len(partial) == 1 or (partial and not require_unique):
        return _tappable(partial, nodes)
    return None


def _tappable(candidates: list[UiNode], all_nodes: list[UiNode]) -> UiNode:
    """Prefer a clickable candidate, else the clickable container around it."""
    for node in candidates:
        if node.clickable:
            return node
    for candidate in candidates:
        for node in all_nodes:
            if not node.clickable:
                continue
            bx1, by1, bx2, by2 = node.bounds
            cx, cy = candidate.center
            if bx1 <= cx <= bx2 and by1 <= cy <= by2:
                return node
    return candidates[0]
