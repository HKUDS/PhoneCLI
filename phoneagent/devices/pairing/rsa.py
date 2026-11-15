"""ADB RSA identity: the key pair and the on-the-wire public key blob.

``adbkey.pub`` is base64 of a 524-byte structure (``adb/crypto/rsa_2048.h``)::

    uint32 len        // modulus size in 32-bit words (64 for RSA-2048)
    uint32 n0inv      // -1 / n mod 2^32
    uint8 n[256]      // modulus, little endian
    uint8 rr[256]     // R^2 mod n, little endian (R = 2^2048), for Montgomery
    uint32 exponent   // 65537

The layout was checked byte-for-byte against a real ``~/.android/adbkey.pub``.
"""

from __future__ import annotations

import base64
import struct

RSA_BITS = 2048
RSA_BYTES = RSA_BITS // 8
RSA_WORDS = RSA_BITS // 32
BLOB_SIZE = 4 + 4 + RSA_BYTES + RSA_BYTES + 4  # 524


def generate_key(bits: int = RSA_BITS):
    """A fresh RSA private key (needs the ``cryptography`` package)."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    if bits != RSA_BITS:
        raise ValueError("ADB only accepts 2048-bit keys")
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def public_key_blob(key) -> bytes:
    """The 524-byte structure that goes into ``adbkey.pub``."""
    numbers = key.private_numbers().public_numbers
    n, e = numbers.n, numbers.e
    if n.bit_length() != RSA_BITS:
        raise ValueError(f"modulus must be {RSA_BITS} bits")

    r = 1 << RSA_BITS
    n0inv = (-pow(n, -1, 1 << 32)) % (1 << 32)
    rr = pow(r, 2, n)

    return (
        struct.pack("<II", RSA_WORDS, n0inv)
        + n.to_bytes(RSA_BYTES, "little")
        + rr.to_bytes(RSA_BYTES, "little")
        + struct.pack("<I", e)
    )


def adb_public_key(key, name: str = "phoneagent") -> str:
    """The ``"<base64> <name>"`` line the device stores for a paired client."""
    clean = "".join(ch for ch in name if not ch.isspace()) or "phoneagent"
    return f"{base64.b64encode(public_key_blob(key)).decode()} {clean}"


def self_signed_cert(key, common_name: str = "phoneagent") -> tuple[bytes, bytes]:
    """A self-signed X.509 pair (cert, key) in PEM, for the pairing TLS channel."""
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import NameOID

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def parse_public_key(line: str) -> dict:
    """Decode a line in that format, mostly for tests and diagnostics."""
    blob = base64.b64decode(line.split()[0])
    if len(blob) != BLOB_SIZE:
        raise ValueError(f"unexpected blob size {len(blob)}")
    words, n0inv = struct.unpack("<II", blob[:8])
    n = int.from_bytes(blob[8 : 8 + RSA_BYTES], "little")
    rr = int.from_bytes(blob[8 + RSA_BYTES : 8 + 2 * RSA_BYTES], "little")
    exponent = struct.unpack("<I", blob[-4:])[0]
    return {"words": words, "n0inv": n0inv, "n": n, "rr": rr, "exponent": exponent}
