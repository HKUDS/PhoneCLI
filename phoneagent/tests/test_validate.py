"""Tests for the map validator (no device needed)."""

from __future__ import annotations

from pathlib import Path

import yaml

from phoneagent.validate import validate_map

GOOD = {
    "app": "Settings",
    "package": "com.android.settings",
    "screen_w": 1080,
    "screen_h": 1920,
    "screens": [
        {
            "id": "screen_0",
            "elements": [
                {"text": "WLAN", "center": [0.5, 0.2], "leads_to": "screen_1"},
                {"text": "Bluetooth", "center": [0.5, 0.4]},
                {"text": "Display", "center": [0.5, 0.6]},
            ],
        },
        {
            "id": "screen_1",
            "elements": [
                {"text": "Wi-Fi", "center": [0.3, 0.3]},
                {"text": "Networks", "center": [0.3, 0.5]},
            ],
        },
    ],
    "screen_macros": {
        "screen_0": [
            {"action": "force_stop", "package": "com.android.settings"},
            {"action": "launch", "package": "com.android.settings", "wait": 3.0},
        ],
        "screen_1": [
            {"action": "force_stop", "package": "com.android.settings"},
            {"action": "launch", "package": "com.android.settings", "wait": 3.0},
            {"action": "tap", "x": 540, "y": 384, "text": "WLAN"},
        ],
    },
}


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "map.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_good_map_has_no_issues(tmp_path):
    errors, warnings = validate_map(_write(tmp_path, GOOD))
    assert errors == []


def test_missing_top_level_key(tmp_path):
    data = dict(GOOD)
    data.pop("screen_macros")
    errors, _ = validate_map(_write(tmp_path, data))
    assert any(e["code"] == "E_MISSING_KEY" for e in errors)


def test_center_outside_unit_range(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(GOOD))
    data["screens"][0]["elements"][0]["center"] = [1.4, 0.2]
    errors, _ = validate_map(_write(tmp_path, data))
    assert any(e["code"] == "E_BAD_CENTER" for e in errors)


def test_unknown_edge_target(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(GOOD))
    data["screens"][0]["elements"][0]["leads_to"] = "screen_99"
    errors, _ = validate_map(_write(tmp_path, data))
    assert any(e["code"] == "E_UNKNOWN_TARGET" for e in errors)


def test_personal_data_is_an_error_but_hotlines_are_not(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(GOOD))
    data["screens"][0]["elements"][1]["text"] = "手机号 13800001111"
    errors, _ = validate_map(_write(tmp_path, data))
    assert any(e["code"] == "E_PII" for e in errors)

    data["screens"][0]["elements"][1]["text"] = "客服 400-100-3399"
    errors, _ = validate_map(_write(tmp_path, data))
    assert not any(e["code"] == "E_PII" for e in errors)


def test_duplicate_screens_warn(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(GOOD))
    data["screens"][1]["elements"] = [
        dict(e) for e in data["screens"][1]["elements"]
    ]
    data["screens"].append(
        {"id": "screen_2", "elements": [dict(e) for e in data["screens"][1]["elements"]]}
    )
    data["screen_macros"]["screen_2"] = [
        {"action": "launch", "package": "com.android.settings"}
    ]
    _, warnings = validate_map(_write(tmp_path, data))
    assert any(w["code"] == "W_DUP_SCREEN" for w in warnings)


def test_redacted_elements_are_excluded_from_matching(tmp_path):
    data = yaml.safe_load(yaml.safe_dump(GOOD))
    data["screens"][0]["elements"].append(
        {"text": "<personal>", "center": [0.2, 0.9], "redacted": True}
    )
    errors, warnings = validate_map(_write(tmp_path, data))
    assert errors == []
