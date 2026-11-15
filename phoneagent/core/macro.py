"""Replay abstract macro steps against any :class:`PhoneDevice`.

The macro format is the one stored in ``screen_macros`` and synthesised by
``AppMap.build_operations``: a list of small dicts, no shell strings, no
platform assumptions::

    {"action": "force_stop", "package": "com.android.settings", "wait": 0.5}
    {"action": "launch",     "package": "com.android.settings", "wait": 3.0}
    {"action": "tap",        "x": 540, "y": 960, "wait": 1.0}

iOS maps use ``bundle_id`` where Android maps use ``package``; both keys are
accepted so a single executor can replay either.

This is the piece that turns "same algorithm, different transport" into
reality: the executor only ever calls protocol methods.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from ..devices.base import DeviceError, PhoneDevice, UnicodeTextUnsupported
from .ui import find_label

#: Actions the executor knows how to perform.
SUPPORTED_ACTIONS = frozenset(
    {
        "force_stop",
        "launch",
        "tap",
        "long_press",
        "swipe",
        "type",
        "text",
        "input_text",
        "back",
        "home",
        "key",
        "wait",
        "sleep",
        "screenshot",
    }
)


class MacroError(RuntimeError):
    """A macro step is malformed or not supported."""


@dataclass
class StepResult:
    index: int
    action: str
    ok: bool
    detail: str = ""


@dataclass
class MacroResult:
    ok: bool
    executed: int
    steps: list[StepResult] = field(default_factory=list)
    error: str = ""

    def summary(self) -> str:
        total = len(self.steps)
        if self.ok:
            return f"macro ok ({self.executed}/{total} steps)"
        failed = self.steps[-1] if self.steps else None
        where = f" at step {failed.index} ({failed.action})" if failed else ""
        return f"macro failed{where}: {self.error}"


def _package_of(step: dict) -> str:
    return step.get("package") or step.get("bundle_id") or ""


def validate_macro(macro: Iterable[dict]) -> list[str]:
    """Static check of a macro; returns a list of human-readable problems."""
    problems: list[str] = []
    for index, step in enumerate(macro):
        action = step.get("action", "")
        if action not in SUPPORTED_ACTIONS:
            problems.append(f"step {index}: unknown action {action!r}")
            continue
        if action in ("force_stop", "launch") and not _package_of(step):
            problems.append(f"step {index}: {action} without package/bundle_id")
        if action in ("tap", "long_press") and ("x" not in step or "y" not in step):
            problems.append(f"step {index}: {action} without x/y")
        if action == "swipe" and not all(k in step for k in ("x1", "y1", "x2", "y2")):
            problems.append(f"step {index}: swipe without x1/y1/x2/y2")
        if action in ("type", "text", "input_text") and not (
            "text" in step or "value" in step
        ):
            problems.append(f"step {index}: {action} without text")
    return problems


class MacroExecutor:
    """Execute macro steps sequentially and stop at the first failure.

    Parameters
    ----------
    device:
        Any object implementing :class:`PhoneDevice`.
    sleep:
        Injectable sleep (tests pass a no-op to stay fast).
    wait_scale:
        Multiplier on every ``wait`` in the macro; 0 disables waiting.
    dry_run:
        Validate and record instead of touching the device.
    source_size:
        ``(width, height)`` the macro coordinates were compiled against. When
        given, every coordinate is scaled to the device's current screen so a
        map built on one resolution still replays on another.
    """

    def __init__(
        self,
        device: PhoneDevice,
        sleep: Callable[[float], None] = time.sleep,
        wait_scale: float = 1.0,
        dry_run: bool = False,
        source_size: Optional[tuple[int, int]] = None,
        resolve_text: bool = True,
        launch_intent: str = "",
        verify_screen: Optional[Callable[[str], bool]] = None,
        strict_screen: bool = False,
        human: bool = False,
    ) -> None:
        self.device = device
        self.sleep = sleep
        self.wait_scale = wait_scale
        self.dry_run = dry_run
        self.source_size = source_size
        # When a tap step carries `text`, look the label up in a live dump
        # first and only fall back to the compiled coordinates.
        self.resolve_text = resolve_text
        self.launch_intent = launch_intent
        #: Called with the screen id a tap was recorded on; returning False
        #: aborts the replay instead of pressing a coordinate that now belongs
        #: to a different screen.
        self.verify_screen = verify_screen
        #: With strict_screen the replay stops on a disagreement; without it the
        #: disagreement is recorded and the tap still fires. Which one is right
        #: depends on how stable screen identification is, so it is measurable
        #: rather than assumed.
        self.strict_screen = strict_screen
        #: Tap like a hand, not like a script: a point inside the element rather
        #: than its exact centre, and waits that vary. A commercial app that
        #: watches for automation sees the opposite -- the same pixel, the same
        #: rhythm, every single run. Off by default so tests and benchmarks stay
        #: reproducible; the agent turns it on.
        self.human = human
        self._random = random.Random()

    # -- public ----------------------------------------------------------

    def run(self, macro: Optional[list[dict]]) -> MacroResult:
        steps: list[StepResult] = []
        if not macro:
            return MacroResult(ok=True, executed=0, steps=steps)

        for index, step in enumerate(macro):
            action = str(step.get("action", ""))
            try:
                expected = step.get("screen")
                if expected and self.verify_screen is not None and action == "tap":
                    if not self.verify_screen(expected):
                        # Warn, do not abort. Measured on the clock: the screen
                        # identified during a crawl and the one identified a
                        # second later at replay disagree often enough that
                        # aborting threw away 9 of 10 otherwise valid macros.
                        # The check is kept as a signal until identification is
                        # stable enough to act on.
                        if self.strict_screen:
                            raise MacroError(
                                f"screen changed before step {index}: expected {expected}"
                            )
                        self.screen_warnings = getattr(self, "screen_warnings", [])
                        self.screen_warnings.append((index, expected))
                detail = self._apply(step)
            except MacroError as exc:
                steps.append(StepResult(index, action, False, str(exc)))
                return MacroResult(False, index, steps, str(exc))
            except UnicodeTextUnsupported as exc:
                steps.append(StepResult(index, action, False, str(exc)))
                return MacroResult(False, index, steps, str(exc))
            except (DeviceError, OSError, RuntimeError) as exc:
                steps.append(StepResult(index, action, False, str(exc)))
                return MacroResult(False, index, steps, str(exc))
            steps.append(StepResult(index, action, True, detail))
            self._wait_after(step)

        return MacroResult(True, len(steps), steps)

    # -- internals -------------------------------------------------------

    def _lookup(self, label: str):
        """Find a label in a fresh dump; None means "use the coordinates"."""
        try:
            xml = self.device.dump_ui()
        except DeviceError:
            return None
        return find_label(xml, str(label))

    def _scale(self, x: int, y: int) -> tuple[int, int]:
        """Map coordinates from the compile-time screen to the live screen."""
        if not self.source_size:
            return x, y
        src_w, src_h = self.source_size
        if src_w <= 0 or src_h <= 0:
            return x, y
        info = self.device.info()
        if (info.screen_width, info.screen_height) == (src_w, src_h):
            return x, y
        return (
            round(x * info.screen_width / src_w),
            round(y * info.screen_height / src_h),
        )

    def _human_point(self, node, cx: int, cy: int) -> tuple:
        """A point somewhere inside the element, not its exact centre."""
        bounds = getattr(node, "bounds", None)
        if not bounds or len(bounds) != 4:
            return self._jitter(cx, cy, 10)
        left, top, right, bottom = bounds
        width, height = right - left, bottom - top
        if width < 8 or height < 8:
            return self._jitter(cx, cy, 3)
        # keep away from the edges: a tap on a border is not what a finger does
        x = self._rand_int(left + max(2, width // 6), right - max(2, width // 6))
        y = self._rand_int(top + max(2, height // 6), bottom - max(2, height // 6))
        return x, y

    def _jitter(self, x: int, y: int, radius: int) -> tuple:
        return (self._rand_int(x - radius, x + radius),
                self._rand_int(y - radius, y + radius))

    def _rand_int(self, low: int, high: int) -> int:
        if high <= low:
            return low
        return self._random.randint(low, high)

    def _wait_after(self, step: dict) -> None:
        delay = float(step.get("wait", 0) or 0) * self.wait_scale
        if delay > 0:
            if self.human:
                delay *= self._random.uniform(0.8, 1.35)
            self.sleep(delay)

    def _wait_for_app(self, device, package: str, budget: float = 15.0) -> bool:
        """Wait until ``package`` is really on screen, before any tap.

        A macro's steps are coordinates measured on a drawn screen. Launching
        an app and waiting a fixed three seconds is not the same thing: measured
        on 小红书, the app took longer than that to draw, the next step's tap
        landed on the status bar of the launcher, and the notification shade came
        down on top of the task -- three times in one run.
        """
        current = getattr(device, "current_app", None)
        if current is None:
            return False
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline:
            try:
                if current() == package:
                    return True
            except Exception:  # noqa: BLE001 - keep waiting; the app is starting
                pass
            time.sleep(0.4)
        return False

    def _apply(self, step: dict) -> str:
        action = str(step.get("action", ""))
        device = self.device

        if action in ("wait", "sleep"):
            seconds = float(step.get("duration", step.get("seconds", step.get("wait", 0))) or 0)
            if not self.dry_run and seconds > 0:
                self.sleep(seconds * self.wait_scale)
            return f"waited {seconds}s"

        if self.dry_run:
            return "dry-run"

        if action == "force_stop":
            package = _package_of(step)
            return self._expect(device.force_stop(package), f"force_stop {package}")

        if action == "launch":
            package = _package_of(step)
            if self.launch_intent:
                landed = False
                try:
                    # Short wait: the fallback below is cheap, and sitting out
                    # the full timeout on a hijacked intent is wasted time.
                    landed = bool(device.launch(  # type: ignore[call-arg]
                        package, intent=self.launch_intent, wait=2.0))
                except TypeError:  # backend without intent support
                    landed = False
                if landed:
                    return f"launch via {self.launch_intent}"
                # The action reached some other app. HyperOS answers
                # android.settings.SETTINGS with com.android.phone's mobile
                # network page, so a map recorded with the generic action
                # silently replays into the wrong app -- every later tap is
                # then a coordinate in a screen it was never measured on.
                started = self._expect(
                    device.launch(package),
                    f"launch {package} (intent {self.launch_intent} landed elsewhere)",
                )
                self._wait_for_app(device, package)
                return started
            started = self._expect(device.launch(package), f"launch {package}")
            self._wait_for_app(device, package)
            return started

        if action == "tap":
            label = step.get("text")
            if self.resolve_text and label:
                # Accessibility first: ACTION_CLICK targets the node itself, so
                # it does not depend on where the row happens to sit, and it
                # reports whether the action was accepted -- `tap()` always
                # returns true, even when nothing was there.
                click = getattr(device, "click_text", None)
                if click is not None:
                    try:
                        if click(label):
                            return f"click_text {label!r} (accessibility)"
                    except DeviceError:
                        pass
                node = self._lookup(label)
                if node is not None:
                    cx, cy = node.center
                    if self.human:
                        cx, cy = self._human_point(node, cx, cy)
                    return self._expect(device.tap(cx, cy), f"tap_text {label!r} @ {cx},{cy}")
            x, y = self._scale(int(step["x"]), int(step["y"]))
            if self.human:
                x, y = self._jitter(x, y, 6)
            return self._expect(device.tap(x, y), f"tap {x},{y}")

        if action == "long_press":
            x, y = self._scale(int(step["x"]), int(step["y"]))
            duration = int(step.get("duration_ms", step.get("duration", 3000)))
            return self._expect(device.long_press(x, y, duration), f"long_press {x},{y}")

        if action == "swipe":
            duration = int(step.get("duration", 400))
            x1, y1 = self._scale(int(step["x1"]), int(step["y1"]))
            x2, y2 = self._scale(int(step["x2"]), int(step["y2"]))
            return self._expect(
                device.swipe(x1, y1, x2, y2, duration), f"swipe {x1},{y1}->{x2},{y2}"
            )

        if action in ("type", "text", "input_text"):
            text = step.get("text", step.get("value", ""))
            return self._expect(device.input_text(str(text)), f"type {text!r}")

        if action == "back":
            return self._expect(device.press_back(), "back")

        if action == "home":
            return self._expect(device.press_home(), "home")

        if action == "key":
            name = str(step.get("key", step.get("name", "")))
            return self._expect(device.press_key(name), f"key {name}")

        if action == "screenshot":
            path = step.get("path")
            device.screenshot(path)
            return f"screenshot -> {path or 'bytes'}"

        raise MacroError(f"unknown action {action!r}")

    @staticmethod
    def _expect(ok: bool, what: str) -> str:
        if not ok:
            raise DeviceError(f"{what} returned false")
        return what
