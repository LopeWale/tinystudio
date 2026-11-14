"""Own a finalization-only worker for one supplied private test database.

Export/import/bridge/verifier queues remain under each test's explicit control.
The real operation worker owns its normal token/generation compare-and-swap.
"""
from contextlib import contextmanager
import sys
import threading

from sqlalchemy import select

from tinystudio.lab import operations
from tinystudio.lab.models import LabOperation


@contextmanager
def finalization_worker(factory):
    stopped=threading.Event()
    errors=[]
    def work():
        try:
            while not stopped.is_set():
                with factory() as db:
                    identities=list(db.scalars(select(LabOperation.id).where(
                        LabOperation.kind=='attempt_finalize',LabOperation.state=='queued')))
                # Close the reader transaction before the production worker
                # locks and claims a committed operation on its own connection.
                for identity in identities:
                    if stopped.is_set():break
                    operations.run_operation(identity,session_factory=factory)
                stopped.wait(.02)
        except BaseException as cause:
            errors.append(cause)
            stopped.set()
    thread=threading.Thread(target=work,name='owned-lab-finalization-worker')
    thread.start()
    try:
        yield
    finally:
        primary=sys.exc_info()[1]
        stopped.set()
        thread.join()
        if errors:
            message='owned finalization worker failed: '+type(errors[0]).__name__
            if primary is not None:primary.add_note(message)
            else:raise RuntimeError(message) from errors[0]
