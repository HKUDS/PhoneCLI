"""Tests for map redaction (no device needed)."""

from __future__ import annotations

from pathlib import Path

import yaml

from phoneagent.redact import (
    EMAIL_TOKEN,
    PERSONAL_TOKEN,
    PHONE_TOKEN,
    _redact_text,
    redact_map,
)

MAP = """app: 设置
package: com.android.settings
screen_w: 1280
screen_h: 2772
screens:
- id: screen_0
  elements:
  - text: yangqin
    center: [0.2, 0.3]
  - text: 手机号：181****8488
    center: [0.2, 0.4]
  - text: me@example.com
    center: [0.2, 0.5]
  - text: WLAN
    center: [0.5, 0.6]
screen_macros:
  screen_1:
  - action: force_stop
    package: com.android.settings
  - action: tap
    x: 256
    y: 831
    text: yangqin
  - action: tap
    x: 640
    y: 1600
    text: WLAN
"""


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "map.yaml"
    path.write_text(MAP, encoding="utf-8")
    return path


def test_redact_text_masks_phone_email_and_terms():
    text, changed = _redact_text("手机号：181****8488", [])
    assert changed and PHONE_TOKEN in text and "181****8488" not in text

    text, changed = _redact_text("me@example.com", [])
    assert changed and text == EMAIL_TOKEN

    text, changed = _redact_text("yangqin的REDMI K90", ["yangqin"])
    assert changed and text.startswith(PERSONAL_TOKEN)


def test_redact_text_leaves_normal_labels_alone():
    text, changed = _redact_text("WLAN", ["yangqin"])
    assert not changed and text == "WLAN"


def test_redact_map_rewrites_elements_and_marks_them(tmp_path):
    path = _write(tmp_path)
    report = redact_map(path, ["yangqin"])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    texts = [e["text"] for e in data["screens"][0]["elements"]]
    assert PERSONAL_TOKEN in texts
    assert all("yangqin" not in t for t in texts)
    assert sum(1 for e in data["screens"][0]["elements"] if e.get("redacted")) >= 3
    assert report["elements"] >= 3


def test_redact_map_drops_text_anchors_that_no_longer_match(tmp_path):
    path = _write(tmp_path)
    redact_map(path, ["yangqin"])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    taps = [s for s in data["screen_macros"]["screen_1"] if s["action"] == "tap"]

    assert "text" not in taps[0]      # "yangqin" was redacted away
    assert taps[1]["text"] == "WLAN"  # untouched label keeps its anchor
    assert taps[1]["x"] == 640        # coordinates survive either way


def test_redact_can_write_a_public_copy_and_leave_the_source_alone(tmp_path):
    path = _write(tmp_path)
    original = path.read_text(encoding="utf-8")
    public = tmp_path / "map.public.yaml"

    report = redact_map(path, ["yangqin"], out=public)

    assert path.read_text(encoding="utf-8") == original      # source untouched
    assert "yangqin" in original
    assert "yangqin" not in public.read_text(encoding="utf-8")
    assert report["output"] == str(public)
