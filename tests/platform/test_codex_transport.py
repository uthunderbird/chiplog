import json
import time
from pathlib import Path

import httpx
import pytest
from oauth_cli_kit.models import OAuthToken  # type: ignore[import-untyped]

from chiplog.adapters.driven.codex_auth import CodexTokenStorage, credential_identity
from chiplog.adapters.driven.codex_model import CodexModel, read_completion
from chiplog.adapters.driven.loop_prompts import render_prompt
from chiplog.capabilities.agent_loop.contracts import (
    DisclosureLabel,
    ModelAttempt,
    VisibilityManifest,
)
from chiplog.capabilities.agent_loop.live_contract import CodexError, LiveModelBinding


class Session:
    def current_worker(self) -> str:
        return "worker"


@pytest.mark.parametrize(
    "mutation", ["denied", "recipient", "account", "worker", "not-emitted", "model"]
)
async def test_invalid_binding_never_reaches_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    storage = CodexTokenStorage(tmp_path)
    token = OAuthToken("secret", "refresh", int(time.time() * 1000) + 600000, "account")
    storage.save(token)
    binding = LiveModelBinding(credential_identity=credential_identity(token))
    manifest = VisibilityManifest(
        tenant="t",
        principal="p",
        contour_head="c",
        run_id="r",
        turn_id="r/1",
        generation=0,
        worker_session="worker",
        members=(),
        joined_label=DisclosureLabel(
            value="ENDPOINT_RESTRICTED", allowed_endpoints=("openai-codex",)
        ),
        artifact=await render_prompt("test"),
    )
    attempt = ModelAttempt(
        attempt_id="attempt",
        lineage_id="lineage",
        generation=0,
        state="EMITTED_OUTCOME_UNKNOWN",
        head="head",
        manifest=manifest,
        request="request",
        provider_contract=binding.provider,
        recipient=binding.recipient,
        live_model=binding,
        worker_session="worker",
    )
    if mutation == "denied":
        manifest = manifest.model_copy(
            update={"joined_label": DisclosureLabel(value="DENY_ALL", allowed_endpoints=())}
        )
        attempt = attempt.model_copy(update={"manifest": manifest})
    elif mutation == "recipient":
        attempt = attempt.model_copy(update={"recipient": "hermetic-model"})
    elif mutation == "account":
        storage.save(OAuthToken("other", "refresh", 0, "other-account"))
    elif mutation == "worker":
        attempt = attempt.model_copy(update={"worker_session": "stale"})
    elif mutation == "not-emitted":
        attempt = attempt.model_copy(update={"state": "PREPARED_NOT_EMITTED"})
    else:
        attempt = attempt.model_copy(
            update={"live_model": binding.model_copy(update={"model": "other"})}
        )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid attempt reached network/token refresh")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    model = CodexModel(storage, binding)
    model.session = Session()
    with pytest.raises(CodexError):
        await model.invoke(attempt)


@pytest.mark.parametrize(
    "payload",
    [
        b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n',
        b'data: {"type":"response.failed"}\n\n',
        b"data: not-json\n\n",
        b"data: [DONE]\n\n",
    ],
)
async def test_partial_or_failed_stream_cannot_be_success(payload: bytes) -> None:
    with pytest.raises(CodexError):
        await read_completion(httpx.Response(200, content=payload))


async def test_complete_sse_output_is_authoritative_not_accumulated_deltas() -> None:
    event = {
        "type": "response.completed",
        "response": {
            "id": "resp",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "final"}],
                }
            ],
        },
    }
    body = b'data: {"type":"response.output_text.delta","delta":"final"}\n\n'
    body += b"data: " + json.dumps(event).encode() + b"\r\n\r\n"
    assert await read_completion(httpx.Response(200, content=body)) == (b"final", "resp")


async def test_codex_empty_terminal_output_closes_streamed_text() -> None:
    events = [
        {"type": "response.output_text.delta", "delta": "final", "sequence_number": 0},
        {"type": "response.output_text.done", "text": "final", "sequence_number": 1},
        {
            "type": "response.completed",
            "sequence_number": 2,
            "response": {"id": "resp", "status": "completed", "output": []},
        },
    ]
    body = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
    assert await read_completion(httpx.Response(200, content=body)) == (b"final", "resp")


@pytest.mark.parametrize("sequence", [[0, 0], [1, 0], [0, 2]])
async def test_duplicate_reordered_or_missing_stream_events_fail(sequence: list[int]) -> None:
    body = b"".join(
        b"data: "
        + json.dumps(
            {
                "type": "response.output_text.delta",
                "delta": "x",
                "sequence_number": number,
            }
        ).encode()
        + b"\n\n"
        for number in sequence
    )
    with pytest.raises(CodexError, match="malformed"):
        await read_completion(httpx.Response(200, content=body))
