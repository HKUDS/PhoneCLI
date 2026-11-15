"""Native tool calling: the model calls a function instead of writing one.

Parsing `Action: tap(0.5, 0.3)` out of prose was the single most brittle part
of the loop -- quoting, truncation and formatting drift all broke it. These
tests pin the replacement and the text fallback that keeps non-tool models
working.
"""

from __future__ import annotations

from phoneagent.core.agent import (
    AgentConfig,
    PhoneAgent,
    action_tools,
    tool_arguments,
)
from phoneagent.core.llm import Choice, OpenAICompatClient, ToolCall
from phoneagent.devices.mock import MockDevice


class ToolDevice(MockDevice):
    def __init__(self):
        super().__init__()
        self.actions = []

    def dump_ui(self):
        return '<hierarchy><node text="蓝牙" bounds="[0,0][200,120]"/></hierarchy>'

    def tap_text(self, label):
        self.actions.append(("tap_text", label))
        return True


class ToolLLM:
    """Calls the given tool on the first turn, then finishes."""

    def __init__(self, name, arguments, answer="done"):
        self._queue = [(name, arguments), ("finish", {"message": answer})]
        self.calls = []

    def choose(self, system, user, image_png=None, tools=None, temperature=0.0):
        assert tools, "the loop must advertise tools"
        self.calls.append(system[:20])
        if not self._queue:
            return Choice(text="out of script")
        name, arguments = self._queue.pop(0)
        return Choice(call=ToolCall(name=name, arguments=arguments))


def test_tool_schemas_follow_the_labels_available():
    with_labels = {t["function"]["name"] for t in action_tools(True)}
    without = {t["function"]["name"] for t in action_tools(False)}
    assert {"tap", "finish", "launch"} <= with_labels
    assert "tap_text" in with_labels and "scroll" in with_labels
    assert "tap_text" not in without and "scroll" not in without


def test_named_arguments_map_to_positional():
    assert tool_arguments("tap", {"rx": 0.5, "ry": 0.25}) == [0.5, 0.25]
    assert tool_arguments("finish", {"message": "已打开"}) == ["已打开"]
    assert tool_arguments("back", {}) == []
    # unknown tool: keep declaration order rather than dropping values
    assert tool_arguments("mystery", {"a": 1}) == [1]


def test_agent_runs_on_tool_calls():
    device = ToolDevice()
    llm = ToolLLM("tap_text", {"label": "蓝牙"}, answer="已打开蓝牙设置")
    agent = PhoneAgent(device, [], llm, config=AgentConfig(verify_with_vlm=False),
                       sleep=lambda _s: None)

    result = agent.run("打开蓝牙设置")

    assert result.ok
    assert result.answer == "已打开蓝牙设置"
    assert device.actions == [("tap_text", "蓝牙")]
    assert [step.kind for step in result.steps][-1] == "finish"


def test_text_answers_still_work_without_tools():
    """A client without choose() keeps using the prose parser."""

    class ProseLLM:
        def text(self, system, user, temperature=0.0):
            return "FINISH: cached"

        def vision(self, system, user, image_png, temperature=0.0):
            return 'Action: finish("from prose")'

    device = ToolDevice()
    agent = PhoneAgent(device, [], ProseLLM(),
                       config=AgentConfig(verify_with_vlm=False), sleep=lambda _s: None)
    result = agent.run("do something")
    assert result.ok
    assert result.answer == "from prose"


def test_client_parses_tool_calls(monkeypatch):
    client = OpenAICompatClient(api_key="k")
    canned = {
        "choices": [{"message": {
            "content": "",
            "tool_calls": [{"function": {"name": "tap_text",
                                         "arguments": '{"label": "蓝牙"}'}}],
        }}]
    }
    monkeypatch.setattr(client, "_post", lambda payload: canned)
    choice = client.choose("sys", "user", tools=action_tools(True))

    assert choice.call is not None
    assert choice.call.name == "tap_text"
    assert choice.call.arguments == {"label": "蓝牙"}


def test_client_returns_text_when_the_model_does_not_call(monkeypatch):
    client = OpenAICompatClient(api_key="k")
    monkeypatch.setattr(client, "_post", lambda payload: {
        "choices": [{"message": {"content": "done already"}}]})
    choice = client.choose("sys", "user", tools=action_tools(True))
    assert choice.call is None
    assert choice.text == "done already"


# ---------------------------------------------------------------------------
# loop policy: self-checks, no-progress, honest closeout
# ---------------------------------------------------------------------------


class ChangingDevice(MockDevice):
    """A screen that changes every time it is read."""

    def __init__(self):
        super().__init__()
        self.reads = 0

    def dump_ui(self):
        self.reads += 1
        return (f'<hierarchy><node text="行{self.reads}" bounds="[0,0][200,120]"/>'
                "</hierarchy>")


class FrozenDevice(MockDevice):
    """A screen that never changes, however many actions are taken."""

    def dump_ui(self):
        return '<hierarchy><node text="设置" bounds="[0,0][200,120]"/></hierarchy>'


class LoopingLLM:
    """Always asks for another tap, never finishes."""

    def __init__(self):
        self.offered: list[list[str]] = []

    def choose(self, system, user, image_png=None, tools=None, temperature=0.0):
        self.offered.append([t["function"]["name"] for t in (tools or [])])
        return Choice(call=ToolCall(name="tap", arguments={"rx": 0.5, "ry": 0.5}))


def test_no_progress_ends_the_run_instead_of_burning_steps():
    agent = PhoneAgent(
        FrozenDevice(), [], LoopingLLM(),
        # loop_threshold would fire first on the repeated tap; this test is
        # about the screen standing still, not about the action repeating
        config=AgentConfig(max_rounds=20, no_new_data_limit=3, loop_threshold=0,
                           verify_with_vlm=False, include_ui_text=True),
        sleep=lambda _s: None,
    )
    result = agent.run("打开蓝牙")

    assert not result.ok
    assert result.reason.startswith("no_new_data")
    # three unchanged rounds plus the first, not twenty
    assert result.rounds <= 5


def test_progress_check_fires_once_at_the_halfway_point():
    agent = PhoneAgent(
        ChangingDevice(), [], LoopingLLM(),
        config=AgentConfig(max_rounds=20, no_new_data_limit=99, verify_with_vlm=False),
        sleep=lambda _s: None,
    )
    noticed: set = set()
    # round 11 means 10 steps used, which is 50% of 20
    first = agent._loop_notice(11, noticed)
    second = agent._loop_notice(11, noticed)
    assert "10 of 20" in first
    assert second == ""


def test_short_runs_get_no_progress_notice():
    agent = PhoneAgent(
        ChangingDevice(), [], LoopingLLM(),
        config=AgentConfig(max_rounds=6), sleep=lambda _s: None,
    )
    assert agent._loop_notice(3, set()) == ""


def test_last_round_only_offers_finish():
    llm = LoopingLLM()
    agent = PhoneAgent(
        ChangingDevice(), [], llm,
        config=AgentConfig(max_rounds=3, no_new_data_limit=99,
                           verify_with_vlm=False, include_ui_text=True),
        sleep=lambda _s: None,
    )
    agent.run("打开蓝牙")

    assert llm.offered, "the model was never asked"
    assert "tap" in llm.offered[0]
    assert llm.offered[-1] == ["finish"]      # the honest way out


def test_two_empty_turns_end_the_run():
    class SilentLLM:
        def choose(self, system, user, image_png=None, tools=None, temperature=0.0):
            return Choice(text="")            # neither a call nor an answer

    agent = PhoneAgent(
        ChangingDevice(), [], SilentLLM(),
        config=AgentConfig(max_rounds=10, no_new_data_limit=99, verify_with_vlm=False),
        sleep=lambda _s: None,
    )
    result = agent.run("打开蓝牙")

    assert not result.ok
    assert result.reason.startswith("empty_reply")
    assert result.rounds == 2


# ---------------------------------------------------------------------------
# checking the claim against what the map can see
# ---------------------------------------------------------------------------


class ScriptedFinishLLM:
    """Finishes with the given answers, in order."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.prompts = []

    def choose(self, system, user, image_png=None, tools=None, temperature=0.0):
        self.prompts.append(user)
        answer = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return Choice(call=ToolCall(name="finish", arguments={"message": answer}))


class LabelledDevice(MockDevice):
    def dump_ui(self):
        return '<hierarchy><node text="设置" bounds="[0,0][200,120]"/></hierarchy>'


def _routed_agent(device, llm, landed):
    agent = PhoneAgent(device, [], llm, config=AgentConfig(verify_with_vlm=False),
                       sleep=lambda _s: None)
    agent._expectation = (object(), "screen_9", "蓝牙设置")
    agent._landed = lambda _map: landed
    return agent


def test_a_false_done_is_sent_back_once_then_accepted():
    llm = ScriptedFinishLLM(["已打开蓝牙设置", "没能打开蓝牙设置，当前仍在设置首页"])
    agent = _routed_agent(LabelledDevice(), llm, landed="screen_0")

    result = agent.run("打开蓝牙设置")

    assert result.ok
    assert "没能打开" in result.answer
    assert any(s.kind == "verify" and s.action == "mismatch" for s in result.steps)
    # the nudge reached the model
    assert "the map puts the current screen" in llm.prompts[1]


def test_a_correct_done_is_not_second_guessed():
    llm = ScriptedFinishLLM(["已打开蓝牙设置"])
    agent = _routed_agent(LabelledDevice(), llm, landed="screen_9")

    result = agent.run("打开蓝牙设置")

    assert result.ok
    assert result.answer == "已打开蓝牙设置"
    assert not any(s.kind == "verify" for s in result.steps)


def test_an_honest_failure_is_never_contradicted():
    llm = ScriptedFinishLLM(["没有找到蓝牙入口，任务没有完成"])
    agent = _routed_agent(LabelledDevice(), llm, landed="screen_0")

    result = agent.run("打开蓝牙设置")

    assert result.ok
    assert "没有完成" in result.answer
    assert not any(s.kind == "verify" for s in result.steps)


def test_unreadable_screen_cannot_confirm_anything():
    class BlankDevice(MockDevice):
        """Readable for the preflight and the first round, blank at the answer."""

        def __init__(self):
            super().__init__()
            self.reads = 0

        def dump_ui(self):
            self.reads += 1
            if self.reads <= 1:
                return '<hierarchy><node text="设置" bounds="[0,0][200,120]"/></hierarchy>'
            return "<hierarchy></hierarchy>"

    device = BlankDevice()
    llm = ScriptedFinishLLM(["已打开蓝牙设置", "屏幕上没有可读内容，无法确认"])
    agent = _routed_agent(device, llm, landed="screen_9")

    result = agent.run("打开蓝牙设置")

    assert result.ok
    assert any(s.kind == "verify" and "nothing readable" in s.detail
               for s in result.steps)


def test_hand_over_is_offered_to_the_model():
    from phoneagent.core.agent import action_tools

    names = [tool["function"]["name"] for tool in action_tools(True)]
    assert "hand_over" in names
    tool = next(t for t in action_tools(True) if t["function"]["name"] == "hand_over")
    assert "reason" in tool["function"]["parameters"]["properties"]


def test_the_macro_tool_only_exists_when_there_are_maps():
    """GUI-only means no map, so a macro target could not exist."""
    from phoneagent.core.agent import action_tools

    with_maps = {t["function"]["name"] for t in action_tools(True, with_maps=True)}
    without = {t["function"]["name"] for t in action_tools(True, with_maps=False)}
    assert "macro" in with_maps
    assert "macro" not in without
