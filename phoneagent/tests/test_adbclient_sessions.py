"""Session tests for the ADB transport client, against a scripted fake adbd.

These exist because two bugs cost real time and neither was covered:

* `remote_id` was taken from ``arg1`` (our own id) instead of ``arg0`` (the
  device's), and
* adbd answers a late OKAY on a finished service with a stray CLSE, which the
  *next* command then read as its own close and returned empty output for.

Both are ordering bugs, so they only show up with more than one service on the
same connection -- exactly what this fake provides.
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from phoneagent.devices import adbclient as A

HEADER = A.HEADER


def _v2(text: bytes, packet_id: int = A.STDOUT) -> bytes:
    """One shell-v2 packet: id byte, length, payload."""
    return bytes([packet_id]) + struct.pack("<I", len(text)) + text


def _recv(sock: socket.socket, size: int) -> bytes:
    buf = b""
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise ConnectionError("fake adbd closed")
        buf += chunk
    return buf


def _read_frame(sock: socket.socket) -> A.Frame:
    command, arg0, arg1, length, _crc, _magic = HEADER.unpack(_recv(sock, HEADER.size))
    data = _recv(sock, length) if length else b""
    return A.Frame(command, arg0, arg1, data)


class FakeAdbd:
    """Accepts one OPEN per behaviour and plays its script back."""

    def __init__(self, behaviours) -> None:
        self.server, self.client = socket.socketpair()
        self._behaviours = list(behaviours)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def wait(self, timeout: float = 3.0) -> None:
        """Block until the scripted dialogue is over (the client may return first)."""
        self._thread.join(timeout)

    def _loop(self) -> None:
        try:
            for behaviour in self._behaviours:
                frame = _read_frame(self.server)
                while frame.command != A.OPEN:
                    frame = _read_frame(self.server)
                behaviour(self.server, frame.arg0)
        except (ConnectionError, OSError):
            return

    def close(self) -> None:
        for sock in (self.server, self.client):
            try:
                sock.close()
            except OSError:
                pass


@pytest.fixture
def client():
    made = []

    def build(behaviours):
        fake = FakeAdbd(behaviours)
        made.append(fake)
        instance = A.AdbClient(b"", b"")
        instance._sock = fake.client
        instance._fake = fake  # let a test wait for the scripted dialogue
        return instance

    yield build
    for fake in made:
        fake.close()


def test_sequential_commands_survive_a_stale_close(client):
    """A stray CLSE from the previous service must not end the next command."""

    def first(sock, local):
        sock.sendall(
            A._pack(A.OKAY, 100, local)
            + A._pack(A.WRTE, 100, local, _v2(b"one\n"))
            + A._pack(A.CLSE, 100, local)
        )

    def second(sock, local):
        # adbd's answer to the ack we sent after it had already closed service 100
        sock.sendall(A._pack(A.CLSE, 100, local))
        sock.sendall(
            A._pack(A.OKAY, 101, local)
            + A._pack(A.WRTE, 101, local, _v2(b"two\n"))
            + A._pack(A.CLSE, 101, local)
        )

    adb = client([first, second])
    assert str(adb.run("echo one")) == "one"
    assert str(adb.run("echo two")) == "two"


def test_exit_code_comes_from_the_payload_byte(client):
    def only(sock, local):
        payload = _v2(b"hi\n") + _v2(bytes([7]), A.EXIT)
        sock.sendall(
            A._pack(A.OKAY, 5, local)
            + A._pack(A.WRTE, 5, local, payload)
            + A._pack(A.CLSE, 5, local)
        )

    adb = client([only])
    result = adb.run("exit 7")
    assert result.stdout.strip() == "hi"
    # the packet length is 1; 1 is not the exit code
    assert result.exit_code == 7


def test_stdout_and_stderr_stay_apart(client):
    def only(sock, local):
        payload = _v2(b"out\n") + _v2(b"err\n", A.STDERR)
        sock.sendall(
            A._pack(A.OKAY, 9, local)
            + A._pack(A.WRTE, 9, local, payload)
            + A._pack(A.CLSE, 9, local)
        )

    adb = client([only])
    result = adb.run("x")
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_frames_for_another_service_are_ignored(client):
    """Traffic for a different local id must not be mistaken for ours."""

    def only(sock, local):
        sock.sendall(A._pack(A.OKAY, 42, 999))  # someone else's service
        sock.sendall(
            A._pack(A.OKAY, 42, local)
            + A._pack(A.WRTE, 42, local, _v2(b"mine\n"))
            + A._pack(A.CLSE, 42, local)
        )

    adb = client([only])
    assert str(adb.run("x")) == "mine"


def test_write_waits_for_the_device_to_name_the_service(client):
    """stdin cannot be routed before the OKAY: remote_id would still be 0."""
    seen = {}

    def only(sock, local):
        sock.sendall(A._pack(A.OKAY, 77, local))
        frame = _read_frame(sock)  # the WRTE carrying our stdin
        seen["command"] = frame.command
        seen["arg0"] = frame.arg0  # our local id
        seen["arg1"] = frame.arg1  # the device's id, must be 77
        seen["data"] = frame.data
        sock.sendall(A._pack(A.CLSE, 77, local))

    adb = client([only])
    stream = adb.open_service("shell,raw:cat")
    stream.write(b"hello\n")
    adb._fake.wait()

    assert seen == {
        "command": A.WRTE,
        "arg0": 1,
        "arg1": 77,
        "data": b"hello\n",
    }


def test_belongs_to_routes_by_service_id():
    remote = 100
    # our OKAY, someone else's
    assert A._belongs_to(A.Frame(A.OKAY, 0, 1, b""), 1, None)
    assert not A._belongs_to(A.Frame(A.OKAY, 0, 2, b""), 1, None)
    # before the device names the service, only a refusal (arg0 == 0) is ours
    assert not A._belongs_to(A.Frame(A.CLSE, 100, 1, b""), 1, None)
    assert A._belongs_to(A.Frame(A.CLSE, 0, 1, b""), 1, None)
    # once it has, match on the device's id
    assert A._belongs_to(A.Frame(A.WRTE, remote, 1, b""), 1, remote)
    assert not A._belongs_to(A.Frame(A.WRTE, 999, 1, b""), 1, remote)


def test_bad_magic_is_rejected():
    adb = A.AdbClient(b"", b"")
    good, bad = socket.socketpair()
    try:
        bad.sendall(struct.pack("<6I", A.OKAY, 0, 1, 0, 0, 0xDEADBEEF))
        adb._sock = good
        with pytest.raises(A.AdbError):
            adb._read()
    finally:
        good.close()
        bad.close()


def test_refused_service_ends_instead_of_hanging(client):
    """adbd refuses an OPEN with CLSE arg0 == 0; that must not block forever."""

    def refuse(sock, local):
        sock.sendall(A._pack(A.CLSE, 0, local))

    adb = client([refuse])
    result = adb.run("no such service")
    assert result.stdout == ""
    assert result.exit_code == -1


def test_truncated_v2_packet_is_dropped_not_crashed(client):
    def only(sock, local):
        # a packet header promising more bytes than the frame carries
        payload = bytes([A.STDOUT]) + struct.pack("<I", 99) + b"short"
        sock.sendall(
            A._pack(A.OKAY, 3, local)
            + A._pack(A.WRTE, 3, local, payload)
            + A._pack(A.CLSE, 3, local)
        )

    adb = client([only])
    assert str(adb.run("x")) == ""
