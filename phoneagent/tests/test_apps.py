"""The crawl verdict for an installed app, without a device."""

from __future__ import annotations

from phoneagent.apps import catalog, mapped_packages, verdict


def test_a_standard_launcher_activity_can_be_crawled():
    entry = verdict("com.android.settings", 0)
    assert entry["state"] == "crawlable"
    assert "可以爬" in entry["reason"]


def test_single_top_can_be_crawled():
    assert verdict("com.miui.notes", 1)["state"] == "crawlable"


def test_a_single_task_launcher_activity_is_only_a_warning():
    """Static launchMode is a hint: the clock is singleTask and crawls fine."""
    entry = verdict("com.android.calendar", 2)
    assert entry["state"] == "risky"
    assert "singleTask" in entry["reason"]


def test_single_instance_is_a_warning_too():
    assert verdict("com.example", 3)["state"] == "risky"


def test_an_app_that_already_has_a_map_says_so():
    assert verdict("com.android.settings", 0, mapped=True)["state"] == "mapped"


def test_mapped_packages_reads_the_maps_on_disk(tmp_path):
    (tmp_path / "settings.yaml").write_text(
        "app: 设置\npackage: com.android.settings\nscreens: []\n", encoding="utf-8")
    (tmp_path / "notes.yaml").write_text("package: 'com.miui.notes'\n", encoding="utf-8")
    assert mapped_packages(str(tmp_path)) == {"com.android.settings", "com.miui.notes"}


def test_mapped_packages_is_empty_without_a_directory():
    assert mapped_packages("") == set()
    assert mapped_packages("/nonexistent-path-for-tests") == set()


def test_a_catalog_is_empty_outside_the_app():
    # No JVM here, so the list must degrade to nothing rather than raise.
    assert catalog("") == []


# ---------------------------------------------------------------------------
# refusals remembered from real attempts
# ---------------------------------------------------------------------------


def test_a_refusal_is_written_once(tmp_path):
    from phoneagent.apps import load_refusals, record_refusal

    path = str(tmp_path / "refused-apps.txt")
    record_refusal(path, "com.android.browser")
    record_refusal(path, "com.android.browser")
    record_refusal(path, "com.android.calendar")
    assert load_refusals(path) == {"com.android.browser", "com.android.calendar"}


def test_reading_refusals_without_a_file_is_empty():
    from phoneagent.apps import load_refusals

    assert load_refusals("") == set()
    assert load_refusals("/nonexistent-path-for-tests") == set()


def test_the_catalog_marks_a_remembered_refusal(tmp_path, monkeypatch):
    import phoneagent.apps as apps

    path = str(tmp_path / "refused-apps.txt")
    apps.record_refusal(path, "com.android.browser")
    monkeypatch.setattr(apps, "installed_apps", lambda: [
        {"package": "com.android.browser", "label": "浏览器", "launch_mode": 0},
        {"package": "com.android.settings", "label": "设置", "launch_mode": 0},
    ])
    catalog = apps.catalog(str(tmp_path), path)
    states = {entry["package"]: entry["state"] for entry in catalog}
    assert states["com.android.browser"] == "refused"       # proven by a failed crawl
    assert states["com.android.settings"] == "crawlable"


def test_known_apps_maps_a_name_to_its_package(monkeypatch):
    import phoneagent.apps as apps

    monkeypatch.setattr(apps, "installed_apps", lambda: [
        {"package": "com.android.calendar", "label": "日历"},
        {"package": "com.android.settings", "label": "设置"},
        {"package": "com.miui.notes", "label": "笔记"},
    ])
    assert apps.known_apps() == {
        "日历": "com.android.calendar",
        "设置": "com.android.settings",
        "笔记": "com.miui.notes",
    }


def test_known_apps_falls_back_to_every_installed_app(monkeypatch):
    """MIUI's calendar has no launcher entry; the name must still resolve."""
    import phoneagent.apps as apps

    monkeypatch.setattr(apps, "installed_apps", lambda: [])
    monkeypatch.setattr(apps, "all_app_labels",
                        lambda: {"日历": "com.android.calendar"})
    assert apps.known_apps() == {"日历": "com.android.calendar"}


def test_known_apps_prefers_the_launcher_label(monkeypatch):
    import phoneagent.apps as apps

    monkeypatch.setattr(apps, "installed_apps",
                        lambda: [{"package": "com.android.calendar", "label": "日历"}])
    monkeypatch.setattr(apps, "all_app_labels",
                        lambda: {"日历": "com.xiaomi.calendar.other"})
    assert apps.known_apps()["日历"] == "com.android.calendar"


def test_known_apps_skips_entries_without_a_label(monkeypatch):
    import phoneagent.apps as apps

    monkeypatch.setattr(apps, "installed_apps", lambda: [
        {"package": "com.example.nameless", "label": ""},
        {"package": "com.android.settings", "label": "设置"},
    ])
    assert apps.known_apps() == {"设置": "com.android.settings"}


def test_a_successful_crawl_forgives_an_earlier_refusal(tmp_path):
    from phoneagent.apps import forgive_refusal, load_refusals, record_refusal

    path = str(tmp_path / "refused-apps.txt")
    record_refusal(path, "com.miui.notes")
    record_refusal(path, "com.android.browser")
    forgive_refusal(path, "com.miui.notes")
    assert load_refusals(path) == {"com.android.browser"}


def test_forgiving_an_app_that_was_never_refused_changes_nothing(tmp_path):
    from phoneagent.apps import forgive_refusal, load_refusals, record_refusal

    path = str(tmp_path / "refused-apps.txt")
    record_refusal(path, "com.android.browser")
    forgive_refusal(path, "com.miui.notes")
    assert load_refusals(path) == {"com.android.browser"}


def test_a_crawled_map_makes_an_app_look_mapped(tmp_path):
    """The catalogue asked only the parent directory and never saw a crawl."""
    from phoneagent.apps import mapped_packages

    crawls = tmp_path / "crawls"
    crawls.mkdir()
    (crawls / "淘宝.yaml").write_text(
        "app_name: 淘宝\npackage: com.taobao.taobao\nscreens: []\n", encoding="utf-8"
    )
    (crawls / "淘宝.public.yaml").write_text("package: com.example.share\n", encoding="utf-8")

    assert mapped_packages(str(crawls)) == {"com.taobao.taobao"}
    # the parent directory has nothing in it, which is what used to be passed
    assert mapped_packages(str(tmp_path)) == set()
