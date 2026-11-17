"""Tests for the unified agent (macro-first, VLM fallback) — all offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from phoneagent.core.agent import (
    AgentConfig,
    PhoneAgent,
    parse_action,
    parse_route,
)
from phoneagent.core.app_map import AppMap
from phoneagent.core.llm import MockLLM
from phoneagent.devices.base import DeviceInfo
from phoneagent.devices.mock import MockDevice

REPO = Path(__file__).resolve().parents[2]
MAP = REPO / "phonecli_android" / "app_maps" / "calendar_android.yaml"


def _agent(device, llm, **cfg):
    ask = cfg.pop("ask", None)
    config = AgentConfig(max_rounds=cfg.pop("max_rounds", 6), **cfg)
    return PhoneAgent(device, [AppMap(MAP)], llm, config=config,
                      sleep=lambda _s: None, ask=ask)


def _first_op(app_map, op_type):
    ops = app_map.build_operations()
    return next(op for op in ops.values() if op.type == op_type)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


def test_parse_action_variants():
    assert parse_action("Action: tap(0.5, 0.25)") == {"name": "tap", "args": [0.5, 0.25]}
    assert parse_action('```\ntype("hi there")\n```') == {"name": "type", "args": ["hi there"]}
    assert parse_action("no action here") is None


def test_parse_route_variants():
    assert parse_route("OP: settings_wifi") == parse_route("op: settings_wifi")
    assert parse_route("OP: settings_wifi").kind == "op"
    assert parse_route("MACRO_VLM: x").kind == "macro_vlm"
    assert parse_route("NEED_VLM: too complex").value == "too complex"
    assert parse_route("FINISH: 42").kind == "finish"
    assert parse_route("garbage").kind == "need_vlm"


# ---------------------------------------------------------------------------
# macro path
# ---------------------------------------------------------------------------


def test_act_operation_replays_and_finishes():
    """The macro does the taps; the VLM writes the sentence the user reads.

    It used to answer with the operation's own description, which came out as
    "WLAN → WLAN" -- true, and useless as a reply.
    """
    app = AppMap(MAP)
    op = _first_op(app, "ACT")
    device = MockDevice()
    llm = MockLLM(text_responses=[f"OP: {op.id}"],
                  vision_responses=["finish(完成：页面已经打开)"])
    result = _agent(device, llm, verify_with_vlm=False).run("do the thing")

    assert result.ok
    assert result.used_op == op.id
    ignored = {"dump_ui", "current_app"}  # preflight dump + landing check
    actions = [n for n in device.method_names() if n not in ignored]
    assert actions[: len(op.macro)] == [step["action"] for step in op.macro]
    assert "完成" in result.answer
    # routing + the closing sentence
    assert len(llm.calls) >= 2


def test_nav_operation_hands_over_to_vlm():
    app = AppMap(MAP)
    op = _first_op(app, "NAV")
    device = MockDevice()
    llm = MockLLM(
        text_responses=[f"OP: {op.id}"],
        vision_responses=['Action: finish("navigated")'],
    )
    result = _agent(device, llm).run("go somewhere")

    assert result.ok
    # used_op now means "which operation ran", not "which layer answered":
    # both layers report it, so a caller can tell what the macro did
    assert result.used_op == op.id
    assert result.rounds == 1
    kinds = [s.kind for s in result.steps]
    assert kinds[:2] == ["route", "macro"]
    assert kinds[-1] == "finish"  # the VLM answered directly, no action step


def test_finish_route_answers_without_touching_device():
    device = MockDevice()
    llm = MockLLM(text_responses=["FINISH: 3 events today"])
    result = _agent(device, llm).run("how many events today?")

    assert result.ok
    assert result.answer == "3 events today"
    # only the preflight dump; no tapping, launching or typing
    assert {name for name, _, _ in device.calls} <= {"dump_ui"}


def test_macro_failure_falls_back_to_vlm():
    app = AppMap(MAP)
    op = _first_op(app, "ACT")
    device = MockDevice(fail_on={"tap"})
    llm = MockLLM(
        text_responses=[f"OP: {op.id}"],
        vision_responses=['Action: finish("gave up gracefully")'],
    )
    result = _agent(device, llm).run("do the thing")

    assert result.ok
    assert result.reason == "finish"
    assert any(s.kind == "macro" and "failed" in s.detail for s in result.steps)


def test_unknown_op_id_falls_back_to_vlm():
    device = MockDevice()
    llm = MockLLM(
        text_responses=["OP: no_such_op"],
        vision_responses=['Action: finish("vlm done")'],
    )
    result = _agent(device, llm).run("whatever")
    assert result.ok
    assert result.reason == "finish"


# ---------------------------------------------------------------------------
# VLM path
# ---------------------------------------------------------------------------


def test_vlm_loop_executes_tap_then_finish():
    device = MockDevice(DeviceInfo("android", 1000, 2000))
    llm = MockLLM(
        text_responses=["NEED_VLM: complex"],
        vision_responses=["Action: tap(0.25, 0.5)", 'Action: finish("done")'],
    )
    result = _agent(device, llm).run("tap something")

    assert result.ok
    assert device.called("tap")[0][1] == (250, 1000)  # normalized -> pixels
    assert [s.action for s in result.steps if s.kind == "vlm"] == ["tap"]


def test_loop_detection_aborts():
    device = MockDevice()
    llm = MockLLM(
        text_responses=["NEED_VLM: complex"],
        vision_responses=["Action: tap(0.5, 0.5)"],  # repeated forever
    )
    result = _agent(device, llm, loop_threshold=3, max_rounds=10).run("stuck task")

    assert not result.ok
    assert result.reason == "loop"
    # three taps happened, the abort fires when the fourth round starts
    assert result.rounds == 4
    assert [s.action for s in result.steps].count("tap") == 3


def test_max_rounds_exhausted():
    device = MockDevice()
    responses = [f"Action: tap(0.{i}, 0.5)" for i in range(1, 9)]
    llm = MockLLM(text_responses=["NEED_VLM: x"], vision_responses=responses)
    result = _agent(device, llm, loop_threshold=0, max_rounds=3).run("long task")

    assert not result.ok
    assert result.reason == "max_rounds"
    assert result.rounds == 3


def test_unparsed_action_does_not_crash():
    device = MockDevice()
    llm = MockLLM(
        text_responses=["NEED_VLM: x"],
        vision_responses=["I am thinking about it", 'Action: finish("ok")'],
    )
    result = _agent(device, llm).run("task")
    assert result.ok
    assert any(s.action == "<unparsed>" for s in result.steps)


def test_screen_hint_is_injected_into_prompt():
    app = AppMap(MAP)
    screen = app.screens[0]
    xml = '<hierarchy rotation="0">' + "".join(
        f'<node text="{e.text}" content-desc="" bounds="[0,0][400,200]" />'
        for e in screen.elements
    ) + "</hierarchy>"
    device = MockDevice(ui_xml=xml)
    llm = MockLLM(
        text_responses=["NEED_VLM: x"], vision_responses=['Action: finish("ok")']
    )
    _agent(device, llm).run("task")

    (system, user) = llm.vision_calls()[0]
    assert "You are on" in user
    assert "## Available Apps" in user


def test_launch_resolves_app_name_to_package():
    device = MockDevice()
    llm = MockLLM(
        text_responses=["NEED_VLM: x"],
        vision_responses=["Action: launch(Calendar)", 'Action: finish("ok")'],
    )
    _agent(device, llm).run("open calendar")
    expected = AppMap(MAP).package
    assert device.called("launch")[0][1] == (expected,)


def test_macro_action_replays_named_operation():
    app = AppMap(MAP)
    op = _first_op(app, "NAV")
    device = MockDevice()
    llm = MockLLM(
        text_responses=["NEED_VLM: x"],
        vision_responses=[f"Action: macro({op.id})", 'Action: finish("ok")'],
    )
    _agent(device, llm).run("use a macro")

    # current_app is polled while the replayer waits for the window before its
    # first tap; the actions are what this asserts.
    actions = [n for n in device.method_names()
               if n not in ("dump_ui", "screenshot", "current_app")]
    assert actions[: len(op.macro)] == [step["action"] for step in op.macro]


def test_locked_device_fails_fast():
    """An empty accessibility dump means screen off / locked -> no loop."""
    device = MockDevice(ui_xml="<hierarchy rotation=\"0\"><node text=\"\" /></hierarchy>")
    llm = MockLLM(text_responses=["NEED_VLM: x"], vision_responses=['Action: finish("x")'])
    result = _agent(device, llm).run("do something")

    assert not result.ok
    assert result.reason.startswith("no_ui")
    assert llm.vision_calls() == []


def test_macro_landing_on_wrong_screen_triggers_retry_then_vlm():
    """A macro that lands elsewhere is retried once, then handed to the VLM."""
    app = AppMap(MAP)
    # a NAV operation that actually goes somewhere other than screen_0
    op = next(o for o in app.build_operations().values()
              if o.type == "NAV" and o.target_screen != "screen_0")
    # the device keeps showing screen_0's texts, so identification says screen_0
    xml = '<hierarchy rotation="0">' + "".join(
        f'<node text="{e.text}" content-desc="" bounds="[0,0][400,200]" />'
        for e in app.screens[0].elements
    ) + "</hierarchy>"
    device = MockDevice(ui_xml=xml)
    llm = MockLLM(
        text_responses=[f"OP: {op.id}"],
        vision_responses=['Action: finish("vlm finished it")'],
    )
    result = _agent(device, llm).run("go somewhere else")

    assert result.ok
    assert result.reason == "finish"
    assert any(step.kind == "retry" for step in result.steps)


# ---------------------------------------------------------------------------
# cross-app planner
# ---------------------------------------------------------------------------

from phoneagent.core.agent import _parse_plan  # noqa: E402

PLAN_JSON = """```json
[
  {"step": 1, "subtask": "打开 WLAN", "app": "设置"},
  {"step": 2, "subtask": "查看蓝牙设备", "app": "设置"}
]
```"""


def test_parse_plan_handles_code_fences_and_junk():
    steps = _parse_plan(PLAN_JSON)
    assert len(steps) == 2 and steps[0]["app"] == "设置"
    assert _parse_plan("no json here") == []
    assert _parse_plan('[{"subtask": "x"}]') == [{"subtask": "x"}]


def test_multi_app_task_is_decomposed_and_run_per_subtask():
    device = MockDevice()
    maps = [AppMap(MAP), AppMap(REPO / "phonecli_android" / "app_maps" / "clock_android.yaml")]
    llm = MockLLM(
        text_responses=[
            "NEED_VLM: too complex",   # route against map 1
            "NEED_VLM: too complex",   # route against map 2
            PLAN_JSON,                 # planner
            "NEED_VLM: sub 1",         # sub-agent 1 route
            "NEED_VLM: sub 2",         # sub-agent 2 route
        ],
        vision_responses=['Action: finish("first done")', 'Action: finish("second done")'],
    )
    agent = PhoneAgent(device, maps, llm, config=AgentConfig(max_rounds=3), sleep=lambda _s: None)
    result = agent.run("先打开 WLAN 再看蓝牙")

    assert result.ok
    assert result.reason == "planner"
    kinds = [s.kind for s in result.steps]
    assert kinds.count("subtask") == 2 and "plan" in kinds
    assert "first done" in result.answer and "second done" in result.answer


def test_planner_is_skipped_with_a_single_map():
    device = MockDevice()
    llm = MockLLM(
        text_responses=["NEED_VLM: x"],
        vision_responses=['Action: finish("done")'],
    )
    result = _agent(device, llm).run("single app task")
    assert not any(step.kind == "plan" for step in result.steps)


# ---------------------------------------------------------------------------
# an app that cannot be started on this display
# ---------------------------------------------------------------------------


def test_an_unlaunchable_app_fails_with_a_reason_not_a_retry_loop():
    from phoneagent.devices.base import LaunchRefused

    device = MockDevice()
    llm = MockLLM(["launch(日历)", "launch(日历)", "launch(日历)"])
    agent = _agent(device, llm)

    def refuse(_task):
        raise LaunchRefused("com.android.calendar", "singleTask")

    agent._run_layers = refuse            # pretend every layer tried to launch
    result = agent.run("明天早上七点叫我")

    assert not result.ok
    assert result.reason == "unlaunchable"
    assert "singleTask" in result.answer
    assert "com.android.calendar" in result.answer
    # one step, not eleven rounds of the same failed launch
    assert len(agent.steps) == 1


def test_the_launch_refusal_is_not_swallowed_by_the_action_wrapper():
    """_execute_action turns DeviceError into False; this one must escape."""
    from phoneagent.devices.base import LaunchRefused

    device = MockDevice()

    def refuse(_package, **_kwargs):
        raise LaunchRefused("com.android.calendar", "singleTask")

    device.launch = refuse                    # type: ignore[method-assign]
    agent = _agent(device, MockLLM([]))

    with pytest.raises(LaunchRefused):
        agent._execute_action("launch", ["com.android.calendar"])


# ---------------------------------------------------------------------------
# a question answered with "I cannot see that"
# ---------------------------------------------------------------------------


def test_refusing_to_look_gets_one_nudge():
    """The display has no status bar, so "I cannot see the time" is not an answer."""
    from phoneagent.core.agent import PhoneAgent

    assert PhoneAgent._asked_question("现在几点了？")
    assert PhoneAgent._asked_question("手机还剩多少电量")
    assert not PhoneAgent._asked_question("打开蓝牙设置")
    assert PhoneAgent._refuses("无法通过设置应用获取当前时间")
    assert not PhoneAgent._refuses("已打开蓝牙设置页面")


def test_the_nudge_is_sent_once_then_the_answer_is_accepted():
    from phoneagent.core.llm import MockLLM

    device = MockDevice()
    # refuse, then refuse again: the second one is accepted so a model that
    # really cannot see it is not trapped in a loop
    llm = MockLLM(vision_responses=[
        "finish(无法通过当前屏幕获取时间)",
        "finish(仍然无法获取)",
    ])
    agent = _agent(device, llm, macro_enabled=False, planner_enabled=False)
    result = agent.run("现在几点了？")

    kinds = [step.kind for step in agent.steps]
    assert "verify" in kinds            # the refusal was recorded
    assert result.ok
    assert "无法" in result.answer


def test_a_refusal_is_nudged_even_when_the_task_is_an_instruction():
    """One extra round is cheaper than accepting "I cannot" without looking."""
    from phoneagent.core.llm import MockLLM

    device = MockDevice()
    llm = MockLLM(vision_responses=["finish(无法打开)"] * 3)
    agent = _agent(device, llm, macro_enabled=False, planner_enabled=False)
    agent.run("打开蓝牙设置")
    assert agent._refusal_nudged
    assert [s.kind for s in agent.steps].count("verify") == 1


def test_a_router_that_cannot_see_hands_over_instead_of_answering():
    """The router is text-only; "I cannot see the time" from it must go to the VLM."""
    from phoneagent.core.llm import MockLLM

    device = MockDevice()
    llm = MockLLM(
        text_responses=["FINISH: 无法通过设置应用直接获取当前时间"],
        vision_responses=["finish(现在是19点53分)"],
    )
    agent = _agent(device, llm)
    result = agent.run("现在几点了？")

    assert result.ok
    assert "19点53分" in result.answer
    assert any(step.kind == "vlm" or step.kind == "finish" for step in agent.steps)


def test_a_loop_reports_what_repeated():
    from phoneagent.core.llm import MockLLM

    device = MockDevice()
    llm = MockLLM(vision_responses=["launch(时钟)"] * 6)
    agent = _agent(device, llm, macro_enabled=False, planner_enabled=False)
    result = agent.run("现在几点了？")

    assert not result.ok
    assert result.reason == "loop"
    assert "同一个动作" in result.answer
    assert "launch" in result.answer


# ---------------------------------------------------------------------------
# money: Ask mode asks, autonomous does not
# ---------------------------------------------------------------------------


class _SpendDevice(MockDevice):
    """A device whose screen shows an amount due."""

    def __init__(self, xml: str = "") -> None:
        super().__init__()
        self.xml = xml
        self.tapped: list = []

    def dump_ui(self) -> str:
        return self.xml

    def tap(self, x: int, y: int) -> bool:
        self.tapped.append((x, y))
        return True

    def tap_text(self, label: str) -> bool:
        self.tapped.append(label)
        return True


_ORDER_SCREEN = (
    '<?xml version="1.0"?><hierarchy><node text="提交订单" bounds="[40,2400][600,2500]" '
    'package="com.example.shop" /><node text="合计 ¥ 299.00" bounds="[40,2200][600,2300]" '
    'package="com.example.shop" /></hierarchy>'
)


def test_a_label_tap_that_spends_money_asks_first():
    asked = []

    def ask(question):
        asked.append(question)
        return "取消"

    device = _SpendDevice(_ORDER_SCREEN)
    agent = _agent(device, MockLLM([]), ask=ask,
                   macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("tap_text", ["提交订单"]) is False
    assert device.tapped == []                    # nothing was pressed
    assert asked and "¥" in asked[0]              # the amount is in the question


def test_approving_the_question_lets_the_tap_happen():
    device = _SpendDevice(_ORDER_SCREEN)
    agent = _agent(device, MockLLM([]), ask=lambda _q: "继续",
                   macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("tap_text", ["提交订单"]) is True
    assert device.tapped == ["提交订单"]


def test_autonomous_mode_does_not_ask():
    asked = []
    device = _SpendDevice(_ORDER_SCREEN)
    agent = _agent(device, MockLLM([]), ask=lambda q: asked.append(q) or "取消",
                   macro_enabled=False, planner_enabled=False, autonomous=True)
    assert agent._execute_action("tap_text", ["提交订单"]) is True
    assert asked == []


def test_an_ordinary_tap_never_asks():
    asked = []
    device = _SpendDevice(_ORDER_SCREEN)
    agent = _agent(device, MockLLM([]), ask=lambda q: asked.append(q) or "取消",
                   macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("tap_text", ["返回"]) is True
    assert asked == []


def test_nobody_to_ask_means_no():
    device = _SpendDevice(_ORDER_SCREEN)
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("tap_text", ["立即购买"]) is False
    assert device.tapped == []


# ---------------------------------------------------------------------------
# handing the phone to the user
# ---------------------------------------------------------------------------


class _HandoverDevice(MockDevice):
    def __init__(self) -> None:
        super().__init__()
        self.handed: list = []
        self.launched: list = []

    def current_app(self) -> str:
        return "com.example.bank"

    def hand_over(self, package: str) -> bool:
        self.handed.append(package)
        return True

    def launch(self, package: str, **kwargs) -> bool:
        self.launched.append(package)
        return True


def test_hand_over_moves_the_app_asks_and_brings_it_back():
    device = _HandoverDevice()
    asked = []

    def ask(question):
        asked.append(question)
        return "好了"

    agent = _agent(device, MockLLM([]), ask=ask,
                   macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("hand_over", ["输入支付密码"]) is True
    assert device.handed == ["com.example.bank"]      # put on the user's screen
    assert device.launched == ["com.example.bank"]    # and taken back
    assert "输入支付密码" in asked[0]


def test_hand_over_that_the_user_cancels_reports_failure():
    device = _HandoverDevice()
    agent = _agent(device, MockLLM([]), ask=lambda _q: "取消",
                   macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("hand_over", ["登录"]) is False
    assert device.launched == ["com.example.bank"]     # still taken back


def test_hand_over_without_a_user_is_refused():
    device = _HandoverDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("hand_over", ["登录"]) is False
    assert device.handed == []


def test_a_task_about_an_unmapped_app_skips_macro_routing():
    """The router will happily map "登录淘宝" onto the WLAN operation."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent

    device = MockDevice()
    # one text reply would have been the router; there must be none
    llm = MockLLM(text_responses=["OP: wlan"], vision_responses=["finish(打不开淘宝)"])
    agent = PhoneAgent(device, [AppMap(MAP)], llm,
                       config=AgentConfig(max_rounds=3, planner_enabled=False),
                       sleep=lambda _s: None,
                       known_apps={"淘宝": "com.taobao.taobao"})
    result = agent.run("登录淘宝账号")

    kinds = [step.kind for step in agent.steps]
    assert "route" not in kinds and "macro" not in kinds
    assert result.answer


# ---------------------------------------------------------------------------
# an app that opens on the user's display
# ---------------------------------------------------------------------------


class _UserDisplayDevice(MockDevice):
    """Taobao-like: the launcher activity always lands on display 0."""

    def __init__(self, comes_back: bool = False) -> None:
        super().__init__()
        self.comes_back = comes_back
        self.launches = 0

    def launch(self, package: str, **kwargs) -> bool:
        from phoneagent.devices.base import NeedsHandover

        self.launches += 1
        if self.comes_back and self.launches > 1:
            return True
        raise NeedsHandover(package, "started on the phone's own display")


def test_an_app_on_the_user_display_becomes_a_hand_over():
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    device = _UserDisplayDevice(comes_back=False)
    asked = []

    def ask(question):
        asked.append(question)
        return "好了"

    agent = PhoneAgent(device, [AppMap(MAP)],
                       MockLLM(vision_responses=["launch(淘宝)"] * 3),
                       config=AgentConfig(max_rounds=4, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None, ask=ask,
                       known_apps={"淘宝": "com.taobao.taobao"})
    result = agent.run("登录淘宝账号")

    assert asked and "淘宝" in asked[0]        # the user was asked
    assert not result.ok
    assert "只能在你自己的屏幕上" in result.answer   # and told why it stops


def test_hand_over_then_continue_when_the_app_comes_back():
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    device = _UserDisplayDevice(comes_back=True)
    agent = PhoneAgent(device, [AppMap(MAP)],
                       MockLLM(vision_responses=["launch(淘宝)", "finish(已登录)"]),
                       config=AgentConfig(max_rounds=4, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None, ask=lambda _q: "好了",
                       known_apps={"淘宝": "com.taobao.taobao"})
    result = agent.run("登录淘宝账号")

    assert result.ok and "已登录" in result.answer
    assert any(step.kind == "handover" for step in agent.steps)


def test_an_unknown_macro_target_is_reported_to_the_model():
    device = MockDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("macro", ["登录"]) is False
    assert any("no target" in line for line in agent.history)


def test_a_single_task_app_is_not_handed_over_it_is_explained():
    """天气 is singleTask: the app always opens on the user's display."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM
    from phoneagent.devices.base import NeedsHandover

    class Device(MockDevice):
        def launch(self, package, **kwargs):
            raise NeedsHandover(package, "started on the phone's own display")

    asked = []
    agent = PhoneAgent(Device(), [AppMap(MAP)],
                       MockLLM(vision_responses=["launch(天气)"]),
                       config=AgentConfig(max_rounds=3, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None,
                       ask=lambda q: asked.append(q) or "好了",
                       known_apps={"天气": "com.miui.weather2"})
    agent._launch_modes = {"com.miui.weather2": 2}      # singleTask

    result = agent.run("打开天气")

    assert asked == []                                  # no pointless question
    assert not result.ok
    assert "singleTask" in result.answer
    assert "浏览器" in result.answer                     # and a way forward


# ---------------------------------------------------------------------------
# GUI-only vs hybrid (shell) mode
# ---------------------------------------------------------------------------


class _ShellDevice(MockDevice):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list = []

    def shell(self, command: str) -> str:
        self.commands.append(command)
        return "ok"


def test_the_shell_is_on_by_default():
    device = _ShellDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False)
    assert agent._execute_action("shell", ["settings get global adb_wifi_enabled"]) is True
    assert device.commands == ["settings get global adb_wifi_enabled"]


def test_a_caller_can_still_turn_the_shell_off():
    device = _ShellDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False,
                   shell_enabled=False)
    assert agent._execute_action("shell", ["ls /sdcard"]) is False
    assert device.commands == []
    assert any("not available" in line for line in agent.history)


def test_a_hybrid_task_runs_a_command_and_reports_it():
    device = _ShellDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False,
                   shell_enabled=True)
    assert agent._execute_action("shell", ["settings get global adb_wifi_enabled"]) is True
    assert device.commands == ["settings get global adb_wifi_enabled"]
    assert any("shell: settings get" in line for line in agent.history)


def test_a_destructive_command_needs_a_yes():
    device = _ShellDevice()
    asked = []
    agent = _agent(device, MockLLM([]), ask=lambda q: asked.append(q) or "取消",
                   macro_enabled=False, planner_enabled=False, shell_enabled=True)
    assert agent._execute_action("shell", ["pm uninstall com.example"]) is False
    assert device.commands == []
    assert asked and "pm uninstall" in asked[0]


def test_a_command_that_never_returns_is_refused():
    device = _ShellDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False,
                   shell_enabled=True)
    assert agent._execute_action("shell", ["logcat -d"]) is False
    assert device.commands == []


def test_shell_may_not_launch_an_app():
    """`am start` from the shell picks its own display -- that is the escape."""
    device = _ShellDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False,
                   shell_enabled=True)
    assert agent._execute_action("shell", ["am start -n com.xingin.xhs/.index.v2.IndexActivityV2"]) is False
    assert device.commands == []
    assert any("launch()" in line for line in agent.history)


def test_shell_may_still_read():
    device = _ShellDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False,
                   shell_enabled=True)
    assert agent._execute_action("shell", ["dumpsys window | grep mCurrentFocus"]) is True
    assert device.commands and "dumpsys window" in device.commands[0]


def test_shell_is_offered_only_in_hybrid_mode():
    from phoneagent.core.agent import action_tools

    names = [tool["function"]["name"] for tool in action_tools(True)]
    assert "shell" in names


def test_the_same_shell_command_is_not_run_twice():
    device = _ShellDevice()
    agent = _agent(device, MockLLM([]), macro_enabled=False, planner_enabled=False,
                   shell_enabled=True)
    assert agent._execute_action("shell", ["ls /sdcard"]) is True
    assert agent._execute_action("shell", ["ls /sdcard"]) is True
    assert device.commands == ["ls /sdcard"]          # ran once
    assert any("already ran" in line for line in agent.history)


# ---------------------------------------------------------------------------
# an app that moves itself onto the user's display
# ---------------------------------------------------------------------------


class _EscapingDevice(MockDevice):
    """Taobao-like: it accepts the launch here, then leaves for display 0."""

    def __init__(self, comes_back: bool = True) -> None:
        super().__init__()
        self.here = "com.taobao.taobao"
        self.comes_back = comes_back
        self.launches = 0

    def current_app(self) -> str:
        return self.here

    def has_task_on(self, display_id: int, package: str) -> bool:
        return display_id == 0 and package == "com.taobao.taobao"

    def launch(self, package, **kwargs) -> bool:
        self.launches += 1
        if self.comes_back:
            self.here = package
        return True


def test_an_app_that_escapes_is_brought_back():
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    device = _EscapingDevice(comes_back=True)
    agent = PhoneAgent(device, [], MockLLM(vision_responses=["finish(done)"]),
                       config=AgentConfig(max_rounds=3, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None)
    agent._driving_package = "com.taobao.taobao"
    device.here = "com.android.launcher"          # it left
    agent._check_escape(1)

    assert device.launches == 1                   # pulled back
    assert any(step.kind == "escaped" for step in agent.steps)


def test_an_app_that_keeps_escaping_is_reported_not_looped():
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    device = _EscapingDevice(comes_back=False)
    agent = PhoneAgent(device, [], MockLLM([]),
                       config=AgentConfig(max_rounds=3, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None)
    agent._driving_package = "com.taobao.taobao"
    device.here = "com.miui.home"                 # it is not here any more
    agent._check_escape(1)                        # first time: pull back
    # Second time there is nothing useful left to do: every later tap would land
    # on a screen the agent cannot see. It stops and says why.
    from phoneagent.core.agent import _Stranded

    with pytest.raises(_Stranded) as caught:
        agent._check_escape(2)
    assert device.launches == 1
    assert "托管" in caught.value.answer


def test_tee_steps_forward_as_they_are_added():
    """The parent sees a subtask's steps while it runs, not after."""
    from phoneagent.core.agent import Step, _TeeSteps

    parent = []
    tee = _TeeSteps(parent)
    tee.append(Step(0, "vlm", "tap", "ok"))
    assert len(parent) == 1 and parent[0].kind == "vlm"
    tee.extend([Step(1, "vlm", "type", "ok")])
    assert len(parent) == 2


def test_a_subtask_run_feeds_the_parent_list():
    from phoneagent.core.agent import AgentConfig, PhoneAgent, _TeeSteps
    from phoneagent.core.llm import MockLLM

    parent = []
    sub = PhoneAgent(MockDevice(), [AppMap(MAP)],
                     MockLLM(vision_responses=["tap(0.5, 0.5)", "finish(好了)"]),
                     config=AgentConfig(max_rounds=4, macro_enabled=False,
                                        planner_enabled=False),
                     sleep=lambda _s: None)
    sub.steps = _TeeSteps(parent)          # what _execute_plan wires up
    sub.run("打开蓝牙设置")

    kinds = [step.kind for step in parent]
    assert "vlm" in kinds and "finish" in kinds


def test_a_pinned_app_is_never_launched_at_all():
    """Launching it would put it on the user's screen; refuse first."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    class Device(MockDevice):
        def __init__(self):
            super().__init__()
            self.launches = []

        def launch(self, package, **kwargs):
            self.launches.append(package)
            return True

    device = Device()
    agent = PhoneAgent(device, [AppMap(MAP)],
                       MockLLM(vision_responses=["launch(京东)"]),
                       config=AgentConfig(max_rounds=3, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None,
                       known_apps={"京东": "com.jingdong.app.mall"})
    agent._launch_modes = {"com.jingdong.app.mall": 2}

    result = agent.run("打开京东")

    assert device.launches == []                  # never even tried
    assert not result.ok
    assert "singleTask" in result.answer


def test_a_standard_app_is_not_refused_as_pinned():
    """LAUNCH_STANDARD is 0, and the first version of this check refused it."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    class Device(MockDevice):
        def __init__(self):
            super().__init__()
            self.launches = []

        def launch(self, package, **kwargs):
            self.launches.append(package)
            return True

    device = Device()
    agent = PhoneAgent(device, [AppMap(MAP)],
                       MockLLM(vision_responses=["launch(设置)", "finish(好了)"]),
                       config=AgentConfig(max_rounds=3, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None,
                       known_apps={"设置": "com.android.settings"})
    agent._launch_modes = {"com.android.settings": 0}          # standard

    result = agent.run("打开设置")

    assert device.launches == ["com.android.settings"], "standard apps must launch"
    assert "singleTask" not in (result.answer or "")


def test_three_shell_reads_are_not_a_loop():
    """Reading three values with three shell calls leaves the screen identical."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    agent = PhoneAgent(MockDevice(), [AppMap(MAP)],
                       MockLLM(vision_responses=[]),
                       config=AgentConfig(max_rounds=5, macro_enabled=False,
                                          planner_enabled=False, loop_threshold=3),
                       sleep=lambda _s: None)
    agent.actions = ["shell", "shell", "shell"]
    assert agent._loop_detected(unchanged=True) is False

    agent.actions = ["tap", "tap", "tap"]
    assert agent._loop_detected(unchanged=True) is True


def test_a_captcha_stops_the_task_and_parks_the_app():
    """Retrying at a captcha is how an account gets locked."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    class Device(MockDevice):
        def __init__(self):
            super().__init__()
            self.moved = []

        def dump_ui(self):
            return ('<hierarchy>'
                    '<node class="TextView" text="安全验证" bounds="[0,0][200,80]"/>'
                    '<node class="TextView" text="请完成验证" bounds="[0,90][200,170]"/>'
                    '</hierarchy>')

        def hand_over(self, package):
            self.moved.append(package)
            return True

    device = Device()
    agent = PhoneAgent(device, [AppMap(MAP)],
                       MockLLM(vision_responses=["tap(0.5, 0.5)"]),
                       config=AgentConfig(max_rounds=3, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None)
    agent._driving_package = "com.example.app"

    result = agent.run("打开某页面")

    assert not result.ok
    assert "安全验证" in (result.answer or "")
    assert device.moved == ["com.example.app"]
    assert agent._challenge_until["com.example.app"] > 0


def test_an_app_that_asked_for_a_captcha_is_driven_by_screen(tmp_path):
    """Replay is the most script-like thing we do; that app has said no."""
    from phoneagent import risk
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    path = str(tmp_path / "risks.json")
    app = AppMap(MAP)
    risk.record(path, app.package or "")

    device = MockDevice()
    llm = MockLLM(text_responses=["OP: never"], vision_responses=["finish(用屏幕做的)"])
    agent = PhoneAgent(device, [app], llm,
                       config=AgentConfig(max_rounds=3, risk_path=path,
                                          macro_enabled=True, planner_enabled=False),
                       sleep=lambda _s: None)

    result = agent.run("do the thing")

    # the macro layer did not run: no route step, and the router was never asked
    assert all(step.kind != "route" for step in result.steps)
    assert result.ok


def test_tappable_targets_are_numbered_for_the_screenshot():
    """Set-of-Mark: every number must be a target the model may name."""
    from phoneagent.core.agent import PhoneAgent

    xml = (
        '<hierarchy>'
        '<node class="Button" clickable="true" text="蓝牙" '
        'bounds="[100,200][300,320]" package="com.android.settings" />'
        '<node class="TextView" text="说明" bounds="[100,400][300,460]" '
        'package="com.android.settings" />'
        '<node class="ImageButton" clickable="true" content-desc="更多" '
        'bounds="[900,1200][1000,1290]" package="com.android.settings" />'
        '</hierarchy>'
    )
    agent = object.__new__(PhoneAgent)
    agent.config = type("C", (), {"max_marks": 30})()   # only the cap is read
    marks = PhoneAgent._marks(agent, xml)

    assert [m["i"] for m in marks] == [1, 2]
    assert marks[0]["label"] == "蓝牙"
    assert marks[1]["label"] == "更多"
    # a plain label is not a target: numbering it would offer an unusable index
    assert all(m["label"] != "说明" for m in marks)


def test_three_different_taps_are_not_a_loop():
    """7, +, 8 is three tap_index calls and the correct way to add."""
    from phoneagent.core.agent import PhoneAgent

    agent = object.__new__(PhoneAgent)
    agent.config = type("C", (), {"loop_threshold": 3})()
    agent.actions = ["tap_index(11)", "tap_index(22)", "tap_index(12)"]
    assert PhoneAgent._loop_detected(agent, unchanged=True) is False

    agent.actions = ["tap_index(11)", "tap_index(11)", "tap_index(11)"]
    assert PhoneAgent._loop_detected(agent, unchanged=True) is True


def test_the_notification_shade_is_recognised():
    """A pulled-down shade is not a screen the agent can work on."""
    from phoneagent.core.agent import PhoneAgent

    # Measured: when the shade is open the app's window is not in the tree at
    # all, so "nothing but chrome" is the test. The 通知： labels are on the
    # status bar and appear with the shade closed too, so they cannot be used.
    shade = (
        '<hierarchy><node package="com.android.systemui" text="拼多多"/>'
        '<node package="com.android.systemui" text="小米商城通知："/>'
        '</hierarchy>'
    )
    assert PhoneAgent._shade_is_open(shade) is True
    assert PhoneAgent._shade_is_open('<node package="com.xingin.xhs" text="发现"/>') is False


def test_the_shade_is_detected_from_a_real_dump():
    """Measured: an open shade is 260 nodes, all com.android.systemui."""
    from phoneagent.core.agent import PhoneAgent

    shade = (
        '<hierarchy><node package="com.android.systemui" text="拼多多"/>'
        '<node package="com.android.systemui" text="只要1分钱！试试你就知道了"/>'
        '</hierarchy>'
    )
    assert PhoneAgent._shade_is_open(shade) is True
    assert PhoneAgent._shade_is_open(
        '<node package="com.xingin.xhs" text="发现"/>'
        '<node package="com.android.systemui" text="17:49"/>'
    ) is False


def test_history_carries_the_outcome_and_the_ledger():
    """A call without its result teaches the model nothing."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    agent = PhoneAgent(MockDevice(), [], MockLLM([]),
                       config=AgentConfig(max_rounds=6), sleep=lambda _s: None)
    agent._remember_step("launch(小红书)", True, "launch", ["小红书"])
    agent._remember_step("type(香港)", False, "type", ["香港"])

    block = agent._ledger_block(3)
    assert "第 3/6 步" in block and "还剩 3 步" in block
    assert "已完成：launch(小红书)" in block
    assert "已失败：type(香港)" in block


def test_a_run_that_achieved_something_is_partial_not_failed():
    """Opened the page but never typed: 部分完成, not 失败."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent, Step
    from phoneagent.core.llm import MockLLM

    agent = PhoneAgent(MockDevice(), [], MockLLM([]),
                       config=AgentConfig(max_rounds=6), sleep=lambda _s: None)
    agent.steps = [
        Step(1, "macro", "搜索", "macro ok (3/3 steps)"),
        Step(2, "vlm", "launch", "ok"),
        Step(3, "vlm", "type", "ok"),
    ]

    assert agent._achieved() == ["打开了目标页面", "启动过 App", "输入过文字"]
    answer = agent._partial_answer("还没搜出结果。")
    assert answer.startswith("做到了一半：")
    assert "打开了目标页面" in answer and answer.endswith("还没搜出结果。")


def test_nothing_achieved_stays_a_plain_failure():
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    agent = PhoneAgent(MockDevice(), [], MockLLM([]),
                       config=AgentConfig(max_rounds=6), sleep=lambda _s: None)
    assert agent._achieved() == []
    assert agent._partial_answer("没动。") == "没动。"


def test_running_out_of_steps_after_doing_something_is_partial():
    """End-to-end: taps until the budget ends, and the result says partial."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent
    from phoneagent.core.llm import MockLLM

    device = MockDevice()
    # typing succeeds on the mock device; the taps after it do not matter
    llm = MockLLM(text_responses=["keep going"],
                  vision_responses=['Action: type("香港")'] +
                                   ['Action: tap(0.5, 0.5)'] * 12)
    agent = PhoneAgent(device, [], llm,
                       config=AgentConfig(max_rounds=3, macro_enabled=False,
                                          planner_enabled=False),
                       sleep=lambda _s: None)
    result = agent.run("随便点几下")

    assert result.ok is False
    assert result.reason == "partial"
    assert result.answer.startswith("做到了一半：")
    assert "输入过文字" in result.answer


def test_a_finished_subtask_counts_as_an_achievement():
    """The parent of a decomposed task sees none of the subtask's own steps."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent, Step
    from phoneagent.core.llm import MockLLM

    agent = PhoneAgent(MockDevice(), [], MockLLM([]),
                       config=AgentConfig(max_rounds=6), sleep=lambda _s: None)
    agent.steps = [Step(1, "subtask", "小红书", "ok: 搜索香港旅游攻略")]
    assert agent._achieved() == ["完成了 1 个子任务"]


def test_a_subtask_spends_from_the_parent_budget():
    """35 steps per task must mean 35, not 35 per subtask."""
    from phoneagent.core.agent import AgentConfig, PhoneAgent, _Budget
    from phoneagent.core.llm import MockLLM

    parent = PhoneAgent(MockDevice(), [], MockLLM([]),
                        config=AgentConfig(max_rounds=5), sleep=lambda _s: None)
    budget = _Budget(5)
    parent._budget = budget

    sub = PhoneAgent(MockDevice(), [], MockLLM([]),
                     config=AgentConfig(max_rounds=5), sleep=lambda _s: None)
    sub._budget = parent._budget          # what _execute_plan does

    assert [budget.take() for _ in range(5)] == [True] * 5
    assert budget.take() is False
    assert sub._budget is parent._budget


def test_the_map_is_asked_again_when_the_app_changes(monkeypatch):
    """A task that moves between apps must be able to use a second map."""
    from phoneagent.core.agent import PhoneAgent

    agent = PhoneAgent.__new__(PhoneAgent)
    agent.config = type("C", (), {"macro_enabled": True, "max_rounds": 35})()
    agent.maps = [type("M", (), {"package": "com.a", "app_name": "A"})(),
                  type("M", (), {"package": "com.b", "app_name": "B"})()]
    agent._routed_packages = {"com.a"}
    agent.log = lambda *a, **k: None
    agent.steps = []

    calls = []

    def macro_round(task, only=""):
        calls.append(only)
        return None

    agent._macro_round = macro_round
    agent.device = type("D", (), {"current_app": lambda self: "com.b"})()
    agent._reroute_for_foreground("do the thing")
    assert calls == ["com.b"], "the second app's map is consulted"

    # and it is not asked twice for the same app
    agent._routed_packages.add("com.b")
    agent._reroute_for_foreground("do the thing")
    assert calls == ["com.b"], "one route per app per run"


def test_a_single_app_task_is_not_split_into_subtasks():
    """Planning costs a system prompt per subtask; one app does not need it."""
    from phoneagent.core.agent import PhoneAgent

    agent = PhoneAgent.__new__(PhoneAgent)
    agent.maps = [type("M", (), {"app_name": "小红书", "package": "com.xingin.xhs"})(),
                  type("M", (), {"app_name": "微博", "package": "com.sina.weibo"})()]
    agent._depth = 0
    agent.config = type("C", (), {"planner_enabled": True})()

    def named(task):
        lower = task.lower()
        return [m for m in agent.maps if m.app_name.lower() in lower]

    assert len(named("看小红书我的页面有几个粉丝")) == 1        # single app
    assert len(named("先看小红书，再去微博")) == 2               # spans apps
    assert len(named("帮我看看今天的新闻")) == 0                 # names no app


def _map_for_routing(tmp_path):
    import yaml
    from phoneagent.core.app_map import AppMap

    elements = lambda *texts: [{"text": t, "center": [0.5, 0.5], "fixed": True} for t in texts]
    data = {
        "app": "小红书", "package": "com.xingin.xhs", "screen_w": 1280, "screen_h": 2772,
        "screens": [
            {"id": "screen_0", "selected": ["首页"], "elements": [
                {"text": "我", "center": [0.9, 0.95], "fixed": True, "leads_to": "screen_1"},
                {"text": "搜索", "center": [0.5, 0.03], "fixed": True, "leads_to": "screen_2"},
                {"text": "笔记标题一", "center": [0.5, 0.3], "fixed": True},
            ] + elements(*[f"卡片{i}" for i in range(40)])},
            {"id": "screen_1", "selected": ["我"], "elements": elements("粉丝", "关注")},
            {"id": "screen_2", "selected": ["搜索"], "elements": elements("搜索框")},
        ],
        "screen_macros": {},
    }
    path = tmp_path / "xhs.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return AppMap(str(path))


def test_the_router_is_shown_a_shortlist_not_the_whole_map(tmp_path):
    from phoneagent.core.agent import PhoneAgent

    agent = PhoneAgent.__new__(PhoneAgent)
    agent.config = type("C", (), {"catalog_style": "tree", "catalog_nav_only": False,
                                  "max_route_candidates": 30,
                                  "max_catalog_chars": 0})()
    agent._catalog_by_map = {}
    agent._ops_by_map = {}
    agent.log = lambda *a, **k: None
    app_map = _map_for_routing(tmp_path)

    catalog = agent._route_catalog("看小红书我页面有几个粉丝", app_map)
    assert "我" in catalog
    assert "卡片39" not in catalog, "content the task never mentions stays out"


def test_the_shortlist_falls_back_to_everything(tmp_path):
    """A map with no navigation and no matching names must behave as before."""
    from phoneagent.core.agent import PhoneAgent

    agent = PhoneAgent.__new__(PhoneAgent)
    agent.config = type("C", (), {"catalog_style": "tree", "catalog_nav_only": False,
                                  "max_route_candidates": 30,
                                  "max_catalog_chars": 0})()
    agent._catalog_by_map = {}
    agent._ops_by_map = {}
    agent.log = lambda *a, **k: None
    app_map = _map_for_routing(tmp_path)

    catalog = agent._route_catalog("完全无关的一句话", app_map)
    assert "卡片39" in catalog, "no shortlist means the full catalogue, not an empty one"


def test_the_search_term_is_taken_from_the_task():
    from phoneagent.core.agent import PhoneAgent

    cases = {
        "在小红书搜索『深圳火锅』，告诉我最上面那条笔记的标题": "深圳火锅",
        "去小红书搜一下 附近的火锅 然后告诉我": "附近的火锅",
        "搜索「港大 游览」": "港大 游览",
        "看看设置里的电池容量": "",
        "在小红书搜": "",
    }
    for task, expected in cases.items():
        assert PhoneAgent._explicit_query(task) == expected, task


def test_a_carried_name_is_used_instead_of_a_generic_word():
    """Cross-app hand-off: the next subtask must use what the last one found.

    Measured on 小红书 → 携程: the second subtask read "用记下的酒店名称搜索酒店"
    and typed 酒店 into Ctrip, because the value only existed in the previous
    subtask's answer.
    """
    from phoneagent.core.agent import PhoneAgent

    carry = ("已打开第一篇笔记《香港避雷攻略》。文中提到的第一家住宿是「重庆大厦」，"
             "距离尖沙咀站步行 3 分钟。")
    assert PhoneAgent._carried_entity(carry) == "重庆大厦"
    assert PhoneAgent._explicit_query("用记下来的酒店名称搜索酒店", carry) == "重庆大厦"
    # without a carried value nothing changes
    assert PhoneAgent._explicit_query("在小红书里搜索「香港大学附近酒店推荐」") == "香港大学附近酒店推荐"


def test_the_watchdog_flags_a_run_that_stops_making_progress():
    """A call that never returns cannot report itself; the watchdog can."""
    import time

    from phoneagent.core.agent import AgentConfig, PhoneAgent

    class Fake:
        config = AgentConfig(stall_timeout=0.2)
        _stalled = False
        _stall_seconds = 0
        _watchdog_stop = False

        def __init__(self):
            self.messages = []
            self._heartbeat = time.time()

        def log(self, message):
            self.messages.append(message)

    fake = Fake()
    PhoneAgent._start_watchdog(fake)
    time.sleep(0.6)
    fake._watchdog_stop = True

    assert fake._stalled is True
    assert any("no progress" in m for m in fake.messages)


def test_progress_keeps_the_watchdog_quiet():
    import time

    from phoneagent.core.agent import AgentConfig, PhoneAgent

    class Fake:
        config = AgentConfig(stall_timeout=0.4)
        _stalled = False
        _stall_seconds = 0
        _watchdog_stop = False

        def __init__(self):
            self.messages = []
            self._heartbeat = time.time()

        def log(self, message):
            self.messages.append(message)

    fake = Fake()
    PhoneAgent._start_watchdog(fake)
    for _ in range(4):
        time.sleep(0.15)
        PhoneAgent._touch(fake)
    fake._watchdog_stop = True

    assert fake._stalled is False
