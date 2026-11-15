"""Offline tests for the merged app map (no phone, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest

from phoneagent.core import app_map as am
from phoneagent.core.app_map import AppMap
from phoneagent.core.macro import validate_macro

REPO = Path(__file__).resolve().parents[2]
IOS_MAP = REPO / "phonecli" / "app_maps" / "settings_map.yaml"
ANDROID_MAP = REPO / "phonecli_android" / "app_maps" / "settings_android.yaml"
IOS_MAPS = sorted((REPO / "phonecli" / "app_maps").glob("*.yaml"))
ANDROID_MAPS = sorted((REPO / "phonecli_android" / "app_maps").glob("*.yaml"))


# ---------------------------------------------------------------------------
# loading / platform inference
# ---------------------------------------------------------------------------


def test_ios_map_loads():
    m = AppMap(IOS_MAP)
    assert m.platform == "ios"
    assert m.package == "com.apple.Preferences"
    assert m.size == (402, 874)
    assert len(m.screens) > 3


def test_android_map_loads():
    m = AppMap(ANDROID_MAP)
    assert m.platform == "android"
    assert m.package == "com.android.settings"
    assert m.size == (1440, 3120)


def test_platform_inferred_from_macro_key_for_third_party_ios_map():
    """iOS maps for non-Apple apps (weibo, dianping...) still infer as iOS."""
    m = AppMap(REPO / "phonecli" / "app_maps" / "dianping_map.yaml")
    assert m.platform == "ios"
    assert m.package.startswith("com.dianping")


def test_platform_can_be_forced():
    m = AppMap(ANDROID_MAP, platform="ios")
    assert m.platform == "ios"


# ---------------------------------------------------------------------------
# XML parsing
# ---------------------------------------------------------------------------


def test_ios_xml_texts():
    xml = (
        '<XCUIElementTypeApplication name="Settings">'
        '<XCUIElementTypeCell label="Wi-Fi" name="Wi-Fi"/>'
        '<XCUIElementTypeStaticText label="Bluetooth" name="Bluetooth"/>'
        "</XCUIElementTypeApplication>"
    )
    assert am.texts_from_ios_xml(xml) == {"Wi-Fi", "Bluetooth"}
    assert am.detect_xml_platform(xml) == "ios"


def test_android_xml_texts():
    xml = (
        '<hierarchy rotation="0">'
        '<node text="Network &amp; internet" content-desc="" />'
        '<node text="" content-desc="Bluetooth" />'
        "</hierarchy>"
    )
    assert am.texts_from_android_xml(xml) == {"Network &amp; internet", "Bluetooth"}
    assert am.detect_xml_platform(xml) == "android"


def _ios_dump_for(screen, limit=None):
    parts = ['<XCUIElementTypeApplication name="app">']
    # a real dump only contains what is on screen, i.e. nothing that needs
    # scrolling to reach
    visible = [e for e in screen.elements if e.found_at_scroll == 0] or screen.elements
    for element in (visible if limit is None else visible[:limit]):
        safe = element.text.replace('"', "&quot;")
        parts.append(f'<XCUIElementTypeStaticText label="{safe}" name="{safe}"/>')
    parts.append("</XCUIElementTypeApplication>")
    return "".join(parts)


def _android_dump_for(screen, limit=None):
    parts = ['<hierarchy rotation="0">']
    visible = [e for e in screen.elements if e.found_at_scroll == 0] or screen.elements
    for element in (visible if limit is None else visible[:limit]):
        safe = element.text.replace('"', "&quot;")
        parts.append(f'<node text="{safe}" content-desc="" />')
    parts.append("</hierarchy>")
    return "".join(parts)


def test_identify_ios_screen():
    m = AppMap(IOS_MAP)
    target = m.screens[1]
    screen_id, confidence = m.identify_current_screen(_ios_dump_for(target))
    assert screen_id == target.id
    assert confidence >= 0.5


def test_identify_android_screen():
    m = AppMap(ANDROID_MAP)
    target = m.screens[1]
    screen_id, confidence = m.identify_current_screen(_android_dump_for(target))
    assert screen_id == target.id
    assert confidence >= 0.5


def test_identify_ignores_declared_platform_when_dump_differs():
    """An XCUITest dump handed to an Android-declared map still identifies.

    The map records which platform it was built for, but the dump format is
    what actually decides the parser; this keeps one map usable from either
    backend while a migration is in flight.
    """
    m = AppMap(ANDROID_MAP)  # declared android
    target = m.screens[1]
    screen_id, _ = m.identify_current_screen(_ios_dump_for(target), platform="auto")
    assert screen_id == target.id


def test_identify_unknown_returns_none():
    m = AppMap(IOS_MAP)
    screen_id, confidence = m.identify_current_screen("<hierarchy></hierarchy>")
    assert screen_id is None
    assert confidence == 0.0


# ---------------------------------------------------------------------------
# operations catalog
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [IOS_MAP, ANDROID_MAP])
def test_build_operations(path):
    m = AppMap(path)
    ops = m.build_operations()
    assert ops, "catalog must not be empty"
    for op in ops.values():
        assert op.macro, f"{op.id} has an empty macro"
        assert op.macro[-1]["action"] == "tap"
        if op.type == "NAV":
            assert op.target_screen, f"NAV {op.id} without target_screen"
        if op.type == "ACT":
            assert op.target_screen == ""


def test_catalog_styles_differ():
    m = AppMap(ANDROID_MAP)
    ops = m.build_operations()
    flat = m.format_ops_catalog(ops, style="flat")
    tree = m.format_ops_catalog(ops, style="tree")
    assert "[NAV]" in flat or "[ACT]" in flat
    assert "# " in tree
    assert tree != flat
    assert len(tree) <= len(flat)


def test_nav_only_filter():
    m = AppMap(ANDROID_MAP)
    ops = m.build_operations()
    nav_count = sum(1 for op in ops.values() if op.type == "NAV")
    tree_nav = m.format_ops_catalog(ops, style="tree", nav_only=True)
    tree_all = m.format_ops_catalog(ops, style="tree")
    assert tree_nav.count("# ") == nav_count
    assert tree_nav.count("# ") <= tree_all.count("# ")


def test_target_screen_info():
    m = AppMap(ANDROID_MAP)
    ops = m.build_operations()
    nav_op = next(op for op in ops.values() if op.type == "NAV")
    # Not every map has descriptions, but the call must be safe.
    assert isinstance(m.get_target_screen_info(ops, nav_op.id), str)


# ---------------------------------------------------------------------------
# macros / navigation
# ---------------------------------------------------------------------------


def test_find_relative_macro_strips_shared_prefix():
    m = AppMap(IOS_MAP)
    assert m.find_relative_macro("screen_0", "screen_0") == []
    # Unknown source behaves like a cold start.
    assert m.find_relative_macro("screen_99", "screen_0") == m.get_macro("screen_0")


def test_every_map_macro_is_executable():
    """Static validation of all shipped macros (17 maps)."""
    problems = []
    for path in IOS_MAPS + ANDROID_MAPS:
        m = AppMap(path)
        for screen_id, macro in m._macros.items():
            for issue in validate_macro(macro):
                problems.append(f"{path.name}:{screen_id}: {issue}")
    assert not problems, "\n".join(problems)


def test_nav_reference_renders():
    m = AppMap(ANDROID_MAP)
    ref = m.format_nav_reference()
    assert "Navigation Reference" in ref or ref.startswith("App:")


def test_enriched_screen_hint():
    m = AppMap(IOS_MAP)
    hint = m.build_enriched_screen_hint("screen_0")
    assert hint.startswith('You are on "screen_0"')
    assert m.build_enriched_screen_hint("nope") == ""


def test_identification_can_be_scoped_to_the_foreground_package(tmp_path):
    """Two screens with the same labels, owned by different packages."""
    import yaml as _yaml

    data = {
        "app": "Settings",
        "package": "com.android.settings",
        "screen_w": 1000,
        "screen_h": 2000,
        "screens": [
            {"id": "screen_0", "elements": [
                {"text": "应用设置", "center": [0.5, 0.5]},
                {"text": "应用双开", "center": [0.5, 0.6]},
            ]},
            {"id": "screen_1", "package": "com.miui.securitycenter", "elements": [
                {"text": "应用设置", "center": [0.5, 0.5]},
                {"text": "存储占用", "center": [0.5, 0.7]},
            ]},
        ],
        "screen_macros": {"screen_0": [], "screen_1": []},
    }
    path = tmp_path / "m.yaml"
    path.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    m = AppMap(path)
    dump = ('<hierarchy><node text="应用设置" bounds="[0,0][10,10]" />'
            '<node text="存储占用" bounds="[0,0][10,10]" /></hierarchy>')

    sid, _ = m.identify_current_screen(dump)
    assert sid == "screen_1"
    sid, _ = m.identify_current_screen(dump, package="com.miui.securitycenter")
    assert sid == "screen_1"
    sid, conf = m.identify_current_screen(dump, package="com.android.settings")
    assert sid == "screen_0"  # only its own screens are considered


def test_rarity_breaks_ties_between_near_identical_screens(tmp_path):
    """Two screens sharing every label except one distinctive row.

    Identification used to resolve this by insertion order, so the answer could
    differ between two identical dumps. The screen holding the distinctive
    label must win every time.
    """
    import yaml as _yaml

    shared = [{"text": "闹钟", "center": [0.2, 0.95]},
              {"text": "计时", "center": [0.6, 0.95]}]
    data = {
        "app": "Clock", "package": "com.android.deskclock",
        "screen_w": 1000, "screen_h": 2000,
        "screens": [
            {"id": "screen_0", "elements": shared + [
                {"text": "06:00", "center": [0.5, 0.2]}]},
            {"id": "screen_1", "elements": shared + [
                {"text": "添加闹钟", "center": [0.8, 0.9]}]},
        ],
        "screen_macros": {"screen_0": [], "screen_1": []},
    }
    path = tmp_path / "clock.yaml"
    path.write_text(_yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    app = AppMap(path)

    dump = ('<hierarchy>'
            '<node text="闹钟" bounds="[0,0][10,10]"/>'
            '<node text="计时" bounds="[0,0][10,10]"/>'
            '<node text="添加闹钟" bounds="[0,0][10,10]"/>'
            '</hierarchy>')

    first = app.identify_current_screen(dump)[0]
    second = app.identify_current_screen(dump)[0]
    assert first == second == "screen_1"


def test_a_selected_tab_decides_which_tab_screen_is_current(tmp_path):
    """Four clock tabs share their chrome; the highlighted tab tells them apart."""
    from phoneagent.core.app_map import AppMap

    def screen(sid, tab, extra):
        elements = [
            {"text": "闹钟", "center": [0.5, 0.1], "fixed": True},
            {"text": "世界时钟", "center": [0.5, 0.2], "fixed": True},
            {"text": "秒表", "center": [0.5, 0.3], "fixed": True},
            {"text": "计时", "center": [0.5, 0.4], "fixed": True},
        ] + [{"text": t, "center": [0.5, 0.5 + i / 20], "fixed": True}
             for i, t in enumerate(extra)]
        return {"id": sid, "description": f"{tab} page", "selected": [tab],
                "elements": elements}

    data = {
        "app": "时钟", "package": "com.android.deskclock",
        "screens": [
            screen("screen_0", "闹钟", []),
            screen("screen_1", "世界时钟", []),
        ],
        "screen_macros": {},
    }
    import yaml
    path = tmp_path / "clock.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    def dump(tab):
        return (
            '<hierarchy>'
            f'<node text="{tab}" selected="true" bounds="[0,2600][200,2740]" />'
            '<node text="闹钟" bounds="[0,200][200,300]" />'
            '<node text="世界时钟" bounds="[0,300][200,400]" />'
            '<node text="秒表" bounds="[0,400][200,500]" />'
            '<node text="计时" bounds="[0,500][200,600]" />'
            '</hierarchy>'
        )

    app_map = AppMap(str(path))
    assert app_map.identify_current_screen(dump("世界时钟"))[0] == "screen_1"
    assert app_map.identify_current_screen(dump("闹钟"))[0] == "screen_0"


def test_a_one_character_chinese_tab_becomes_an_operation(tmp_path):
    """我 is the profile tab; dropping it made the page unreachable."""
    import yaml
    from phoneagent.core.app_map import AppMap

    data = {
        "app": "小红书", "package": "com.xingin.xhs", "screen_w": 1280, "screen_h": 2772,
        "screens": [
            {"id": "screen_0", "selected": ["首页"], "elements": [
                {"text": "我", "center": [0.9, 0.95], "fixed": True, "leads_to": "screen_1"},
                {"text": "x", "center": [0.1, 0.1], "fixed": True},
            ]},
            {"id": "screen_1", "selected": ["我"], "elements": [
                {"text": "粉丝", "center": [0.9, 0.1], "fixed": True}]},
        ],
        "screen_macros": {},
    }
    path = tmp_path / "xhs.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    ops = AppMap(str(path)).build_operations()
    assert "我" in ops, "the profile tab must be routable"
    assert ops["我"].target_screen == "screen_1"
    assert "x" not in ops, "a one-letter Latin label is still noise"
