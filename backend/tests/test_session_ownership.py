"""One visitor must never see or touch another visitor's conversations."""

from __future__ import annotations

A = {"X-Client-Id": "client-a-0123456789"}
B = {"X-Client-Id": "client-b-0123456789"}


def _create(client, headers):
    response = client.post("/sessions", json={}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


def test_list_only_returns_own_sessions(client):
    a_id = _create(client, A)
    _create(client, B)
    listed = client.get("/sessions", headers=A).json()
    assert [s["id"] for s in listed["sessions"]] == [a_id] and listed["count"] == 1


def test_other_client_gets_404_on_every_session_route(client):
    sid = _create(client, A)
    assert client.get(f"/sessions/{sid}", headers=B).status_code == 404
    assert client.patch(f"/sessions/{sid}", json={"title": "x"}, headers=B).status_code == 404
    assert client.post(f"/sessions/{sid}/clear", headers=B).status_code == 404
    assert client.delete(f"/sessions/{sid}", headers=B).status_code == 404
    chat = client.post("/chat", json={"session_id": sid, "message": "hi there"}, headers=B)
    assert chat.status_code == 404
    # ... while the owner is unaffected and the session still exists
    assert client.get(f"/sessions/{sid}", headers=A).status_code == 200
    assert client.post("/chat", json={"session_id": sid, "message": "hi there"}, headers=A).status_code == 200


def test_missing_header_only_sees_ownerless_sessions(client):
    _create(client, A)
    ownerless = client.post("/sessions", json={}).json()["id"]
    listed = client.get("/sessions").json()
    assert [s["id"] for s in listed["sessions"]] == [ownerless]
    assert client.get(f"/sessions/{ownerless}", headers=B).status_code == 200  # legacy/dev sessions stay open


def test_malformed_client_id_is_rejected(client):
    response = client.get("/sessions", headers={"X-Client-Id": "short"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
