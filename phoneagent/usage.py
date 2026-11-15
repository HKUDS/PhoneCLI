"""Token usage: what each call cost, kept by day and by model.

The agent makes two kinds of calls -- one cheap one to route a task, then one
per round while it acts -- and until now nothing recorded what either cost.
Providers return a ``usage`` block with every response; this module writes it
down and adds it up.

One JSON object per line, append-only. A month of ordinary use is a few
thousand lines, so nothing more elaborate is warranted; the file is pruned on
write so it cannot grow without bound.
"""

from __future__ import annotations

import json
import os
import threading
import time

#: Older lines are dropped when the file is rewritten.
KEEP_DAYS = 45

#: Set per task so every call can be attributed to the request that caused it.
#: Thread-local because a task runs on its own thread and two tasks can overlap
#: while one waits in a queue.
_context = threading.local()


def set_context(task_id: str, label: str = "") -> None:
    """Attribute subsequent records to a task."""
    _context.task = str(task_id or "")
    _context.label = str(label or "")[:28]


def clear_context() -> None:
    _context.task = ""
    _context.label = ""


def record(
    path: str,
    model: str,
    base: str,
    prompt: int,
    completion: int,
    kind: str = "",
    when: float = 0.0,
) -> None:
    """Append one call's usage. Missing counts are recorded as zero."""
    if not path:
        return
    entry = {
        "t": int(when or time.time()),
        "m": str(model or ""),
        "b": str(base or ""),
        "p": int(prompt or 0),
        "c": int(completion or 0),
        "k": str(kind or ""),
    }
    task = getattr(_context, "task", "")
    if task:
        entry["task"] = task
        label = getattr(_context, "label", "")
        if label:
            entry["x"] = label
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:  # a usage log must never break a task
        print(f"[usage] could not record: {exc}")


def load(path: str) -> list:
    """Every recorded call, oldest first. Unreadable lines are skipped."""
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and "t" in entry:
                    out.append(entry)
    except OSError as exc:
        print(f"[usage] could not read: {exc}")
    return out


def prune(path: str, keep_days: int = KEEP_DAYS, now: float = 0.0) -> int:
    """Drop lines older than ``keep_days``; returns how many were removed."""
    entries = load(path)
    if not entries:
        return 0
    cutoff = (now or time.time()) - keep_days * 86400
    kept = [entry for entry in entries if float(entry.get("t", 0)) >= cutoff]
    if len(kept) == len(entries):
        return 0
    try:
        with open(path, "w", encoding="utf-8") as handle:
            for entry in kept:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[usage] could not prune: {exc}")
        return 0
    return len(entries) - len(kept)


def _day(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(timestamp))


#: Known providers get their own name; anything else keeps its host.
_PROVIDERS = (
    ("openrouter", "OpenRouter"),
    ("openai", "OpenAI"),
    ("deepseek", "DeepSeek"),
    ("dashscope", "阿里云百炼"),
    ("aliyuncs", "阿里云百炼"),
    ("moonshot", "Moonshot"),
    ("google", "Google"),
    ("anthropic", "Anthropic"),
    ("groq", "Groq"),
    ("localhost", "本地"),
    ("127.0.0.1", "本地"),
)


def _provider(base: str) -> str:
    """A readable provider name from an API base URL."""
    base = (base or "").strip()
    if not base:
        return "unknown"
    try:
        from urllib.parse import urlparse

        host = (urlparse(base).netloc or base).lower()
    except Exception:  # noqa: BLE001
        host = base.lower()
    for needle, name in _PROVIDERS:
        if needle in host:
            return name
    name = host.split(":")[0]
    return name[4:] if name.startswith("api.") else name


def _empty() -> dict:
    return {"calls": 0, "prompt": 0, "completion": 0, "total": 0}


def _add(bucket: dict, entry: dict) -> None:
    prompt = int(entry.get("p", 0) or 0)
    completion = int(entry.get("c", 0) or 0)
    bucket["calls"] += 1
    bucket["prompt"] += prompt
    bucket["completion"] += completion
    bucket["total"] += prompt + completion


def summary(path: str, days: int = 7, now: float = 0.0) -> dict:
    """Totals for the last ``days`` days, broken down by day and by model."""
    now = now or time.time()
    cutoff = now - max(1, days) * 86400
    entries = [e for e in load(path) if float(e.get("t", 0)) >= cutoff]

    by_day: dict = {}
    by_model: dict = {}
    by_provider: dict = {}
    by_task: dict = {}
    task_label: dict = {}
    total = _empty()
    for entry in entries:
        _add(total, entry)
        task = str(entry.get("task") or "")
        if task:
            _add(by_task.setdefault(task, _empty()), entry)
            label = str(entry.get("x") or "")
            if label and not task_label.get(task):
                task_label[task] = label
        day = _day(float(entry.get("t", 0)))
        _add(by_day.setdefault(day, _empty()), entry)
        label = str(entry.get("m") or "unknown")
        _add(by_model.setdefault(label, _empty()), entry)
        _add(by_provider.setdefault(_provider(entry.get("b")), _empty()), entry)

    today = by_day.get(_day(now), _empty())
    return {
        "days": days,
        "total": total,
        "today": today,
        "by_day": [
            dict(day=day, **counts) for day, counts in sorted(by_day.items(), reverse=True)
        ],
        # Top few of each, newest-by-spend first: the app shows the rest behind
        # 展开, and an unbounded list is what made the page grow forever.
        "by_model": [
            dict(model=model, **counts)
            for model, counts in sorted(
                by_model.items(), key=lambda item: item[1]["total"], reverse=True
            )[:8]
        ],
        "by_task": [
            dict(task=task, label=task_label.get(task, ""), **counts)
            for task, counts in sorted(
                by_task.items(), key=lambda item: item[1]["total"], reverse=True
            )[:8]
        ],
        "by_provider": [
            dict(provider=provider, **counts)
            for provider, counts in sorted(
                by_provider.items(), key=lambda item: item[1]["total"], reverse=True
            )
        ],
    }


def format_report(data: dict) -> str:
    """A few lines a person can read in a dialog."""
    def line(label: str, counts: dict) -> str:
        return (f"{label}：{counts['calls']} 次调用，"
                f"输入 {counts['prompt']} / 输出 {counts['completion']}"
                f"（合计 {counts['total']}）")

    out = [line("今天", data.get("today") or _empty()),
           line(f"近 {data.get('days', 7)} 天", data.get("total") or _empty())]
    models = data.get("by_model") or []
    if models:
        out.append("")
        out.append("按模型：")
        for item in models[:6]:
            out.append(f"  {item['model']}  {item['total']}（{item['calls']} 次）")
    providers = data.get("by_provider") or []
    if len(providers) > 1:
        out.append("")
        out.append("按厂商：")
        for item in providers[:6]:
            out.append(f"  {item['provider']}  {item['total']}（{item['calls']} 次）")
    days = data.get("by_day") or []
    if days:
        out.append("")
        out.append("按天：")
        for item in days[:7]:
            out.append(f"  {item['day']}  {item['total']}（{item['calls']} 次）")
    return "\n".join(out)
