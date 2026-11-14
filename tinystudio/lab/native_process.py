"""Per-attempt Linux child reaping, isolated from the API/worker process.

The executable portion imports only the standard library and runs by file path
with Python isolated mode. The engine keeps its own environment and working
directory. A private inherited socket carries identity/exit status, never logs.
Closing the controller's socket also requests cleanup after controller death.
"""
from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time


class NativeSupervisor:
    """Controller-side handle for one disposable Linux reaper process."""

    def __init__(self, command, *, grace, settings=None, **kwargs):
        from tinystudio.config import NativeProcessSettings

        self.settings = settings or NativeProcessSettings.from_environment()
        command = [os.fsdecode(value) for value in command]
        self.command, self.grace = command, grace
        self.pid, self.returncode = None, None
        self._buffer = b""
        self._eof, self._closed = False, False
        self._close_error = None
        self._lock = threading.RLock()
        pass_fds = tuple(kwargs.pop("pass_fds", ()))
        # Validate before opening private descriptors: an invalid caller FD
        # must never become an alias for our newly allocated control socket.
        for descriptor in pass_fds:
            os.fstat(descriptor)
        parent, child = socket.socketpair()
        self._selector = None
        try:
            if parent.fileno() < 3:
                descriptor = fcntl.fcntl(parent.fileno(), fcntl.F_DUPFD_CLOEXEC, 3)
                parent.close()
                parent = socket.socket(fileno=descriptor)
            if child.fileno() < 3:
                descriptor = fcntl.fcntl(child.fileno(), fcntl.F_DUPFD_CLOEXEC, 3)
                child.close()
                child = socket.socket(fileno=descriptor)
            self._socket = parent
            self._selector = selectors.DefaultSelector()
            self._selector.register(parent, selectors.EVENT_READ)
            self.owner = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).absolute()), str(child.fileno()),
                 json.dumps({"command": command, "grace": grace, "poll_seconds": self.settings.poll_seconds,
                             "pass_fds": pass_fds})],
                start_new_session=True, pass_fds=(*pass_fds, child.fileno()), **kwargs)
        except BaseException:
            parent.close()
            if self._selector is not None:
                self._selector.close()
            raise
        finally:
            child.close()
        self.stdin, self.stdout, self.stderr = self.owner.stdin, self.owner.stdout, self.owner.stderr
        try:
            deadline = time.monotonic() + self.settings.startup_seconds
            while self.pid is None:
                self._receive(max(0, deadline - time.monotonic()))
                if self._eof:
                    raise RuntimeError("native supervisor exited before reporting engine identity")
                if self.pid is None and time.monotonic() >= deadline:
                    raise TimeoutError("native supervisor did not report engine identity before the startup deadline")
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup:
                error.add_note(f"Native supervisor cleanup also reported: {cleanup}")
            raise

    def _receive(self, timeout):
        if self._eof:
            return
        if not self._selector.select(timeout):
            return
        try:
            block = self._socket.recv(4096)
        except OSError as error:
            self._eof = True
            raise RuntimeError("native supervisor status channel failed") from error
        if not block:
            self._eof = True
            if self._buffer:
                raise RuntimeError("native supervisor returned an incomplete status message")
            return
        self._buffer += block
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            message = json.loads(line)
            if message.get("type") == "started":
                if self.pid is not None or type(message.get("pid")) is not int or message["pid"] <= 0:
                    raise RuntimeError("native supervisor returned an invalid engine identity")
                self.pid = message["pid"]
            elif message.get("type") == "exited":
                if type(message.get("returncode")) is not int:
                    raise RuntimeError("native supervisor returned an invalid exit status")
                self.returncode = message["returncode"]
            elif message.get("type") == "error":
                if message.get("errno"):
                    raise OSError(message["errno"], message["message"])
                raise RuntimeError(message["message"])
            else:
                raise RuntimeError("native supervisor returned an unknown status message")

    def poll(self):
        with self._lock:
            if self._closed and self.returncode is None:
                raise RuntimeError("native supervisor lost engine exit status")
            if not self._closed:
                self._receive(0)
                if self._eof and self.returncode is None:
                    raise RuntimeError("native supervisor lost engine exit status")
            return self.returncode

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            while self.poll() is None:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise subprocess.TimeoutExpired(self.command, timeout)
                self._receive(remaining)
            return self.returncode

    def close(self):
        with self._lock:
            if self._closed:
                if self._close_error is not None:
                    raise self._close_error
                return
            deadline = time.monotonic() + self.settings.startup_seconds + 2 * max(self.grace, .1)
            failure = None
            try:
                try:
                    self._socket.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                while not self._eof:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("native supervisor cleanup timed out")
                    self._receive(remaining)
            except BaseException as error:
                failure = error
            finally:
                try:
                    # An error status still needs wait(): otherwise the helper
                    # itself becomes an unreaped child of the controller.
                    self.owner.wait(timeout=max(0, deadline - time.monotonic()))
                    if self.owner.returncode != 0:
                        raise RuntimeError("native supervisor could not confirm descendant cleanup")
                except BaseException as error:
                    if failure is None:
                        failure = error
                    else:
                        failure.add_note(f"Supervisor exit also reported: {error}")
                finally:
                    self._selector.close()
                    self._socket.close()
                    self._closed = True
            self._close_error = failure
            if failure is not None:
                raise failure


class _Reaper:
    """Lives only inside the supervisor; every direct child belongs to this attempt."""

    def __init__(self, channel, command, grace, poll_seconds, pass_fds):
        self.channel, self.grace, self.poll_seconds = channel, grace, poll_seconds
        # This process owns no other workload. Never set subreaper mode in the
        # multithreaded API/worker, where it would adopt unrelated attempts.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            code = ctypes.get_errno()
            raise OSError(code, "cannot enable per-attempt child reaping")
        signal.signal(signal.SIGCHLD, signal.SIG_DFL)
        self.leader = subprocess.Popen(command, start_new_session=True, pass_fds=tuple(pass_fds))
        # Only the engine owns data streams. Keeping copies in this long-lived
        # reaper would delay pipe EOF until cleanup, even after engine exit.
        for stream in (sys.stdin, sys.stdout, sys.stderr):
            if stream is not None:
                stream.close()
        for descriptor in (set(pass_fds) | {0, 1, 2}) - {self.channel.fileno()}:
            try:
                os.close(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
        self.exit_reported = False
        self.send({"type": "started", "pid": self.leader.pid})

    def send(self, message):
        try:
            self.channel.sendall(json.dumps(message).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError):
            # Losing the controller cannot prevent local cleanup.
            pass

    def reap(self):
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return True
            if pid == 0:
                return False
            if pid == self.leader.pid:
                self.leader.returncode = os.waitstatus_to_exitcode(status)
                if not self.exit_reported:
                    self.send({"type": "exited", "returncode": self.leader.returncode})
                    self.exit_reported = True

    def children(self):
        # Some Linux kernels omit the optional task/children file. Fall back
        # to parent IDs, never an arbitrary process name or process-group scan.
        # Only this process reaps its children: a matching PID cannot be
        # recycled between reading its parent and signalling it.
        path = Path("/proc/self/task") / str(os.getpid()) / "children"
        try:
            return [int(value) for value in path.read_text().split()]
        except FileNotFoundError:
            result = []
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    fields = (entry / "stat").read_text().rpartition(") ")[2].split()
                    if int(fields[1]) == os.getpid():
                        result.append(int(entry.name))
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
            return result

    def stop(self):
        for sig, seconds in ((signal.SIGTERM, max(0, self.grace)), (signal.SIGKILL, max(self.grace, .1))):
            deadline = time.monotonic() + seconds
            while True:
                if self.reap():
                    return
                # Includes descendants that created another session. They are
                # still our children after their original parents exit.
                for pid in self.children():
                    try:
                        os.kill(pid, sig)
                    except ProcessLookupError:
                        pass
                if time.monotonic() >= deadline:
                    break
                time.sleep(min(self.poll_seconds, max(0, deadline - time.monotonic())))
        if not self.reap():
            raise RuntimeError("cannot confirm owned descendant reaping")

    def run(self):
        try:
            with selectors.DefaultSelector() as watcher:
                watcher.register(self.channel, selectors.EVENT_READ)
                while True:
                    self.reap()
                    if watcher.select(self.poll_seconds):
                        if not self.channel.recv(1):
                            break
                        raise RuntimeError("invalid native supervisor control message")
        finally:
            self.stop()


def _main():
    channel = socket.socket(fileno=int(sys.argv[1]))
    try:
        options = json.loads(sys.argv[2])
        owner = _Reaper(channel, **options)
        owner.run()
        return 0
    except BaseException as error:
        try:
            channel.sendall(json.dumps({"type": "error", "message": str(error),
                                        "errno": getattr(error, "errno", None)}).encode() + b"\n")
        except OSError:
            pass
        return 1
    finally:
        channel.close()


if __name__ == "__main__":
    raise SystemExit(_main())
