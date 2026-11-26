"""The LLM path, with OpenRouter replaced by a fake that asks for one tool call."""

import json

import httpx
from sqlalchemy import select

from app import agent, db as dbmod
from app.config import get_settings
from app.models import User


def test_tool_calling_loop(app_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        assert request.headers["authorization"] == "Bearer test-key"
        if len(calls) == 1:
            assert any(t["function"]["name"] == "get_my_charges" for t in body["tools"])
            return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "get_my_charges", "arguments": "{}"}}]}}]})
        tool_msg = body["messages"][-1]
        assert tool_msg["role"] == "tool" and "120,00" in tool_msg["content"]
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant",
                                                                  "content": "Tens 120,00 € em atraso."}}]})

    real_client = httpx.Client
    monkeypatch.setattr(agent.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))

    with dbmod.db_session() as db:
        user = db.scalar(select(User).where(User.student_number == "20260001"))
        result = agent.answer(db, user, [], "quanto devo?")
    assert result["mode"] == "llm" and result["tools"] == ["get_my_charges"]
    assert result["answer"] == "Tens 120,00 € em atraso."
    assert "Ana Demo" in calls[0]["messages"][0]["content"]  # system prompt knows who is asking


def test_provider_failure_falls_back(app_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "openrouter_api_key", "test-key")
    real_client = httpx.Client
    monkeypatch.setattr(agent.httpx, "Client", lambda **kw: real_client(
        transport=httpx.MockTransport(lambda r: httpx.Response(503, json={"error": "down"})), **kw))
    with dbmod.db_session() as db:
        user = db.scalar(select(User).where(User.student_number == "20260001"))
        result = agent.answer(db, user, [], "que aulas tenho hoje?")
    assert result["mode"] == "offline" and "indisponível" in result["answer"]
