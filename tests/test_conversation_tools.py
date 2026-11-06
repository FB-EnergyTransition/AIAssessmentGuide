"""Step 5b: the advisory runs the tool loop before the structured answer,
cites tool results as T1…, stores them on message and profile; datasets are
imported and deleted through the pack's dataset and delete tools."""

import json
from datetime import UTC, datetime

import pytest

from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.context import build_advisory_context
from guideos.context.prompt import build_advisory_messages
from guideos.context.sources import describe_source, resolve_sources, unverified_values
from guideos.conversations.repository import LocalConversationRepository
from guideos.conversations.service import (
    DATASET_DELETED_REPLY,
    RESULT_CONVERSATION_TITLE,
    ConversationError,
    ConversationService,
)
from guideos.domains.loader import load_domain_pack
from guideos.domains.models import ToolSettings
from guideos.profiles.repository import LocalProfileRepository
from guideos.profiles.service import delete_profile_data
from guideos.providers.base import ModelWithoutToolsError
from guideos.reports.renderer import render_profile_report
from guideos.tools.models import DatasetRef, ToolCall, ToolError, ToolResult, ToolServer
from guideos.tools.service import ToolService
from guideos.tools.store import LocalToolServerStore
from tests.pack_fixtures import PERSONAS
from tests.tool_fakes import (
    ANALYZE_OUTPUT,
    ANALYZE_SPEC,
    ANALYZE_SUMMARY,
    DELETE_SPEC,
    IMPORT_SPEC,
    FakeToolClient,
    FakeToolProvider,
)

def _pack(tools: ToolSettings | None):
    return load_domain_pack().model_copy(update={"tools": tools})


def _setup(tmp_path, provider, tools_enabled=True, allowed=("analyze_dataset", "import_dataset",
                                                               "delete_dataset")):
    pack = _pack(ToolSettings(enabled=True) if tools_enabled else None)
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    orchestrator = AssessmentOrchestrator(pack, assessments, profiles=profiles)
    response = orchestrator.start()
    for question_id, value in PERSONAS["sportverein_gefoerdert"]:
        response = orchestrator.submit(response.session_id, question_id, value)
    orchestrator.complete(response.session_id)

    store = LocalToolServerStore(tmp_path / "mcp_servers.json")
    server = ToolServer.create(pack.domain_id, "demo", "http://127.0.0.1:8600/mcp", "token-0123456789abcdef")
    store.save(server.model_copy(update={"tool_specs": [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC],
                                         "allowed_tools": list(allowed)}))
    client = FakeToolClient()
    tool_service = ToolService(store, client, clock=lambda: datetime(2026, 10, 19, 12, tzinfo=UTC))
    service = ConversationService(conversations, profiles, provider, load_pack=lambda d, v: pack,
                                  knowledge=None, tools=tool_service)
    return service, profiles, client, pack, assessments, conversations


def _import(service, filename="export.csv"):
    return service.import_dataset("verein-demo", filename, b"timestamp,value\n2025-01-01T00:00,0.1\n",
                                  format_hint="auto")


def test_import_dataset_stores_the_reference_and_posts_a_result_message(tmp_path):
    provider = FakeToolProvider()
    service, profiles, client, pack, _, conversations = _setup(tmp_path, provider)

    profile = _import(service, "Kassabuch-AT0000000000000000000000000000000-2026.csv")

    assert client.calls[0][0] == "import_dataset"
    assert client.calls[0][1]["owner"] == "local"  # local process without pilot login
    assert client.calls[0][1]["content"].startswith("timestamp,value")
    assert client.calls[0][1]["format_hint"] == "auto"
    assert isinstance(profile.dataset, DatasetRef)
    assert profile.dataset.dataset_id == "ds_1"
    assert profile.dataset.content_hash == "c" * 64
    assert profile.dataset.filename == "Kassabuch-AT…-2026.csv"
    assert profile.dataset.server_name == "demo" and profile.dataset.tool_name == "import_dataset"
    assert profile.dataset.details["interval_count"] == 2976
    assert profile.events[-1].event_type == "dataset_imported"
    assert profile.events[-1].payload == {"dataset_id": "ds_1", "server": "demo"}
    assert profiles.get(pack.domain_id).dataset == profile.dataset

    result_chat = next(c for c in conversations.list() if c.title == RESULT_CONVERSATION_TITLE)
    last = result_chat.messages[-1]
    assert last.role == "assistant"
    assert last.content.startswith("Messdaten importiert")
    assert "01.01.2025 bis 31.01.2025" in last.content
    assert [r.call.name for r in last.tool_results] == ["import_dataset"]
    assert last.sections and last.sections[0].sources[0].kind == "tool"

    # A second import replaces the dataset; the old one is deleted on the server
    # only after the new import succeeded.
    again = _import(service)
    assert again.dataset.dataset_id == "ds_2"
    assert [c[0] for c in client.calls] == ["import_dataset", "import_dataset", "delete_dataset"]
    assert client.calls[2][1] == {"owner": "local", "dataset_id": "ds_1"}

    deleted = service.delete_dataset("verein-demo")
    assert deleted.dataset is None and deleted.tool_results == []
    assert deleted.events[-1].event_type == "dataset_deleted"
    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_2"})
    assert conversations.get(result_chat.conversation_id).messages[-1].content == DATASET_DELETED_REPLY
    with pytest.raises(ConversationError) as excinfo:
        service.delete_dataset("verein-demo")
    assert excinfo.value.code == "invalid_state"


def test_import_dataset_errors_have_stable_codes(tmp_path):
    service, *_ = _setup(tmp_path, FakeToolProvider(), tools_enabled=False)
    with pytest.raises(ConversationError) as excinfo:
        _import(service)
    assert excinfo.value.code == "invalid_state"

    service, profiles, client, pack, _, _ = _setup(tmp_path / "b", FakeToolProvider())
    with pytest.raises(ConversationError) as excinfo:
        service.import_dataset("verein-demo", "x.csv", b"a" * (64 * 1024 * 1024 + 1))
    assert excinfo.value.code == "invalid_input"
    client.fail_with = ToolError("tool_error", "Die Kopfzeile wurde nicht erkannt.",
                                 {"code": "invalid_format"})
    with pytest.raises(ConversationError) as excinfo:
        _import(service)
    assert excinfo.value.code == "tool_error" and "Kopfzeile" in str(excinfo.value)
    assert profiles.get(pack.domain_id).dataset is None
    client.fail_with = ToolError("tool_server_unavailable", "Server weg.")
    with pytest.raises(ConversationError) as excinfo:
        _import(service)
    assert excinfo.value.code == "tool_error"
    with pytest.raises(ConversationError) as excinfo:
        service.import_dataset("andere-demo", "x.csv", b"x")
    assert excinfo.value.code == "profile_not_found"
    # ISO-8859-1 content (an older export) is decoded, not rejected.
    client.fail_with = None
    profile = service.import_dataset("verein-demo", "vorlage.csv",
                                     "Abrechnungsmaßeinheit;x\n".encode("iso-8859-1"))
    assert profile.dataset is not None
    assert client.calls[-1][1]["content"].startswith("Abrechnungsmaßeinheit")


def test_advisory_runs_the_tool_loop_and_cites_tool_results(tmp_path):
    provider = FakeToolProvider(turns=[[("analyze_dataset", {"dataset_id": "ds_1"})], "fertig"])
    service, profiles, client, pack, _, conversations = _setup(tmp_path, provider)
    _import(service)
    conversation = service.create(pack.domain_id)

    updated = service.send_message(conversation.conversation_id, "Wie hoch war mein Höchstwert im Jänner?",
                                   "http://fake", "fake")

    answer = updated.messages[-1]
    assert answer.role == "assistant"
    assert [r.call.name for r in answer.tool_results] == ["analyze_dataset"]
    assert answer.tool_results[0].calculation_id == "calc-analyze-1"
    assert answer.sections[0].sources[0].kind == "tool"
    assert answer.sections[0].sources[0].reference == "calc-analyze-1"
    assert answer.sections[0].unverified_values == []  # 8 is in the tool summary
    assert "_Quelle: Berechnung „analyze_dataset“_" in answer.content
    assert "Hinweis" not in answer.content

    # The loop saw the dataset and the allowed tools without owner; GuideOS set the owner.
    first_request = provider.tool_requests[0]
    assert [t["function"]["name"] for t in first_request["tools"]] == [
        "analyze_dataset", "import_dataset", "delete_dataset"]
    assert all("owner" not in t["function"]["parameters"]["properties"] for t in first_request["tools"])
    context_payload = json.loads(first_request["messages"][1]["content"])
    assert context_payload["dataset"]["dataset_id"] == "ds_1"
    assert context_payload["tool_results"] == []
    assert client.calls[-1] == ("analyze_dataset", {"dataset_id": "ds_1", "owner": "local"})
    # The structured answer got the new result as T1.
    final_payload = json.loads(provider.structured_requests[-1][1]["content"])
    assert final_payload["tool_results"][0]["source_id"] == "T1"
    assert final_payload["tool_results"][0]["tool"] == "analyze_dataset"
    assert final_payload["tool_results"][0]["result"]["months"][0]["peak"] == "8"
    # Last result per tool on the profile, for the report.
    profile = profiles.get(pack.domain_id)
    assert [r.call.name for r in profile.tool_results] == ["analyze_dataset"]
    report = render_profile_report(pack, profile).content
    assert "## Berechnungen" in report
    assert "analyze_dataset" in report and "8,00" in report
    assert "export.csv" in report

    # Earlier results stay available to the model in later turns (as T1, new ones follow).
    provider.turns = ["keine neuen Aufrufe"]
    provider.source_ids = ["T1"]
    service.send_message(conversation.conversation_id, "Und war der Monat vollständig?", "http://fake", "fake")
    payload = json.loads(provider.structured_requests[-1][1]["content"])
    assert [item["source_id"] for item in payload["tool_results"]] == ["T1"]
    assert conversations.get(conversation.conversation_id).messages[-1].tool_results == []


def test_advisory_notes_when_tools_cannot_be_used(tmp_path):
    provider = FakeToolProvider(turns=[ModelWithoutToolsError("Modell ohne Werkzeuge.")], source_ids=[])
    service, _, client, pack, _, _ = _setup(tmp_path, provider)
    _import(service)
    conversation = service.create(pack.domain_id)
    updated = service.send_message(conversation.conversation_id, "Wie hoch war mein Höchstwert?", "http://fake", "fake")
    assert "Hinweis" in updated.messages[-1].content and "Berechnungswerkzeuge" in updated.messages[-1].content
    assert updated.messages[-1].tool_results == []

    client.fail_with = ToolError("tool_server_unavailable", "Server weg.")
    provider.turns = [[("analyze_dataset", {"dataset_id": "ds_1"})], "x"]
    updated = service.send_message(conversation.conversation_id, "Noch einmal?", "http://fake", "fake")
    assert "nicht erreichbar" in updated.messages[-1].content

    # Without a dataset the loop still runs (the model may explain the upload), with the
    # dataset block empty.
    provider.turns = ["Lade zuerst Messdaten hoch."]
    provider.source_ids = []
    service.delete_dataset(pack.domain_id)
    client.fail_with = None
    service.send_message(conversation.conversation_id, "Was kostet mich der Höchstwert?", "http://fake", "fake")
    assert json.loads(provider.tool_requests[-1]["messages"][1]["content"])["dataset"] is None


def test_advisory_without_tools_block_or_tool_service_does_not_call_the_model_for_tools(tmp_path):
    provider = FakeToolProvider(source_ids=[])
    service, _, _, pack, _, _ = _setup(tmp_path, provider, tools_enabled=False)
    conversation = service.create(pack.domain_id)
    service.send_message(conversation.conversation_id, "Was ist ein Monatshöchstwert?", "http://fake", "fake")
    assert provider.tool_requests == []
    assert "tool_results" in json.loads(provider.structured_requests[-1][1]["content"])

    provider = FakeToolProvider(source_ids=[])
    service, _, _, pack, _, _ = _setup(tmp_path / "b", provider, allowed=())
    conversation = service.create(pack.domain_id)
    service.send_message(conversation.conversation_id, "Was ist ein Monatshöchstwert?", "http://fake", "fake")
    assert provider.tool_requests == []


def test_delete_profile_data_deletes_the_dataset_on_the_server(tmp_path):
    service, profiles, client, pack, assessments, conversations = _setup(tmp_path, FakeToolProvider())
    _import(service)
    tools = service.tools

    delete_profile_data(profiles, conversations, assessments, pack.domain_id, tools=tools,
                        load_pack=lambda d, v: pack)

    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_1"})
    with pytest.raises(LookupError):
        profiles.get(pack.domain_id)

    # A failing server does not block the deletion in GuideOS.
    service, profiles, client, pack, assessments, conversations = _setup(tmp_path / "b", FakeToolProvider())
    _import(service)
    client.fail_with = ToolError("tool_server_unavailable", "weg")
    delete_profile_data(profiles, conversations, assessments, pack.domain_id, tools=service.tools,
                        load_pack=lambda d, v: pack)
    with pytest.raises(LookupError):
        profiles.get(pack.domain_id)


def test_context_prompt_and_sources_carry_dataset_and_tool_results(tmp_path):
    service, profiles, _, pack, _, _ = _setup(tmp_path, FakeToolProvider())
    _import(service)
    profile = profiles.get(pack.domain_id)
    context = build_advisory_context(pack, profile)
    assert context.dataset == profile.dataset
    result = ToolResult(call=ToolCall(name="analyze_dataset", arguments={"dataset_id": "ds_1"}),
                        server_name="demo", structured_content=ANALYZE_OUTPUT, summary=ANALYZE_SUMMARY,
                        calculation_id="calc-analyze-1")
    context = context.model_copy(update={"tool_results": [result]})

    messages = build_advisory_messages(context, [], "Frage?")
    system = messages[0]["content"]
    assert "TOOL_RESULTS" in system and "DATASET" in system and "fünf Informationsarten" in system
    payload = json.loads(messages[1]["content"])
    assert payload["dataset"]["dataset_id"] == "ds_1" and "content" not in payload["dataset"]
    assert payload["tool_results"][0] == {
        "source_id": "T1", "tool": "analyze_dataset", "arguments": {"dataset_id": "ds_1"},
        "summary": ANALYZE_SUMMARY, "result": ANALYZE_OUTPUT,
    }

    sources = resolve_sources(["T1", "t1", "T9", "D1"], context, [])
    assert [s.kind for s in sources] == ["tool"]
    assert sources[0].title == "analyze_dataset" and sources[0].reference == "calc-analyze-1"
    assert sources[0].excerpt == ANALYZE_SUMMARY
    assert describe_source(sources[0]) == "Berechnung „analyze_dataset“"
    assert unverified_values("Dein Höchstwert lag bei 8 im Jänner 2025.", ["T1"], context, []) == []
    assert unverified_values("Dein Höchstwert lag bei 9,5.", ["T1"], context, []) == ["9,5"]
    # Uncited amounts that a tool result states count as verified.
    assert unverified_values("Das kostet 8 Euro.", [], context, []) == []
    assert unverified_values("Das kostet 130 Euro.", [], context, []) == ["130"]


def test_import_runs_the_packs_first_analysis_and_posts_it(tmp_path):
    from guideos.conversations.service import ANALYSIS_INTRO
    from guideos.domains.models import AfterImportSettings

    provider = FakeToolProvider()
    service, profiles, client, pack, _, conversations = _setup(tmp_path, provider)
    pack.tools = ToolSettings(enabled=True, after_import=AfterImportSettings(
        tool="analyze_dataset", arguments={"threshold": 10},
        note="Monate mit Ausgaben über 10 sind mit höheren Kosten verbunden."))

    profile = _import(service)

    assert [c[0] for c in client.calls] == ["import_dataset", "analyze_dataset"]
    assert client.calls[1][1] == {"dataset_id": "ds_1", "threshold": 10, "owner": "local"}
    assert [r.call.name for r in profile.tool_results] == ["analyze_dataset"]
    assert profiles.get(pack.domain_id).tool_results[0].calculation_id == "calc-analyze-1"
    result_chat = next(c for c in conversations.list() if c.title == RESULT_CONVERSATION_TITLE)
    analysis = result_chat.messages[-1]
    assert analysis.content.startswith(ANALYSIS_INTRO)
    assert ANALYZE_SUMMARY in analysis.content
    assert analysis.content.endswith("höheren Kosten verbunden.")
    assert [r.call.name for r in analysis.tool_results] == ["analyze_dataset"]
    assert analysis.sections[0].sources[0].kind == "tool"
    assert result_chat.messages[-2].content.startswith("Messdaten importiert")

    # A failing analysis does not undo the import; the chat says what happened.
    client.fail_with = None
    original = client.call

    def failing(server, name, arguments, timeout):
        if name == "analyze_dataset":
            raise ToolError("tool_timeout", "Zu langsam.")
        return original(server, name, arguments, timeout)

    client.call = failing
    profile = _import(service)
    assert profile.dataset.dataset_id == "ds_3" and profile.tool_results == []
    last = conversations.get(result_chat.conversation_id).messages[-1]
    assert "konnte nicht erstellt werden" in last.content and "Zu langsam" in last.content


def test_manual_conversation_sees_the_profile_analyses_once(tmp_path):
    """The first analysis lives on the profile; every chat gets it as T1…,
    deduplicated against results the conversation already carries, and new
    loop results follow."""
    provider = FakeToolProvider(turns=["fertig"], source_ids=("T1",), text="In 9 Monaten über 10.")
    service, profiles, _, pack, _, conversations = _setup(tmp_path, provider)
    _import(service)
    profile = profiles.get(pack.domain_id)
    tiers = ToolResult(call=ToolCall(name="analyze_tiers", arguments={"dataset_id": "ds_1"}),
                       server_name="demo", structured_content={"totals": {"months_above": 9}},
                       summary="Stufen: 9 Monate über 10.", calculation_id="calc-tiers-1")
    profile.tool_results = [tiers]
    profiles.save(profile)
    conversation = service.create(pack.domain_id)

    updated = service.send_message(conversation.conversation_id, "Wie viele Monate lag ich über 10?",
                                   "http://fake", "fake")

    payload = json.loads(provider.structured_requests[-1][1]["content"])
    assert [(t["source_id"], t["tool"]) for t in payload["tool_results"]] == [("T1", "analyze_tiers")]
    answer = updated.messages[-1]
    assert answer.sections[0].sources[0].kind == "tool" and answer.sections[0].sources[0].reference == "calc-tiers-1"
    assert answer.sections[0].unverified_values == []  # 9 and 10 stand in the tool result
    assert answer.tool_results == []  # nothing new was computed

    # A result the conversation carries is not repeated, a different one is added after the profile's.
    peaks = ToolResult(call=ToolCall(name="analyze_dataset", arguments={"dataset_id": "ds_1"}),
                       server_name="demo", structured_content=ANALYZE_OUTPUT, summary=ANALYZE_SUMMARY,
                       calculation_id="calc-analyze-1")
    stored = conversations.get(conversation.conversation_id)
    stored.messages[-1].tool_results = [tiers, peaks]
    conversations.save(stored)
    provider.turns = ["fertig"]
    service.send_message(conversation.conversation_id, "Und der Höchstwert?", "http://fake", "fake")
    payload = json.loads(provider.structured_requests[-1][1]["content"])
    assert [t["source_id"] + " " + t["tool"] for t in payload["tool_results"]] == [
        "T1 analyze_tiers", "T2 analyze_dataset"]
