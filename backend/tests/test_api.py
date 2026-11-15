"""API contract tests: health, chat, validation, CORS and static frontend."""

from __future__ import annotations

from app.config import PROJECT_ROOT
from app.services.grounding import FALLBACK_SENTENCE
from app.services import health_service as health_module


def _create_session(client) -> str:
    response = client.post("/sessions", json={})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_health_reports_all_components(client, monkeypatch) -> None:
    async def fake_ollama(settings):
        return {
            "status": "ok",
            "connected": True,
            "host": settings.ollama_host,
            "latency_ms": 3,
            "models": [settings.llm_model, settings.embedding_model],
            "llm_model": settings.llm_model,
            "embedding_model": settings.embedding_model,
            "llm_model_available": True,
            "embedding_model_available": True,
        }

    monkeypatch.setattr(health_module, "check_ollama", fake_ollama)

    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["components"]["chromadb"]["indexed_chunks"] == 38
    assert body["protected_files"]["ok"] is True
    assert body["components"]["grounding"]["fallback_message"] == FALLBACK_SENTENCE
    assert body["configuration"]["llm_model"] == client.app.state.settings.llm_model
    assert body["configuration"]["collection"] == "local_research_kb"


def test_grounded_chat_returns_verified_answer_and_sources(client) -> None:
    session_id = _create_session(client)
    worker = client.app.state.worker
    worker.answer = "The Vice Chancellor is Prof. Ali Raza Moosvi (vc@kbn.university)."

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Who is the Vice Chancellor of KBN University?",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["grounded"] is True
    assert body["verification"]["status"] == "verified_kb"
    assert "Ali Raza Moosvi" in body["assistant_message"]["content"]
    assert len(body["sources"]) == 1
    assert body["sources"][0]["file"] == "kbn_clean_page_3.md"
    assert body["retrieval"]["attempted"] is True
    assert body["retrieval"]["used"] is True

    submission = worker.submissions[-1]
    assert "KB Snippet 1" in submission["kb_knowledge"]
    assert submission["system_prompt"].count(FALLBACK_SENTENCE) == 1
    assert submission["worker_key"].startswith(session_id)

    transcript = client.get(f"/sessions/{session_id}").json()
    assert transcript["message_count"] == 2
    assert [message["role"] for message in transcript["messages"]] == [
        "user",
        "assistant",
    ]


def test_chat_with_invented_email_falls_back(client) -> None:
    session_id = _create_session(client)
    client.app.state.worker.answer = (
        "The Vice Chancellor is Prof. Ali Raza Moosvi, email vc@fake-university.com."
    )

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Who is the Vice Chancellor of KBN University?",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["grounded"] is False
    assert body["verification"]["status"] == "unavailable"
    assert body["assistant_message"]["content"] == FALLBACK_SENTENCE
    assert body["sources"] == []
    assert "email" in body["verification"]["reason"].lower()


def test_absent_entity_is_blocked_before_the_model_runs(client) -> None:
    session_id = _create_session(client)
    worker = client.app.state.worker
    before = len(worker.submissions)

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "Does KBN University offer a course in Astrobiology?",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["assistant_message"]["content"] == FALLBACK_SENTENCE
    assert body["verification"]["status"] == "unavailable"
    assert body["retrieval"]["used"] is False
    assert body["sources"] == []
    assert len(worker.submissions) == before, "The model must not run for blocked queries"


def test_general_question_is_labelled_general_knowledge(client) -> None:
    session_id = _create_session(client)
    client.app.state.worker.answer = "2 + 2 = 4."

    response = client.post(
        "/chat",
        json={"session_id": session_id, "message": "What is 2 + 2?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["verification"]["status"] == "general_unverified"
    assert body["grounded"] is False
    assert body["retrieval"]["attempted"] is False
    assert body["sources"] == []
    assert body["assistant_message"]["content"] == "2 + 2 = 4."


def test_session_crud_and_persistence(client) -> None:
    created = client.post("/sessions", json={"title": "Admissions"})
    assert created.status_code == 201
    session_id = created.json()["id"]

    listed = client.get("/sessions")
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["sessions"][0]["title"] == "Admissions"

    detail = client.get(f"/sessions/{session_id}")
    assert detail.status_code == 200
    assert detail.json()["message_count"] == 0

    renamed = client.patch(f"/sessions/{session_id}", json={"title": "New title"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "New title"

    cleared = client.post(f"/sessions/{session_id}/clear")
    assert cleared.status_code == 200
    assert cleared.json()["messages"] == []

    deleted = client.delete(f"/sessions/{session_id}")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert client.get(f"/sessions/{session_id}").status_code == 404


def test_missing_session_returns_404(client) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/sessions/{missing}").status_code == 404
    response = client.post(
        "/chat", json={"session_id": missing, "message": "Hello there"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_validation_errors_are_structured(client) -> None:
    response = client.post("/chat", json={"session_id": "short", "message": ""})
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["message"]

    unknown_field = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-0000-0000-000000000000",
            "message": "Hello",
            "extra": True,
        },
    )
    assert unknown_field.status_code == 422


def test_cors_allows_the_vite_dev_origin(client) -> None:
    response = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert response.headers.get("x-request-id")


def test_openapi_exposes_required_paths(client) -> None:
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/chat" in paths
    assert "/health" in paths
    assert "/sessions" in paths
    assert "/sessions/{session_id}" in paths
    assert "/sessions/{session_id}/clear" in paths


def test_built_frontend_is_served_when_present(client) -> None:
    index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if not index.exists():
        return
    response = client.get("/")
    assert response.status_code == 200
    assert "KBN University AI Helpdesk" in response.text
