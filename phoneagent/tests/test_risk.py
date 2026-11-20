"""An app that asked for a captcha is not replayed until the record ages out."""

from __future__ import annotations

import time

import pytest

from phoneagent import risk


def test_record_and_avoid(tmp_path):
    path = str(tmp_path / "risks.json")
    assert risk.avoid(path, "com.example.app") is False

    entry = risk.record(path, "com.example.app")
    assert entry["count"] == 1
    assert risk.avoid(path, "com.example.app") is True
    assert "安全验证" in risk.describe(path, "com.example.app")


def test_the_record_ages_out(tmp_path):
    path = str(tmp_path / "risks.json")
    old = time.time() - (risk.RISK_DAYS + 1) * 86400
    risk.record(path, "com.example.app", now=old)
    assert risk.avoid(path, "com.example.app") is False


def test_a_second_challenge_is_counted(tmp_path):
    path = str(tmp_path / "risks.json")
    risk.record(path, "com.example.app")
    risk.record(path, "com.example.app")
    assert risk.load(path)["com.example.app"]["count"] == 2


def test_a_hand_mark_does_not_expire(tmp_path):
    path = str(tmp_path / "risks.json")
    risk.mark(path, "com.taobao.taobao", note="commercial anti-bot")
    very_old = time.time() + 400 * 86400
    assert risk.avoid(path, "com.taobao.taobao", now=very_old) is True
    assert "风控" in risk.describe(path, "com.taobao.taobao")


@pytest.mark.parametrize("path", ["risks.json", "state/risks.json"])
def test_relative_path_keeps_challenge_records(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    risk.record(path, "com.example.app", now=1000.0)
    assert risk.avoid(path, "com.example.app", now=1001.0)
    risk.record(path, "com.example.app", now=1002.0)
    assert risk.load(path)["com.example.app"]["count"] == 2


@pytest.mark.parametrize("path", ["risks.json", "state/risks.json"])
def test_relative_path_keeps_manual_marks(tmp_path, monkeypatch, path):
    monkeypatch.chdir(tmp_path)
    risk.mark(path, "com.example.app", note="owner marked", now=1000.0)
    assert risk.avoid(path, "com.example.app", now=1000.0 + 400 * 86400)
    assert risk.load(path)["com.example.app"]["note"] == "owner marked"
