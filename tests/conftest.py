"""Real SDK with a network-free HTTP transport; production code is unchanged."""

import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import httpx
import openai
import pytest

from llm_client import LLMSettings


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def project(tmp_path):
    for name in ("tickets", "config"):
        destination = "tickets.json" if name == "tickets" else "triage_config.json"
        shutil.copyfile(FIXTURES / f"sample_{name}.json", tmp_path / destination)
    return tmp_path


@pytest.fixture
def settings():
    return LLMSettings(api_key="test-not-a-credential", model="test-model", base_url="https://test.invalid/v1", response_format="json_object")


@pytest.fixture
def provider(monkeypatch):
    real_client = openai.OpenAI
    state = SimpleNamespace(requests=[], status=200, mutate=None, raw=None, refusal=None, finish_reason="stop", transport_error=None)
    def handler(request):
        data = json.loads(request.content)
        state.requests.append(data)
        if state.transport_error:
            raise state.transport_error("injected transport failure", request=request)
        if state.status != 200:
            return httpx.Response(state.status, json={"error": {"message": "test-not-a-credential must not leak", "type": "provider_error"}})
        payload = json.loads(data["messages"][1]["content"])
        config = payload["triage_config"]
        response = {"predictions": [{
            "ticket_id": t["ticket_id"], "category": config["allowed_categories"][0],
            "priority": config["allowed_priorities"][-1], "reason": "Test classification rationale.",
            "suggested_reply": "Acknowledged.", "confidence": 0.82,
        } for t in reversed(payload["normalized_tickets"])]}
        if state.mutate:
            response = state.mutate(response)
        content = state.raw if state.raw is not None else json.dumps(response, ensure_ascii=False)
        return httpx.Response(200, json={
            "id": "test-completion", "object": "chat.completion", "created": 0, "model": data["model"],
            "choices": [{"index": 0, "finish_reason": state.finish_reason, "message": {"role": "assistant", "content": content, "refusal": state.refusal}}],
        })
    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return real_client(**kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(openai, "OpenAI", factory)
    return state
