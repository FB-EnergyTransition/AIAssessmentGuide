import io
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib import error, request
from urllib.parse import urlparse

from pypdf import PdfReader

from guideos.knowledge.models import KnowledgeChunk, KnowledgeError


SUPPORTED_EXTENSIONS = (".pdf", ".md", ".txt")
MAX_CHUNK_CHARS = 1200
MAX_WEB_BYTES = 10 * 1024 * 1024

Segment = tuple[str | None, str]


@dataclass(frozen=True)
class ExtractedText:
    """Text of one source, split into segments with a citable locator
    (page or heading)."""

    title: str
    segments: list[Segment]


@dataclass(frozen=True)
class FetchedPage:
    url: str
    raw: bytes
    content_type: str
    charset: str | None


def extract_document(filename: str, raw: bytes) -> ExtractedText:
    suffix = Path(filename).suffix.lower()
    title = Path(filename).stem
    if suffix == ".pdf":
        extracted = _extract_pdf(raw, title)
    elif suffix == ".md":
        extracted = _extract_markdown(_decode(raw), title)
    elif suffix == ".txt":
        extracted = ExtractedText(title, _text_segments(_decode(raw)))
    else:
        raise KnowledgeError(
            "Unterstützt werden PDF-, Markdown- und Textdateien (.pdf, .md, .txt)."
        )
    return _require_text(extracted)


def extract_web_page(page: FetchedPage) -> ExtractedText:
    if page.content_type == "application/pdf":
        extracted = _extract_pdf(page.raw, page.url)
    elif page.content_type in {"text/html", "application/xhtml+xml"}:
        parser = _HtmlText()
        parser.feed(page.raw.decode(page.charset or "utf-8", errors="replace"))
        parser.close()
        extracted = ExtractedText(" ".join(parser.title.split()) or page.url, parser.segments)
    elif page.content_type in {"text/plain", "text/markdown"}:
        text = page.raw.decode(page.charset or "utf-8", errors="replace")
        extracted = ExtractedText(page.url, _text_segments(text))
    else:
        raise KnowledgeError(
            f"Der Inhaltstyp {page.content_type or 'unbekannt'} wird nicht unterstützt."
        )
    return _require_text(extracted)


def fetch_web_page(url: str, timeout: int = 20) -> FetchedPage:
    require_http(url)
    page_request = request.Request(url.strip(), headers={"User-Agent": "GuideOS"})
    try:
        with request.urlopen(page_request, timeout=timeout) as response:
            raw = response.read(MAX_WEB_BYTES + 1)
            final_url = response.geturl() or url.strip()
            header = response.headers.get("Content-Type") or ""
    except (error.URLError, TimeoutError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", None) or exc
        raise KnowledgeError(f"Die Webseite konnte nicht abgerufen werden: {reason}") from exc
    if len(raw) > MAX_WEB_BYTES:
        raise KnowledgeError("Die Webseite ist zu groß für die Wissensbasis.")
    require_http(final_url)
    content_type, _, parameters = header.partition(";")
    charset = re.search(r"charset=\"?([\w.:-]+)", parameters, re.IGNORECASE)
    return FetchedPage(
        final_url,
        raw,
        content_type.strip().lower(),
        charset.group(1).lower() if charset else None,
    )


def chunk_segments(
    segments: list[Segment],
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[KnowledgeChunk]:
    """Pack each segment's paragraphs into chunks of at most max_chars; a
    chunk never spans two segments, so its locator stays exact."""
    chunks = []
    for locator, text in segments:
        current = ""
        for piece in _pieces(text, max_chars):
            if current and len(current) + 2 + len(piece) > max_chars:
                chunks.append(KnowledgeChunk(locator=locator, text=current))
                current = piece
            else:
                current = f"{current}\n\n{piece}" if current else piece
        if current:
            chunks.append(KnowledgeChunk(locator=locator, text=current))
    return chunks


def _pieces(text: str, max_chars: int) -> list[str]:
    pieces = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = " ".join(paragraph.split())
        while len(paragraph) > max_chars:
            cut = paragraph.rfind(". ", 0, max_chars) + 1
            if cut <= 0:
                cut = paragraph.rfind(" ", 0, max_chars)
            if cut <= 0:
                cut = max_chars
            pieces.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            pieces.append(paragraph)
    return pieces


def require_http(url: str) -> None:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise KnowledgeError("Bitte eine vollständige http- oder https-Adresse angeben.")


def _require_text(extracted: ExtractedText) -> ExtractedText:
    if not extracted.segments:
        raise KnowledgeError("Das Dokument enthält keinen auslesbaren Text.")
    return extracted


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KnowledgeError("Die Textdatei muss UTF-8-kodiert sein.") from exc


def _text_segments(text: str) -> list[Segment]:
    text = text.strip()
    return [(None, text)] if text else []


def _extract_pdf(raw: bytes, title: str) -> ExtractedText:
    try:
        reader = PdfReader(io.BytesIO(raw))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises many error types for damaged or encrypted files
        raise KnowledgeError("Das PDF konnte nicht gelesen werden.") from exc
    return ExtractedText(
        title,
        [
            (f"Seite {number}", text.strip())
            for number, text in enumerate(pages, start=1)
            if text.strip()
        ],
    )


def _extract_markdown(text: str, fallback_title: str) -> ExtractedText:
    title = None
    segments: list[Segment] = []
    locator = None
    lines: list[str] = []

    def flush() -> None:
        body = "\n".join(lines).strip()
        if body:
            segments.append((locator, f"{locator}\n\n{body}" if locator else body))

    for line in text.splitlines():
        heading = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if heading and heading.group(2):
            flush()
            locator = heading.group(2)
            if title is None and len(heading.group(1)) == 1:
                title = locator
            lines = []
        else:
            lines.append(line)
    flush()
    return ExtractedText(title or fallback_title, segments)


class _HtmlText(HTMLParser):
    """Visible page text, split at h1-h3 headings."""

    SKIPPED = {"script", "style", "noscript", "nav", "footer", "svg", "template"}
    HEADINGS = {"h1", "h2", "h3"}
    BLOCKS = {
        "p", "div", "li", "ul", "ol", "tr", "table", "section", "article",
        "blockquote", "pre", "h4", "h5", "h6", "dt", "dd",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.segments: list[Segment] = []
        self._skipped = 0
        self._in_title = False
        self._heading: list[str] | None = None
        self._locator: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIPPED:
            self._skipped += 1
        elif tag == "title":
            self._in_title = True
        elif self._skipped:
            return
        elif tag in self.HEADINGS:
            self._flush()
            self._heading = []
        elif tag in self.BLOCKS or tag == "br":
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIPPED:
            self._skipped = max(0, self._skipped - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in self.HEADINGS and self._heading is not None:
            self._locator = " ".join("".join(self._heading).split()) or None
            self._parts = [self._locator or "", "\n\n"]
            self._heading = None
        elif tag in self.BLOCKS:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._skipped:
            return
        elif self._heading is not None:
            self._heading.append(data)
        else:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        paragraphs = [
            " ".join(part.split(" ")).strip()
            for part in re.split(r"\n\s*\n", "".join(self._parts))
        ]
        text = "\n\n".join(part for part in paragraphs if part)
        if text and text != self._locator:
            self.segments.append((self._locator, text))
        self._parts = []
