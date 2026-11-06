"""File questions (decisions of 2026-10-20): the orchestrator
imports an uploaded file through the pack's dataset tool when the answer is
accepted, stores only the dataset reference, and deletes datasets on the
server when answers are replaced, skipped, hidden or their session is
deleted."""

from datetime import UTC, datetime

import pytest

from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.conversations.repository import LocalConversationRepository
from guideos.domains.loader import validate_domain_pack_data
from guideos.profiles.repository import LocalProfileRepository
from guideos.profiles.service import (
    dataset_refs,
    delete_profile_data,
    delete_session_data,
)
from guideos.tools.datasets import DatasetImporter
from guideos.tools.models import DatasetRef, ToolCall, ToolError, ToolResult, ToolServer
from guideos.tools.service import ToolService
from guideos.tools.store import LocalToolServerStore
from tests.tool_fakes import DELETE_SPEC, IMPORT_SPEC, ANALYZE_SPEC, FakeToolClient

CSV = "timestamp,value\n2025-01-01T00:00,0.1\n"
UPLOAD = {"filename": "Export-AT0000000000000000000000000000000.csv", "content": CSV, "format_hint": "csv"}


def _pack(tools=None, *, with_files=True, after_import=None):
    file_questions = [
        {"question_id": "dataset_file", "field_key": "dataset_file", "text": "Messdaten?",
         "field_type": "file", "required": False, "upload": {}},
        {"question_id": "second_file", "field_key": "second_file", "text": "Zweite Datei?",
         "field_type": "file", "required": False,
         "visible_if": {"field_key": "pv_system", "operator": "equals", "value": True},
         "upload": {"arguments": {"purpose": "previous_year"}, "use_in_advisory": False}},
    ]
    tools = {"enabled": True} if tools is None else tools
    if after_import is not None:
        tools = {**tools, "after_import": after_import}
    return validate_domain_pack_data({
        "domain_id": "file-demo", "version": "1.0.0", "name": "Datei-Demo", "description": "Test",
        "default_locale": "de",
        "questions": [
            {"question_id": "pv_system", "field_key": "pv_system", "text": "PV?", "field_type": "boolean",
             "required": True},
            *(file_questions if with_files else []),
        ],
        # A rule, so that the result chat offers knowledge recommendations
        # instead of generating them (no model call in these tests).
        "rules": [{"rule_id": "pv", "field_key": "pv_system", "operator": "equals", "value": True,
                   "reason_code": "PV", "recommendation_ids": ["pv-advice"]}],
        "recommendations": [{"recommendation_id": "pv-advice", "priority": 1, "title": "PV nutzen",
                             "message": "Eigenverbrauch erhöhen."}],
        "tools": tools,
    })


def _tools(tmp_path, pack):
    store = LocalToolServerStore(tmp_path / "mcp_servers.json")
    server = ToolServer.create(pack.domain_id, "demo", "http://127.0.0.1:8600/mcp",
                               "token-0123456789abcdef")
    store.save(server.model_copy(update={
        "tool_specs": [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC],
        "allowed_tools": ["import_dataset", "delete_dataset", "analyze_dataset"],
    }))
    client = FakeToolClient()
    return ToolService(store, client, clock=lambda: datetime(2026, 10, 20, 12, tzinfo=UTC)), client


def _setup(tmp_path, *, importer=True, participant_id=None, pack=None):
    pack = pack or _pack()
    tools, client = _tools(tmp_path, pack)
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    orchestrator = AssessmentOrchestrator(
        pack, assessments, profiles=profiles,
        dataset_importer=DatasetImporter(tools) if importer else None,
    )
    response = orchestrator.start()
    if participant_id is not None:
        session = assessments.get(response.session_id)
        session.participant_id = participant_id
        assessments.save(session)
    return orchestrator, client, tools, assessments, profiles, response.session_id


def test_submit_file_imports_through_the_dataset_tool_and_stores_the_reference(tmp_path):
    orchestrator, client, _, assessments, _, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", True)

    response = orchestrator.submit(session_id, "dataset_file", UPLOAD)

    assert response.validation_errors == []
    assert client.calls == [("import_dataset", {
        "owner": "local", "filename": "Export-AT….csv", "content": CSV, "format_hint": "csv",
    })]
    answer = response.accepted_answer
    assert answer.raw_answer == "Export-AT….csv"
    reference = DatasetRef.model_validate(answer.normalized_value)
    assert reference.dataset_id == "ds_1"
    assert reference.field_key == "dataset_file"
    assert reference.filename == "Export-AT….csv"
    assert reference.server_name == "demo" and reference.tool_name == "import_dataset"
    assert reference.details["interval_count"] == 2976
    assert "content" not in answer.normalized_value
    session = assessments.get(session_id)
    assert session.events[-2].event_type == "dataset_imported"
    assert session.events[-2].payload == {"field_key": "dataset_file", "dataset_id": "ds_1",
                                          "server": "demo"}
    assert response.current_question.question_id == "second_file"

    # The second file question passes its fixed arguments to the same tool.
    response = orchestrator.submit(session_id, "second_file", {"filename": "vorjahr.csv", "content": CSV})
    assert client.calls[-1][1]["purpose"] == "previous_year"
    assert client.calls[-1][1]["format_hint"] == "auto"
    assert response.status == "review"
    assert response.answers["second_file"].normalized_value["dataset_id"] == "ds_2"


def test_submit_file_uses_the_participant_as_owner(tmp_path):
    orchestrator, client, _, _, _, session_id = _setup(tmp_path, participant_id="p-7")
    orchestrator.submit(session_id, "pv_system", False)

    orchestrator.submit(session_id, "dataset_file", UPLOAD)

    assert client.calls[-1][1]["owner"] == "p-7"


def test_submit_file_reports_tool_errors_as_validation_issues(tmp_path):
    orchestrator, client, _, assessments, _, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", False)

    client.fail_with = ToolError("tool_error", "Die Kopfzeile wurde nicht erkannt.", {"code": "invalid_format"})
    response = orchestrator.submit(session_id, "dataset_file", UPLOAD)
    assert [issue.code for issue in response.validation_errors] == ["tool_error"]
    assert "Kopfzeile" in response.validation_errors[0].message
    assert response.validation_errors[0].field_key == "dataset_file"
    assert "dataset_file" not in assessments.get(session_id).answers
    assert response.current_question.question_id == "dataset_file"

    client.fail_with = ToolError("tool_server_unavailable", "Server weg.")
    response = orchestrator.submit(session_id, "dataset_file", UPLOAD)
    assert [issue.code for issue in response.validation_errors] == ["tool_server_unavailable"]

    client.fail_with = ToolError("tool_timeout", "Zeitüberschreitung.")
    response = orchestrator.submit(session_id, "dataset_file", UPLOAD)
    assert [issue.code for issue in response.validation_errors] == ["tool_server_unavailable"]

    # Shape errors are caught before any tool call.
    client.fail_with = None
    calls = len(client.calls)
    response = orchestrator.submit(session_id, "dataset_file", {"filename": "a.pdf", "content": "x"})
    assert [issue.code for issue in response.validation_errors] == ["file_extension"]
    assert len(client.calls) == calls

    # Skipping the optional question stores no value and calls no tool.
    response = orchestrator.submit(session_id, "dataset_file", None)
    assert response.accepted_answer.normalized_value is None
    assert len(client.calls) == calls


def test_submit_file_without_a_configured_importer_keeps_the_question_open(tmp_path):
    orchestrator, client, _, _, _, session_id = _setup(tmp_path, importer=False)
    orchestrator.submit(session_id, "pv_system", False)

    response = orchestrator.submit(session_id, "dataset_file", UPLOAD)

    assert [issue.code for issue in response.validation_errors] == ["tool_server_unavailable"]
    assert client.calls == []
    assert response.current_question.question_id == "dataset_file"


def test_replacing_or_skipping_a_file_answer_deletes_the_old_dataset(tmp_path):
    orchestrator, client, _, assessments, _, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", False)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)

    response = orchestrator.revise(session_id, "dataset_file", {"filename": "neu.csv", "content": CSV})

    assert response.answers["dataset_file"].normalized_value["dataset_id"] == "ds_2"
    assert [call[0] for call in client.calls] == ["import_dataset", "import_dataset",
                                                  "delete_dataset"]
    assert client.calls[-1][1] == {"owner": "local", "dataset_id": "ds_1"}
    events = [event for event in assessments.get(session_id).events if event.event_type == "dataset_deleted"]
    assert events[-1].payload == {"field_key": "dataset_file", "dataset_id": "ds_1"}

    # Revising with the stored reference keeps the dataset.
    response = orchestrator.revise(session_id, "dataset_file", response.answers["dataset_file"].normalized_value)
    assert response.answers["dataset_file"].normalized_value["dataset_id"] == "ds_2"
    assert len(client.calls) == 3

    # A failed replacement keeps the old dataset and reference.
    client.fail_with = ToolError("tool_error", "kaputt")
    response = orchestrator.revise(session_id, "dataset_file", {"filename": "x.csv", "content": CSV})
    assert [issue.code for issue in response.validation_errors] == ["tool_error"]
    assert assessments.get(session_id).answers["dataset_file"].normalized_value["dataset_id"] == "ds_2"
    client.fail_with = None

    response = orchestrator.revise(session_id, "dataset_file", None)
    assert response.answers["dataset_file"].normalized_value is None
    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_2"})

    # A deletion the server refuses is logged, the answer changes anyway.
    orchestrator.revise(session_id, "dataset_file", UPLOAD)
    client.fail_with = ToolError("tool_server_unavailable", "weg")
    orchestrator.revise(session_id, "dataset_file", None)
    client.fail_with = None
    assert assessments.get(session_id).answers["dataset_file"].normalized_value is None


def test_hidden_file_answers_are_deleted_on_the_server(tmp_path):
    orchestrator, client, _, assessments, _, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", True)
    orchestrator.submit(session_id, "dataset_file", None)
    orchestrator.submit(session_id, "second_file", {"filename": "vorjahr.csv", "content": CSV})

    orchestrator.revise(session_id, "pv_system", False)

    assert "second_file" not in assessments.get(session_id).answers
    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_1"})


def test_dataset_refs_reads_only_imported_file_answers(tmp_path):
    orchestrator, _, _, assessments, _, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", True)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    orchestrator.submit(session_id, "second_file", None)

    refs = dataset_refs(assessments.get(session_id))

    assert [ref.dataset_id for ref in refs] == ["ds_1"]
    assert refs[0].field_key == "dataset_file"


def test_delete_session_data_deletes_datasets_and_forgets_them_on_the_profile(tmp_path):
    orchestrator, client, tools, assessments, profiles, session_id = _setup(tmp_path)
    pack = orchestrator.pack
    orchestrator.submit(session_id, "pv_system", True)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    orchestrator.submit(session_id, "second_file", {"filename": "vorjahr.csv", "content": CSV})
    orchestrator.complete(session_id)
    profile = profiles.get(pack.domain_id)
    profile.dataset = DatasetRef.model_validate(profile.answers["dataset_file"].normalized_value)
    profiles.save(profile)
    calls = len(client.calls)

    deletion = delete_session_data(assessments, profiles, session_id, tools=tools, load_pack=lambda d, v: pack)

    assert deletion.session_id == session_id
    assert deletion.dataset_ids == ["ds_1", "ds_2"]
    assert deletion.profile_changed is True
    assert client.calls[calls:] == [("delete_dataset", {"owner": "local", "dataset_id": "ds_1"}),
                                    ("delete_dataset", {"owner": "local", "dataset_id": "ds_2"})]
    with pytest.raises(LookupError):
        assessments.get(session_id)
    profile = profiles.get(pack.domain_id)
    assert profile.dataset is None and profile.tool_results == []
    assert profile.answers["dataset_file"].normalized_value is None
    assert profile.answers["dataset_file"].origin == "chat_update"
    assert profile.answers["second_file"].normalized_value is None
    assert profile.events[-1].event_type == "dataset_deleted"
    assert profile.events[-1].payload == {"session_id": session_id, "dataset_ids": ["ds_1", "ds_2"]}

    # Without datasets nothing but the session goes; without a profile no error.
    response = orchestrator.start()
    orchestrator.submit(response.session_id, "pv_system", False)
    deletion = delete_session_data(assessments, profiles, response.session_id, tools=tools,
                                   load_pack=lambda d, v: pack)
    assert deletion.dataset_ids == [] and deletion.profile_changed is False


def test_delete_session_data_without_tools_still_deletes_the_session(tmp_path):
    orchestrator, client, _, assessments, profiles, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", False)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    client.fail_with = ToolError("tool_server_unavailable", "weg")

    deletion = delete_session_data(assessments, profiles, session_id, tools=orchestrator.dataset_importer.tools,
                                   load_pack=lambda d, v: orchestrator.pack)

    assert deletion.dataset_ids == ["ds_1"]
    with pytest.raises(LookupError):
        assessments.get(session_id)


def test_delete_profile_data_deletes_datasets_of_the_domain_sessions(tmp_path):
    orchestrator, client, tools, assessments, profiles, session_id = _setup(tmp_path)
    pack = orchestrator.pack
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    orchestrator.submit(session_id, "pv_system", False)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    orchestrator.complete(session_id)
    # An unfinished second assessment with its own dataset.
    second = orchestrator.start().session_id
    orchestrator.submit(second, "pv_system", False)
    orchestrator.submit(second, "dataset_file", UPLOAD)
    profile = profiles.get(pack.domain_id)
    profile.dataset = DatasetRef.model_validate(profile.answers["dataset_file"].normalized_value)
    profiles.save(profile)
    calls = len(client.calls)

    delete_profile_data(profiles, conversations, assessments, pack.domain_id, tools=tools,
                        load_pack=lambda d, v: pack)

    deleted = sorted(call[1]["dataset_id"] for call in client.calls[calls:])
    assert deleted == ["ds_1", "ds_2"]
    assert assessments.list() == []


# --- Step 3: profile, result chat and panel -------------------------------

ANALYSIS = {"tool": "analyze_dataset", "arguments": {"threshold": 10}, "note": "Hinweis."}


def _service(tmp_path, orchestrator, profiles, tools):
    from guideos.conversations.service import ConversationService
    from tests.tool_fakes import FakeToolProvider

    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    service = ConversationService(conversations, profiles, FakeToolProvider(),
                                  load_pack=lambda d, v: orchestrator.pack, tools=tools)
    return service, conversations


def test_complete_sets_the_profile_dataset_from_the_advisory_file_answer(tmp_path):
    orchestrator, client, _, _, profiles, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", True)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    orchestrator.submit(session_id, "second_file", {"filename": "vorjahr.csv", "content": CSV})

    orchestrator.complete(session_id)

    profile = profiles.get("file-demo")
    assert profile.dataset.dataset_id == "ds_1" and profile.dataset.field_key == "dataset_file"
    assert profile.tool_results == []
    assert profile.answers["second_file"].normalized_value["dataset_id"] == "ds_2"
    assert [call[0] for call in client.calls] == ["import_dataset", "import_dataset"]


def test_a_newer_assessment_replaces_the_profile_dataset_and_deletes_the_old_one(tmp_path):
    orchestrator, client, _, _, profiles, first = _setup(tmp_path)
    orchestrator.submit(first, "pv_system", False)
    orchestrator.submit(first, "dataset_file", UPLOAD)  # ds_1
    orchestrator.complete(first)
    profile = profiles.get("file-demo")
    profile.tool_results = [ToolResult(call=ToolCall(name="analyze_dataset", arguments={"dataset_id": "ds_1"}),
                                       server_name="demo", structured_content={"months": []})]
    profiles.save(profile)

    second = orchestrator.start().session_id
    orchestrator.submit(second, "pv_system", False)
    orchestrator.submit(second, "dataset_file", {"filename": "neu.csv", "content": CSV})  # ds_2
    orchestrator.complete(second)

    profile = profiles.get("file-demo")
    assert profile.dataset.dataset_id == "ds_2"
    assert profile.tool_results == []
    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_1"})
    assert profile.events[-2].event_type == "replaced_by_session"
    assert profile.events[-1].event_type == "dataset_deleted"
    assert profile.events[-1].payload["dataset_id"] == "ds_1"

    # Skipping the upload in a newer assessment drops the dataset.
    third = orchestrator.start().session_id
    orchestrator.submit(third, "pv_system", False)
    orchestrator.submit(third, "dataset_file", None)
    orchestrator.complete(third)
    profile = profiles.get("file-demo")
    assert profile.dataset is None
    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_2"})


def test_packs_without_file_questions_keep_the_panel_dataset_across_assessments(tmp_path):
    orchestrator, client, tools, _, profiles, first = _setup(tmp_path, pack=_pack(with_files=False))
    orchestrator.submit(first, "pv_system", False)
    orchestrator.complete(first)
    service, _ = _service(tmp_path, orchestrator, profiles, tools)
    service.import_dataset("file-demo", "panel.csv", CSV.encode("utf-8"))

    second = orchestrator.start().session_id
    orchestrator.submit(second, "pv_system", True)
    orchestrator.complete(second)

    profile = profiles.get("file-demo")
    assert profile.dataset.dataset_id == "ds_1"
    assert [call[0] for call in client.calls] == ["import_dataset"]


def test_result_chat_announces_the_session_dataset_and_runs_the_first_analysis(tmp_path):
    from guideos.conversations.service import ANALYSIS_INTRO, DATASET_IMPORTED_INTRO, RESULT_CONVERSATION_TITLE

    orchestrator, client, tools, _, profiles, session_id = _setup(tmp_path, pack=_pack(after_import=ANALYSIS))
    orchestrator.submit(session_id, "pv_system", True)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    orchestrator.submit(session_id, "second_file", None)
    orchestrator.complete(session_id)
    service, conversations = _service(tmp_path, orchestrator, profiles, tools)

    conversation = service.ensure_result_conversation("file-demo", "http://fake", "fake")

    stored = conversations.get(conversation.conversation_id)
    assert stored.title == RESULT_CONVERSATION_TITLE
    texts = [message.content for message in stored.messages]
    imported = next(text for text in texts if text.startswith(DATASET_IMPORTED_INTRO))
    assert "Export-AT….csv" in imported and "2976 Werte" in imported
    analysis = stored.messages[-1]
    assert analysis.content.startswith(ANALYSIS_INTRO) and analysis.content.endswith("Hinweis.")
    assert analysis.sections[0].sources[0].kind == "tool"
    assert client.calls[-1][1] == {"owner": "local", "dataset_id": "ds_1", "threshold": 10}
    profile = profiles.get("file-demo")
    assert [result.call.name for result in profile.tool_results] == ["analyze_dataset"]

    # Calling it again changes nothing.
    again = service.ensure_result_conversation("file-demo", "http://fake", "fake")
    assert len(conversations.get(again.conversation_id).messages) == len(stored.messages)
    assert client.calls[-1][0] == "analyze_dataset" and len([c for c in client.calls
                                                                    if c[0] == "analyze_dataset"]) == 1


def test_result_chat_announces_a_session_dataset_without_analysis_once(tmp_path):
    from guideos.conversations.service import DATASET_IMPORTED_INTRO

    orchestrator, client, tools, _, profiles, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", True)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    orchestrator.submit(session_id, "second_file", None)
    orchestrator.complete(session_id)
    service, conversations = _service(tmp_path, orchestrator, profiles, tools)

    conversation = service.ensure_result_conversation("file-demo", "http://fake", "fake")

    texts = [message.content for message in conversations.get(conversation.conversation_id).messages]
    assert sum(text.startswith(DATASET_IMPORTED_INTRO) for text in texts) == 1
    assert [call[0] for call in client.calls] == ["import_dataset"]


def test_panel_import_and_deletion_keep_the_file_answer_in_step(tmp_path):
    orchestrator, client, tools, _, profiles, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", False)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)  # ds_1
    orchestrator.complete(session_id)
    service, _ = _service(tmp_path, orchestrator, profiles, tools)

    profile = service.import_dataset("file-demo", "panel.csv", CSV.encode("utf-8"))

    answer = profile.answers["dataset_file"]
    assert answer.normalized_value["dataset_id"] == "ds_2" == profile.dataset.dataset_id
    assert profile.dataset.field_key == "dataset_file"
    assert answer.raw_answer == "panel.csv" and answer.origin == "chat_update"
    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_1"})

    profile = service.delete_dataset("file-demo")
    assert profile.dataset is None
    assert profile.answers["dataset_file"].normalized_value is None
    assert profile.answers["dataset_file"].origin == "chat_update"


def test_context_facts_show_file_answers_without_the_import_details(tmp_path):
    from guideos.context import build_advisory_context

    orchestrator, _, _, _, profiles, session_id = _setup(tmp_path)
    orchestrator.submit(session_id, "pv_system", False)
    orchestrator.submit(session_id, "dataset_file", UPLOAD)
    orchestrator.complete(session_id)
    profile = profiles.get("file-demo")

    context = build_advisory_context(orchestrator.pack, profile)

    fact = next(fact for fact in context.facts if fact.field_key == "dataset_file")
    assert fact.normalized_value == {"dataset_id": "ds_1", "filename": "Export-AT….csv",
                                     "summary": "Messdaten importiert: 2976 Werte von 01.01.2025 "
                                                "bis 31.01.2025."}
    assert fact.display_value.startswith("Export-AT….csv (Messdaten importiert")
    assert context.dataset == profile.dataset


def test_response_names_the_format_hints_of_the_current_file_question(tmp_path):
    """The current file question carries the import tool's format_hint enum
    ("auto" without one), so a client without the pack can offer the formats."""
    orchestrator, _, tools, _, _, session_id = _setup(tmp_path)
    response = orchestrator.submit(session_id, "pv_system", False)
    assert response.current_question.field_type == "file"
    registered = (IMPORT_SPEC.input_schema.get("properties") or {}).get("format_hint", {}).get("enum")
    assert response.upload_formats == [str(item) for item in registered]
    assert [q.question_id for q in response.answered_questions] == ["pv_system"]

    server = tools.store.list("file-demo")[0]
    with_enum = IMPORT_SPEC.model_copy(update={"input_schema": {
        **IMPORT_SPEC.input_schema,
        "properties": {**(IMPORT_SPEC.input_schema.get("properties") or {}),
                       "format_hint": {"type": "string", "enum": ["auto", "csv", "xlsx"]}},
    }})
    tools.store.save(server.model_copy(update={"tool_specs": [ANALYZE_SPEC, with_enum, DELETE_SPEC]}))
    assert orchestrator.get(session_id).upload_formats == ["auto", "csv", "xlsx"]

    tools.store.save(server.model_copy(update={"tool_specs": [ANALYZE_SPEC, IMPORT_SPEC.model_copy(update={"input_schema": {"type": "object", "properties": {}}}), DELETE_SPEC]}))
    assert orchestrator.get(session_id).upload_formats == ["auto"]
