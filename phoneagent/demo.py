"""Offline demo of the unified agent — no phone, no API key, no network.

    python3 -m phoneagent.demo

Two scenarios run against a MockDevice with a scripted MockLLM:

    1. macro hit      route -> replay -> done (one model call, zero actions by a VLM)
    2. VLM fallback   route misses -> screenshot loop -> two actions -> finish
"""

from __future__ import annotations

from pathlib import Path

from .core.agent import AgentConfig, PhoneAgent
from .core.app_map import AppMap
from .core.llm import MockLLM
from .devices.mock import MockDevice

REPO = Path(__file__).resolve().parents[1]
MAP = REPO / "phonecli_android" / "app_maps" / "calendar_android.yaml"


def _first(app_map: AppMap, op_type: str):
    return next(op for op in app_map.build_operations().values() if op.type == op_type)


def scenario_macro() -> None:
    app = AppMap(MAP)
    op = _first(app, "ACT")
    device = MockDevice()
    llm = MockLLM(text_responses=[f"OP: {op.id}"])

    agent = PhoneAgent(
        device,
        [app],
        llm,
        config=AgentConfig(verify_with_vlm=False),
        log=print,
        sleep=lambda _s: None,
    )
    result = agent.run("turn off task notifications")

    print(f"  routed to: {result.used_op}")
    print(f"  device calls: {[n for n in device.method_names()]}")
    print(f"  model calls: {len(llm.calls)} (1 text routing, 0 vision)")
    print(f"  result: {result.summary()}")


def scenario_vlm_fallback() -> None:
    app = AppMap(MAP)
    device = MockDevice()
    llm = MockLLM(
        text_responses=["NEED_VLM: the task needs to read a list"],
        vision_responses=[
            "State Assessment: a list is visible\nAction: swipe(up)",
            "State Assessment: scrolled\nAction: tap(0.5, 0.6)",
            "State Assessment: detail page open\nAction: finish(\"3 events today\")",
        ],
    )
    agent = PhoneAgent(device, [app], llm, log=print, sleep=lambda _s: None)
    result = agent.run("how many events are on the calendar today?")

    for step in result.steps:
        print(f"  round {step.round} [{step.kind:<6}] {step.action:<6} {step.detail}")
    print(f"  device calls: {[n for n in device.method_names()]}")
    print(f"  result: {result.summary()} -> {result.answer!r}")


def main() -> int:
    print("scenario 1 — macro hit")
    scenario_macro()
    print()
    print("scenario 2 — VLM fallback")
    scenario_vlm_fallback()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
