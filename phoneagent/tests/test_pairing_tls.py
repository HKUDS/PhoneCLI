"""Offline test: the pairing TLS channel works end to end (loopback)."""

from __future__ import annotations

import socket
import threading

import pytest

pytest.importorskip("cryptography", reason="needs the cryptography package")

from phoneagent.devices.pairing.tls import generate_identity, wrap  # noqa: E402


def _server(listener, cert, key, seen):
    conn, _ = listener.accept()
    with wrap(conn, cert, key, server_side=True) as ssl_conn:
        seen.append(ssl_conn.recv(64))
        ssl_conn.sendall(b"pong")


def test_pairing_tls_channel_roundtrip():
    cert, key = generate_identity("server")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    seen: list[bytes] = []
    thread = threading.Thread(target=_server, args=(listener, cert, key, seen), daemon=True)
    thread.start()

    client_cert, client_key = generate_identity("client")
    raw = socket.create_connection(("127.0.0.1", port), timeout=5)
    with wrap(raw, client_cert, client_key) as ssl_conn:
        ssl_conn.sendall(b"ping")
        assert ssl_conn.recv(64) == b"pong"

    thread.join(timeout=5)
    assert seen == [b"ping"]
    listener.close()
