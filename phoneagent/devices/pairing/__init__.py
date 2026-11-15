"""ADB wireless-debugging pairing (implemented from the AOSP reference)."""
from .auth import PairingAuth, derive_key
# The self-contained implementation is the one pair.py uses; exporting the
# wrapper around the third-party `spake2` package instead meant the tests
# exercised a different implementation than production -- and never ran at all
# when that package was absent.
from .spake25519 import CLIENT_NAME, SERVER_NAME, Spake2Peer
from .transport import HEADER, MSG_SPAKE2, TRANSPORT_VERSION, PeerInfo, read_packet, write_packet

__all__ = [
    "PairingAuth",
    "derive_key",
    "Spake2Peer",
    "CLIENT_NAME",
    "SERVER_NAME",
    "HEADER",
    "MSG_SPAKE2",
    "TRANSPORT_VERSION",
    "PeerInfo",
    "read_packet",
    "write_packet",
]
