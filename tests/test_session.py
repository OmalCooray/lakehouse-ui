from app.session import create_session, delete_session, get_session


def test_create_session_returns_an_id_that_get_session_resolves():
    session_id = create_session("cid", "secret", "loader")

    session = get_session(session_id)

    assert session is not None
    assert session.client_id == "cid"
    assert session.client_secret == "secret"
    assert session.principal_name == "loader"


def test_get_session_returns_none_for_unknown_id():
    assert get_session("does-not-exist") is None


def test_get_session_returns_none_for_none():
    assert get_session(None) is None


def test_two_sessions_get_different_ids():
    first = create_session("cid1", "secret1", "loader")
    second = create_session("cid2", "secret2", "lakehouse-ui")

    assert first != second
    assert get_session(first).principal_name == "loader"
    assert get_session(second).principal_name == "lakehouse-ui"


def test_delete_session_removes_it():
    session_id = create_session("cid", "secret", "loader")

    delete_session(session_id)

    assert get_session(session_id) is None


def test_delete_session_on_unknown_id_does_not_raise():
    delete_session("does-not-exist")
    delete_session(None)
