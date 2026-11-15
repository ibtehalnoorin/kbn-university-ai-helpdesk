"""GroqWorkerClient: drop-in replacement for the Ollama WorkerClient."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from app.config import Settings
from app.errors import WorkerQueueFullError, WorkerResultError, WorkerUnavailableError
from app.services.groq_client import GroqWorkerClient


def _settings(**overrides) -> Settings:
    base = dict(llm_provider="groq", groq_api_key="test-key", groq_model="test-model", start_worker=False)
    base.update(overrides)
    return Settings(**base)


def _reply(content, status=200):
    body = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}
    return httpx.Response(status, json=body)


def _client(handler, **overrides) -> GroqWorkerClient:
    return GroqWorkerClient(_settings(**overrides), transport=httpx.MockTransport(handler))


def _submit(client, key="s1", query="Who is the VC?", kb="CONTEXT-A", system="SYSTEM-PROMPT"):
    return client.submit(
        worker_key=key,
        query=query,
        kb_knowledge=kb,
        web_context="",
        persona_name="helpdesk",
        system_prompt=system,
    )


def _wait(client, future):
    return asyncio.run(client.await_result(future, 5))


def test_submit_returns_answer_with_worker_result_keys():
    client = _client(lambda request: _reply("Hello"))
    result = _wait(client, _submit(client, key="abc"))
    assert result["status"] == "success"
    assert result["answer"] == "Hello"
    assert result["session_id"] == "abc"
    assert result["persona"] == "helpdesk"
    assert {"time", "tokens_used", "max_tokens", "utilization", "query"} <= set(result)


def test_system_prompt_and_context_are_sent():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["authorization"]
        return _reply("ok")

    client = _client(handler)
    _wait(client, _submit(client))
    messages = seen["body"]["messages"]
    assert seen["body"]["model"] == "test-model"
    assert seen["auth"] == "Bearer test-key"
    assert messages[0] == {"role": "system", "content": "SYSTEM-PROMPT"}
    assert "User Query: Who is the VC?" in messages[-1]["content"]
    assert "CONTEXT-A" in messages[-1]["content"]


def test_history_is_kept_per_worker_key_and_sessions_do_not_mix():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content)["messages"])
        return _reply("answer")

    client = _client(handler)
    _wait(client, _submit(client, key="A", query="first A"))
    _wait(client, _submit(client, key="B", query="first B"))
    _wait(client, _submit(client, key="A", query="second A"))
    text_for_a_second = json.dumps(bodies[2])
    assert "first A" in text_for_a_second
    assert "first B" not in text_for_a_second
    assert "first A" not in json.dumps(bodies[1])


def test_http_429_raises_queue_full_with_friendly_message():
    client = _client(lambda request: httpx.Response(429, json={"error": {"message": "rate"}}))
    with pytest.raises(WorkerQueueFullError) as info:
        _wait(client, _submit(client))
    assert "busy" in str(info.value).lower()


def test_failed_call_does_not_poison_history():
    calls = {"n": 0}
    bodies = []

    def handler(request):
        calls["n"] += 1
        bodies.append(json.loads(request.content)["messages"])
        return httpx.Response(500) if calls["n"] == 1 else _reply("fine")

    client = _client(handler)
    with pytest.raises(WorkerResultError):
        _wait(client, _submit(client, query="lost question"))
    _wait(client, _submit(client, query="retry question"))
    assert "lost question" not in json.dumps(bodies[1])


@pytest.mark.parametrize("content", [None, "", "   "])
def test_empty_content_raises_result_error(content):
    client = _client(lambda request: _reply(content))
    with pytest.raises(WorkerResultError):
        _wait(client, _submit(client))


def test_missing_api_key_raises_unavailable_on_submit():
    client = _client(lambda request: _reply("x"), groq_api_key="")
    with pytest.raises(WorkerUnavailableError):
        _submit(client)


def test_parallel_requests_do_not_block_each_other():
    def handler(request):
        time.sleep(0.3)
        return _reply("ok")

    client = _client(handler)
    started = time.perf_counter()
    futures = [_submit(client, key=f"k{i}") for i in range(4)]
    for future in futures:
        _wait(client, future)
    assert time.perf_counter() - started < 0.9


def test_pending_limit_raises_queue_full():
    def handler(request):
        time.sleep(0.4)
        return _reply("ok")

    client = _client(handler, max_pending_requests=1)
    first = _submit(client, key="one")
    with pytest.raises(WorkerQueueFullError):
        _submit(client, key="two")
    _wait(client, first)


def test_status_reports_provider_and_model():
    client = _client(lambda request: _reply("ok"))
    status = client.status()
    assert status["provider"] == "groq" and status["model"] == "test-model"
    assert client.alive is True and client.pending_count == 0
