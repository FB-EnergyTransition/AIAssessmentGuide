"""Configurable retention for pilot data: unlimited by default, otherwise
sessions, profiles and conversations older than N days are removed, never a
profile with a fresh chat and never the source session of a kept profile."""

from datetime import UTC, datetime, timedelta

import pytest

from guideos.assessment.orchestrator import AssessmentOrchestrator
from guideos.assessment.repository import LocalAssessmentRepository
from guideos.conversations.repository import LocalConversationRepository
from guideos.conversations.service import ConversationService
from guideos.domains.loader import load_domain_pack
from guideos.pilot.retention import apply_retention, main, plan_retention, retention_days_from_env
from guideos.profiles.repository import LocalProfileRepository

from tests.test_streamlit_adapter import _completed_response

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=90)


@pytest.mark.parametrize("value, expected", [(None, None), ("", None), ("  ", None), ("unbegrenzt", None),
                                             ("30", 30), (" 7 ", 7)])
def test_retention_days_default_unlimited(value, expected):
    assert retention_days_from_env(value) == expected


@pytest.mark.parametrize("value", ["0", "-3", "abc", "1.5"])
def test_retention_days_rejects_nonsense(value):
    with pytest.raises(ValueError, match="GUIDEOS_RETENTION_DAYS"):
        retention_days_from_env(value)


def _stores(tmp_path):
    return (LocalAssessmentRepository(tmp_path / "assessments.json"),
            LocalProfileRepository(tmp_path / "profiles.json"),
            LocalConversationRepository(tmp_path / "conversations.json"))


def _age(repository, item, updated_at, save):
    aged = item.model_copy(update={"updated_at": updated_at})
    save(aged)
    return aged


def test_plan_keeps_profiles_with_fresh_chats_and_their_source_sessions(tmp_path):
    assessments, profiles, conversations = _stores(tmp_path)
    orchestrator = AssessmentOrchestrator(load_domain_pack(), assessments, profiles=profiles)
    old_session = assessments.get(_completed_response(orchestrator).session_id)
    source = assessments.get(_completed_response(orchestrator).session_id)  # profile source
    service = ConversationService(conversations, profiles, provider=None)
    fresh_chat = service.create("verein-demo")
    old_chat = service.create("verein-demo")
    assessments.save(old_session.model_copy(update={"updated_at": OLD}))
    assessments.save(source.model_copy(update={"updated_at": OLD}))
    profile = profiles.get("verein-demo")
    profiles.save(profile.model_copy(update={"updated_at": OLD}))
    conversations.save(old_chat.model_copy(update={"updated_at": OLD}))

    plan = plan_retention(assessments.list(), profiles.list(), conversations.list(), NOW - timedelta(days=30))

    assert plan.conversation_ids == [old_chat.conversation_id]
    assert plan.profile_ids == []  # a fresh chat keeps the profile
    assert plan.session_ids == [old_session.session_id]  # the kept profile keeps its source session

    conversations.save(fresh_chat.model_copy(update={"updated_at": OLD}))
    plan = plan_retention(assessments.list(), profiles.list(), conversations.list(), NOW - timedelta(days=30))
    assert sorted(plan.conversation_ids) == sorted([fresh_chat.conversation_id, old_chat.conversation_id])
    assert plan.profile_ids == [profile.profile_id]
    assert sorted(plan.session_ids) == sorted([old_session.session_id, source.session_id])


def test_apply_retention_is_a_no_op_when_unlimited_and_deletes_only_with_apply(tmp_path):
    assessments, profiles, conversations = _stores(tmp_path)
    orchestrator = AssessmentOrchestrator(load_domain_pack(), assessments, profiles=profiles)
    session = assessments.get(_completed_response(orchestrator).session_id)
    assessments.save(session.model_copy(update={"updated_at": OLD}))
    profiles.save(profiles.get("verein-demo").model_copy(update={"updated_at": OLD}))

    assert apply_retention(assessments, profiles, conversations, None, now=NOW, apply=True).is_empty
    assert len(assessments.list()) == 1
    dry = apply_retention(assessments, profiles, conversations, 30, now=NOW, apply=False)
    assert dry.session_ids == [session.session_id] and len(assessments.list()) == 1
    applied = apply_retention(assessments, profiles, conversations, 30, now=NOW, apply=True)
    assert applied.session_ids == [session.session_id] and applied.profile_ids
    assert assessments.list() == [] and profiles.list() == []


def test_cli_reads_env_and_reports_in_german(tmp_path, monkeypatch, capsys):
    assessments, profiles, conversations = _stores(tmp_path)
    orchestrator = AssessmentOrchestrator(load_domain_pack(), assessments, profiles=profiles)
    session = assessments.get(_completed_response(orchestrator).session_id)
    assessments.save(session.model_copy(update={"updated_at": OLD}))
    profiles.save(profiles.get("verein-demo").model_copy(update={"updated_at": OLD}))
    monkeypatch.setenv("GUIDEOS_ASSESSMENT_PATH", str(tmp_path / "assessments.json"))
    monkeypatch.setenv("GUIDEOS_PROFILE_PATH", str(tmp_path / "profiles.json"))
    monkeypatch.setenv("GUIDEOS_CONVERSATION_PATH", str(tmp_path / "conversations.json"))
    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)

    monkeypatch.delenv("GUIDEOS_RETENTION_DAYS", raising=False)
    assert main(["--apply"]) == 0
    assert "unbegrenzt" in capsys.readouterr().out
    assert len(assessments.list()) == 1

    monkeypatch.setenv("GUIDEOS_RETENTION_DAYS", "30")
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "Probelauf" in out and "1 Session" in out
    assert len(assessments.list()) == 1
    assert main(["--apply"]) == 0
    assert assessments.list() == []


def test_retention_deletes_datasets_of_expired_profiles_on_the_server(tmp_path):
    from datetime import UTC, datetime, timedelta

    from guideos.assessment.repository import LocalAssessmentRepository
    from guideos.conversations.repository import LocalConversationRepository
    from guideos.pilot.retention import apply_retention
    from guideos.profiles.repository import LocalProfileRepository
    from guideos.profiles.service import delete_datasets_on_server
    from guideos.tools.models import DatasetRef, ToolServer
    from guideos.tools.service import ToolService
    from guideos.tools.store import LocalToolServerStore
    from tests.tool_fakes import DELETE_SPEC, IMPORT_SPEC, ANALYZE_SPEC, FakeToolClient
    from tests.pack_fixtures import TOOLS_PACK_ANSWERS, tools_pack

    pack = tools_pack()
    assessments = LocalAssessmentRepository(tmp_path / "assessments.json")
    profiles = LocalProfileRepository(tmp_path / "profiles.json")
    conversations = LocalConversationRepository(tmp_path / "conversations.json")
    orchestrator = AssessmentOrchestrator(pack, assessments, profiles=profiles)
    response = orchestrator.start()
    for question_id, value in TOOLS_PACK_ANSWERS:
        response = orchestrator.submit(response.session_id, question_id, value)
    orchestrator.complete(response.session_id)
    profile = profiles.get(pack.domain_id)
    profile.dataset = DatasetRef(dataset_id="ds_alt", content_hash="c" * 64, filename="alt.csv",
                                 imported_at=datetime(2026, 1, 1, tzinfo=UTC), server_name="demo",
                                 tool_name="import_dataset")
    profiles.save(profile)
    store = LocalToolServerStore(tmp_path / "mcp_servers.json")
    server = ToolServer.create(pack.domain_id, "demo", "http://127.0.0.1:8600/mcp", "token-0123456789abcdef")
    store.save(server.model_copy(update={"tool_specs": [ANALYZE_SPEC, IMPORT_SPEC, DELETE_SPEC],
                                         "allowed_tools": ["delete_dataset"]}))
    client = FakeToolClient()
    tools = ToolService(store, client)

    assert delete_datasets_on_server(profiles.list(), tools, load_pack=lambda d, v: pack) == 1
    assert client.calls == [("delete_dataset", {"owner": "local", "dataset_id": "ds_alt"})]

    future = datetime.now(UTC) + timedelta(days=400)
    plan = apply_retention(assessments, profiles, conversations, 30, now=future, apply=True, tools=tools,
                           load_pack=lambda d, v: pack)
    assert plan.profile_ids == [profile.profile_id]
    assert client.calls[-1] == ("delete_dataset", {"owner": "local", "dataset_id": "ds_alt"})
    assert profiles.list() == []


def test_retention_deletes_datasets_of_expired_sessions_but_keeps_those_of_kept_profiles(tmp_path):
    from guideos.tools.datasets import DatasetImporter
    from guideos.tools.models import DatasetRef
    from tests.test_file_questions import CSV, UPLOAD, _pack, _tools

    pack = _pack()
    tools, client = _tools(tmp_path, pack)
    assessments, profiles, conversations = _stores(tmp_path)
    orchestrator = AssessmentOrchestrator(pack, assessments, profiles=profiles,
                                          dataset_importer=DatasetImporter(tools))
    # Source session of the kept profile (fresh chat), with the advisory dataset.
    source = orchestrator.start().session_id
    orchestrator.submit(source, "pv_system", False)
    orchestrator.submit(source, "dataset_file", UPLOAD)  # ds_1
    orchestrator.complete(source)
    profile = profiles.get(pack.domain_id)
    profile.dataset = DatasetRef.model_validate(profile.answers["dataset_file"].normalized_value)
    profiles.save(profile.model_copy(update={"updated_at": OLD}))
    service = ConversationService(conversations, profiles, provider=None, load_pack=lambda d, v: pack)
    service.create(pack.domain_id)  # fresh: keeps the profile and its source session
    # An abandoned assessment with its own dataset.
    abandoned = orchestrator.start().session_id
    orchestrator.submit(abandoned, "pv_system", False)
    orchestrator.submit(abandoned, "dataset_file", {"filename": "alt.csv", "content": CSV})  # ds_2
    assessments.save(assessments.get(abandoned).model_copy(update={"updated_at": OLD}))
    calls = len(client.calls)

    plan = apply_retention(assessments, profiles, conversations, 30, now=NOW, apply=True, tools=tools,
                           load_pack=lambda d, v: pack)

    assert plan.session_ids == [abandoned]
    assert plan.profile_ids == []
    assert client.calls[calls:] == [("delete_dataset", {"owner": "local", "dataset_id": "ds_2"})]
    assert profiles.get(pack.domain_id).dataset.dataset_id == "ds_1"
