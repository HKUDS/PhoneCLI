"""The web tools, with a scripted bridge instead of a real page."""

from __future__ import annotations

from phoneagent.core.agent import AgentConfig, PhoneAgent, action_tools, tool_arguments
from phoneagent.core.llm import MockLLM
from phoneagent.devices.mock import MockDevice
from phoneagent.web import WebPage


class FakeBridge:
    """Answers beginEval/pollEval immediately, like a fast page."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.calls: list = []
        self.url = "https://example.com/"

    def beginEval(self, js: str) -> int:
        self.calls.append(js)
        self.pending = js
        return 1

    def pollEval(self, tag: int) -> str:
        js = self.pending
        for marker, value in self.answers.items():
            if marker in js:
                return value
        return '"ok"'

    def beginLoad(self, url: str) -> None:
        self.url = url
        self.calls.append("load:" + url)

    def pollLoad(self) -> str:
        return self.url

    def currentUrl(self) -> str:
        return self.url


def _agent(device, **cfg):
    ask = cfg.pop("ask", None)
    return PhoneAgent(device, [], MockLLM([]), config=AgentConfig(**cfg),
                      sleep=lambda _s: None, ask=ask)


def test_web_tools_are_offered_to_the_model():
    names = [tool["function"]["name"] for tool in action_tools(True)]
    for name in ("web_open", "web_text", "web_click", "web_type", "web_scroll"):
        assert name in names, name
    args = tool_arguments("web_type", {"selector": "#q", "text": "hello"})
    assert args == ["#q", "hello"]


def test_web_text_puts_the_page_text_in_the_history(monkeypatch):
    bridge = FakeBridge({"innerText": '"Example Domain body"'})
    monkeypatch.setattr("phoneagent.web.page", lambda: WebPage(bridge=bridge))
    agent = _agent(MockDevice(), macro_enabled=False, planner_enabled=False)

    assert agent._execute_action("web_text", []) is True
    assert any("Example Domain body" in line for line in agent.history)


def test_web_click_reports_whether_it_found_the_target(monkeypatch):
    bridge = FakeBridge({})
    monkeypatch.setattr("phoneagent.web.page", lambda: WebPage(bridge=bridge))
    agent = _agent(MockDevice(), macro_enabled=False, planner_enabled=False)

    assert agent._execute_action("web_click", ["Learn more"]) is True
    assert any("click 'Learn more' -> ok" in line for line in agent.history)


def test_web_tools_report_when_there_is_no_browser(monkeypatch):
    monkeypatch.setattr("phoneagent.web.page", lambda: WebPage(bridge=None))
    agent = _agent(MockDevice(), macro_enabled=False, planner_enabled=False)

    assert agent._execute_action("web_text", []) is False
    assert any("not available" in line for line in agent.history)


def test_a_url_task_skips_the_planner_and_the_maps():
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    device = MockDevice()
    llm = MockLLM(vision_responses=["finish(读完了)"])
    agent = PhoneAgent(device, [], llm,
                       config=AgentConfig(max_rounds=3, planner_enabled=True),
                       sleep=lambda _s: None)
    result = agent.run("打开 https://example.com 并告诉我这页说了什么")

    kinds = [step.kind for step in agent.steps]
    assert "plan" not in kinds and "subtask" not in kinds
    assert result.ok


def test_web_network_is_a_read_and_does_not_trip_the_loop_detector():
    from phoneagent.core.agent import PhoneAgent

    assert "web_network" in PhoneAgent.READ_ONLY_TOOLS


def test_the_page_supplies_the_signature_on_a_webview_screen(monkeypatch):
    """A WebView's tree never changes; the page's url and scroll do."""
    bridge = FakeBridge({
        "scrollingElement": '{"url":"https://example.com/","y":100,"n":500}'
    })
    monkeypatch.setattr("phoneagent.web.page", lambda: WebPage(bridge=bridge))
    agent = _agent(MockDevice(), macro_enabled=False, planner_enabled=False)

    signature = agent._screen_signature('<node class="WebView"/>')
    assert "example.com" in signature and "|100|" in signature
    assert agent._screen_signature('<node class="Button" text="OK"/>') == \
        '<node class="Button" text="OK"/>'


def test_a_verification_wall_goes_to_the_user_and_then_continues(monkeypatch):
    """The page is shown on the user's display, then reloaded and checked."""
    state = {"blocked": "title says '安全验证'", "opens": []}

    class Page:
        bridge = object()

        def blocked(self):
            return state["blocked"]

        def url(self):
            return "https://example.com/verify"

    def fake_ensure_open(device, url, display=-1):
        state["opens"].append((url, display))
        state["blocked"] = ""          # solving it on the user's screen works
        return url

    monkeypatch.setattr("phoneagent.web.page", lambda: Page())
    monkeypatch.setattr("phoneagent.web.ensure_open", fake_ensure_open)

    asked = []
    agent = _agent(MockDevice(), ask=lambda q: asked.append(q) or "好了",
                   macro_enabled=False, planner_enabled=False)

    assert agent._clear_verification("https://example.com/verify") is True
    assert asked and "验证" in asked[0]
    assert state["opens"][0][1] == 0     # first on the user's display
    assert state["opens"][-1][1] == -1   # then back on the agent's


def test_declining_the_verification_stops_the_step(monkeypatch):
    class Page:
        bridge = object()

        def blocked(self):
            return "title says '安全验证'"

        def url(self):
            return "https://example.com/verify"

    monkeypatch.setattr("phoneagent.web.page", lambda: Page())
    monkeypatch.setattr("phoneagent.web.ensure_open", lambda d, u, display=-1: u)

    agent = _agent(MockDevice(), ask=lambda q: "取消",
                   macro_enabled=False, planner_enabled=False)
    assert agent._clear_verification("https://example.com/verify") is False
    assert any("declined" in line for line in agent.history)
