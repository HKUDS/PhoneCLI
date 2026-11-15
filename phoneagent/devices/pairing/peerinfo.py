"""The PeerInfo record exchanged after the SPAKE2 handshake.

From ``pairing_connection.h``::

    struct PeerInfo {
        uint8_t type;
        uint8_t data[kMaxPeerInfoSize - 1];
    } __attribute__((packed));

with ``kMaxPeerInfoSize == 8192``, so the record is always exactly 8192 bytes
and ``data`` is a NUL-terminated string.
"""

from __future__ import annotations

PEER_INFO_SIZE = 8192
TYPE_RSA_PUBLIC_KEY = 0
TYPE_DEVICE_GUID = 1


def pack_peer_info(data: str, info_type: int = TYPE_RSA_PUBLIC_KEY) -> bytes:
    encoded = data.encode()
    if len(encoded) > PEER_INFO_SIZE - 1:
        raise ValueError("peer info does not fit in the record")
    buf = bytearray(PEER_INFO_SIZE)
    buf[0] = info_type
    buf[1 : 1 + len(encoded)] = encoded
    return bytes(buf)


def unpack_peer_info(blob: bytes) -> tuple[int, str]:
    if len(blob) != PEER_INFO_SIZE:
        raise ValueError(f"peer info must be {PEER_INFO_SIZE} bytes, got {len(blob)}")
    return blob[0], blob[1:].split(b"\x00", 1)[0].decode()
