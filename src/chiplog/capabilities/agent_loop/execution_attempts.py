"""Executable attempt transport transitions; persistence and invocation belong to runtime.

An emitted unknown attempt has no transition back to prepared. Capturing bytes
neither parses a tool request nor accepts delivery or any external action.
"""

import base64

from .contracts import AttemptState, LoopRejected
from .execution_contracts import ExecutionModelAttempt, ExecutionRunRecord, ExecutionTurn


def _selected(run: ExecutionRunRecord, state: AttemptState) -> ExecutionModelAttempt:
    if (
        run.state != "ACTIVE"
        or not run.turns
        or run.head != "loop:" + run.model_copy(update={"head": "pending"}).digest()
    ):
        raise LoopRejected("missing active self-bound execution Run")
    turn = run.turns[-1]
    if (
        turn.state != "CALL_ACTIVE"
        or not turn.attempts
        or turn.selector != len(turn.attempts) - 1
        or turn.response_seal is not None
        or turn.initialized_calls is not None
    ):
        raise LoopRejected("missing unsealed current execution attempt")
    attempt = turn.attempts[turn.selector]
    manifest = attempt.manifest
    if (
        attempt.generation != turn.selector
        or attempt.state != state
        or attempt.response_base64 is not None
        or attempt.receipt is not None
        or attempt.rejection is not None
        or manifest.tenant != run.tenant
        or manifest.principal != run.principal
        or manifest.run_id != run.run_id
        or manifest.turn_id != turn.turn_id
        or manifest.generation != attempt.generation
        or manifest.contour_head != run.contour_head
        or run.worker_session != manifest.worker_session
        or run.worker_session != attempt.worker_session
    ):
        raise LoopRejected("attempt state or original manifest identity differs")
    return attempt


def _advance(
    run: ExecutionRunRecord,
    attempt: ExecutionModelAttempt,
    *,
    captured: bool,
) -> ExecutionRunRecord:
    attempt = attempt.model_copy(update={"head": "pending"})
    attempt = attempt.model_copy(update={"head": "execution-attempt:" + attempt.digest()})
    previous = run.turns[-1]
    turn = ExecutionTurn.model_validate(
        previous.model_copy(
            update={
                "head": "pending",
                "state": "RESPONSE_AVAILABLE" if captured else "CALL_ACTIVE",
                "attempts": (*previous.attempts[:-1], attempt),
            }
        ).model_dump()
    )
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    result = run.model_copy(
        update={
            "head": "pending",
            "predecessor": run.head,
            "event": "ModelResponseCaptured" if captured else "ModelAttemptEmitted",
            "turns": (*run.turns[:-1], turn),
        }
    )
    return result.model_copy(update={"head": "loop:" + result.digest()})


def emit_execution_attempt(run: ExecutionRunRecord) -> ExecutionRunRecord:
    run = ExecutionRunRecord.model_validate_json(run.canonical_bytes())
    attempt = _selected(run, "PREPARED_NOT_EMITTED")
    return _advance(
        run, attempt.model_copy(update={"state": "EMITTED_OUTCOME_UNKNOWN"}), captured=False
    )


def capture_execution_response(
    run: ExecutionRunRecord, raw: bytes, receipt: str
) -> ExecutionRunRecord:
    run = ExecutionRunRecord.model_validate_json(run.canonical_bytes())
    attempt = _selected(run, "EMITTED_OUTCOME_UNKNOWN")
    if not receipt or len(raw) > run.policy.max_response_bytes:
        raise LoopRejected("missing transport receipt or response byte bound exceeded")
    return _advance(
        run,
        attempt.model_copy(
            update={
                "state": "RESPONSE_CAPTURED",
                "response_base64": base64.b64encode(raw).decode(),
                "receipt": receipt,
            }
        ),
        captured=True,
    )
