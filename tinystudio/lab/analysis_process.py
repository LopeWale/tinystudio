"""Custom-analysis child ownership inside an already owned runner attempt."""
from __future__ import annotations

import math
import os
import subprocess
import sys

from tinystudio.config import NativeProcessSettings


class AnalysisProcess:
    """Keep POSIX children in the outer attempt unless Linux supplies a reaper.

    On Linux, ProcessGroup retains its per-attempt descendant supervision. On
    other POSIX hosts this handle owns only the exact child leader: descendants
    inherit the outer runner's session/group, which that runner closes before
    publishing outputs. This handle must never signal its inherited group.
    """

    def __init__(self, command, *, grace=3, **kwargs):
        if os.name != "posix":
            raise ValueError("custom analysis requires POSIX process lifecycle support")
        if not math.isfinite(grace) or grace < 0:
            raise ValueError("custom analysis termination grace must be finite and nonnegative")
        self.closed = False
        self._group = None
        settings = NativeProcessSettings.from_environment()
        self._wait_seconds = max(grace, settings.poll_seconds)
        if sys.platform == "linux":
            from .runner import ProcessGroup

            self._group = ProcessGroup(command, grace=grace, **kwargs)
            self.process = self._group.process
        else:
            self.process = subprocess.Popen(command, start_new_session=False, **kwargs)

    def close(self):
        if self.closed:
            return
        if self._group is not None:
            self._group.close()
        else:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=self._wait_seconds)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            try:
                self.process.wait(timeout=self._wait_seconds)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("cannot confirm custom analysis process cleanup") from error
        self.closed = True
