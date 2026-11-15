"""Write a file so that a crash cannot leave half of it behind.

The app is force-stopped a lot (every install during development, and every
OEM battery manager in production), and these files hold state that is not
recoverable from anywhere else: the pins that decide which map is in use, the
risk marks that stop the agent from tapping a captcha, the user's memories.
A plain ``open(path, "w")`` truncates first, so being killed mid-write loses the
whole file rather than the last change.

``os.replace`` is atomic on the same filesystem, which is why the temporary
file is written beside the target and not in /tmp.
"""
from __future__ import annotations

import json
import os


def write_text(path: str, text: str, mode: int = 0o600) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def write_json(path: str, data, indent: int = 1, mode: int = 0o600) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=indent), mode)
