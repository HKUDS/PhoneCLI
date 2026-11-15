"""AES-128-GCM layer that protects the peer-info exchange.

From ``pairing_auth/aes_128_gcm.cpp``:

* the key is ``HKDF-SHA256(ikm=SPAKE2 output, info="adb pairing_auth
  aes-128-gcm key", L=16)`` -- *not* the head of the key material,
* the nonce is 12 zero bytes with a message counter copied into its first
  bytes, so nothing is transmitted per message (the ciphertext is exactly
  plaintext + 16-byte tag),
* the counter starts at zero for each direction and increments per message.
"""

from __future__ import annotations

KEY_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
HKDF_INFO = b"adb pairing_auth aes-128-gcm key"


def derive_key(key_material: bytes) -> bytes:
    """HKDF-SHA256 of the SPAKE2 output, as ADB does it."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    if not key_material:
        raise ValueError("empty key material")
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=None,
        info=HKDF_INFO,
    ).derive(key_material)


def _nonce(sequence: int) -> bytes:
    """12 zero bytes with the little-endian counter in the first 8."""
    return sequence.to_bytes(8, "little") + b"\x00" * (NONCE_SIZE - 8)


class PairingAuth:
    """Encrypt/decrypt the peer info, keeping a counter per direction."""

    def __init__(self, key_material: bytes) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._aes = AESGCM(derive_key(key_material))
        self._enc_sequence = 0
        self._dec_sequence = 0

    def encrypt(self, data: bytes) -> bytes:
        if not data:
            raise ValueError("nothing to encrypt")
        blob = self._aes.encrypt(_nonce(self._enc_sequence), data, None)
        self._enc_sequence += 1
        return blob

    def decrypt(self, blob: bytes) -> bytes:
        if len(blob) < TAG_SIZE:
            raise ValueError("ciphertext too short")
        plain = self._aes.decrypt(_nonce(self._dec_sequence), blob, None)
        self._dec_sequence += 1
        return plain
