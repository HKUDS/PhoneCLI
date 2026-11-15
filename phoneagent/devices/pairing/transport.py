"""Pairing packet framing.

``pairing_connection.cpp`` defines::

    struct PairingPacketHeader {
        uint8_t  version;      // kCurrentKeyHeaderVersion
        uint8_t  type;         // PairingPacket::Type
        uint32_t payload;      // network byte order, the payload length
    };

The struct is written as-is, so it is a 6-byte packed header.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO

#: kCurrentKeyHeaderVersion
TRANSPORT_VERSION = 1
#: PairingPacket::Type values from adb.proto.
MSG_SPAKE2 = 0
MSG_PEER_INFO = 1
#: kMaxPayloadSize = kMaxPeerInfoSize * 2
MAX_PAYLOAD = 8192 * 2

HEADER = struct.Struct(">BBI")  # version, type, payload length


@dataclass
class PeerInfo:
    """What each side proves it knows after the handshake."""

    type: int
    data: bytes


def write_packet(stream: BinaryIO, msg_type: int, payload: bytes) -> None:
    if not payload:
        raise ValueError("pairing payload must not be empty")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload too large: {len(payload)}")
    stream.write(HEADER.pack(TRANSPORT_VERSION, msg_type, len(payload)))
    stream.write(payload)
    flush = getattr(stream, "flush", None)  # sockets have no flush
    if flush is not None:
        flush()


def read_packet(stream: BinaryIO) -> tuple[int, bytes]:
    raw = _read_exactly(stream, HEADER.size)
    version, msg_type, length = HEADER.unpack(raw)
    if version != TRANSPORT_VERSION:
        raise ValueError(f"pairing header version mismatch: {version}")
    if length == 0 or length > MAX_PAYLOAD:
        raise ValueError(f"bad payload length: {length}")
    return msg_type, _read_exactly(stream, length)


def _read_exactly(stream: BinaryIO, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            raise EOFError("pairing connection closed")
        buf.extend(chunk)
    return bytes(buf)
