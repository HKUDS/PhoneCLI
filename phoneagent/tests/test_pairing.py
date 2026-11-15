"""Offline tests for the pairing primitives (no device, no socket)."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("cryptography", reason="pairing tests need the cryptography package")

from phoneagent.devices.pairing import (
    MSG_SPAKE2,
    PairingAuth,
    Spake2Peer,
    read_packet,
    write_packet,
)
from phoneagent.devices.pairing.transport import MSG_PEER_INFO, TRANSPORT_VERSION


def test_spake2_both_sides_derive_the_same_key():
    client = Spake2Peer("725139", is_client=True)
    server = Spake2Peer("725139", is_client=False)

    msg_c = client.start()
    msg_s = server.start()
    assert msg_c and msg_s

    key_c = client.finish(msg_s)
    key_s = server.finish(msg_c)
    assert key_c == key_s
    assert len(key_c) >= 16


def test_spake2_with_a_wrong_code_does_not_agree():
    client = Spake2Peer("725139", is_client=True)
    server = Spake2Peer("000000", is_client=False)
    msg_c, msg_s = client.start(), server.start()
    key_c = client.finish(msg_s)
    # the server may raise or produce a different key; either way they differ
    try:
        key_s = server.finish(msg_c)
    except Exception:
        return
    assert key_c != key_s


def test_pairing_auth_roundtrip_between_two_sides():
    client = Spake2Peer("654321")
    server = Spake2Peer("654321", is_client=False)
    msg_c, msg_s = client.start(), server.start()
    key_c, key_s = client.finish(msg_s), server.finish(msg_c)

    alice, bob = PairingAuth(key_c), PairingAuth(key_s)
    blob = alice.encrypt(b"@@peer-info@@")
    assert bob.decrypt(blob) == b"@@peer-info@@"
    assert blob[:12] != blob[-16:]  # iv prefix, tag suffix


def test_transport_roundtrip():
    buf = io.BytesIO()
    write_packet(buf, MSG_SPAKE2, b"spake2-message")
    write_packet(buf, MSG_PEER_INFO, b"peer-info")
    buf.seek(0)

    kind, payload = read_packet(buf)
    assert kind == MSG_SPAKE2 and payload == b"spake2-message"
    kind, payload = read_packet(buf)
    assert kind == MSG_PEER_INFO and payload == b"peer-info"


def test_transport_rejects_a_bad_version():
    buf = io.BytesIO(bytes([TRANSPORT_VERSION + 1, MSG_SPAKE2]) + (4).to_bytes(4, "big") + b"x")
    with pytest.raises(ValueError):
        read_packet(buf)
