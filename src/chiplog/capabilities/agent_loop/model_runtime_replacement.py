"""Native model replacement proposal; composition must prove the old gate fenced."""

import json

from .contracts import LoopRejected
from .execution_client_transitions import ExecutionRun, advance, current, emit, headed


def prepare_fenced_model_replacement(
    run: ExecutionRun, worker: str
) -> tuple[ExecutionRun, ExecutionRun]:
    run = current(run)
    if run.state != "ACTIVE" or not run.turns:
        raise LoopRejected("model replacement requires an ACTIVE Run")
    turn = run.turns[-1]
    if (
        turn.state != "CALL_ACTIVE" or not turn.attempts
        or turn.selector != len(turn.attempts) - 1
        or turn.response_seal is not None or turn.initialized_calls is not None
    ):
        raise LoopRejected("model replacement requires the selected unsealed attempt")
    original = turn.attempts[-1]
    if (
        original.state != "EMITTED_OUTCOME_UNKNOWN"
        or original.generation != turn.selector
        or original.response_base64 is not None or original.receipt is not None
        or original.generation >= 2**64 - 1
    ):
        raise LoopRejected("model replacement state or selector bound differs")
    generation = original.generation + 1
    if run.policy.max_model_retries is not None and generation > run.policy.max_model_retries:
        raise LoopRejected("durable model retry budget exhausted")
    manifest = original.manifest.model_copy(update={
        "generation": generation, "worker_session": worker,
    })
    request = json.loads(original.request)
    request["manifest"] = manifest.digest()
    encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode()) > run.policy.max_request_bytes:
        raise LoopRejected("replacement model request exceeds its byte bound")
    replacement = headed(original.model_copy(update={
        "attempt_id": original.lineage_id + "/generation/" + str(generation),
        "generation": generation, "state": "PREPARED_NOT_EMITTED", "head": "pending",
        "manifest": manifest, "worker_session": worker, "request": encoded,
    }), "execution-attempt:")
    changed = headed(turn.model_copy(update={
        "attempts": (*turn.attempts, replacement), "selector": generation,
    }), "execution-turn:")
    prepared = advance(
        run, "ModelAttemptReplacedWithoutExposure", worker_session=worker,
        turns=(*run.turns[:-1], changed),
    )
    return prepared, emit(prepared)
