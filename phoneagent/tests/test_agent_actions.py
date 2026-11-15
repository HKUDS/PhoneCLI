"""The agent should use accessibility actions when the device offers them.

`VirtualDisplayDevice.tap_text` / `scroll` call performAction on the node; the
coordinate path is only a fallback. Nothing in the agent tests covered that
preference, so a refactor could silently go back to blind coordinates.
"""

from __future__ import annotations

from phoneagent.core.agent import AgentConfig, PhoneAgent
from phoneagent.devices.mock import MockDevice


class ActionDevice(MockDevice):
    """A device that records label actions the way the real one performs them."""

    def __init__(self, accepts: bool = True) -> None:
        super().__init__()
        self.accepts = accepts
        self.actions: list[tuple[str, str]] = []

    def tap_text(self, label: str) -> bool:
        self.actions.append(("tap_text", label))
        return self.accepts

    def scroll(self, direction: str = "down") -> bool:
        self.actions.append(("scroll", direction))
        return self.accepts


def _agent(device, **config):
    class NoLLM:
        def text(self, system, user, temperature=0.0):
            raise AssertionError("no model call expected")

        def vision(self, system, user, image_png, temperature=0.0):
            raise AssertionError("no model call expected")

    return PhoneAgent(device, [], NoLLM(), config=AgentConfig(), sleep=lambda _s: None)


def test_tap_text_uses_the_accessibility_action():
    device = ActionDevice(accepts=True)
    agent = _agent(device)

    assert agent._execute_action("tap_text", ["蓝牙"])
    assert device.actions == [("tap_text", "蓝牙")]
    # the coordinate path must not have run
    assert device.called("tap") == []


def test_tap_text_falls_back_to_coordinates_when_the_action_is_refused():
    device = ActionDevice(accepts=False)
    device.dump_ui = lambda: (
        '<hierarchy rotation="0">'
        '<node text="蓝牙" bounds="[10,10][110,60]" clickable="true"/>'
        "</hierarchy>"
    )
    agent = _agent(device)

    assert agent._execute_action("tap_text", ["蓝牙"])
    assert device.actions == [("tap_text", "蓝牙")]  # tried first
    assert device.called("tap") == [("tap", (60, 35), {})]


def test_scroll_prefers_the_accessibility_action():
    device = ActionDevice(accepts=True)
    agent = _agent(device)

    assert agent._execute_action("scroll", ["down"])
    assert device.actions == [("scroll", "down")]


def test_scroll_reports_failure_without_a_scroll_api():
    device = MockDevice()  # no scroll()
    agent = _agent(device)

    assert not agent._execute_action("scroll", ["down"])
    assert any("no scroll()" in line for line in agent.history)


def test_named_arguments_keep_the_value_not_the_key():
    """Models write finish(message="done"); the answer is "done"."""
    from phoneagent.core.agent import parse_action

    action = parse_action('Action: finish(message="已打开蓝牙设置页面")')
    assert action == {"name": "finish", "args": ["已打开蓝牙设置页面"]}

    # an equals sign inside a quoted argument must survive
    assert parse_action('Action: type("a=b")') == {"name": "type", "args": ["a=b"]}


def test_cancelled_before_start_calls_nothing():
    device = ActionDevice()
    agent = PhoneAgent(
        device, [], _NoLLM(), config=AgentConfig(), sleep=lambda _s: None,
        cancel=lambda: True,
    )
    result = agent.run("do something")

    assert not result.ok
    assert result.reason == "cancelled"
    assert result.rounds == 0
    assert device.actions == []


def test_cancel_is_honoured_between_rounds():
    """The flag may flip while a task is running; the next round must stop."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    state = {"stop": False}
    device = ActionDevice()
    device.dump_ui = lambda: '<hierarchy><node text="设置" bounds="[0,0][200,120]"/></hierarchy>'

    def vision(system, user, image_png, temperature=0.0):
        state["stop"] = True  # the user hit cancel during this round
        return 'Action: tap(0.5, 0.5)'

    llm = MockLLM(text_responses=["keep going"])
    llm.vision = vision

    agent = PhoneAgent(
        device, [], llm,
        config=AgentConfig(verify_with_vlm=False, max_rounds=5),
        sleep=lambda _s: None,
        cancel=lambda: state["stop"],
    )
    result = agent.run("tap something")

    assert result.reason == "cancelled"
    # the flag flipped during round 1, so round 2 is where it stops: no second
    # action was executed
    assert result.rounds == 2
    assert [step.action for step in result.steps] == ["tap"]


class _NoLLM:
    def text(self, system, user, temperature=0.0):
        raise AssertionError("no model call expected")

    def vision(self, system, user, image_png, temperature=0.0):
        raise AssertionError("no model call expected")


def test_a_screen_change_is_recorded_as_a_warning():
    """A disagreement is recorded, not acted on.

    Measured on the clock: the screen identified during a crawl and the one
    identified a moment later at replay disagree often enough that aborting
    discarded 9 of 10 otherwise valid macros. It stays a signal until screen
    identification is stable enough to act on.
    """
    from phoneagent.core.macro import MacroExecutor
    from phoneagent.devices.mock import MockDevice

    device = MockDevice()
    macro = [{"action": "tap", "x": 10, "y": 20, "text": "添加闹钟",
              "wait": 0, "screen": "screen_1"}]

    ok = MacroExecutor(device, sleep=lambda _s: None,
                       verify_screen=lambda _e: True).run(macro)
    assert ok.ok

    executor = MacroExecutor(device, sleep=lambda _s: None,
                             verify_screen=lambda _e: False)
    result = executor.run(macro)
    assert result.ok                      # the replay still happened
    assert executor.screen_warnings == [(0, "screen_1")]


def test_a_macro_without_screen_metadata_still_replays():
    from phoneagent.core.macro import MacroExecutor
    from phoneagent.devices.mock import MockDevice

    device = MockDevice()
    macro = [{"action": "tap", "x": 10, "y": 20, "text": "", "wait": 0}]
    result = MacroExecutor(device, sleep=lambda _s: None,
                           verify_screen=lambda _e: False).run(macro)
    assert result.ok          # no expectation recorded, nothing to check
