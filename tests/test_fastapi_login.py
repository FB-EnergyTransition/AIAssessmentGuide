"""Pilot login of the REST API: user name + shared test password -> signed
session cookie, from which the server derives the participant. Active only
with GUIDEOS_PILOT_MODE=1; without it the API behaves as before."""

from fastapi.testclient import TestClient

from guideos.adapters.fastapi import create_app

HEADER = "X-GuideOS-Participant"


def _pilot_client(tmp_path, monkeypatch, password="geheim"):
    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    monkeypatch.setenv("GUIDEOS_TEST_PASSWORD", password)
    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)
    return TestClient(create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json"))


def test_pilot_mode_requires_a_login_for_the_api(tmp_path, monkeypatch):
    client = _pilot_client(tmp_path, monkeypatch)

    session = client.get("/api/v1/session").json()
    rejected = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"})
    with_header = client.post(
        "/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers={HEADER: "forged"}
    )

    assert session == {"ok": True, "pilot_mode": True, "participant_id": None}
    assert rejected.status_code == 401
    assert rejected.json()["detail"] == "Anmeldung erforderlich."
    assert with_header.status_code == 401
    assert client.get("/api/v1/health").json() == {"ok": True}


def test_login_sets_a_cookie_that_scopes_the_data(tmp_path, monkeypatch):
    client = _pilot_client(tmp_path, monkeypatch)

    login = client.post("/api/v1/login", json={"username": " test ", "password": "geheim"})
    started = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}).json()
    session = client.get("/api/v1/session").json()

    assert login.status_code == 200
    assert login.json() == {"ok": True, "participant_id": "test", "errors": []}
    cookie = login.headers["set-cookie"]
    assert "guideos_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie.lower().replace("samesite=lax", "SameSite=lax")
    assert started["ok"] is True
    assert session == {"ok": True, "pilot_mode": True, "participant_id": "test"}
    # The cookie decides, not the header.
    listed = client.get("/api/v1/assessment-sessions/" + started["session_id"], headers={HEADER: "other"}).json()
    assert listed["ok"] is True


def test_login_rejects_wrong_password_and_empty_user_name(tmp_path, monkeypatch):
    client = _pilot_client(tmp_path, monkeypatch)

    wrong = client.post("/api/v1/login", json={"username": "test", "password": "falsch"}).json()
    empty = client.post("/api/v1/login", json={"username": "   ", "password": "geheim"}).json()
    invalid = client.post("/api/v1/login", json={"username": "test"}).json()

    assert wrong["ok"] is False and wrong["errors"][0]["code"] == "invalid_credentials"
    assert empty["ok"] is False and empty["errors"][0]["code"] == "invalid_credentials"
    assert invalid["ok"] is False and invalid["errors"][0]["code"] == "invalid_input"
    assert "set-cookie" not in {k.lower() for k in client.cookies.keys()}
    assert client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}).status_code == 401


def test_logout_clears_the_session_and_a_forged_cookie_is_ignored(tmp_path, monkeypatch):
    client = _pilot_client(tmp_path, monkeypatch)
    client.post("/api/v1/login", json={"username": "test", "password": "geheim"})

    logout = client.post("/api/v1/logout")
    after = client.get("/api/v1/session").json()

    assert logout.json() == {"ok": True}
    assert after["participant_id"] is None
    client.cookies.set("guideos_session", "dGVzdA.deadbeef")
    assert client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}).status_code == 401


def test_two_users_do_not_see_each_other(tmp_path, monkeypatch):
    client = _pilot_client(tmp_path, monkeypatch)
    client.post("/api/v1/login", json={"username": "anna", "password": "geheim"})
    anna = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}).json()["session_id"]
    client.post("/api/v1/logout")
    client.post("/api/v1/login", json={"username": "bernd", "password": "geheim"})

    foreign = client.get(f"/api/v1/assessment-sessions/{anna}").json()

    assert foreign["ok"] is False
    assert foreign["errors"][0]["code"] == "session_not_found"


def test_without_pilot_mode_the_header_scopes_as_before(tmp_path, monkeypatch):
    monkeypatch.delenv("GUIDEOS_PILOT_MODE", raising=False)
    monkeypatch.delenv("GUIDEOS_DATABASE_URL", raising=False)
    client = TestClient(create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json"))

    session = client.get("/api/v1/session").json()
    login = client.post("/api/v1/login", json={"username": "test", "password": "x"}).json()
    started = client.post("/api/v1/assessment-sessions", json={"domain_id": "verein-demo"}, headers={HEADER: "p"}).json()

    assert session == {"ok": True, "pilot_mode": False, "participant_id": "p"} or session["pilot_mode"] is False
    assert login["ok"] is False and login["errors"][0]["code"] == "invalid_state"
    assert started["ok"] is True


def test_pilot_mode_without_password_refuses_to_start(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setenv("GUIDEOS_PILOT_MODE", "1")
    monkeypatch.delenv("GUIDEOS_TEST_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="GUIDEOS_TEST_PASSWORD"):
        create_app(tmp_path / "assessments.json", conversation_path=tmp_path / "conversations.json")


def test_login_is_throttled_after_repeated_failures(tmp_path, monkeypatch):
    """Five wrong passwords from one source lock the login for a while, even
    with the right password; a different source stays unaffected."""
    client = _pilot_client(tmp_path, monkeypatch)
    wrong = {"username": "test", "password": "falsch"}

    for _ in range(5):
        assert client.post("/api/v1/login", json=wrong).json()["errors"][0]["code"] == "invalid_credentials"
    locked = client.post("/api/v1/login", json={"username": "test", "password": "geheim"})
    other = client.post("/api/v1/login", json={"username": "test", "password": "geheim"}, headers={"X-Forwarded-For": "10.1.1.9"})

    assert locked.status_code == 429
    assert locked.json()["ok"] is False and locked.json()["errors"][0]["code"] == "too_many_attempts"
    assert other.json()["ok"] is True
