"""Static validation for app maps — the Android counterpart of the iOS
``validate_map.py`` stage.

    python3 -m phoneagent.validate --map phoneagent/maps/k90_scroll.yaml
    python3 -m phoneagent.validate --maps phoneagent/maps --strict

Checks are split into errors (the map cannot be trusted) and warnings (it
probably works, but something looks off). Exit code is 1 when any error is
found, or when ``--strict`` turns warnings into failures.
"""

from __future__ import annotations

import argparse
import re
import sys
from itertools import combinations
from pathlib import Path

import yaml

REQUIRED_KEYS = ("app", "package", "screen_w", "screen_h", "screens", "screen_macros")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?\d[\d\s\-]{6,}\d")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")


_HOTLINE_RE = re.compile(r"\b(?:400|800)[\s-]?\d{3}[\s-]?\d{4}\b")


def _looks_personal(text: str) -> bool:
    """Phone/e-mail heuristics that skip Chinese service hotlines (400/800)."""
    if EMAIL_RE.search(text):
        return True
    if _HOTLINE_RE.search(text):
        return False
    return bool(PHONE_RE.search(text))


def _issue(code: str, message: str, detail: str = "") -> dict:
    return {"code": code, "message": message, "detail": detail}


def validate_map(path: str | Path, max_duplicate_jaccard: float = 0.9) -> tuple[list[dict], list[dict]]:
    """Return ``(errors, warnings)`` for one map file."""
    errors: list[dict] = []
    warnings: list[dict] = []
    path = Path(path)

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [_issue("E_LOAD", "map cannot be parsed", str(exc))], []

    for key in REQUIRED_KEYS:
        if key not in data:
            errors.append(_issue("E_MISSING_KEY", f"missing top-level key {key!r}"))
    if errors:
        return errors, warnings

    screens = data.get("screens") or []
    macros = data.get("screen_macros") or {}
    ids = [s.get("id", "") for s in screens]

    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        errors.append(_issue("E_DUP_SCREEN", "duplicate screen ids", ", ".join(dupes)))

    id_set = set(ids)
    if "screen_0" not in id_set:
        errors.append(_issue("E_NO_ROOT", "screen_0 is missing"))

    element_total = 0
    redacted_total = 0
    for screen in screens:
        sid = screen.get("id", "?")
        elements = screen.get("elements") or []
        if not elements:
            warnings.append(_issue("W_EMPTY_SCREEN", f"{sid} has no elements"))
            continue
        usable = 0
        for element in elements:
            element_total += 1
            text = element.get("text", "")
            if element.get("redacted"):
                redacted_total += 1
            if text and not element.get("redacted"):
                usable += 1
            center = element.get("center")
            if not (isinstance(center, (list, tuple)) and len(center) == 2):
                errors.append(
                    _issue("E_BAD_CENTER", f"{sid}: element without center", repr(text)[:60])
                )
                continue
            x, y = center
            if not (0.0 <= float(x) <= 1.0 and 0.0 <= float(y) <= 1.0):
                errors.append(
                    _issue("E_BAD_CENTER", f"{sid}: center outside [0,1]", f"{x},{y}")
                )
            target = element.get("leads_to")
            if target and target not in id_set:
                errors.append(
                    _issue("E_UNKNOWN_TARGET", f"{sid}: leads_to unknown screen", str(target))
                )
            if _looks_personal(text):
                errors.append(
                    _issue("E_PII", f"{sid}: unredacted personal data", text[:40])
                )
        if usable < 2:
            warnings.append(
                _issue("W_FEW_ELEMENTS", f"{sid} has fewer than 2 usable labels", str(usable))
            )

    # macro sanity + reachability
    reachable: set[str] = set()
    for sid, macro in macros.items():
        if sid not in id_set:
            errors.append(_issue("E_UNKNOWN_TARGET", "macro for unknown screen", str(sid)))
        steps = macro or []
        if not steps:
            warnings.append(_issue("W_EMPTY_MACRO", f"{sid} has no macro steps"))
        for step in steps:
            action = step.get("action")
            if action == "tap" and ("x" not in step or "y" not in step):
                errors.append(_issue("E_BAD_MACRO", f"{sid}: tap step without x/y"))
        if any(step.get("action") == "launch" for step in steps):
            reachable.add(sid)
    unreachable = sorted(id_set - reachable - {"screen_0"})
    if unreachable:
        warnings.append(
            _issue("W_UNREACHABLE", "screens without a launch-prefixed macro",
                   ", ".join(unreachable[:8]))
        )

    # near-duplicate screens hurt identification
    texts = {
        s.get("id", ""): {
            e.get("text")
            for e in (s.get("elements") or [])
            if e.get("text") and not e.get("redacted")
        }
        for s in screens
    }
    for a, b in combinations(sorted(texts), 2):
        ta, tb = texts[a], texts[b]
        if not ta or not tb:
            continue
        jaccard = len(ta & tb) / len(ta | tb)
        if jaccard >= max_duplicate_jaccard:
            warnings.append(
                _issue("W_DUP_SCREEN", f"{a} and {b} are nearly identical", f"J={jaccard:.2f}")
            )

    foreign = sorted({
        s.get("package", "") for s in screens if s.get("package") and s.get("package") != data.get("package")
    })
    if foreign:
        warnings.append(
            _issue("W_FOREIGN_SCREEN", "screens owned by other packages", ", ".join(foreign))
        )

    if redacted_total and element_total:
        share = redacted_total / element_total
        if share > 0.3:
            warnings.append(
                _issue("W_MANY_REDACTED", "a large share of labels is redacted",
                       f"{redacted_total}/{element_total}")
            )

    return errors, warnings


def format_report(path: Path, errors: list[dict], warnings: list[dict]) -> str:
    lines = [f"{path.name}: {len(errors)} errors, {len(warnings)} warnings"]
    for issue in errors:
        lines.append(f"  ERROR   {issue['code']:<16} {issue['message']}  {issue['detail']}")
    for issue in warnings:
        lines.append(f"  warning {issue['code']:<16} {issue['message']}  {issue['detail']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--map", action="append", default=None)
    parser.add_argument("--maps", default=None, help="directory of *.yaml maps")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    args = parser.parse_args(argv)

    paths: list[Path] = [Path(p) for p in (args.map or [])]
    if args.maps:
        paths += sorted(Path(args.maps).glob("*.yaml"))
    if not paths:
        parser.error("pass --map or --maps")

    failed = 0
    for path in paths:
        errors, warnings = validate_map(path)
        print(format_report(path, errors, warnings))
        if errors or (args.strict and warnings):
            failed += 1
    print(f"\n{len(paths)} maps, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
