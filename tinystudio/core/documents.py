"""Plain-text extraction from local documents and YouTube transcripts for synthetic dataset generation.

Supported files: .txt, .md, .markdown, .html, .htm, .pdf, .docx, .pptx. DOCX
and PPTX are read with the standard library (they are zip archives of XML).
YouTube transcripts need the optional ``youtube-transcript-api`` package.
Nothing is sent anywhere by this module except the transcript request to
YouTube when you pass a YouTube URL.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
HTML_SUFFIXES = {".html", ".htm"}
PDF_SUFFIXES = {".pdf"}
DOCX_SUFFIXES = {".docx"}
PPTX_SUFFIXES = {".pptx"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | HTML_SUFFIXES | PDF_SUFFIXES | DOCX_SUFFIXES | PPTX_SUFFIXES


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


def _xml_paragraphs(xml: str, paragraph_tag: str, run_tag: str) -> list[str]:
    paragraphs = []
    for block in re.findall(rf"<{paragraph_tag}\b[^>]*>(.*?)</{paragraph_tag}>", xml, flags=re.S):
        runs = re.findall(rf"<{run_tag}\b[^>]*>(.*?)</{run_tag}>", block, flags=re.S)
        text = html.unescape("".join(runs)).strip()
        if text:
            paragraphs.append(text)
    return paragraphs


def _read_docx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", "replace")
    except (zipfile.BadZipFile, KeyError) as cause:
        raise DocumentError(f"{path}: not a Word document ({cause})") from cause
    return "\n\n".join(_xml_paragraphs(xml, "w:p", "w:t"))


def _read_pptx(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            slides = sorted((n for n in archive.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
            parts = []
            for name in slides:
                xml = archive.read(name).decode("utf-8", "replace")
                parts.append("\n".join(_xml_paragraphs(xml, "a:p", "a:t")))
    except (zipfile.BadZipFile, KeyError) as cause:
        raise DocumentError(f"{path}: not a PowerPoint document ({cause})") from cause
    return "\n\n".join(p for p in parts if p.strip())


def youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower().removeprefix("www.").removeprefix("m.")
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0] or None
    if host in ("youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        match = re.match(r"^/(?:shorts|embed|live|v)/([A-Za-z0-9_-]{6,})", parsed.path)
        return match.group(1) if match else None
    return None


def youtube_transcript(url: str, languages: tuple[str, ...] = ("en",)) -> str:
    """Fetch a video's captions (auto or manual) as plain text. Needs `pip install youtube-transcript-api`."""
    video_id = youtube_video_id(url)
    if not video_id:
        raise DocumentError(f"{url}: not a YouTube video URL")
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as cause:
        raise DocumentError("YouTube sources need the optional dependency: uv pip install youtube-transcript-api") from cause
    try:
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=list(languages))
        snippets = fetched.to_raw_data() if hasattr(fetched, "to_raw_data") else list(fetched)
    except Exception as cause:  # noqa: BLE001 - the library raises many specific classes; the message is what matters
        raise DocumentError(f"{url}: transcript unavailable ({type(cause).__name__}: {cause})") from cause
    text = " ".join((s["text"] if isinstance(s, dict) else getattr(s, "text", "")) for s in snippets)
    return re.sub(r"\s+", " ", text).strip()


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
        elif suffix in HTML_SUFFIXES:
            text = _strip_html(path.read_text(encoding="utf-8", errors="replace"))
        elif suffix in PDF_SUFFIXES:
            text = _read_pdf(path)
        elif suffix in DOCX_SUFFIXES:
            text = _read_docx(path)
        elif suffix in PPTX_SUFFIXES:
            text = _read_pptx(path)
        else:
            raise DocumentError(f"{path}: unsupported document type {suffix or '(none)'}; supported: {', '.join(sorted(SUPPORTED_SUFFIXES))}")
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
