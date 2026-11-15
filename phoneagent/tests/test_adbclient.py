"""Framing tests for the ADB transport client (no device needed)."""

from __future__ import annotations


from phoneagent.devices.adbclient import CLSE, CNXN, HEADER, MAGIC_MASK, OKAY, WRTE, Frame, _pack


def test_header_layout_matches_adb():
    raw = _pack(CNXN, 0x01000001, 4096, b"host::\x00")
    command, arg0, arg1, length, crc, magic = HEADER.unpack(raw[: HEADER.size])
    assert command == CNXN and arg0 == 0x01000001 and arg1 == 4096
    assert length == len(b"host::\x00")
    assert magic == CNXN ^ 0xFFFFFFFF
    assert raw[HEADER.size :] == b"host::\x00"


def test_magic_is_command_xor_mask():
    for cmd in (CNXN, OKAY, WRTE, CLSE):
        _c, _a, _b, _len, _crc, magic = HEADER.unpack(_pack(cmd, 0, 0)[: HEADER.size])
        assert magic == cmd ^ MAGIC_MASK


def test_frame_eof_flag():
    assert Frame(CLSE, 0, 0, b"").is_eof
    assert not Frame(WRTE, 0, 0, b"x").is_eof


def test_a_failed_connect_leaves_no_socket_behind(monkeypatch):
    """Recovery retries in a loop; a socket per attempt runs the phone dry."""
    import socket as socket_mod

    from phoneagent.devices import adbclient

    closed = []

    class FakeSocket:
        def setsockopt(self, *a):
            pass

        def sendall(self, *a):
            raise OSError("connection reset")

        def settimeout(self, *a):
            pass

        def close(self):
            closed.append(True)

    monkeypatch.setattr(socket_mod, "create_connection",
                        lambda *a, **k: FakeSocket())

    client = adbclient.AdbClient(b"cert", b"key")
    monkeypatch.setattr(client, "_context", lambda: object())
    try:
        client.connect("127.0.0.1", 5555)
    except Exception:
        pass
    assert closed, "the socket must be closed when connect fails"
