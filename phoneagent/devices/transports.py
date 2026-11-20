"""Where a shell comes from.

The virtual-display backend needs four things from the outside world: run a
command, keep a process alive and talk to its stdin/stdout, read a file, and
write a file. During development those come from the host's ``adb`` binary;
inside the APK they come from the app's own connection to adbd over wireless
debugging.

Keeping the two behind one interface is what makes the packaged app possible:
``VirtualDisplayDevice`` does not know, and must not care, whether a computer is
attached.
"""

from __future__ import annotations

import socket

import select
import subprocess
from typing import Optional, Protocol

from .base import DeviceError


class Process(Protocol):
    """A long-lived command whose stdin and stdout we own."""

    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout: float = 10.0) -> str: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...


class Transport(Protocol):
    """The four operations the device backend needs."""

    def shell(self, command: str) -> str: ...

    def spawn(self, command: str) -> Process: ...

    def read_file(self, path: str) -> bytes: ...

    def write_file(self, data: bytes, path: str) -> None: ...


# -- host side ---------------------------------------------------------------


class HostProcess:
    """``adb shell`` kept open, with stdin and stdout on pipes."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc

    def write_line(self, line: str) -> None:
        if not self._proc.stdin:
            raise DeviceError("process has no stdin")
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def read_line(self, timeout: float = 10.0) -> str:
        if not self._proc.stdout:
            return ""
        ready, _, _ = select.select([self._proc.stdout], [], [], timeout)
        if not ready:
            return ""
        return self._proc.stdout.readline().strip()

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    def terminate(self) -> None:
        if self._proc.poll() is None:
            self._proc.kill()


class HostAdbTransport:
    """Talks to the phone through the ``adb`` binary on this machine."""

    def __init__(self, adb: str = "adb", serial: Optional[str] = None, timeout: float = 30.0) -> None:
        self.adb = adb
        self.serial = serial
        self.timeout = timeout

    def _base(self) -> list[str]:
        cmd = [self.adb]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def shell(self, command: str) -> str:
        proc = subprocess.run(
            self._base() + ["shell", command],
            capture_output=True, text=True, timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise DeviceError(proc.stderr.strip()[:200] or f"{command} failed")
        return proc.stdout

    def spawn(self, command: str) -> Process:
        proc = subprocess.Popen(
            self._base() + ["shell", command],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        return HostProcess(proc)

    def read_file(self, path: str) -> bytes:
        # exec-out keeps the bytes intact; `adb shell` would translate newlines
        proc = subprocess.run(
            self._base() + ["exec-out", "cat " + path],
            capture_output=True, timeout=self.timeout,
        )
        if proc.returncode != 0:
            raise DeviceError(proc.stderr.decode(errors="replace")[:200] or f"cannot read {path}")
        return proc.stdout

    def write_file(self, data: bytes, path: str) -> None:
        import base64

        encoded = base64.b64encode(data).decode()
        self.shell(f"mkdir -p $(dirname {path}); echo {encoded} | base64 -d > {path}")


# -- on-device side ----------------------------------------------------------


class OnDeviceProcess:
    """A ``shell,raw:`` service kept open for a long-running child process."""

    def __init__(self, stream) -> None:
        self._stream = stream

    def write_line(self, line: str) -> None:
        self._stream.writeline(line)

    def read_line(self, timeout: float = 10.0) -> str:
        return self._stream.readline(timeout).decode(errors="replace").strip()

    def is_alive(self) -> bool:
        return not self._stream.eof

    def terminate(self) -> None:
        self._stream.close()


class SelfAdbTransport:
    """Talks to the phone through the phone's own adbd.

    No USB, no computer: the client is :class:`~phoneagent.devices.adbclient.AdbClient`,
    already authenticated with the key that pairing installed.
    """

    def __init__(self, client, timeout: float = 30.0) -> None:
        self.client = client
        self.timeout = timeout

    @staticmethod
    def _is_dead_connection(exc: Exception) -> bool:
        """True when the socket to adbd is gone and a fresh one is the fix.

        ``ssl.SSLEOFError`` and friends are ``OSError`` subclasses; so are
        ``BrokenPipeError`` and ``ConnectionResetError``. A timeout is a
        different problem (the command is slow) and must not trigger a
        reconnect, because that would drop a command that was still running.
        """
        if isinstance(exc, (socket.timeout,)) or type(exc).__name__ in ("timeout", "TimeoutError"):
            return False
        if isinstance(exc, OSError):
            return True
        # A desynchronised stream is as dead as a closed one: after "frame magic
        # mismatch" every following frame is garbage, so retrying without a
        # fresh connection loops forever (measured: two spills in a row failed
        # with 0x00000103 and 0x005f0000).
        text = str(exc).lower()
        return (
            "not connected" in text
            or "frame magic mismatch" in text
            or "connection reset" in text
            or "broken pipe" in text
        )

    def _with_reconnect(self, run):
        """Run one command; if the connection died, rebuild it and run again."""
        try:
            return run()
        except Exception as exc:  # noqa: BLE001 - classified below
            if not self._is_dead_connection(exc):
                raise
            print(f"[adb] connection lost ({type(exc).__name__}); reconnecting")
            try:
                self.client.reconnect()
            except Exception as again:  # noqa: BLE001
                raise DeviceError(f"reconnect failed: {again}") from exc
            return run()

    def shell(self, command: str) -> str:
        # The timeout is the difference between "this screen would not dump" and
        # "the agent stopped answering": without it a stuck command holds the
        # thread that also serves the app's API.
        try:
            return str(self._with_reconnect(
                lambda: self.client.run(command, timeout=self.timeout)))
        except Exception as exc:  # noqa: BLE001 - re-raised as a device error
            name = type(exc).__name__
            if "timeout" in str(exc).lower() or name in ("timeout", "TimeoutError"):
                raise DeviceError(f"{command.strip()[:60]}: timed out") from exc
            raise

    def spawn(self, command: str) -> Process:
        # `raw` matters: a pty would echo stdin back and rewrite newlines,
        # which breaks the helper's line protocol
        stream = self.client.open_service(f"shell,raw:{command}")
        return OnDeviceProcess(stream)

    def read_file(self, path: str) -> bytes:
        return self._with_reconnect(
            lambda: self.client.exec_bytes(f"cat {path}", self.timeout))

    def write_file(self, data: bytes, path: str) -> None:
        import base64

        encoded = base64.b64encode(data).decode()
        # one short command line beats streaming: the helper jar is ~4 KiB
        result = self._with_reconnect(
            lambda: self.client.run(
                f"mkdir -p $(dirname {path}); echo {encoded} | base64 -d > {path}"))
        if result.exit_code not in (0, -1):
            raise DeviceError(f"cannot write {path}: exit {result.exit_code}")
