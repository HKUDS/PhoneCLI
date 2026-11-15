"""TLS for the pairing connection.

``pairing_connection.cpp`` does ``TlsConnection::Create(role, cert, key)`` with
the peer's certificate supplied by the pairing framework itself, so neither
side validates the other's chain: trust comes from the SPAKE2 handshake that
runs *inside* this TLS channel.

Python's ``ssl`` needs the identity as files, hence the temp-file dance.
"""

from __future__ import annotations

import datetime
import os
import ssl
import tempfile
from typing import Optional


class TlsError(RuntimeError):
    """TLS setup or handshake failed."""


def generate_identity(common_name: str = "phoneagent") -> tuple[bytes, bytes]:
    """A self-signed RSA identity, PEM encoded: (certificate, private_key)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
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


def _write_identity(cert_pem: bytes, key_pem: bytes) -> tuple[str, str]:
    cert_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    key_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    cert_file.write(cert_pem)
    key_file.write(key_pem)
    cert_file.close()
    key_file.close()
    return cert_file.name, key_file.name


def context(
    cert_pem: bytes,
    key_pem: bytes,
    server_side: bool = False,
    cafile: Optional[str] = None,
) -> ssl.SSLContext:
    """A TLS context that presents ``cert_pem`` and never validates the peer."""
    cert_path, key_path = _write_identity(cert_pem, key_pem)
    try:
        if server_side:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        else:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        if cafile:
            ctx.load_verify_locations(cafile=cafile)
        return ctx
    finally:
        for path in (cert_path, key_path):
            try:
                os.unlink(path)
            except OSError:
                pass


def wrap(sock, cert_pem: bytes, key_pem: bytes, server_side: bool = False):
    """Wrap an existing socket, returning the SSL socket."""
    ctx = context(cert_pem, key_pem, server_side=server_side)
    try:
        return ctx.wrap_socket(sock, server_side=server_side)
    except ssl.SSLError as exc:
        raise TlsError(f"TLS handshake failed: {exc}") from exc
