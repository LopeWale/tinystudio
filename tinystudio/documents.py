"""Plain-text extraction from local documents for synthetic dataset generation.

Supported: .txt, .md, .markdown, .html, .htm, .pdf. Nothing is sent anywhere by
this module; it only reads files you name.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
HTML_SUFFIXES = {".html", ".htm"}
PDF_SUFFIXES = {".pdf"}


class DocumentError(RuntimeError):
    """A document could not be read; the message names the file and why."""


def _strip_html(markup: str) -> str:
    markup = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", markup, flags=re.S | re.I)
    markup = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h[1-6]>", "\n", markup, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", markup)
    return html.unescape(text)


def _read_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as cause:  # pragma: no cover - dependency is declared
        raise DocumentError("pypdf is not installed") from cause
    reader = PdfReader(str(path))
    return "\n\n".join((page.extract_text() or "") for page in reader.pages)


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
        elif suffix in HTML_SUFFIXES:
            text = _strip_html(path.read_text(encoding="utf-8", errors="replace"))
        elif suffix in PDF_SUFFIXES:
            text = _read_pdf(path)
        else:
            raise DocumentError(f"{path}: unsupported document type {suffix or '(none)'}")
    except OSError as cause:
        raise DocumentError(f"{path}: {cause.strerror}") from cause
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, max_chars: int = 2400, overlap: int = 200) -> list[str]:
    """Split on paragraph boundaries into non-empty chunks of at most ``max_chars`` characters."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    overlap = max(0, min(overlap, max_chars // 4))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = (
            [paragraph]
            if len(paragraph) <= max_chars
            else [paragraph[start : start + max_chars] for start in range(0, len(paragraph), max_chars - overlap)]
        )
        for piece in pieces:
            if not current:
                current = piece
            elif len(current) + 2 + len(piece) <= max_chars:
                current = f"{current}\n\n{piece}"
            else:
                chunks.append(current)
                tail = current[-overlap:].strip() if overlap else ""
                current = f"{tail}\n\n{piece}" if tail and len(tail) + 2 + len(piece) <= max_chars else piece
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]
