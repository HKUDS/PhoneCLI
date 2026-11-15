"""Token usage tracker — global accumulator for all LLM/VLM calls.

Usage:
    from phonecli.token_usage import token_usage

    # After each API call:
    token_usage.add(prompt_tokens=500, completion_tokens=50,
                    cache_read_tokens=0, cache_write_tokens=0,
                    label="classify")

    # Report:
    token_usage.report()
    token_usage.print_report()

    # Per-task tracking:
    snap = token_usage.snapshot()       # before task
    task_usage = token_usage.diff(snap) # after task → dict
    token_usage.save_task_report(snap, path)  # save per-task to file
"""

# The app runs this file on Python 3.8 (Chaquopy), where `list[str]` in an
# annotation is evaluated at def time and fails; the future import makes
# annotations strings on every version.
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class TokenUsage:
    """Global accumulator for token usage across all LLM/VLM calls."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_calls: int = 0
    per_label: dict[str, dict] = field(default_factory=dict)

    def add(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        label: str = "",
    ):
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_write_tokens += cache_write_tokens
        self.total_calls += 1

        if label:
            entry = self.per_label.setdefault(label, {
                "calls": 0, "prompt": 0, "completion": 0,
                "cache_read": 0, "cache_write": 0,
            })
            entry["calls"] += 1
            entry["prompt"] += prompt_tokens
            entry["completion"] += completion_tokens
            entry["cache_read"] += cache_read_tokens
            entry["cache_write"] += cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    # ------------------------------------------------------------------
    # Snapshot / diff for per-task tracking
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a lightweight copy of current state (for diff later)."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_calls": self.total_calls,
            "per_label": {k: dict(v) for k, v in self.per_label.items()},
        }

    def diff(self, snap: dict) -> dict:
        """Compute delta from a previous snapshot. Returns a task-level report dict."""
        def _label_diff(key):
            prev = snap["per_label"].get(key, {})
            cur = self.per_label.get(key, {})
            return {
                "calls": cur.get("calls", 0) - prev.get("calls", 0),
                "prompt": cur.get("prompt", 0) - prev.get("prompt", 0),
                "completion": cur.get("completion", 0) - prev.get("completion", 0),
                "cache_read": cur.get("cache_read", 0) - prev.get("cache_read", 0),
                "cache_write": cur.get("cache_write", 0) - prev.get("cache_write", 0),
            }

        all_labels = set(snap.get("per_label", {}).keys()) | set(self.per_label.keys())
        per_label = {}
        for label in sorted(all_labels):
            d = _label_diff(label)
            if d["calls"] > 0:
                per_label[label] = d

        return {
            "prompt_tokens": self.prompt_tokens - snap["prompt_tokens"],
            "completion_tokens": self.completion_tokens - snap["completion_tokens"],
            "total_tokens": (self.prompt_tokens - snap["prompt_tokens"]
                             + self.completion_tokens - snap["completion_tokens"]),
            "cache_read_tokens": self.cache_read_tokens - snap["cache_read_tokens"],
            "cache_write_tokens": self.cache_write_tokens - snap["cache_write_tokens"],
            "total_calls": self.total_calls - snap["total_calls"],
            "per_label": per_label,
        }

    def to_dict(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "total_calls": self.total_calls,
            "per_label": {k: dict(v) for k, v in sorted(self.per_label.items())},
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str):
        """Save aggregate usage to a JSON file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

    def save_task_report(self, snap: dict, path: str):
        """Save per-task token usage (diff from snapshot) to a JSON file."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.diff(snap), f, indent=2)

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def report(self) -> str:
        lines = [
            "=" * 60,
            f"Token Usage Report",
            f"  Total calls:     {self.total_calls}",
            f"  Prompt tokens:   {self.prompt_tokens:,}",
            f"  Completion:      {self.completion_tokens:,}",
            f"  Total tokens:    {self.total_tokens:,}",
        ]
        if self.cache_read_tokens or self.cache_write_tokens:
            lines.append(f"  Cache read:      {self.cache_read_tokens:,}")
            lines.append(f"  Cache write:     {self.cache_write_tokens:,}")
            uncached = self.prompt_tokens
            if uncached > 0:
                lines.append(f"  Cache hit rate:  {self.cache_read_tokens / uncached * 100:.1f}%")
        if self.per_label:
            lines.append("  --- By label ---")
            for label, stats in sorted(self.per_label.items()):
                lines.append(
                    f"  [{label}] calls={stats['calls']} "
                    f"prompt={stats['prompt']:,} "
                    f"completion={stats['completion']:,}"
                )
        lines.append("=" * 60)
        return "\n".join(lines)

    def print_report(self):
        print(self.report())

    def reset(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.total_calls = 0
        self.per_label.clear()


# Global singleton
token_usage = TokenUsage()
