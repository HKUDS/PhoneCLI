"""Pair with a device's wireless-debugging service.

Flow, per ``pairing_connection.cpp``:

1. plain TCP to the pairing port (shown in the on-device dialog),
2. TLS with a self-signed certificate, neither side validating the other,
3. exchange ``SPAKE2_MSG`` packets; the pairing code is the SPAKE2 password,
4. derive an AES-128-GCM key from the SPAKE2 output,
5. exchange ``PEER_INFO`` packets (encrypted): our RSA public key, the device's
   public key or GUID.

After this the device has our public key stored and will accept an ADB
connection that authenticates with it.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from .auth import PairingAuth
from .peerinfo import pack_peer_info, unpack_peer_info
from .rsa import adb_public_key, generate_key, self_signed_cert
from .spake25519 import Spake2Peer
from .tls import wrap
from .transport import MSG_PEER_INFO, MSG_SPAKE2, read_packet, write_packet


class PairingError(RuntimeError):
    """The device rejected the pairing attempt."""


@dataclass
class PairingResult:
    peer_type: int
    peer_data: str
    key_material: bytes
    identity: object  # the RSA private key that is now paired


def pair(
    host: str,
    port: int,
    code: str,
    identity=None,
    name: str = "phoneagent",
    timeout: float = 15.0,
    sock=None,
) -> PairingResult:
    """Run the whole handshake; returns the device's peer info."""
    if not code.strip():
        raise PairingError("pairing code is empty")
    key = identity if identity is not None else generate_key()
    cert_pem, key_pem = self_signed_cert(key)

    own_socket = sock is None
    raw = sock if sock is not None else socket.create_connection((host, port), timeout)
    if sock is not None:
        raw.settimeout(timeout)
    try:
        channel = wrap(raw, cert_pem, key_pem, server_side=False)
    except Exception:
        if own_socket:
            raw.close()
        raise

    with channel:
        # 1. SPAKE2
        peer = Spake2Peer(code, is_client=True)
        write_packet(channel, MSG_SPAKE2, peer.start())
        kind, their_msg = read_packet(channel)
        if kind != MSG_SPAKE2:
            raise PairingError(f"expected a SPAKE2 packet, got type {kind}")
        key_material = peer.finish(their_msg)

        # 2. peer info, encrypted
        auth = PairingAuth(key_material)
        write_packet(channel, MSG_PEER_INFO, auth.encrypt(pack_peer_info(adb_public_key(key, name))))
        kind, payload = read_packet(channel)
        if kind != MSG_PEER_INFO:
            raise PairingError(f"expected a PEER_INFO packet, got type {kind}")
        peer_type, peer_data = unpack_peer_info(auth.decrypt(payload))

    return PairingResult(peer_type, peer_data, key_material, key)
