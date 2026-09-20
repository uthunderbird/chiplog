"""Deterministic registered model leaf: inert bytes, no provider/channel client."""

import hashlib

from chiplog.capabilities.agent_loop.contracts import LoopRejected, ModelAttempt, WorkerSessionPort


class HermeticModel:
    def __init__(
        self, responses: tuple[bytes, ...] = (), matches: tuple[str, ...] | None = None
    ) -> None:
        if type(responses) is not tuple or any(type(raw) is not bytes for raw in responses):
            raise LoopRejected("hermetic model requires inert exact response bytes")
        self._responses = responses
        if matches is not None and (
            type(matches) is not tuple
            or len(matches) != len(responses)
            or any(type(value) is not str for value in matches)
        ):
            raise LoopRejected("invalid hermetic matcher set")
        self._matches = matches
        self.session: WorkerSessionPort | None = None
        self.requests: list[ModelAttempt] = []

    async def invoke(self, attempt: ModelAttempt) -> tuple[bytes, str]:
        if attempt.live_model is not None or attempt.provider_contract != "hermetic-model.v1":
            raise LoopRejected("live attempt cannot use the hermetic model")
        if attempt.state != "EMITTED_OUTCOME_UNKNOWN":
            raise LoopRejected("request not durably marked emitted")
        if self.session is not None and attempt.worker_session != self.session.current_worker():
            raise LoopRejected("model emission belongs to a stale worker generation")
        if len(self.requests) >= len(self._responses):
            raise LoopRejected("hermetic script exhausted; no fallback provider")
        raw = self._responses[len(self.requests)]
        if self._matches is not None:
            expected = self._matches[len(self.requests)]
            if expected != "turn " + attempt.manifest.turn_id.rsplit("/", 1)[-1]:
                raise LoopRejected("fixture matcher did not match actual model request")
        self.requests.append(attempt)
        return raw, "hermetic:" + hashlib.sha256(attempt.request.encode() + raw).hexdigest()
