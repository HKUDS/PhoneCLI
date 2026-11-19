import os


def test_a_dead_device_is_rebuilt_before_the_task_runs(monkeypatch):
    """adbd drops the connection; the next task must not just fail."""
    from phoneagent.server import CANCEL, PENDING, TASKS, AUTONOMOUS, ServerConfig, _run
    import threading

    class Device:
        def __init__(self, alive):
            self.alive = alive
            self.shells = []

        def shell(self, command):
            self.shells.append(command)
            if not self.alive:
                raise OSError("EOF occurred in violation of protocol")
            return "ok"

        def screenshot(self, path=None):
            return b""

        def dump_ui(self):
            return "<hierarchy/>"

        def start(self, **kwargs):
            return 3

        @property
        def display_id(self):
            return 3

        def is_ready(self):
            return True

    dead = Device(alive=False)
    alive = Device(alive=True)
    state = {"recovered": 0}

    def recover():
        state["recovered"] += 1
        return "rebuilt"

    devices = iter([dead, alive])
    config = ServerConfig(
        device_factory=lambda: next(devices),
        llm_factory=lambda: None,
        recover=recover,
        map_paths=[],
        memory_path="",
    )

    tid = "deadbeef"
    CANCEL[tid] = threading.Event()
    PENDING[tid] = {"question": "", "answer": None, "event": threading.Event()}
    AUTONOMOUS[tid] = False
    TASKS[tid] = {"state": "queued", "steps": [], "answer": "", "frame": ""}

    _run(tid, "打开设置", config)

    assert state["recovered"] == 1
    assert TASKS[tid]["state"] in ("done", "failed")


def test_the_map_list_skips_redacted_copies_and_keeps_the_newest(tmp_path):
    from phoneagent.server import ServerConfig, _scan_maps

    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "clock.yaml").write_text("app_name: clock\n")
    (first / "clock.public.yaml").write_text("app_name: clock\n")   # share copy
    (second / "clock.yaml").write_text("app_name: clock - newer\n")
    (second / "淘宝.yaml").write_text("app_name: taobao\n")

    config = ServerConfig(device_factory=lambda: None, llm_factory=lambda: None,
                          map_dirs=[str(first), str(second)])
    paths = _scan_maps(config)

    names = sorted(os.path.basename(p) for p in paths)
    assert names == ["clock.yaml", "淘宝.yaml"]
    kept = next(p for p in paths if p.endswith("clock.yaml"))
    assert str(second) in kept        # the later directory wins


def test_a_handler_crash_answers_with_the_reason():
    """It used to close the socket; the caller saw only RemoteDisconnected."""
    from phoneagent.server import Handler

    class Probe(Handler):
        def __init__(self):
            self.sent = []

        def _send(self, code, payload):
            self.sent.append((code, payload))

    probe = Probe()
    probe._guarded(lambda: 1 / 0)

    assert probe.sent and probe.sent[0][0] == 500
    assert "ZeroDivisionError" in probe.sent[0][1]["error"]


def test_the_guard_ignores_a_client_that_left():
    from phoneagent.server import Handler

    class Probe(Handler):
        def __init__(self):
            self.sent = []

        def _send(self, code, payload):
            self.sent.append((code, payload))

    def boom():
        raise BrokenPipeError()

    probe = Probe()
    probe._guarded(boom)
    assert probe.sent == []          # nothing to report to a dead client


def test_finished_tasks_do_not_accumulate_frames():
    """Each task keeps a ~200 KB screenshot; an app left open would grow."""
    from phoneagent import server

    server.TASKS.clear()
    for index in range(server.KEEP_TASKS + 5):
        task_id = f"t{index}"
        server.TASKS[task_id] = {"frame": "x" * 100}
        server._update(task_id, state="done", answer="ok")

    assert len(server.TASKS) == server.KEEP_TASKS
    assert all(task.get("frame") == "" for task in server.TASKS.values())
    assert "t0" not in server.TASKS          # the oldest went first


def test_requests_without_the_token_are_rejected():
    """Loopback is reachable by every app on the phone; the token is the gate."""
    from phoneagent import server

    class Server:
        class config:
            token = "secret"

    class Probe(server.Handler):
        def __init__(self, headers, path="/tasks"):
            self.headers = headers
            self.path = path
            self.server = Server()
            self.sent = []

        def _send(self, code, payload):
            self.sent.append((code, payload))

        def _get(self):                     # would answer 200
            self._send(200, {"ok": True})

    denied = Probe({"X-Agent-Token": "wrong"})
    denied._guarded(denied._get)
    assert denied.sent[0][0] == 401

    allowed = Probe({"X-Agent-Token": "secret"})
    allowed._guarded(allowed._get)
    assert allowed.sent[0][0] == 200

    query = Probe({}, path="/tasks?token=secret")
    query._guarded(query._get)
    assert query.sent[0][0] == 200


def test_partial_tasks_also_clear_frames_and_prune():
    """Partial is a terminal state and must drop its frame and prune TASKS."""
    from phoneagent import server

    server.TASKS.clear()
    for index in range(server.KEEP_TASKS + 3):
        task_id = f"p{index}"
        server.TASKS[task_id] = {"frame": "x" * 100}
        server._update(task_id, state="partial", answer="half done")

    assert len(server.TASKS) == server.KEEP_TASKS
    assert all(task.get("frame") == "" for task in server.TASKS.values())
    assert "p0" not in server.TASKS


def test_reused_device_restores_screenshot_and_does_not_resurrect_tasks():
    """keep_device=True reuses one device; _run must unwrap device.screenshot."""
    import threading
    from phoneagent import server
    from phoneagent.core.llm import MockLLM

    class SharedDevice:
        display_id = 3

        def __init__(self):
            self.captures = 0

        def current_app(self):
            return "com.android.settings"

        def screenshot(self, path=None):
            self.captures += 1
            return b"\x89PNG\r\n\x1a\nfakedata"

        def dump_ui(self):
            return '<hierarchy><node text="Settings"/></hierarchy>'

        def shell(self, command):
            return "ok"

        def press_back(self):
            return True

    device = SharedDevice()
    config = server.ServerConfig(
        device_factory=lambda: device,
        llm_factory=lambda: MockLLM(
            text_responses=["keep going"],
            vision_responses=["Action: finish(\"done\")"],
        ),
        keep_device=True,
        map_paths=[],
        memory_path="",
    )

    server.TASKS.clear()

    def run_task(tid: str) -> None:
        server.CANCEL[tid] = threading.Event()
        server.PENDING[tid] = {"question": "", "answer": None, "event": threading.Event()}
        server.AUTONOMOUS[tid] = False
        server.TASKS[tid] = {"state": "queued", "steps": [], "answer": "", "frame": ""}
        server._run(tid, "打开设置", config)

    run_task("task1")
    assert "screenshot" not in device.__dict__
    assert server.TASKS["task1"]["frame"] == ""

    # Simulate task1 having been pruned out of TASKS before task2 runs.
    server.TASKS.pop("task1", None)

    run_task("task2")
    assert "screenshot" not in device.__dict__
    assert "task1" not in server.TASKS  # old wrapper must not resurrect pruned tasks
    assert server.TASKS["task2"]["frame"] == ""

    # Also verify an instance-level screenshot callable is restored intact.
    custom = lambda path=None: b"\x89PNG\r\n\x1a\ncustom"
    device.screenshot = custom
    run_task("task3")
    assert device.screenshot is custom
    assert server.TASKS["task3"]["frame"] == ""
