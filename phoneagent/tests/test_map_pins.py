"""Choosing which map represents a package, and pinning over the rule."""

from __future__ import annotations

import os
import time

from phoneagent.map_pins import choose, load_pins, pinned_name, save_pins


class FakeMap:
    def __init__(self, package: str, screens: int) -> None:
        self.package = package
        self.screens = list(range(screens))


def _entry(tmp_path, name: str, package: str, screens: int, age_hours: float = 1.0):
    path = tmp_path / name
    path.write_text("app_name: x\n")
    stamp = time.time() - age_hours * 3600
    os.utime(path, (stamp, stamp))
    return (str(path), FakeMap(package, screens))


def test_the_map_with_more_screens_wins(tmp_path):
    small = _entry(tmp_path, "a.yaml", "com.x", 1)
    big = _entry(tmp_path, "b.yaml", "com.x", 9)
    chosen = choose([small, big], {})
    assert os.path.basename(chosen[0][0]) == "b.yaml"


def test_a_tie_goes_to_the_newer_file(tmp_path):
    old = _entry(tmp_path, "old.yaml", "com.x", 5, age_hours=10)
    new = _entry(tmp_path, "new.yaml", "com.x", 5, age_hours=1)
    chosen = choose([old, new], {})
    assert os.path.basename(chosen[0][0]) == "new.yaml"


def test_a_pin_beats_the_rule(tmp_path):
    small = _entry(tmp_path, "a.yaml", "com.x", 1)
    big = _entry(tmp_path, "b.yaml", "com.x", 9)
    chosen = choose([small, big], {"com.x": "a.yaml"})
    assert os.path.basename(chosen[0][0]) == "a.yaml"


def test_a_pin_that_matches_nothing_falls_back_to_the_rule(tmp_path):
    small = _entry(tmp_path, "a.yaml", "com.x", 1)
    big = _entry(tmp_path, "b.yaml", "com.x", 9)
    chosen = choose([small, big], {"com.x": "deleted.yaml"})
    assert os.path.basename(chosen[0][0]) == "b.yaml"


def test_different_packages_are_kept_apart(tmp_path):
    one = _entry(tmp_path, "a.yaml", "com.x", 1)
    two = _entry(tmp_path, "b.yaml", "com.y", 1)
    chosen = choose([one, two], {})
    assert len(chosen) == 2


def test_pins_round_trip(tmp_path):
    path = str(tmp_path / "pins.json")
    save_pins(path, {"com.x": "a.yaml"})
    assert load_pins(path) == {"com.x": "a.yaml"}
    assert pinned_name(load_pins(path), FakeMap("com.x", 1), "/tmp/a.yaml") is True


def test_a_broken_pins_file_is_not_fatal(tmp_path):
    path = tmp_path / "pins.json"
    path.write_text("not json", encoding="utf-8")
    assert load_pins(str(path)) == {}


def test_a_crawl_takes_over_from_a_shipped_map_with_the_same_screens(tmp_path):
    """Shipped maps are re-extracted at every start, so mtime always wins them."""
    shipped = tmp_path / "maps"
    crawled = tmp_path / "crawls"
    shipped.mkdir()
    crawled.mkdir()
    asset = shipped / "settings.yaml"
    asset.write_text("app_name: settings\n")
    crawl = crawled / "设置.yaml"
    crawl.write_text("app_name: 设置\n")
    os.utime(asset, (time.time(), time.time()))          # newest on disk
    os.utime(crawl, (time.time() - 3600, time.time() - 3600))

    entries = [(str(asset), FakeMap("com.android.settings", 16)),
               (str(crawl), FakeMap("com.android.settings", 16))]
    chosen = choose(entries, {}, shipped_dirs=(str(shipped),))
    assert os.path.basename(chosen[0][0]) == "设置.yaml"

    # without the hint the newer file wins, which is the old behaviour
    plain = choose(entries, {})
    assert os.path.basename(plain[0][0]) == "settings.yaml"
