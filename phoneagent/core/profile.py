"""Per-app profile: which on-screen texts are dynamic noise, which are structure.

Port of ``phonecli/profile_builder.py``. A crawl records whatever text happened
to be on screen, so on a personal phone the map bakes in the account name, the
Wi-Fi SSID and the device model:

    yangqin
    yangqin的REDMI K90 Ultra
    手机号：181****8488
    1101,已连接,WLAN 信号强度为三格。

None of that is navigation -- it is one phone's data. A profile names it so the
map stays device-independent:

* ``dynamic_patterns`` -- regexes; matching elements are dropped from the map's
  fingerprints and from the operation catalog (``redacted: true``)
* ``preserve_navigation`` -- texts that must never be dropped even if a pattern
  happens to match them (tab bars, short menu labels)

The same file can be produced by an LLM (``PROFILE_GENERATION_PROMPT``) or by
heuristics when no key is configured.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

import yaml

from .prompts import PROFILE_GENERATION_PROMPT


@dataclass
class Profile:
    app: str = ""
    package: str = ""
    dynamic_patterns: list[str] = field(default_factory=list)
    preserve_navigation: list[str] = field(default_factory=list)
    source: str = ""  # "llm" | "heuristic", for the report

    def __post_init__(self) -> None:
        self._preserve = {t.strip().lower() for t in self.preserve_navigation if t.strip()}
        compiled = []
        for pattern in self.dynamic_patterns:
            try:
                compiled.append(re.compile(pattern, re.IGNORECASE))
            except re.error:
                # a bad pattern in a hand-edited profile must not break a build
                continue
        self._compiled = compiled

    def is_dynamic(self, text: str) -> bool:
        """Should this element be kept out of the map?"""
        stripped = (text or "").strip()
        if not stripped:
            return False
        if stripped.lower() in self._preserve:
            return False
        return any(pattern.match(stripped) for pattern in self._compiled)

    def to_yaml(self) -> str:
        return yaml.safe_dump(
            {
                "app": self.app,
                "package": self.package,
                "dynamic_patterns": self.dynamic_patterns,
                "preserve_navigation": self.preserve_navigation,
            },
            allow_unicode=True,
            sort_keys=False,
        )


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def load_profile(path: str) -> Optional[Profile]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return Profile(
        app=str(data.get("app", "")),
        package=str(data.get("package", data.get("bundle_id", ""))),
        dynamic_patterns=list(data.get("dynamic_patterns", []) or []),
        preserve_navigation=list(data.get("preserve_navigation", []) or []),
        source=str(data.get("source", "file")),
    )


def save_profile(profile: Profile, path: str) -> None:
    import os

    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(profile.to_yaml())


# ---------------------------------------------------------------------------
# building a profile
# ---------------------------------------------------------------------------


def collect_map_texts(map_path: str) -> list[str]:
    """Unique element texts already recorded in a map.

    Lets a profile be generated for a map built before profiles existed,
    without crawling the device again.
    """
    with open(map_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    seen: dict[str, None] = {}
    for screen in data.get("screens", []) or []:
        for element in screen.get("elements", []) or []:
            text = str(element.get("text", "")).strip()
            if text:
                seen[text] = None
    return list(seen)


#: Texts that are obviously one device's data, whatever the app.
_DEVICE_NOISE = [
    (r"^\d+\.\d+\.\d+\.\d+", "ip address"),
    (r"^\d{2}:\d{2}$", "clock"),
    (r"^\d{4}[-/年]\d{1,2}[-/月]\d{1,2}", "date"),
    (r"^[\d,]{4,}$", "long number"),
    (r"^[^,]*,[^,]*,[^,]*,[^,]*$", "comma-joined status"),
]


def heuristic_profile(app: str, package: str, texts: Iterable[str]) -> Profile:
    """A usable profile without a model.

    Deliberately conservative: only patterns that a human would agree are one
    device's data. Anything longer than 8 characters is *not* blanket-dropped
    here -- that would remove real menu rows.
    """
    texts = [t.strip() for t in texts if t and t.strip()]
    patterns = [pattern for pattern, _why in _DEVICE_NOISE]
    preserve = [t for t in texts if 2 <= len(t) <= 6][:200]
    profile = Profile(
        app=app,
        package=package,
        dynamic_patterns=patterns,
        preserve_navigation=preserve,
        source="heuristic",
    )
    # keep the device-name-looking entries out as well
    for text in texts:
        if re.search(r"(的)?(REDMI|Xiaomi|iPhone|Galaxy|Pixel)\b", text):
            profile.dynamic_patterns.append("^" + re.escape(text) + "$")
            profile.__post_init__()
    return profile


def generate_profile(
    app: str,
    package: str,
    texts: Iterable[str],
    llm=None,
) -> Profile:
    """Classify the element list with a model, falling back to heuristics."""
    texts = [t.strip() for t in texts if t and t.strip()]
    if llm is None:
        return heuristic_profile(app, package, texts)

    # A long element list makes a long JSON answer, and a truncating model then
    # returns an unparseable one. Retry with fewer elements rather than give up.
    for limit in (120, 60):
        element_list = "\n".join(f"- {text}" for text in texts[:limit])
        system = PROFILE_GENERATION_PROMPT.format(app_name=app, element_list=element_list)
        try:
            reply = llm.text(system, "Output the JSON profile.", temperature=0.0)
        except Exception:  # noqa: BLE001 - a failed profile must not block the build
            return heuristic_profile(app, package, texts)
        data = _extract_json(reply)
        if isinstance(data, dict):
            break
    else:
        return heuristic_profile(app, package, texts)
    return Profile(
        app=app,
        package=package,
        dynamic_patterns=[str(p) for p in data.get("dynamic_patterns", []) or []],
        preserve_navigation=[str(t) for t in data.get("preserve_navigation", []) or []],
        source="llm",
    )


def _extract_json(text: str):
    text = (text or "").strip()
    if "{" not in text:
        return None
    end = text.rfind("}")
    if end < 0:
        return None  # truncated answer: nothing to salvage
    for candidate in (text, text[text.find("{"): end + 1]):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# applying a profile to a map
# ---------------------------------------------------------------------------


def sanitize_map(map_path: str, profile: Profile, output: Optional[str] = None) -> dict:
    """Mark every dynamic element so it drops out of fingerprints and the catalog.

    Replaces the text with ``<dynamic>`` as well: leaving the raw string in the
    file would keep the personal data the profile exists to remove.
    """
    with open(map_path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    marked: list[str] = []
    total = 0
    for screen in data.get("screens", []) or []:
        for element in screen.get("elements", []) or []:
            total += 1
            text = str(element.get("text", ""))
            if profile.is_dynamic(text):
                marked.append(text)
                element["redacted"] = True
                element["text"] = "<dynamic>"

    data["profile_applied"] = profile.app or profile.package
    data["profile_source"] = profile.source
    target = output or map_path
    with open(target, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False)

    return {"total": total, "marked": len(marked), "examples": marked[:10], "output": target}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate an app profile and sanitize a map.")
    parser.add_argument("--map", required=True, help="app map to profile")
    parser.add_argument("--profile", default="", help="write the profile here")
    parser.add_argument("--out", default="", help="write the sanitized map here (default: in place)")
    parser.add_argument("--api-key", default="", help="enable the LLM profile with this key")
    parser.add_argument("--api-base", default="https://openrouter.ai/api/v1")
    parser.add_argument("--model", default="qwen/qwen3.8-flash")
    args = parser.parse_args(argv)

    with open(args.map, encoding="utf-8") as fh:
        header = yaml.safe_load(fh) or {}
    app = str(header.get("app", ""))
    package = str(header.get("package", ""))

    texts = collect_map_texts(args.map)
    llm = None
    if args.api_key:
        # through the factory: the protocol depends on the base URL
        from ..server import make_llm

        llm = make_llm(args.api_key, args.api_base, args.model)
        # a profile for a 230-element screen is a large JSON answer
        if getattr(llm, "max_tokens", 0) < 3000:
            llm.max_tokens = 3000
    profile = generate_profile(app, package, texts, llm=llm)
    print(f"[profile] {profile.source}: {len(profile.dynamic_patterns)} patterns, "
          f"{len(profile.preserve_navigation)} preserved")
    if args.profile:
        save_profile(profile, args.profile)
        print(f"[profile] written to {args.profile}")

    report = sanitize_map(args.map, profile, args.out or None)
    print(f"[profile] {report['marked']}/{report['total']} elements marked dynamic")
    for example in report["examples"]:
        print(f"  - {example[:60]}")
    print(f"[profile] sanitized map: {report['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
