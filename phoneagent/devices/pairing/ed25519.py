"""Minimal Ed25519 group arithmetic (extended coordinates).

Only what ADB pairing needs: decode, encode, add, subtract, scalar multiply.
Written from RFC 8032 rather than pulled from a library because the pairing
code must be identical on every platform the APK ships to.
"""

from __future__ import annotations

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)

# Base point: encoded y = 4/5 with the sign bit of x set as usual.
BASE_ENCODED = bytes.fromhex("5866666666666666666666666666666666666666666666666666666666666666")


def inv(x: int) -> int:
    return pow(x, P - 2, P)


def decode(data: bytes) -> tuple[int, int, int, int]:
    """32 bytes -> extended point (X, Y, Z, T). Raises ValueError if off-curve."""
    if len(data) != 32:
        raise ValueError("an ed25519 point is 32 bytes")
    y = int.from_bytes(data, "little")
    sign = (y >> 255) & 1
    y &= (1 << 255) - 1
    if y >= P:
        raise ValueError("y out of range")
    y2 = y * y % P
    x2 = (y2 - 1) * inv(D * y2 + 1) % P
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * SQRT_M1 % P
    if (x * x - x2) % P:
        raise ValueError("point is not on the curve")
    if (x & 1) != sign:
        x = P - x
    return (x, y, 1, x * y % P)


def encode(point: tuple[int, int, int, int]) -> bytes:
    """Extended point -> 32 bytes."""
    x, y, z, _ = point
    zi = inv(z)
    x, y = x * zi % P, y * zi % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = (y1 - x1) * (y2 - x2) % P
    b = (y1 + x1) * (y2 + x2) % P
    c = t1 * 2 * D * t2 % P
    d = z1 * 2 * z2 % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def double(point):
    x, y, z, _ = point
    a = x * x % P
    b = y * y % P
    c = 2 * z * z % P
    h = a + b
    e = h - (x + y) * (x + y) % P
    g = a - b
    f = c + g
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def negate(point):
    x, y, z, t = point
    return ((P - x) % P, y, z, (P - t) % P)


def multiply(scalar: int, point) -> tuple[int, int, int, int]:
    """double-and-add; the scalar is used as-is (callers reduce it first)."""
    if scalar == 0:
        return (0, 1, 1, 0)
    if scalar < 0:
        return multiply(-scalar, negate(point))
    result = (0, 1, 1, 0)
    addend = point
    while scalar:
        if scalar & 1:
            result = add(result, addend)
        addend = double(addend)
        scalar >>= 1
    return result


def multiply_base(scalar: int):
    return multiply(scalar, decode(BASE_ENCODED))


def sc_reduce(value: int) -> int:
    """Reduce a 512-bit value modulo the group order."""
    return value % L


def scalar_from_hash(digest: bytes) -> int:
    return sc_reduce(int.from_bytes(digest, "little"))
