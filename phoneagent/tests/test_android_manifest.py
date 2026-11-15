"""Structural checks on the app manifest.

An XML comment cannot contain a double hyphen, and one written inside a
sentence in a manifest comment broke the build with a message that pointed at
the line but not the cause. This parses the file so that mistake fails here
instead, together with the two properties the product depends on.
"""
from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET

import pytest

ANDROID = "{http://schemas.android.com/apk/res/android}"
MANIFEST = (pathlib.Path(__file__).resolve().parents[2]
            / "android-app" / "app" / "src" / "main" / "AndroidManifest.xml")


@pytest.mark.skipif(not MANIFEST.exists(), reason="no app in this checkout")
def test_the_manifest_parses():
    ET.parse(MANIFEST)


@pytest.mark.skipif(not MANIFEST.exists(), reason="no app in this checkout")
def test_the_agent_services_are_not_exported():
    """Any app on the phone could otherwise start the agent."""
    root = ET.parse(MANIFEST).getroot()
    app = root.find("application")
    assert app is not None
    for service in app.findall("service"):
        name = service.get(ANDROID + "name", "")
        if name.endswith(("AgentService", "PairingService")):
            assert service.get(ANDROID + "exported") == "false", name


@pytest.mark.skipif(not MANIFEST.exists(), reason="no app in this checkout")
def test_launchable_apps_are_visible_without_the_broad_permission():
    """The agent launches apps by name, which needs visibility, not QUERY_ALL."""
    root = ET.parse(MANIFEST).getroot()
    permissions = {p.get(ANDROID + "name") for p in root.findall("uses-permission")}
    assert "android.permission.QUERY_ALL_PACKAGES" not in permissions
    queries = root.findall("queries")
    assert queries, "a <queries> block is what grants launcher visibility"
    assert "android.intent.category.LAUNCHER" in ET.tostring(queries[0]).decode()
