"""Offline proof that the pairing flow works end to end (loopback server)."""

from __future__ import annotations

import base64
import socket
import threading
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from phoneagent.devices.pairing import pair as pair_mod
from phoneagent.devices.pairing.auth import PairingAuth  # noqa: E402
from phoneagent.devices.pairing.peerinfo import (  # noqa: E402
    PEER_INFO_SIZE,
    pack_peer_info,
    unpack_peer_info,
)
from phoneagent.devices.pairing.rsa import (  # noqa: E402
    BLOB_SIZE,
    adb_public_key,
    generate_key,
    parse_public_key,
    self_signed_cert,
)
from phoneagent.devices.pairing.spake25519 import Spake2Peer  # noqa: E402
from phoneagent.devices.pairing.tls import wrap  # noqa: E402
from phoneagent.devices.pairing.transport import (  # noqa: E402
    MSG_PEER_INFO,
    MSG_SPAKE2,
    read_packet,
    write_packet,
)


def test_rsa_blob_matches_a_real_adbkey_pub():
    """Byte-for-byte check against the key this machine already paired with."""
    path = Path.home() / ".android" / "adbkey.pub"
    if not path.exists():
        pytest.skip("no ~/.android/adbkey.pub on this machine")
    line = path.read_text().strip()
    parsed = parse_public_key(line)

    blob = base64.b64decode(line.split()[0])
    assert len(blob) == BLOB_SIZE
    assert parsed["words"] == 64 and parsed["exponent"] == 65537
    n = parsed["n"]
    assert parsed["n0inv"] == (-pow(n, -1, 1 << 32)) % (1 << 32)
    assert parsed["rr"] == pow(1 << 2048, 2, n)


def test_generated_key_reproduces_its_own_blob():
    key = generate_key()
    line = adb_public_key(key, "phoneagent")
    parsed = parse_public_key(line)
    assert parsed["n"] == key.private_numbers().public_numbers.n
    assert len(base64.b64decode(line.split()[0])) == BLOB_SIZE
    assert line.split()[1] == "phoneagent"


def test_peer_info_record_is_fixed_size():
    blob = pack_peer_info("abc def")
    assert len(blob) == PEER_INFO_SIZE == 8192
    assert unpack_peer_info(blob) == (0, "abc def")


def _server(listener, code, seen):
    conn, _ = listener.accept()
    key = generate_key()
    cert, key_pem = self_signed_cert(key, "device")
    with wrap(conn, cert, key_pem, server_side=True) as channel:
        peer = Spake2Peer(code, is_client=False)
        write_packet(channel, MSG_SPAKE2, peer.start())
        kind, their = read_packet(channel)
        assert kind == MSG_SPAKE2
        auth = PairingAuth(peer.finish(their))
        kind, payload = read_packet(channel)
        assert kind == MSG_PEER_INFO
        seen.append(unpack_peer_info(auth.decrypt(payload)))
        write_packet(channel, MSG_PEER_INFO, auth.encrypt(pack_peer_info("DEVICE-GUID", 1)))


def test_full_pairing_against_a_loopback_server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    seen: list[tuple[int, str]] = []
    thread = threading.Thread(target=_server, args=(listener, "725139", seen), daemon=True)
    thread.start()

    result = pair_mod.pair("127.0.0.1", port, "725139")
    thread.join(timeout=5)
    listener.close()

    # the server saw our RSA public key, and we got its GUID back
    assert seen and seen[0][0] == 0
    assert seen[0][1].split()[1] == "phoneagent"
    assert (result.peer_type, result.peer_data) == (1, "DEVICE-GUID")
