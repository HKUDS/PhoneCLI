"""Run one task end to end: route -> replay -> verify (the map+CLI path).

    python3 -m phoneagent.run --serial e3fa9e60 \
        --map phoneagent/maps/k90_settings_v2.yaml \
        --task "打开蓝牙" \
        --api-key sk-or-... --model qwen/qwen3.8-flash

Unlike ``demo.py`` (offline, scripted), this drives a real phone: here the
task is first routed to a compiled operation with one text call, replayed
deterministically with zero further model calls until verification, and only
falls back to the GUI loop when routing or verification fails.
"""

from __future__ import annotations

import argparse
import sys

from .core.agent import AgentConfig, PhoneAgent
from .core.app_map import AppMap
from .core.llm import OpenAICompatClient
from .devices.adb import AdbDevice
from .devices.base import DeviceError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--adb", default=None)
    parser.add_argument("--map", action="append", required=True, help="app map (repeatable)")
    parser.add_argument("--task", "-t", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--api-base", default="https://openrouter.ai/api/v1")
    parser.add_argument("--model", default="qwen/qwen3.8-flash")
    parser.add_argument("--max-rounds", type=int, default=12)
    parser.add_argument("--no-verify", action="store_true", help="skip the VLM verification call")
    parser.add_argument("--no-macro", action="store_true", help="skip routing, go straight to the GUI loop")
    parser.add_argument("--wake", action="store_true", help="wake the screen and dismiss the keyguard")
    parser.add_argument("--reset-app", default=None, help="force-stop this package and go home first")
    args = parser.parse_args(argv)

    device = AdbDevice(serial=args.serial, adb=args.adb)
    if args.reset_app or args.wake:
        try:
            if args.reset_app:
                device.shell(f"am force-stop {args.reset_app}")
                device.shell("input keyevent 3")
                device.shell("svc power stayon true")
            if args.wake:
                device.shell("input keyevent 224")
                device.shell("wm dismiss-keyguard")
        except DeviceError as exc:
            print(f"prepare failed: {exc}")

    maps = [AppMap(path) for path in args.map]
    llm = OpenAICompatClient(
        api_key=args.api_key,
        api_base=args.api_base,
        model=args.model,
        disable_reasoning=True,
    )
    agent = PhoneAgent(
        device,
        maps,
        llm,
        config=AgentConfig(
            max_rounds=args.max_rounds,
            macro_enabled=not args.no_macro,
            verify_with_vlm=not args.no_verify,
            catalog_style="tree",
        ),
        known_apps={m.app_name: m.package for m in maps},
        log=lambda msg: print(msg),
    )

    info = device.info()
    print(f"device : {info.model} / Android {info.os_version} / {info.screen_width}x{info.screen_height}")
    print(f"maps   : {', '.join(m.app_name for m in maps)}")
    print(f"task   : {args.task}")
    print()

    result = agent.run(args.task)
    for step in result.steps:
        print(f"  round {step.round} [{step.kind:<6}] {step.action:<10} {step.detail}")
    print()
    print(f"result : {result.summary()} -> {result.answer!r}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
