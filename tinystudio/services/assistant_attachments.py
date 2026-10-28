"""Private attachment metadata and bounded extraction; raw bytes stay in artifact storage."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from tinystudio.core.documents import SUPPORTED_SUFFIXES
from tinystudio.db.models import AssistantAttachment, new_id
from tinystudio.storage import ArtifactStorage, project_key, storage

MAX_BYTES = 10 * 1024 * 1024
MAX_EXTRACT_CHARS = 40000
EXTRACTION_TIMEOUT_SECONDS = 8
_EXTRACTORS = threading.BoundedSemaphore(2)


class ExtractionBusy(RuntimeError):
    pass


def bounded_extract(path: Path) -> tuple[str, int]:
    if not _EXTRACTORS.acquire(blocking=False):
        raise ExtractionBusy('attachment extraction is busy; retry after the active uploads finish')
    try:
        worker = Path(__file__).with_name('attachment_extract_worker.py')
        try:
            result = subprocess.run([sys.executable, '-I', str(worker), str(path)], stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=EXTRACTION_TIMEOUT_SECONDS, check=False)
        except subprocess.TimeoutExpired as cause:
            raise ValueError('attachment extraction exceeded its time limit') from cause
        if len(result.stdout) > MAX_EXTRACT_CHARS * 8:
            raise ValueError('attachment extraction exceeded its output limit')
        try:
            output = json.loads(result.stdout)
        except (ValueError, UnicodeError) as cause:
            raise ValueError('attachment extraction stopped at its memory, CPU or execution limit') from cause
        if result.returncode or 'error' in output:
            raise ValueError(f"attachment extraction failed: {output.get('error', 'resource limit exceeded')}")
        text, count = output['text'], output['extracted_chars']
        if not isinstance(text, str) or len(text) > MAX_EXTRACT_CHARS or not isinstance(count, int) or count < len(text):
            raise ValueError('attachment extraction returned invalid bounded output')
        return text, count
    finally:
        _EXTRACTORS.release()



def configuration() -> dict[str, Any]:
    return {'max_bytes': MAX_BYTES, 'accept': ','.join(sorted(SUPPORTED_SUFFIXES)), 'image_input': False, 'max_extract_chars': MAX_EXTRACT_CHARS, 'extraction_timeout_seconds': EXTRACTION_TIMEOUT_SECONDS, 'max_document_pages': 100, 'max_expanded_bytes': 8 * 1024 * 1024, 'max_processed_chars': 1000000, 'max_pdf_stream_bytes': 2 * 1024 * 1024}


def metadata(row: AssistantAttachment) -> dict[str, Any]:
    return {'id': row.id, 'name': row.name, 'size': row.size, 'sha256': row.sha256, 'project_id': row.project_id,
            'user_id': row.user_id, 'extracted_chars': row.extracted_chars, 'retained_chars': len(row.extracted_text), 'status': 'ready'}


def owned(db: Session, project_id: str, user_id: str, attachment_id: str) -> AssistantAttachment:
    row = db.scalar(select(AssistantAttachment).where(AssistantAttachment.id == attachment_id,
                    AssistantAttachment.project_id == project_id, AssistantAttachment.user_id == user_id))
    if row is None:
        raise ValueError('attachment not found in this user and project')
    return row


def create(db: Session, project_id: str, user_id: str, name: str, data: bytes, store: ArtifactStorage | None = None) -> dict[str, Any]:
    name = Path(name.replace('\\', '/')).name[:255]
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError('unsupported attachment format; images are not supported by this text adapter')
    if len(data) > MAX_BYTES or not data:
        raise ValueError(f'attachment must contain 1 to {MAX_BYTES} bytes')
    attachment_id = new_id()
    key = project_key(project_id, 'uploads', user_id, attachment_id + suffix)
    store = store or storage()
    with tempfile.TemporaryDirectory(prefix='tinystudio-attachment-') as temporary:
        path = Path(temporary) / ('document' + suffix)
        path.write_bytes(data)
        text, extracted_chars = bounded_extract(path)
        store.upload_file(path, key)
    row = AssistantAttachment(id=attachment_id, project_id=project_id, user_id=user_id, name=name, size=len(data),
                              sha256=hashlib.sha256(data).hexdigest(), storage_key=key,
                              extracted_text=text, extracted_chars=extracted_chars)
    try:
        db.add(row)
        db.flush()
    except BaseException:
        store.delete(key)
        raise
    return metadata(row)


def context(db: Session, project_id: str, user_id: str, ids: list[str]) -> list[dict[str, Any]]:
    if len(ids) > 8:
        raise ValueError('at most 8 attachments may be sent in one turn')
    records = []
    remaining = 80000
    for attachment_id in dict.fromkeys(ids):
        row = owned(db, project_id, user_id, attachment_id)
        text = row.extracted_text[:remaining]
        remaining -= len(text)
        records.append({**metadata(row), 'text': text, 'omitted_chars': row.extracted_chars - len(text), 'untrusted': True})
    return records
