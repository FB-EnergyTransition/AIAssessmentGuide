"""Source versions for web pages (concept step 7): a re-fetch with changed
content becomes a new version, the old one stays citable but unsearched,
unchanged content keeps the stored version, and replaced versions can be
removed on purpose."""

import logging

import pytest

from guideos.domains.loader import load_domain_pack
from guideos.domains.models import WebSourceSettings
from guideos.knowledge.ingestion import FetchedPage
from guideos.knowledge.models import KnowledgeError
from guideos.knowledge.service import KnowledgeService
from guideos.knowledge.store import LocalKnowledgeStore

URL = "https://www.klimaaktiv.at/heizung"


def _page(html):
    return FetchedPage(URL, html.encode(), "text/html", "utf-8")


def _service(tmp_path, pages):
    queue = iter(pages)
    return KnowledgeService(LocalKnowledgeStore(tmp_path / "knowledge.json"), fetch=lambda url: next(queue))


def _web_pack():
    return load_domain_pack().model_copy(update={"web_sources": WebSourceSettings(enabled=True)})


def test_unchanged_content_keeps_the_stored_version(tmp_path, caplog):
    service = _service(tmp_path, [_page("<title>A</title><p>Gleich</p>"), _page("<title>A</title><p>Gleich</p>")])

    first = service.add_web_page("verein-demo", URL, "http://fake")
    with caplog.at_level(logging.INFO, logger="guideos"):
        again = service.add_web_page("verein-demo", URL, "http://fake")

    assert again.document_id == first.document_id
    assert len(service.list_documents("verein-demo")) == 1
    assert any("knowledge_web_page_unchanged" in r.getMessage() for r in caplog.records)


def test_search_uses_only_the_current_version_and_refetch_targets_the_stored_address(tmp_path):
    service = _service(tmp_path, [_page("<title>Heizung</title><p>Alte Förderung für Ölkessel.</p>"),
                                  _page("<title>Heizung</title><p>Neue Förderung für Wärmepumpen.</p>")])
    first = service.add_web_page("verein-demo", URL, "http://fake")

    refreshed = service.refetch_web_page("verein-demo", first.document_id, "http://fake")
    passages = service.search(_web_pack(), "Förderung", "http://fake")

    assert refreshed.document_id != first.document_id
    assert [p.document_id for p in passages] == [refreshed.document_id]
    assert passages[0].content_hash == refreshed.content_hash
    assert [d.document_id for d in service.superseded_documents("verein-demo")] == [first.document_id]
    with pytest.raises(KnowledgeError, match="Webseiten"):
        service.refetch_web_page("verein-demo", "doc_unbekannt", "http://fake")


def test_superseded_versions_are_removed_only_on_request(tmp_path):
    service = _service(tmp_path, [_page("<p>eins</p>"), _page("<p>zwei</p>"), _page("<p>drei</p>")])
    first = service.add_web_page("verein-demo", URL, "http://fake")
    second = service.add_web_page("verein-demo", URL, "http://fake")
    third = service.add_web_page("verein-demo", URL, "http://fake")

    stored = {d.document_id: d for d in service.list_documents("verein-demo")}
    assert stored[first.document_id].superseded_by == second.document_id
    assert stored[second.document_id].superseded_by == third.document_id
    assert service.pending_reindex("verein-demo") == 0

    removed = service.delete_superseded("verein-demo")

    assert removed == 2
    assert [d.document_id for d in service.list_documents("verein-demo")] == [third.document_id]
    assert service.delete_superseded("verein-demo") == 0


# --- AppTest: Domain-Admin tab Wissen ------------------------------------------

from streamlit.testing.v1 import AppTest  # noqa: E402

from guideos.domains.catalog import LocalCatalogStore, list_domain_versions  # noqa: E402
from guideos.knowledge.factory import knowledge_service_from_env  # noqa: E402
from guideos.providers.registry import get_provider  # noqa: E402


@pytest.fixture
def admin_app(tmp_path, monkeypatch):
    monkeypatch.setenv("GUIDEOS_ADMIN_ENABLED", "1")
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setattr(
        "guideos.domains.catalog.catalog_store_from_env",
        lambda **kwargs: LocalCatalogStore(tmp_path / "packs", tmp_path / "catalog.json"),
    )
    app = AppTest.from_file("app.py").run()
    app.sidebar.radio[0].set_value("Domain-Admin").run()
    ref = next(r for r in list_domain_versions() if r.domain_id == "verein-demo" and r.version == "1.0.0")
    next(item for item in app.selectbox if item.label == "Domain-Version").set_value(ref).run()
    return app


def test_admin_refetches_a_web_page_and_prunes_replaced_versions(admin_app, monkeypatch):
    contents = iter(["<title>Heizung</title><p>Alter Stand.</p>", "<title>Heizung</title><p>Neuer Stand.</p>"])

    class Response:
        headers = {"Content-Type": "text/html; charset=utf-8"}

        def __init__(self, html):
            self.html = html

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, amount):
            return self.html.encode()

        def geturl(self):
            return URL

    monkeypatch.setattr("guideos.knowledge.ingestion.request.urlopen", lambda req, timeout: Response(next(contents)))
    next(item for item in admin_app.text_input if item.label == "Adresse der Webseite").set_value(URL)
    next(b for b in admin_app.button if b.label == "Webseite abrufen und hinzufügen").click().run()
    assert all(b.label != "Ersetzte Versionen entfernen" for b in admin_app.button)

    next(b for b in admin_app.button if b.label == "Neu abrufen").click().run()

    assert not [item.value for item in admin_app.error]
    assert any("Neue Quellversion" in item.value for item in admin_app.success)
    documents = knowledge_service_from_env(get_provider("ollama")).list_documents("verein-demo")
    assert sorted(bool(d.superseded_by) for d in documents) == [False, True]
    selector = next(item for item in admin_app.selectbox if item.label == "Dokument")
    assert any("(ersetzt)" in option for option in selector.options)

    next(b for b in admin_app.button if b.label == "Ersetzte Versionen entfernen").click().run()

    remaining = knowledge_service_from_env(get_provider("ollama")).list_documents("verein-demo")
    assert len(remaining) == 1 and remaining[0].superseded_by is None
