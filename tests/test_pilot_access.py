from guideos.pilot.access import (
    AUTHENTICATED_KEY,
    PARTICIPANT_KEY,
    authenticate,
    logout,
    participant_id,
    password_matches,
)


def test_authentication_uses_the_user_name_as_participant_id():
    session_state = {}

    assert authenticate(session_state, "test", "pilot-secret", "pilot-secret") is True
    assert session_state[AUTHENTICATED_KEY] is True
    assert participant_id(session_state) == "test"


def test_authentication_strips_the_user_name():
    session_state = {}

    assert authenticate(session_state, "  Anna ", "pilot-secret", "pilot-secret") is True
    assert participant_id(session_state) == "Anna"


def test_authentication_rejects_an_empty_user_name():
    session_state = {}

    assert authenticate(session_state, "   ", "pilot-secret", "pilot-secret") is False
    assert AUTHENTICATED_KEY not in session_state
    assert participant_id(session_state) is None


def test_authentication_with_a_wrong_password_stores_nothing():
    session_state = {}

    assert authenticate(session_state, "test", "wrong", "pilot-secret") is False
    assert session_state == {}


def test_a_new_login_replaces_the_participant_of_the_session():
    session_state = {}
    authenticate(session_state, "test", "pilot-secret", "pilot-secret")
    logout(session_state)

    authenticate(session_state, "zweite", "pilot-secret", "pilot-secret")

    assert participant_id(session_state) == "zweite"


def test_authentication_rejects_missing_or_wrong_password():
    assert password_matches("secret", None) is False
    assert password_matches("wrong", "secret") is False


def test_logout_removes_pilot_access_and_participant_id():
    session_state = {}
    authenticate(session_state, "test", "pilot-secret", "pilot-secret")

    logout(session_state)

    assert AUTHENTICATED_KEY not in session_state
    assert PARTICIPANT_KEY not in session_state
    assert participant_id(session_state) is None


def test_session_token_round_trips_and_rejects_tampering():
    from guideos.pilot.access import session_participant, sign_session

    token = sign_session("test", key="k1", issued_at=1_000_000)

    assert session_participant(token, key="k1", now=1_000_010, max_age=3600) == "test"
    assert session_participant(token, key="k2", now=1_000_010, max_age=3600) is None
    assert session_participant(token, key="k1", now=1_000_000 + 3601, max_age=3600) is None
    assert session_participant(token[:-1] + ("0" if token[-1] != "0" else "1"), key="k1", now=1_000_010, max_age=3600) is None
    assert session_participant("kaputt", key="k1", now=0, max_age=3600) is None
    assert session_participant(None, key="k1", now=0, max_age=3600) is None


def test_login_throttle_locks_a_source_after_repeated_failures():
    from guideos.pilot.access import LoginThrottle

    now = [1000.0]
    throttle = LoginThrottle(max_failures=3, window_seconds=60, lock_seconds=120, clock=lambda: now[0])

    assert throttle.allowed("10.0.0.1") is True
    for _ in range(3):
        throttle.record_failure("10.0.0.1")
    assert throttle.allowed("10.0.0.1") is False
    assert throttle.allowed("10.0.0.2") is True
    now[0] += 119
    assert throttle.allowed("10.0.0.1") is False
    now[0] += 2
    assert throttle.allowed("10.0.0.1") is True

    throttle.record_failure("10.0.0.3")
    throttle.record_success("10.0.0.3")
    throttle.record_failure("10.0.0.3")
    throttle.record_failure("10.0.0.3")
    assert throttle.allowed("10.0.0.3") is True
