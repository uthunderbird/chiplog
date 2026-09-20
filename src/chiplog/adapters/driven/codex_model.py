"""One Responses request per durably emitted Chiplog attempt, with no tools or retries."""

from __future__ import annotations

import asyncio
import json

import httpx

from chiplog.adapters.driven.codex_auth import (
    CodexTokenStorage,
    credential_identity,
    current_token,
)
from chiplog.capabilities.agent_loop.contracts import ModelAttempt, WorkerSessionPort
from chiplog.capabilities.agent_loop.live_contract import CodexError, LiveModelBinding

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
MAX_STREAM_BYTES = 2 * 1024 * 1024


class CodexModel:
    def __init__(self, storage: CodexTokenStorage, binding: LiveModelBinding) -> None:
        self.storage, self.binding = storage, binding
        self.session: WorkerSessionPort | None = None

    async def invoke(self, attempt: ModelAttempt) -> tuple[bytes, str]:
        label = attempt.manifest.joined_label
        if (
            attempt.state != "EMITTED_OUTCOME_UNKNOWN"
            or attempt.live_model != self.binding
            or attempt.provider_contract != self.binding.provider
            or attempt.recipient != self.binding.recipient
            or self.session is None
            or attempt.worker_session != self.session.current_worker()
            or label.value == "DENY_ALL"
            or (
                label.value == "ENDPOINT_RESTRICTED"
                and attempt.recipient not in label.allowed_endpoints
            )
        ):
            raise CodexError("Model attempt binding or disclosure permission is invalid")
        saved = self.storage.load()
        if saved is None or credential_identity(saved) != self.binding.credential_identity:
            raise CodexError("OAuth account changed; start a new dialogue")
        token = await asyncio.to_thread(current_token, self.storage)
        if credential_identity(token) != self.binding.credential_identity:
            raise CodexError("OAuth account changed; start a new dialogue")
        # Check the worker again after token refresh, immediately before the model request.
        if attempt.worker_session != self.session.current_worker():
            raise CodexError("Model worker changed before transmission")
        body = {
            "model": self.binding.model,
            "store": False,
            "stream": True,
            "instructions": "You are Chiplog. Return JSON matching the supplied response schema. "
            "Only proposals and conversation are available in this local dialogue. "
            "Never claim that a plan or external action was committed without a receipt.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "JSON request:\n" + attempt.request}
                    ],
                }
            ],
            "text": {"format": {"type": "json_object"}},
            "reasoning": {"effort": self.binding.effort},
        }
        headers = {
            "Authorization": "Bearer " + token.access,
            "chatgpt-account-id": token.account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": "chiplog",
            "User-Agent": "chiplog/0.1",
            "accept": "text/event-stream",
        }
        try:
            async with (
                httpx.AsyncClient(
                    timeout=httpx.Timeout(120, connect=20), trust_env=False
                ) as client,
                client.stream("POST", CODEX_RESPONSES_URL, headers=headers, json=body) as response,
            ):
                if response.status_code != 200:
                    raise CodexError(
                        f"Codex returned HTTP {response.status_code}; attempt was not retried"
                    )
                return await read_completion(response)
        except httpx.HTTPError:
            raise CodexError(
                "Codex transport failed; outcome is unknown and was not retried"
            ) from None


async def read_completion(response: httpx.Response) -> tuple[bytes, str]:
    buffer = b""
    received = 0
    deltas: list[str] = []
    delta_bytes = 0
    done_text: str | None = None
    sequence: int | None = None
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > MAX_STREAM_BYTES:
            raise CodexError("Codex stream exceeded its size bound")
        buffer += chunk
        # SSE accepts LF and CRLF, including a CR/LF split between chunks.
        buffer = buffer.replace(b"\r\n", b"\n")
        while b"\n\n" in buffer:
            event, buffer = buffer.split(b"\n\n", 1)
            data = b"\n".join(
                line[5:].lstrip() for line in event.split(b"\n") if line.startswith(b"data:")
            )
            if not data or data == b"[DONE]":
                continue
            try:
                value = json.loads(data)
                number = value.get("sequence_number")
                if number is not None:
                    if type(number) is not int or (sequence is not None and number != sequence + 1):
                        raise ValueError
                    sequence = number
                if value.get("type") == "response.output_text.delta":
                    delta = value["delta"]
                    if not isinstance(delta, str) or done_text is not None:
                        raise ValueError
                    delta_bytes += len(delta.encode())
                    if delta_bytes > 65536:
                        raise ValueError
                    deltas.append(delta)
                    continue
                if value.get("type") == "response.output_text.done":
                    text = value["text"]
                    if not isinstance(text, str) or done_text is not None:
                        raise ValueError
                    if deltas and text != "".join(deltas):
                        raise ValueError
                    done_text = text
                    continue
                if value.get("type") in {"error", "response.failed", "response.incomplete"}:
                    raise CodexError("Codex did not complete the response")
                if value.get("type") != "response.completed":
                    continue
                completed = value["response"]
                if completed["status"] != "completed" or not isinstance(completed["id"], str):
                    raise ValueError
                texts = []
                for item in completed["output"]:
                    if item["type"] == "reasoning":
                        continue
                    if item["type"] != "message" or item["role"] != "assistant":
                        raise ValueError
                    for content in item["content"]:
                        if content["type"] != "output_text" or not isinstance(content["text"], str):
                            raise ValueError
                        texts.append(content["text"])
                stream_text = done_text if done_text is not None else "".join(deltas)
                if texts and stream_text and "".join(texts) != stream_text:
                    raise ValueError
                # Codex may send an empty terminal output array; the completed
                # event still closes the text received on this same stream.
                raw = ("".join(texts) if texts else stream_text).encode()
                if not raw or len(raw) > 65536:
                    raise ValueError
                return raw, completed["id"]
            except ValueError, TypeError, KeyError, AttributeError:
                raise CodexError("Codex returned a malformed completion") from None
    raise CodexError("Codex stream ended without a completed response; attempt was not retried")
