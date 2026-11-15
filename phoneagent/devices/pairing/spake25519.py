"""BoringSSL's SPAKE2 variant, as used by ADB.

Not the IRTF draft: BoringSSL predates it and differs in both the masking and
the transcript. Ported from ``crypto/curve25519/spake25519.cc``
(docs/reference/aosp-pairing/spake25519.cc):

* ``x = 8 * (random 512-bit value reduced mod L)``
* ``w = SHA-512(password) reduced mod L``, then L, 2L and 4L are conditionally
  added so that the low three bits are clear (their "copy-paste error" fix)
* our message is ``x*B + w*M`` (client) or ``x*B + w*N`` (server)
* the peer's message is unmasked with the other point, multiplied by ``x``
* the key is ``SHA-512(transcript)[:32]`` where every field of the transcript
  is prefixed with its length as a little-endian 64-bit integer
"""

from __future__ import annotations

import hashlib
import os

from . import ed25519 as E

CLIENT_NAME = b"adb pair client\x00"
SERVER_NAME = b"adb pair server\x00"

#: Encoded points from the comments in spake25519.cc.
M_POINT = E.decode(bytes.fromhex("5ada7e4bf6ddd9adb6626d32131c6b5c51a1e347a3478f53cfcf441b88eed12e"))
N_POINT = E.decode(bytes.fromhex("10e3df0ae37d8e7a99b5fe74b44672103dbddcbd06af680d71329a11693bc778"))

KEY_SIZE = 32


class Spake2Error(RuntimeError):
    """Handshake failed (usually a wrong pairing code)."""


def _password_scalar(code: str) -> tuple[int, bytes]:
    """The mask scalar and the full SHA-512 of the code (for the transcript)."""
    digest = hashlib.sha512(code.encode()).digest()
    w = int.from_bytes(digest, "little") % E.L
    # clear the low three bits by adding multiples of the group order
    if w & 1:
        w += E.L
    if w & 2:
        w += 2 * E.L
    if w & 4:
        w += 4 * E.L
    return w, digest


class Spake2Peer:
    """One side of the exchange; ``is_client`` picks M vs N."""

    def __init__(self, code: str, is_client: bool = True) -> None:
        if not code:
            raise Spake2Error("pairing code is empty")
        self.is_client = is_client
        self._w, self._password_hash = _password_scalar(code)
        self._x = (int.from_bytes(os.urandom(64), "little") % E.L) * 8
        self._msg: bytes | None = None
        self._key: bytes | None = None

    def start(self) -> bytes:
        mask = E.multiply(self._w, M_POINT if self.is_client else N_POINT)
        own = E.add(E.multiply(self._x, E.decode(E.BASE_ENCODED)), mask)
        self._msg = E.encode(own)
        return self._msg

    def finish(self, their_msg: bytes) -> bytes:
        if self._msg is None:
            raise Spake2Error("start() must be called first")
        try:
            their_point = E.decode(bytes(their_msg))
        except ValueError as exc:
            raise Spake2Error(f"peer sent a point off the curve: {exc}") from exc

        peer_mask = E.multiply(self._w, N_POINT if self.is_client else M_POINT)
        unmasked = E.add(their_point, E.negate(peer_mask))
        shared = E.encode(E.multiply(self._x, unmasked))

        sha = hashlib.sha512()

        def field(data: bytes) -> None:
            sha.update(len(data).to_bytes(8, "little"))
            sha.update(data)

        if self.is_client:
            field(CLIENT_NAME)
            field(SERVER_NAME)
            field(self._msg)
            field(bytes(their_msg))
        else:
            # same byte sequence as the client builds: their name (the client's)
            # first, then ours, then their message, then ours
            field(CLIENT_NAME)
            field(SERVER_NAME)
            field(bytes(their_msg))
            field(self._msg)
        field(shared)
        field(self._password_hash)

        self._key = sha.digest()[:KEY_SIZE]
        return self._key

    @property
    def key(self) -> bytes:
        if self._key is None:
            raise Spake2Error("handshake is not finished")
        return self._key
