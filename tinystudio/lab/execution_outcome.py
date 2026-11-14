"""Explicit bootstrap outcome contract shared by the child and its runner."""

CANCELLED_EVIDENCE_EXIT = 85
FAILED_EVIDENCE_EXIT = 86


class RetainedExecutionError(RuntimeError):
    """An explicit failed execution with engine-authored audit evidence.

    This does not make arbitrary partial files publishable. The bootstrap and
    runner still verify the dedicated exit status, result digest, and artifacts.
    """

    def __init__(self, message, *, evidence_result: dict):
        super().__init__(message)
        self.evidence_result = evidence_result


def cancelled_evidence_outcome() -> dict:
    """Return a fresh marker: these artifacts are cancellation audit evidence."""
    return {"format": "tinystudio.cancelled-evidence.v1", "state": "cancelled", "audit_only": True}


def failed_evidence_outcome() -> dict:
    """Return a fresh marker for explicitly retained failure audit evidence."""
    return {"format": "tinystudio.failed-evidence.v1", "state": "failed", "audit_only": True}
