"""Wait for the screen to stop moving before reading it.

A tap is fire-and-forget. Reading straight afterwards can land on a
half-drawn page, which is how a good replay gets marked "landed somewhere
else" and how the model acts on a screen that no longer exists.
"""

from __future__ import annotations

from phoneagent.core.agent import AgentConfig, PhoneAgent
from phoneagent.core.llm import Choice, ToolCall
from phoneagent.devices.mock import MockDevice
from phoneagent.devices.virtual import VirtualDisplayDevice


class ScriptedScreen:
    """Stands in for the device: dump_ui() returns the next scripted tree."""

    def __init__(self, dumps):
        self._dumps = list(dumps)

    def dump_ui(self):
        return self._dumps.pop(0) if len(self._dumps) > 1 else self._dumps[0]


def _settle(device, **kwargs):
    return VirtualDisplayDevice.settle(device, **kwargs)


def test_settle_returns_as_soon_as_two_reads_match():
    screen = ScriptedScreen(["A", "B", "B", "C"])
    device = screen  # settle only uses dump_ui
    result = _settle(device, timeout=1.0, interval=0.0)
    assert result == "B"
    assert screen._dumps == ["C"]  # stopped reading once it was stable


def test_settle_gives_up_after_the_timeout_with_the_last_read():
    screen = ScriptedScreen(["A", "B", "C", "D", "E", "F"])
    result = _settle(screen, timeout=0.05, interval=0.0)
    assert result in {"A", "B", "C", "D", "E", "F"}
    assert isinstance(result, str)


class SettleDevice(MockDevice):
    def __init__(self):
        super().__init__()
        self.settles = 0

    def dump_ui(self):
        return '<hierarchy><node text="设置" bounds="[0,0][200,120]"/></hierarchy>'

    def settle(self, timeout: float = 2.5, interval: float = 0.2, stable_reads: int = 1):
        self.settles += 1
        return self.dump_ui()


class OneActionLLM:
    """Taps once, then finishes."""

    def __init__(self, action="tap", arguments=None):
        self._queue = [(action, arguments or {"rx": 0.5, "ry": 0.5}),
                       ("finish", {"message": "已打开蓝牙设置"})]

    def choose(self, system, user, image_png=None, tools=None, temperature=0.0):
        name, arguments = self._queue.pop(0)
        return Choice(call=ToolCall(name=name, arguments=arguments))


def _agent(device, llm, **config):
    return PhoneAgent(device, [], llm, config=AgentConfig(verify_with_vlm=False, **config),
                      sleep=lambda _s: None)


def test_a_tap_is_followed_by_a_settle():
    device = SettleDevice()
    _agent(device, OneActionLLM("tap")).run("打开蓝牙设置")
    assert device.settles == 1


def test_wait_does_not_settle():
    device = SettleDevice()
    _agent(device, OneActionLLM("wait", {"interval": 1})).run("等一会")
    assert device.settles == 0


def test_settle_can_be_turned_off():
    device = SettleDevice()
    _agent(device, OneActionLLM("tap"), settle_timeout=0).run("打开蓝牙设置")
    assert device.settles == 0
