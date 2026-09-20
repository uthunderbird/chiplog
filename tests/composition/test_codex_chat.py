import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from oauth_cli_kit.models import OAuthToken  # type: ignore[import-untyped]

from chiplog.adapters.driven.codex_auth import CodexTokenStorage, credential_identity
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy
from chiplog.capabilities.agent_loop.live_contract import CodexError, LiveModelBinding
from chiplog.composition.codex_chat import open_codex_chat


async def test_live_dialogue_uses_durable_loop_and_exact_model_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = CodexTokenStorage(tmp_path / "private")
    token = OAuthToken("test-access", "test-refresh", int(time.time() * 1000) + 600000, "account")
    storage.save(token)
    binding = LiveModelBinding(credential_identity=credential_identity(token))
    requests: list[dict[str, Any]] = []
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        run = loop.record("test")
        attempt = run.turns[-1].attempts[-1]
        assert attempt.state == "EMITTED_OUTCOME_UNKNOWN"
        assert attempt.live_model == binding
        assert attempt.provider_contract == "codex-oauth.v1"
        body = json.loads(request.content)
        assert body["model"] == "gpt-5.6-terra" and body["reasoning"]["effort"] == "low"
        # Reached backend 400: json_object requires 'json' in input, not only instructions.
        if "json" not in body["input"][0]["content"][0]["text"].lower():
            return httpx.Response(400)
        assert "test-access" not in body["input"][0]["content"][0]["text"]
        assert request.headers["authorization"] == "Bearer test-access"
        assert request.headers["chatgpt-account-id"] == "account"
        requests.append(body)
        reply = (
            {
                "kind": "Continue",
                "tool_calls": [{"call_id": "c", "tool": "propose_intent", "text": "Swim"}],
            }
            if len(requests) == 1
            else {
                "kind": "Complete",
                "deliveries": [{"kind": "NonAuthoritativeText", "text": "Привет!"}],
            }
        )
        if len(requests) == 2:
            assert run.turns[0].sealed_calls is not None
            assert run.turns[0].sealed_calls[0].state == "TERMINAL"
        event = {
            "type": "response.completed",
            "response": {
                "id": f"resp-{len(requests)}",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(reply),
                            }
                        ],
                    }
                ],
            },
        }
        return httpx.Response(200, content="data: " + json.dumps(event) + "\n\n")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond),
            **kwargs,
        ),
    )
    async with open_codex_chat(storage, binding) as loop:
        view = await loop.create("test", "Привет", BudgetPolicy(live_model=binding))
        view = await loop.activate("test", view.head)
        while view.state == "ACTIVE":
            view = await loop.step("test", view.head)
        assert view.state == "SUCCEEDED"
        assert loop.record("test").accepted_text == ("Привет!",)
    async with open_codex_chat(storage, binding) as loop:
        assert loop.record("test").accepted_text == ("Привет!",)
    assert len(requests) == 2


async def test_lost_response_is_retained_and_not_replayed_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = CodexTokenStorage(tmp_path)
    token = OAuthToken("a", "r", int(time.time() * 1000) + 600000, "account")
    storage.save(token)
    binding = LiveModelBinding(credential_identity=credential_identity(token))
    calls = 0
    original_client = httpx.AsyncClient

    def lost(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("secret provider error", request=request)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(lost),
            **kwargs,
        ),
    )
    async with open_codex_chat(storage, binding) as loop:
        view = await loop.create("lost", "Hi", BudgetPolicy(live_model=binding))
        view = await loop.activate("lost", view.head)
        with pytest.raises(CodexError, match="unknown"):
            await loop.step("lost", view.head)
        assert loop.record("lost").turns[-1].attempts[-1].state == "EMITTED_OUTCOME_UNKNOWN"
    async with open_codex_chat(storage, binding) as loop:
        assert loop.record("lost").turns[-1].attempts[-1].state == "EMITTED_OUTCOME_UNKNOWN"
    assert calls == 1
