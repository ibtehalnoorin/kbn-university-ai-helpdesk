"""Behavioural tests run against both session stores (JSON file and Postgres).

Postgres cases run only when TEST_DATABASE_URL points at a disposable database.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.errors import NotFoundError, SessionLimitError, StoreUnavailableError
from app.schemas import Message
from app.services.session_store import SessionStore, new_message_id

DB_URL = os.environ.get("TEST_DATABASE_URL")
PARAMS = ["json", pytest.param("postgres", marks=pytest.mark.skipif(not DB_URL, reason="no TEST_DATABASE_URL"))]


def _msg(role: str, content: str) -> Message:
    return Message(id=new_message_id(), role=role, content=content)


@pytest.fixture(params=PARAMS)
def make_store(request, tmp_path):
    created = []

    def factory(max_sessions: int = 100, max_messages: int = 500):
        if request.param == "json":
            store = SessionStore(tmp_path / "s.json", max_sessions=max_sessions, max_messages=max_messages)
        else:
            from app.services.pg_session_store import PostgresSessionStore

            store = PostgresSessionStore(DB_URL, max_sessions=max_sessions, max_messages=max_messages)
            store.truncate_for_tests()
        created.append(store)
        return store

    factory.kind = request.param
    yield factory
    for store in created:
        close = getattr(store, "close", None)
        if close:
            close()


def _age(store, kind, session_id, days):
    old = datetime.now(timezone.utc) - timedelta(days=days)
    if kind == "json":
        store._sessions[session_id].updated_at = old
    else:
        store.set_updated_at_for_tests(session_id, old)


def test_create_list_detail_round_trip(make_store):
    store = make_store()
    created = store.create("Admissions")
    assert store.detail(created.id).title == "Admissions"
    assert [s.id for s in store.list()] == [created.id]
    assert store.count() == 1


def test_append_exchange_orders_messages_and_titles_session(make_store):
    store = make_store()
    sid = store.create().id
    store.append_exchange(sid, _msg("user", "Who is the VC?"), _msg("assistant", "Prof. X"))
    store.append_exchange(sid, _msg("user", "Email?"), _msg("assistant", "vc@kbn.university"))
    detail = store.detail(sid)
    assert [m.content for m in detail.messages] == ["Who is the VC?", "Prof. X", "Email?", "vc@kbn.university"]
    assert detail.title == "Who is the VC?"
    assert store.recent_user_messages(sid, limit=1) == ["Email?"]


def test_clear_keeps_session_and_rotates_worker_key(make_store):
    store = make_store()
    sid = store.create().id
    before = store.worker_key_for(sid)
    store.append_exchange(sid, _msg("user", "hi"), _msg("assistant", "hello"))
    cleared = store.clear(sid)
    assert cleared.messages == [] and cleared.title == "New conversation"
    assert store.worker_key_for(sid) != before


def test_rename_then_delete_then_not_found(make_store):
    store = make_store()
    sid = store.create().id
    assert store.rename(sid, "  Renamed  ").title == "Renamed"
    store.delete(sid)
    with pytest.raises(NotFoundError):
        store.detail(sid)
    with pytest.raises(NotFoundError):
        store.delete(sid)


def test_session_limit(make_store):
    store = make_store(max_sessions=2)
    store.create()
    store.create()
    with pytest.raises(SessionLimitError):
        store.create()


def test_message_cap_drops_oldest(make_store):
    store = make_store(max_messages=4)
    sid = store.create().id
    for i in range(3):
        store.append_exchange(sid, _msg("user", f"q{i}"), _msg("assistant", f"a{i}"))
    assert [m.content for m in store.detail(sid).messages] == ["q1", "a1", "q2", "a2"]


def test_purge_older_than_removes_only_stale_sessions(make_store):
    store = make_store()
    old, fresh = store.create().id, store.create().id
    _age(store, make_store.kind, old, 40)
    assert store.purge_older_than(30) == 1
    assert [s.id for s in store.list()] == [fresh]


def test_data_survives_a_new_store_instance(make_store, tmp_path):
    if make_store.kind == "json":
        pytest.skip("covered by existing JSON persistence behaviour")
    first = make_store()
    sid = first.create("Persisted").id
    first.append_exchange(sid, _msg("user", "hello"), _msg("assistant", "hi"))
    from app.services.pg_session_store import PostgresSessionStore

    second = PostgresSessionStore(DB_URL)
    try:
        assert second.detail(sid).messages[1].content == "hi"
    finally:
        second.close()


def test_health_reports_ok(make_store):
    assert make_store().health()["status"] == "ok"


@pytest.mark.skipif(not DB_URL, reason="no TEST_DATABASE_URL")
def test_unreachable_database_does_not_crash_construction_but_reports_errors():
    from app.services.pg_session_store import PostgresSessionStore

    store = PostgresSessionStore("postgresql://nobody@127.0.0.1:1/none", connect_timeout=1)
    try:
        assert store.health()["status"] == "error"
        with pytest.raises(StoreUnavailableError):
            store.create()
    finally:
        store.close()
