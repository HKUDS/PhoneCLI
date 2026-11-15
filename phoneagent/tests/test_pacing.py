"""One app is not crawled twice in a quarter of an hour."""

from __future__ import annotations

import time

from phoneagent import pacing


def test_a_fresh_app_may_be_crawled(tmp_path):
    path = str(tmp_path / "pacing.json")
    assert pacing.remaining(path, "com.example.app") == 0.0


def test_a_crawl_parks_the_app(tmp_path):
    path = str(tmp_path / "pacing.json")
    pacing.mark(path, "com.example.app")
    left = pacing.remaining(path, "com.example.app")
    assert 0 < left <= pacing.CRAWL_COOLDOWN


def test_the_park_expires(tmp_path):
    path = str(tmp_path / "pacing.json")
    old = time.time() - pacing.CRAWL_COOLDOWN - 60
    pacing.mark(path, "com.example.app", now=old)
    assert pacing.remaining(path, "com.example.app") == 0.0


def test_tap_gap_is_human_sized():
    low, high = pacing.TAP_GAP
    assert 0.3 <= low < high <= 3.0


def test_system_apps_are_not_treated_like_shops():
    """Settings has no account to protect; Taobao does."""
    assert pacing.is_commercial("com.taobao.taobao") is True
    assert pacing.is_commercial("com.sankuai.meituan") is True
    assert pacing.is_commercial("com.android.settings") is False
    assert pacing.is_commercial("com.miui.calculator") is False


def test_the_cooldown_depends_on_the_kind_of_app(tmp_path):
    path = str(tmp_path / "pacing.json")
    pacing.mark(path, "com.miui.calculator")
    left = pacing.remaining(path, "com.miui.calculator")
    assert 0 < left <= pacing.UTILITY_COOLDOWN

    pacing.mark(path, "com.taobao.taobao")
    left = pacing.remaining(path, "com.taobao.taobao")
    assert left > pacing.UTILITY_COOLDOWN
