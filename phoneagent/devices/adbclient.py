"""Minimal ADB transport client: just enough to run a shell command.

Used after pairing: the device already trusts our RSA key, so a TLS connection
to its wireless-debugging port authenticates us without an AUTH exchange.

The handshake is the subtle part, and it is *not* "connect and speak TLS".
adbd always starts in plaintext and upgrades in band:

1. host sends ``CNXN`` in the clear (this frame is the only one that is ever
   sent twice-ish: it announces us before the upgrade)
2. adbd answers ``STLS`` (``arg0`` = ``A_STLS_VERSION``)
3. host echoes ``STLS``, and both sides switch to TLS 1.3 on the same socket
4. the *device* speaks first after the handshake: an ``CNXN`` banner
5. host sends ``OPEN`` for ``shell,v2,raw:<command>``

Sending a second ``CNXN`` after step 3 is the classic mistake: adbd reads every
``A_CNXN`` as "the peer wants to begin TLS" and answers with another ``STLS``,
which can never be honoured because the socket is already inside TLS. The
connection then just hangs.

Frame layout (all little-endian), from ``adb/protocol.h``::

    uint32 command, arg0, arg1, data_length, data_crc32, magic

``magic`` is the command XOR 0xffffffff.
"""

from __future__ import annotations

import socket
import ssl
import threading
import struct
import tempfile
import zlib
from dataclasses import dataclass
from typing import Optional

A_VERSION = 0x01000001
A_MAXDATA = 1 << 20
A_STLS_VERSION = 0x01000000

CNXN = 0x4E584E43
AUTH = 0x48545541
OPEN = 0x4E45504F
OKAY = 0x59414B4F
CLSE = 0x45534C43
WRTE = 0x45545257
STLS = 0x534C5453

#: What the real client advertises; adbd echoes the intersection back.
HOST_FEATURES = (
    "shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,abb_exec,"
    "remount_shell,track_app,sendrecv_v2,deviceside_enx,sendrecv_v2_brotli,"
    "sendrecv_v2_lz4,sendrecv_v2_zstd,openscreen_mdns"
)

HEADER = struct.Struct("<6I")
MAGIC_MASK = 0xFFFFFFFF

#: shell v2 packet ids carried inside a WRTE payload.
STDOUT, STDERR, EXIT = 1, 2, 3


class AdbError(RuntimeError):
    """The device refused the connection or the service."""


@dataclass
class Frame:
    command: int
    arg0: int
    arg1: int
    data: bytes

    @property
    def is_eof(self) -> bool:
        return self.command == CLSE


def _pack(command: int, arg0: int, arg1: int, data: bytes = b"") -> bytes:
    crc = zlib.crc32(data) & 0xFFFFFFFF
    return HEADER.pack(command, arg0, arg1, len(data), crc, command ^ MAGIC_MASK) + data


def _belongs_to(frame: Frame, local_id: int, remote_id: Optional[int]) -> bool:
    """Is this frame for the service we just opened?

    adbd answers an OKAY that arrives for an already-finished service with a
    stray CLSE, so frames have to be dispatched by id. Without this the next
    command reads a previous service's close and returns empty output.
    """
    if frame.command == OKAY:
        return not frame.arg1 or frame.arg1 == local_id
    if remote_id is None:
        # nothing has identified our service yet, so only a refusal -- CLSE
        # with arg0 == 0 -- can be ours; anything else predates the OPEN
        return frame.arg0 == 0
    return frame.arg0 == remote_id


@dataclass
class Result:
    """Outcome of one shell command."""

    stdout: str
    stderr: str
    exit_code: int

    def __str__(self) -> str:
        return self.stdout.strip()


#: WRTE payloads larger than this are split; adbd's socket buffers are finite.
WRITE_CHUNK = 64 * 1024


class ShellStream:
    """A long-lived adb service: bytes in on stdin, bytes out on stdout.

    Used for the virtual-display helper, which stays alive and talks a small
    line protocol, so a one-shot ``shell()`` call is not enough.
    """

    def __init__(self, client: "AdbClient", service: str, local_id: int) -> None:
        self._client = client
        self.local_id = local_id
        self.remote_id = 0
        self._buf = bytearray()
        self.eof = False
        client._send(OPEN, local_id, 0, service.encode() + b"\x00")

    # -- reading ----------------------------------------------------------

    def _pump(self) -> bool:
        """Read one frame. Returns False once the device closes the service."""
        while True:
            frame = self._client._read()
            if not _belongs_to(frame, self.local_id, self.remote_id or None):
                continue
            break
        if frame.command == OKAY:
            self.remote_id = frame.arg0
            return True
        if frame.command == WRTE:
            self.remote_id = frame.arg0
            self._buf.extend(frame.data)
            self._send(OKAY, self.local_id, self.remote_id)
            return True
        if frame.command == CLSE:
            self.eof = True
            return False
        raise AdbError(f"unexpected frame 0x{frame.command:08x} on service")

    def read(self, timeout: Optional[float] = None) -> bytes:
        """Return whatever stdout is available, or b"" at end of stream."""
        if self._buf:
            data = bytes(self._buf)
            self._buf.clear()
            return data
        if self.eof:
            return b""
        self._set_timeout(timeout)
        try:
            while not self._buf and not self.eof:
                if not self._pump():
                    break
                if self._buf:
                    break
        except (socket.timeout, TimeoutError):
            return b""
        finally:
            if timeout is not None:
                self._set_timeout(self._client._timeout)
        data = bytes(self._buf)
        self._buf.clear()
        return data

    def readline(self, timeout: Optional[float] = 10.0) -> bytes:
        """Read one newline-terminated line, or b"" on timeout/EOF."""
        if self.eof and b"\n" not in self._buf:
            return b""
        self._set_timeout(timeout)
        try:
            while b"\n" not in self._buf and not self.eof:
                if not self._pump():
                    break
        except (socket.timeout, TimeoutError):
            pass
        finally:
            self._set_timeout(self._client._timeout)
        if b"\n" not in self._buf:
            return b""
        line, _, rest = bytes(self._buf).partition(b"\n")
        self._buf = bytearray(rest)
        return line

    def read_all(self, timeout: Optional[float] = None) -> bytes:
        """Drain the service until the device closes it."""
        if timeout is not None:
            self._set_timeout(timeout)
        try:
            while not self.eof:
                if not self._pump():
                    break
        except (socket.timeout, TimeoutError):
            pass
        finally:
            if timeout is not None:
                self._set_timeout(self._client._timeout)
        data = bytes(self._buf)
        self._buf.clear()
        return data

    # -- writing ----------------------------------------------------------

    def write(self, data: bytes) -> int:
        """Feed stdin. Splits large payloads into several WRTE frames."""
        if not self.remote_id:
            # stdin cannot be routed before the device has named the service
            self._await_open()
        for at in range(0, len(data), WRITE_CHUNK):
            self._send(WRTE, self.local_id, self.remote_id, data[at:at + WRITE_CHUNK])
        return len(data)

    def _await_open(self, timeout: Optional[float] = None) -> None:
        """Block until the device acknowledges the OPEN and gives us an id."""
        self._set_timeout(timeout)
        try:
            while not self.remote_id and not self.eof:
                if not self._pump():
                    break
        except (socket.timeout, TimeoutError):
            pass
        finally:
            self._set_timeout(self._client._timeout if self._client._sock else None)
        if not self.remote_id:
            raise AdbError("the device did not accept the service")

    def writeline(self, line: str) -> int:
        return self.write(line.encode() + b"\n")

    # -- lifecycle --------------------------------------------------------

    def close(self) -> None:
        if self.eof:
            return
        try:
            self._send(CLSE, self.local_id, self.remote_id)
        except OSError:
            pass
        self.eof = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- plumbing ---------------------------------------------------------

    def _send(self, command: int, arg0: int, arg1: int, data: bytes = b"") -> None:
        self._client._send(command, arg0, arg1, data)

    def _set_timeout(self, timeout: Optional[float]) -> None:
        if self._client._sock is not None:
            self._client._sock.settimeout(timeout)


class AdbClient:
    """One TLS connection to adbd."""

    def __init__(self, cert_pem: bytes, key_pem: bytes, timeout: float = 15.0) -> None:
        self._cert = cert_pem
        self._key = key_pem
        self._timeout = timeout
        self._sock = None
        self.banner = ""
        self._next_id = 1
        self._endpoint: tuple = ()
        self._io_lock = threading.Lock()
        # One command at a time: the frames of two commands interleaved on the
        # same socket is what produced "frame magic mismatch" while a task and
        # the debug endpoint ran together.
        self._cmd_lock = threading.RLock()

    # -- connection -------------------------------------------------------

    def connect(self, host: str, port: int) -> str:
        """Plain CNXN -> STLS -> TLS 1.3 -> device banner. Returns the banner."""
        # Remembered so a dead connection can be rebuilt: the TLS session to
        # adbd dies when the phone sleeps, when wireless debugging cycles, and
        # after a failed transfer, and every later command then fails with
        # ssl.SSLEOFError ("EOF occurred in violation of protocol") -- measured
        # on a phone where the next task failed at round 0 with that string.
        self._endpoint = (host, port)
        ctx = self._context()
        raw = socket.create_connection((host, port), self._timeout)
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        # Every failure below must leave no socket behind: recovery retries the
        # connection in a loop, and a leaked socket per attempt is how the phone
        # runs out of descriptors after an hour of reconnects.
        try:
            raw.sendall(_pack(CNXN, A_VERSION, A_MAXDATA,
                              f"host::features={HOST_FEATURES}\x00".encode()))
            hello = self._read_frame_from(raw)
            if hello.command == CNXN:
                # adbd accepted us in plaintext (some devices/ports allow it)
                self._sock = raw
            elif hello.command == STLS:
                raw.sendall(_pack(STLS, A_STLS_VERSION, 0))
                # a socket timeout would abort the TLS handshake, which wants
                # blocking reads
                raw.settimeout(None)
                try:
                    self._sock = ctx.wrap_socket(raw, server_hostname=None)
                except Exception as exc:
                    raise AdbError(f"TLS failed: {exc}") from exc
                # the device speaks first, and only after the handshake
                hello = self._read()
            else:
                raise AdbError(f"expected STLS from adbd, got 0x{hello.command:08x}")

            if hello.command == AUTH:
                raise AdbError("device asked for AUTH: our key is not paired")
            if hello.command != CNXN:
                raise AdbError(f"expected CNXN banner, got 0x{hello.command:08x}")

            self._sock.settimeout(self._timeout)
            self.banner = hello.data.rstrip(b"\x00").decode(errors="replace")
            return self.banner
        except BaseException:
            self.close()
            try:
                raw.close()
            except OSError:
                pass
            raise

    def reconnect(self) -> str:
        """Drop the socket and authenticate again, once.

        The key that pairing installed is kept, so this is the same handshake
        as the first connect. Callers wrap a command with it rather than
        rebuilding the whole device: a phone that slept mid-task should lose
        one command, not the task.
        """
        endpoint = getattr(self, "_endpoint", None)
        if not endpoint:
            raise AdbError("no endpoint to reconnect to")
        self.close()
        return self.connect(endpoint[0], endpoint[1])

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- services ---------------------------------------------------------

    def shell(self, command: str) -> str:
        """Run a command through ``shell,v2`` and return its stdout."""
        return str(self.run(command))

    def open_service(self, service: str) -> ShellStream:
        """Open an arbitrary ``<service>:<args>`` string and return its stream."""
        if self._sock is None:
            raise AdbError("not connected")
        local_id = self._next_id
        self._next_id += 1
        return ShellStream(self, service, local_id)

    def exec_bytes(self, command: str, timeout: Optional[float] = None) -> bytes:
        """Run one command and return raw stdout bytes.

        ``exec:`` is the service behind ``adb exec-out``: no PTY, so newline
        translation cannot corrupt binary payloads the way ``shell:`` would.
        """
        with self._cmd_lock:
            with self.open_service(f"exec:{command}") as stream:
                return stream.read_all(timeout)

    def run(self, command: str, timeout: Optional[float] = None) -> Result:
        """Run a command and keep stdout, stderr and the exit code apart.

        ``timeout`` bounds the whole exchange. Without it a ``dumpsys`` that
        stopped answering blocked the calling thread forever, and because that
        thread was the one serving the app's local API, one stuck command took
        the whole runtime with it (measured: a task went silent for eleven
        minutes with no way to even ask what it was doing).
        """
        if self._sock is None:
            raise AdbError("not connected")
        with self._cmd_lock:
            previous = self._sock.gettimeout()
            if timeout is not None:
                self._sock.settimeout(timeout)
            try:
                return self._run_frames(command)
            except (socket.timeout, TimeoutError) as exc:
                raise AdbError(
                    f"{command.strip()[:60]}: no answer within {timeout or 0:.0f}s"
                ) from exc
            finally:
                self._sock.settimeout(previous)

    def _run_frames(self, command: str) -> Result:
        local_id = self._next_id
        self._next_id += 1
        remote_id = 0
        self._send(OPEN, local_id, 0, f"shell,v2,raw:{command}\x00".encode())

        stdout = bytearray()
        stderr = bytearray()
        exit_code = -1
        remote_id: Optional[int] = None
        while True:
            frame = self._read()
            if not _belongs_to(frame, local_id, remote_id):
                continue
            if frame.command == OKAY:
                remote_id = frame.arg0
                continue
            if frame.command == WRTE:
                remote_id = frame.arg0
                at = 0
                data = frame.data
                while at + 5 <= len(data):
                    packet_id = data[at]
                    length = struct.unpack_from("<I", data, at + 1)[0]
                    if at + 5 + length > len(data):
                        break
                    chunk = data[at + 5:at + 5 + length]
                    if packet_id == STDOUT:
                        stdout.extend(chunk)
                    elif packet_id == STDERR:
                        stderr.extend(chunk)
                    elif packet_id == EXIT:
                        # the payload is the exit code itself, one byte
                        exit_code = chunk[0] if chunk else -1
                    at += 5 + length
                self._send(OKAY, local_id, remote_id)
                continue
            if frame.command == CLSE:
                break
            if frame.command == STLS:
                raise AdbError("device asked to upgrade again mid-service")
            raise AdbError(f"unexpected frame 0x{frame.command:08x}")

        return Result(
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
            exit_code,
        )

    # -- plumbing ---------------------------------------------------------

    def _context(self):
        cert_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
        key_file = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
        cert_file.write(self._cert)
        key_file.write(self._key)
        cert_file.close()
        key_file.close()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        ctx.load_cert_chain(cert_file.name, key_file.name)
        try:
            ctx.set_alpn_protocols(["adb"])
        except NotImplementedError:  # pragma: no cover - some OpenSSL builds
            pass
        return ctx

    def _send(self, command: int, arg0: int, arg1: int, data: bytes = b"") -> None:
        self._sock.sendall(_pack(command, arg0, arg1, data))

    def _read(self) -> Frame:
        raw = self._read_exactly(HEADER.size)
        command, arg0, arg1, length, _crc, magic = HEADER.unpack(raw)
        if command ^ magic != MAGIC_MASK:
            raise AdbError(f"frame magic mismatch for 0x{command:08x}")
        data = self._read_exactly(length) if length else b""
        return Frame(command, arg0, arg1, data)

    def _read_frame_from(self, sock) -> Frame:
        saved, self._sock = self._sock, sock
        try:
            return self._read()
        finally:
            self._sock = saved

    def _read_exactly(self, size: int) -> bytes:
        buf = bytearray()
        while len(buf) < size:
            chunk = self._sock.recv(size - len(buf))
            if not chunk:
                raise AdbError("connection closed")
            buf.extend(chunk)
        return bytes(buf)
