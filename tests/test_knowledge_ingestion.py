import pytest

from guideos.knowledge.ingestion import (
    FetchedPage,
    KnowledgeError,
    chunk_segments,
    extract_document,
    extract_web_page,
    fetch_web_page,
)
from tests.knowledge_fixtures import make_pdf


def test_text_file_becomes_one_segment_titled_by_filename():
    extracted = extract_document("heizung-notizen.txt", "Wärmepumpen brauchen Strom.".encode())

    assert extracted.title == "heizung-notizen"
    assert extracted.segments == [(None, "Wärmepumpen brauchen Strom.")]


def test_markdown_is_split_by_headings_and_titled_by_first_heading():
    raw = (
        "# Leitfaden Heizungstausch\n\nEinleitung.\n\n"
        "## Förderung\n\nFörderungen gibt es vom Bund.\n\n"
        "## Leere Überschrift\n\n"
        "### Wärmepumpe\n\nSie nutzt Umweltwärme.\n"
    ).encode()

    extracted = extract_document("leitfaden.md", raw)

    assert extracted.title == "Leitfaden Heizungstausch"
    assert extracted.segments == [
        ("Leitfaden Heizungstausch", "Leitfaden Heizungstausch\n\nEinleitung."),
        ("Förderung", "Förderung\n\nFörderungen gibt es vom Bund."),
        ("Wärmepumpe", "Wärmepumpe\n\nSie nutzt Umweltwärme."),
    ]


def test_pdf_is_split_by_page_and_skips_pages_without_text():
    raw = make_pdf(["Dämmung der Fassade", "", "Wärmepumpe statt Gaskessel"])

    extracted = extract_document("Sanierung.pdf", raw)

    assert extracted.title == "Sanierung"
    assert extracted.segments == [
        ("Seite 1", "Dämmung der Fassade"),
        ("Seite 3", "Wärmepumpe statt Gaskessel"),
    ]


@pytest.mark.parametrize(
    ("filename", "raw", "message"),
    [
        ("scan.pdf", make_pdf([""]), "keinen auslesbaren Text"),
        ("kaputt.pdf", b"kein pdf", "PDF konnte nicht gelesen werden"),
        ("leer.txt", b"   \n", "keinen auslesbaren Text"),
        ("tabelle.xlsx", b"x", "Unterstützt werden"),
        ("latin.txt", "Wärme".encode("cp1252"), "UTF-8"),
    ],
)
def test_unusable_documents_are_rejected_in_german(filename, raw, message):
    with pytest.raises(KnowledgeError, match=message):
        extract_document(filename, raw)


def test_html_page_uses_title_headings_and_ignores_scripts_and_navigation():
    html = (
        "<html><head><title> Heizungstausch | klimaaktiv </title>"
        "<style>p{color:red}</style></head><body>"
        "<nav><a href='/'>Startseite</a></nav>"
        "<p>Einleitung zum Thema.</p>"
        "<h2>Förderung <em>2026</em></h2><p>Bis zu 75&nbsp;% der Kosten.</p>"
        "<script>alert('x')</script>"
        "<h2>Kontakt</h2><ul><li>Beratung</li><li>Telefon</li></ul>"
        "<footer>Impressum</footer></body></html>"
    ).encode()
    page = FetchedPage("https://www.klimaaktiv.at/heizung", html, "text/html", "utf-8")

    extracted = extract_web_page(page)

    assert extracted.title == "Heizungstausch | klimaaktiv"
    assert extracted.segments == [
        (None, "Einleitung zum Thema."),
        ("Förderung 2026", "Förderung 2026\n\nBis zu 75\xa0% der Kosten."),
        ("Kontakt", "Kontakt\n\nBeratung\n\nTelefon"),
    ]


def test_web_page_may_be_a_pdf_or_plain_text_and_falls_back_to_the_url_as_title():
    pdf = FetchedPage(
        "https://example.at/a.pdf", make_pdf(["Seitentext"]), "application/pdf", None
    )
    text = FetchedPage("https://example.at/a.txt", b"Nur Text", "text/plain", None)
    image = FetchedPage("https://example.at/a.png", b"\x89PNG", "image/png", None)

    assert extract_web_page(pdf).segments == [("Seite 1", "Seitentext")]
    assert extract_web_page(pdf).title == "https://example.at/a.pdf"
    assert extract_web_page(text).segments == [(None, "Nur Text")]
    with pytest.raises(KnowledgeError, match="Inhaltstyp image/png"):
        extract_web_page(image)


def test_chunks_keep_locator_and_respect_the_size_limit():
    long_text = " ".join(f"Satz Nummer {index} endet hier." for index in range(40))
    segments = [("Seite 1", long_text), ("Seite 2", "Kurz.\n\nNoch ein Absatz.")]

    chunks = chunk_segments(segments, max_chars=200)

    assert all(len(chunk.text) <= 200 for chunk in chunks)
    assert [chunk.locator for chunk in chunks[:-1]] == ["Seite 1"] * (len(chunks) - 1)
    assert chunks[-1].locator == "Seite 2"
    assert chunks[-1].text == "Kurz.\n\nNoch ein Absatz."
    assert all(chunk.text.endswith(".") for chunk in chunks)
    assert " ".join(chunk.text for chunk in chunks[:-1]) == long_text
    assert all(chunk.embedding is None for chunk in chunks)


def test_chunks_split_text_without_sentence_or_word_boundaries():
    chunks = chunk_segments([(None, "x" * 450)], max_chars=200)

    assert [len(chunk.text) for chunk in chunks] == [200, 200, 50]


class _FakeResponse:
    def __init__(self, body, content_type="text/html; charset=ISO-8859-1", url=None):
        self.body = body
        self.headers = {"Content-Type": content_type}
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, amount):
        return self.body[:amount]

    def geturl(self):
        return self.url


def test_fetch_web_page_returns_final_url_content_type_and_charset(monkeypatch):
    captured = {}

    def fake_urlopen(page_request, timeout):
        captured["url"] = page_request.full_url
        captured["agent"] = page_request.get_header("User-agent")
        return _FakeResponse(b"<p>Hallo</p>", url="https://www.example.at/ziel")

    monkeypatch.setattr("guideos.knowledge.ingestion.request.urlopen", fake_urlopen)

    page = fetch_web_page("https://example.at/start")

    assert captured == {"url": "https://example.at/start", "agent": "GuideOS"}
    assert page == FetchedPage(
        "https://www.example.at/ziel", b"<p>Hallo</p>", "text/html", "iso-8859-1"
    )


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.at/x", "example.at", ""])
def test_fetch_web_page_only_accepts_http_addresses(url, monkeypatch):
    monkeypatch.setattr(
        "guideos.knowledge.ingestion.request.urlopen",
        lambda *args, **kwargs: pytest.fail("darf nicht abgerufen werden"),
    )

    with pytest.raises(KnowledgeError, match="http"):
        fetch_web_page(url)


def test_fetch_web_page_rejects_oversized_pages_redirects_and_failures(monkeypatch):
    from urllib import error

    import guideos.knowledge.ingestion as ingestion

    monkeypatch.setattr(ingestion, "MAX_WEB_BYTES", 10)
    monkeypatch.setattr(
        ingestion.request, "urlopen",
        lambda *args, **kwargs: _FakeResponse(b"x" * 11, url="https://example.at/"),
    )
    with pytest.raises(KnowledgeError, match="zu groß"):
        fetch_web_page("https://example.at/")

    monkeypatch.setattr(
        ingestion.request, "urlopen",
        lambda *args, **kwargs: _FakeResponse(b"x", url="ftp://example.at/datei"),
    )
    with pytest.raises(KnowledgeError, match="http"):
        fetch_web_page("https://example.at/")

    def failing(*args, **kwargs):
        raise error.URLError("Name or service not known")

    monkeypatch.setattr(ingestion.request, "urlopen", failing)
    with pytest.raises(KnowledgeError, match="konnte nicht abgerufen werden"):
        fetch_web_page("https://example.at/")
