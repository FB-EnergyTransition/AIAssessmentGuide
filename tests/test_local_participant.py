from guideos.pilot.access import local_participant_id


def test_local_participant_defaults_to_local(monkeypatch):
    monkeypatch.delenv("GUIDEOS_PARTICIPANT_ID", raising=False)
    assert local_participant_id() == "local"


def test_local_participant_reads_env(monkeypatch):
    monkeypatch.setenv("GUIDEOS_PARTICIPANT_ID", "  beispiel-test  ")
    assert local_participant_id() == "beispiel-test"


def test_local_participant_rejects_blank(monkeypatch):
    monkeypatch.setenv("GUIDEOS_PARTICIPANT_ID", "   ")
    assert local_participant_id() == "local"


def test_normalize_participant_name_strips_and_rejects_blank():
    from guideos.pilot.access import normalize_participant_name

    assert normalize_participant_name("  Anna Muster ") == "Anna Muster"
    assert normalize_participant_name("   ") is None
    assert normalize_participant_name(None) is None


def test_participant_choices_keep_the_default_first_and_sort_the_rest():
    from guideos.pilot.access import participant_choices

    assert participant_choices(["zoe", None, "anna", "local", "anna"], "local") == [
        "local", "anna", "zoe",
    ]
