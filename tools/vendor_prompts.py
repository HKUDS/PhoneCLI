#!/usr/bin/env python3
"""Copy the upstream prompt texts into phoneagent/core/prompts.py."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HEADER = '''"""Prompt texts used by the agent.

Vendored from the upstream ``phonecli/prompts.py`` so this package is
self-contained: the APK ships only ``phoneagent``, so importing the upstream
tree at runtime is not an option.

Regenerate with:  python3 tools/vendor_prompts.py
"""

'''
(REPO / "phoneagent/core/prompts.py").write_text(
    HEADER + (REPO / "phonecli/prompts.py").read_text()
)
print("vendored prompts")
