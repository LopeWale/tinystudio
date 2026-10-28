"""B dispatcher owns an actual stoppable process, not an abandoned thread."""
import asyncio
import os
from pathlib import Path
import sys
import time

import pytest

from tinystudio.lab.dispatch import Dispatcher, tick


def test_shutdown_stops_owned_process_even_when_it_ignores_cooperative_termination(tmp_path):
    marker = tmp_path / "delivery-count"
    code = "\n".join([
        "import signal,time,pathlib", "signal.signal(signal.SIGTERM,signal.SIG_IGN)",
        f"p=pathlib.Path({str(marker)!r})", "while True:", " with p.open('a') as f: f.write('delivery\\n')",
        " time.sleep(.02)",
    ])
    dispatcher = Dispatcher(command=[sys.executable, "-c", code], grace=.1).start()
    pgid = dispatcher.group.pgid
    try:
        deadline = time.monotonic()+3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists()
        started = time.monotonic()
        asyncio.run(dispatcher.close())
        assert time.monotonic()-started < 2
        with pytest.raises(ProcessLookupError):
            os.killpg(pgid, 0)
        count = marker.read_bytes()
        time.sleep(.1)
        assert marker.read_bytes() == count
    finally:
        asyncio.run(dispatcher.close())


def test_already_cancelled_dispatch_never_opens_stores():
    assert tick(cancelled=lambda: True) == {"stopped": True}
