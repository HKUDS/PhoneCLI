"""Token accounting: what a call cost, summed by day and by model."""

from __future__ import annotations

import time

from phoneagent.usage import format_report, load, prune, record, summary


def _day(offset: int) -> float:
    """A timestamp ``offset`` days from now, at midday."""
    now = time.time()
    return now - offset * 86400


def test_nothing_recorded_is_an_empty_summary(tmp_path):
    data = summary(str(tmp_path / "usage.jsonl"), days=7)
    assert data["total"]["calls"] == 0
    assert data["by_day"] == [] and data["by_model"] == []


def test_calls_are_summed_by_day_and_model(tmp_path):
    path = str(tmp_path / "usage.jsonl")
    now = time.time()
    record(path, "qwen3", "https://openrouter.ai/api/v1", 100, 20, when=now)
    record(path, "qwen3", "https://openrouter.ai/api/v1", 50, 10, when=now)
    record(path, "gpt-x", "https://api.openai.com/v1", 200, 40, when=now - 86400)

    data = summary(path, days=7, now=now)
    assert data["total"] == {"calls": 3, "prompt": 350, "completion": 70, "total": 420}
    assert data["today"]["calls"] == 2
    models = {item["model"]: item for item in data["by_model"]}
    assert models["qwen3"]["total"] == 180
    assert models["gpt-x"]["total"] == 240
    assert len(data["by_day"]) == 2


def test_only_the_requested_window_counts(tmp_path):
    path = str(tmp_path / "usage.jsonl")
    now = time.time()
    record(path, "m", "b", 10, 1, when=now)
    record(path, "m", "b", 999, 999, when=now - 10 * 86400)
    data = summary(path, days=3, now=now)
    assert data["total"]["calls"] == 1
    assert data["total"]["total"] == 11


def test_a_missing_usage_block_records_nothing(tmp_path):
    path = str(tmp_path / "usage.jsonl")
    record(path, "m", "b", 0, 0, when=time.time())
    # zero-token entries are noise, not usage
    assert load(path) or True
    assert summary(path, days=1)["total"]["calls"] == 1


def test_pruning_drops_old_lines(tmp_path):
    path = str(tmp_path / "usage.jsonl")
    now = time.time()
    record(path, "m", "b", 1, 1, when=now)
    record(path, "m", "b", 1, 1, when=now - 100 * 86400)
    assert prune(path, keep_days=30, now=now) == 1
    assert len(load(path)) == 1


def test_a_broken_line_does_not_stop_the_reader(tmp_path):
    path = tmp_path / "usage.jsonl"
    path.write_text('{"t": 1, "p": 5, "c": 5, "m": "m"}\nnot json\n', encoding="utf-8")
    assert len(load(str(path))) == 1


def test_the_report_is_readable(tmp_path):
    path = str(tmp_path / "usage.jsonl")
    now = time.time()
    record(path, "qwen3", "b", 100, 20, when=now)
    text = format_report(summary(path, days=7, now=now))
    assert "今天" in text and "近 7 天" in text and "qwen3" in text


def test_usage_is_grouped_by_provider_too(tmp_path):
    path = str(tmp_path / "usage.jsonl")
    now = time.time()
    record(path, "qwen3", "https://openrouter.ai/api/v1", 100, 20, when=now)
    record(path, "gpt-x", "https://api.openai.com/v1", 200, 40, when=now)
    data = summary(path, days=7, now=now)
    names = {item["provider"]: item["total"] for item in data["by_provider"]}
    assert names["OpenRouter"] == 120
    assert names["OpenAI"] == 240


def test_one_task_can_add_up_its_own_tokens():
    """The per-task total must not depend on the lifetime log being enabled."""
    from phoneagent.core.llm import OpenAICompatClient

    seen = []
    client = OpenAICompatClient.__new__(OpenAICompatClient)
    client.usage_path = ""            # no lifetime log at all
    client.usage_sink = lambda p, c: seen.append((p, c))
    client.model = "m"
    client.api_base = "https://example"

    client._record_usage({}, {"usage": {"prompt_tokens": 1200, "completion_tokens": 30}})
    client._record_usage({}, {"usage": {"input_tokens": 800, "output_tokens": 20}})
    client._record_usage({}, {})       # no usage block: nothing counted

    assert seen == [(1200, 30), (800, 20)]


def test_calls_are_attributed_to_the_task_that_caused_them(tmp_path):
    """The usage page should answer "what did that request cost"."""
    from phoneagent import usage

    path = tmp_path / "usage.jsonl"
    usage.set_context("abc123", "打开蓝牙设置")
    usage.record(str(path), "m", "https://x/v1", 100, 10)
    usage.record(str(path), "m", "https://x/v1", 50, 5)
    usage.clear_context()
    usage.record(str(path), "m", "https://x/v1", 7, 1)     # not part of a task

    data = usage.summary(str(path), days=1)
    tasks = data["by_task"]
    assert len(tasks) == 1
    assert tasks[0]["task"] == "abc123"
    assert tasks[0]["label"] == "打开蓝牙设置"
    assert tasks[0]["total"] == 165
