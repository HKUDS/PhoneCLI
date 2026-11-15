

class _FakeShell:
    """A device that answers the two settings queries and records writes."""

    def __init__(self, services: str = "null", enabled: str = "0") -> None:
        self.services = services
        self.enabled = enabled
        self.writes: list = []

    def shell(self, command: str) -> str:
        if command.startswith("settings get secure enabled_accessibility_services"):
            return self.services
        if command.startswith("settings get secure accessibility_enabled"):
            return self.enabled
        self.writes.append(command)
        return ""


def test_a_reset_accessibility_service_is_turned_back_on():
    from phoneagent.ondevice import A11Y_SERVICE, ensure_accessibility

    device = _FakeShell(services="null", enabled="0")
    message = ensure_accessibility(device)

    assert "re-enabled" in message
    assert any(A11Y_SERVICE in write for write in device.writes)
    assert any("accessibility_enabled 1" in write for write in device.writes)


def test_other_accessibility_services_are_kept():
    from phoneagent.ondevice import A11Y_SERVICE, ensure_accessibility

    device = _FakeShell(services="com.example.other/.ItsService", enabled="1")
    ensure_accessibility(device)
    written = next(w for w in device.writes if "enabled_accessibility_services" in w)
    assert "com.example.other/.ItsService" in written and A11Y_SERVICE in written


def test_an_enabled_service_is_left_alone():
    from phoneagent.ondevice import A11Y_SERVICE, ensure_accessibility

    device = _FakeShell(services=A11Y_SERVICE, enabled="1")
    assert "already" in ensure_accessibility(device)
    assert device.writes == []
"""On-device entry points: the parts that make decisions without a phone."""


def test_recovery_says_whether_it_worked():
    """A string could not: success looked like "display=367 size=..."."""
    from phoneagent import ondevice

    result = ondevice.recover()
    assert result.ok is False
    assert "nothing to recover" in str(result)

    # str() keeps the old logging behaviour
    assert str(result) == result.message


def test_health_needs_the_server_too():
    """A live display with a dead server answers "connection closed" to everything."""
    from phoneagent import ondevice

    class Server:
        server_address = ("127.0.0.1", 1)        # nothing listens on port 1

    class Device:
        def display_alive(self):
            return True

    class Client:
        def shell(self, command):
            return "ok"

    ondevice._client = Client()
    ondevice._device = Device()
    ondevice._server = None
    assert ondevice.healthy() is False           # no server object at all

    ondevice._server = Server()
    assert ondevice.healthy() is False           # object exists, port is silent

    ondevice._client = None
    ondevice._device = None
    ondevice._server = None
