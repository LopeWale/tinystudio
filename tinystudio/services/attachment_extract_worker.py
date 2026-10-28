"""Isolated, resource-limited attachment text parser; run directly, never via app imports."""
from __future__ import annotations

import html
import json
import os
import threading
import time
from pathlib import Path
import re
import resource
import sys
import zipfile
from xml.etree import ElementTree

MAX_MEMORY = 512 * 1024 * 1024
MAX_CPU_SECONDS = 5
MAX_EXPANDED_BYTES = 8 * 1024 * 1024
MAX_PDF_STREAM_BYTES = 2 * 1024 * 1024
MAX_PAGES = 100
MAX_ARCHIVE_ENTRIES = 1000
MAX_TEXT_CHARS = 1_000_000
RETAIN_CHARS = 40000


def restrict_resources() -> None:
    # Install CPU and supported memory guards before importing PDF code or
    # expanding input. Limits affect this child only, never the API process.
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))
    try:
        resource.setrlimit(resource.RLIMIT_DATA, (MAX_MEMORY, MAX_MEMORY))
        resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY, MAX_MEMORY))
    except (ValueError, OSError):
        if sys.platform != 'darwin':
            raise ValueError('this host cannot enforce the attachment process memory limit')
        # Darwin does not support useful AS/DATA caps on this host. Monitor the
        # isolated child's resident memory instead; bounded decoder allocations
        # still apply before parsing on every platform. Never run in the API PID.
        def watch_memory() -> None:
            while True:
                if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss > MAX_MEMORY:
                    os._exit(73)
                time.sleep(.005)
        threading.Thread(target=watch_memory, name='attachment-memory-limit', daemon=True).start()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _office(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_ENTRIES or sum(item.file_size for item in entries) > MAX_EXPANDED_BYTES:
            raise ValueError('expanded document exceeds the extraction limit')
        if any(item.flag_bits & 1 for item in entries):
            raise ValueError('encrypted document archives are unsupported')
        if path.suffix == '.docx':
            names = ['word/document.xml']
        else:
            names = sorted((item.filename for item in entries if re.fullmatch(r'ppt/slides/slide\d+\.xml', item.filename)))
            if len(names) > MAX_PAGES:
                raise ValueError('document exceeds the 100-page extraction limit')
        parts = []
        characters = 0
        remaining = MAX_EXPANDED_BYTES
        for name in names:
            with archive.open(name) as member:
                raw = member.read(remaining + 1)
            remaining -= len(raw)
            if remaining < 0:
                raise ValueError('expanded document exceeds the extraction limit')
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('document XML entity declarations are unsupported')
            tree = ElementTree.fromstring(raw)
            for paragraph in tree.iter():
                if paragraph.tag.rsplit('}', 1)[-1] != 'p':
                    continue
                text = ''.join(node.text or '' for node in paragraph.iter() if node.tag.rsplit('}', 1)[-1] == 't').strip()
                characters += len(text)
                if characters > MAX_TEXT_CHARS:
                    raise ValueError('document text exceeds the extraction limit')
                if text:
                    parts.append(text)
        return '\n\n'.join(parts)


def _pdf(path: Path) -> str:
    import pypdf
    if not hasattr(pypdf, 'Configuration'):
        raise ValueError('PDF attachments require pypdf 6.21 resource-limit support')
    # The installed parser enforces decoder limits while decompressing, BEFORE
    # allocating the entire stream. The outer process bounds cover other filters,
    # hostile page trees, nested forms, object graphs and parsing work as well.
    pypdf.overwrite_configuration(pypdf.Configuration(
        maximum_declared_stream_length=MAX_PDF_STREAM_BYTES,
        array_based_stream_maximum_output_length=MAX_PDF_STREAM_BYTES,
        zlib_maximum_output_length=MAX_PDF_STREAM_BYTES,
        lzw_maximum_output_length=MAX_PDF_STREAM_BYTES,
        run_length_maximum_output_length=MAX_PDF_STREAM_BYTES,
        brotli_maximum_output_length=MAX_PDF_STREAM_BYTES,
        stream_decoding_work_maximum_length=MAX_EXPANDED_BYTES,
        stream_parsing_work_maximum_count=100000,
        array_maximum_element_count=10000,
        dictionary_maximum_element_count=10000,
        jbig2_maximum_output_length=MAX_PDF_STREAM_BYTES,
        image_maximum_buffer_size=MAX_PDF_STREAM_BYTES,
        flate_maximum_row_length=MAX_PDF_STREAM_BYTES,
        flate_maximum_columns=10000,
        page_tree_maximum_entries=1000,
        page_tree_maximum_depth=30,
        xform_maximum_invocations_per_extraction=1000,
        jbig2dec_binary=None,
    ))
    reader = pypdf.PdfReader(path)
    if reader.is_encrypted:
        raise ValueError('encrypted PDFs are unsupported')
    if len(reader.pages) > MAX_PAGES:
        raise ValueError('PDF exceeds the 100-page extraction limit')
    parts = []
    expanded = 0
    for page in reader.pages:
        raw_contents = page.get('/Contents')
        if raw_contents is not None:
            obj = raw_contents.get_object()
            streams = obj if isinstance(obj, list) else [obj]
            for stream in streams:
                decoded = stream.get_object().get_data()
                expanded += len(decoded)
                if len(decoded) > MAX_PDF_STREAM_BYTES or expanded > MAX_EXPANDED_BYTES:
                    raise ValueError('expanded PDF content exceeds the extraction limit')
        text = page.extract_text() or ''
        if sum(map(len, parts)) + len(text) > MAX_TEXT_CHARS:
            raise ValueError('PDF text exceeds the extraction limit')
        parts.append(text)
    return '\n\n'.join(parts)


def extract(path: Path) -> tuple[str, int]:
    suffix = path.suffix.lower()
    if suffix in {'.docx', '.pptx'}:
        text = _office(path)
    elif suffix == '.pdf':
        text = _pdf(path)
    else:
        text = path.read_text(encoding='utf-8', errors='replace')
        if suffix in {'.html', '.htm'}:
            text = re.sub(r'<(script|style)\b[^>]*>.*?</\1>', ' ', text, flags=re.S | re.I)
            text = re.sub(r'<br\s*/?>|</p>|</div>|</li>|</h[1-6]>', '\n', text, flags=re.I)
            text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError('document text exceeds the extraction limit')
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    if not text:
        raise ValueError('no extractable text; scanned images require OCR and are unsupported')
    return text[:RETAIN_CHARS], len(text)


def main() -> None:
    try:
        restrict_resources()
        retained, count = extract(Path(sys.argv[1]))
        print(json.dumps({'text': retained, 'extracted_chars': count}))
    except MemoryError:
        print(json.dumps({'error': 'attachment extraction exceeded its memory limit'}))
        raise SystemExit(1)
    except Exception as cause:
        detail = str(cause) if isinstance(cause, ValueError) else 'file could not be extracted within the document resource limits'
        print(json.dumps({'error': detail[:300]}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
