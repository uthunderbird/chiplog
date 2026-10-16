"""Functional V2/V3 execution transitions, independent of the storage substrate."""

from __future__ import annotations

import base64
import json
from typing import Literal

from .call_acceptance_contracts import CallSubjectHead
from .contracts import BudgetPolicy, DisclosureLabel, Frozen, LoopRejected, VisibilityMember
from .delivery_contracts import OriginSelection
from .domain import join_labels
from .execution_contracts import (
    ExecutionModelAttempt,
    ExecutionPromptArtifact,
    ExecutionRunRecord,
    ExecutionTurn,
    ExecutionVisibilityManifest,
)
from .execution_history_contracts import (
    ExecutionModelAttemptV3,
    ExecutionPromptArtifactV3,
    ExecutionRunRecordV3,
    ExecutionTurnV3,
    ExecutionVisibilityManifestV3,
)
from .recovery_contracts import Present, RecoveryDTO

type ExecutionRun = ExecutionRunRecord | ExecutionRunRecordV3
type ExecutionAttempt = ExecutionModelAttempt | ExecutionModelAttemptV3
type ExecutionArtifact = ExecutionPromptArtifact | ExecutionPromptArtifactV3


def reference(subject_id: str, value: Frozen | RecoveryDTO) -> CallSubjectHead:
    digest = value.digest()
    return CallSubjectHead(
        subject_id=subject_id, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def headed[T: Frozen](value: T, prefix: str) -> T:
    value = value.model_copy(update={"head": "pending"})
    return value.model_copy(update={"head": prefix + value.digest()})


def current(run: ExecutionRun) -> ExecutionRun:
    run = type(run).model_validate_json(run.canonical_bytes())
    if run.head != "loop:" + run.model_copy(update={"head": "pending"}).digest():
        raise LoopRejected("execution Run self head differs")
    return run


def advance(run: ExecutionRun, event: str, **changes: object) -> ExecutionRun:
    values = run.model_dump()
    values.update(changes)
    values.update(head="pending", predecessor=run.head, event=event)
    return headed(type(run).model_validate(values), "loop:")


def create(
    *,
    version: Literal["v2", "v3"],
    tenant: str,
    principal: str,
    run_id: str,
    prompt: str,
    policy: BudgetPolicy,
    origin: OriginSelection,
    worker: str,
) -> ExecutionRun:
    record_type = ExecutionRunRecordV3 if version == "v3" else ExecutionRunRecord
    return headed(
        record_type(
            tenant=tenant,
            principal=principal,
            run_id=run_id,
            prompt=prompt,
            policy=policy,
            origin=origin,
            contour_head="postgres-local.v1",
            policy_head="explicit-execution.v1",
            worker_session=worker,
            root_binding="NOT_APPLICABLE",
            state="CREATED",
            head="pending",
            predecessor=None,
            turns=(),
            delivery_acceptance=None,
            suspension_baseline=None,
            original_obligations=(),
            no_retry_references=(),
            event="RunCreated",
        ),
        "loop:",
    )


def begin(run: ExecutionRun) -> tuple[ExecutionRun, ExecutionRun]:
    run = current(run)
    if (
        run.state != "CREATED"
        or run.event != "RunCreated"
        or run.turns
        or run.predecessor is not None
    ):
        raise LoopRejected("initial Turn requires original empty ingress Run")
    active = advance(run, "RunActivated", state="ACTIVE")
    turn_type = ExecutionTurnV3 if isinstance(run, ExecutionRunRecordV3) else ExecutionTurn
    turn = headed(
        turn_type(
            turn_id=run.run_id + "/turn/1",
            ordinal=1,
            head="pending",
            state="PREPARING",
            accumulator=(),
            attempts=(),
            selector=0,
            response_seal=None,
            initialized_calls=None,
        ),
        "execution-turn:",
    )
    return active, advance(active, "TurnStarted", turns=(turn,))


def continue_turn(run: ExecutionRun) -> ExecutionRun:
    run = current(run)
    if (
        run.state != "ACTIVE"
        or not run.turns
        or run.turns[-1].state != "ACCEPTED"
        or run.turns[-1].response_seal is None
        or run.turns[-1].initialized_calls is None
    ):
        raise LoopRejected("continuation requires a sealed accepted Turn")
    if len(run.turns) >= run.policy.max_turns:
        raise LoopRejected("execution Turn budget exhausted")
    ordinal = len(run.turns) + 1
    turn_type = ExecutionTurnV3 if isinstance(run, ExecutionRunRecordV3) else ExecutionTurn
    turn = headed(
        turn_type(
            turn_id=f"{run.run_id}/turn/{ordinal}",
            ordinal=ordinal,
            head="pending",
            state="PREPARING",
            accumulator=(),
            attempts=(),
            selector=0,
            response_seal=None,
            initialized_calls=None,
        ),
        "execution-turn:",
    )
    return advance(run, "NextTurnStarted", turns=(*run.turns, turn))


def prepare(
    run: ExecutionRun,
    artifact: ExecutionArtifact,
    context: str,
) -> tuple[ExecutionRun, ExecutionRun]:
    run = current(run)
    if run.state != "ACTIVE" or not run.turns:
        raise LoopRejected("prepare requires active execution Turn")
    turn = run.turns[-1]
    if (
        turn.state != "PREPARING"
        or turn.attempts
        or turn.response_seal is not None
        or turn.initialized_calls is not None
    ):
        raise LoopRejected("cannot prepare an existing or sealed attempt")
    v3 = isinstance(run, ExecutionRunRecordV3)
    if v3 != isinstance(artifact, ExecutionPromptArtifactV3):
        raise LoopRejected("Run and prompt artifact versions differ")
    label = join_labels(
        (
            DisclosureLabel(
                value="ENDPOINT_RESTRICTED",
                allowed_endpoints=tuple(
                    sorted(
                        {run.origin.recipient.endpoint.identity, run.policy.live_model.recipient}
                    )
                )
                if run.policy.live_model is not None
                else (run.origin.recipient.endpoint.identity,),
            ),
            *(
                attempt.manifest.joined_label
                for prior in run.turns[:-1]
                for attempt in prior.attempts
            ),
        )
    )
    members = (
        VisibilityMember(
            record_id=turn.turn_id + "/context",
            revision_head=run.head,
            content=context,
            provenance_head=run.head,
            label_head=run.contour_head,
            label=label,
            producer="agent_loop",
            surface="context",
        ),
        VisibilityMember(
            record_id=artifact.prompt_id + "/" + artifact.version,
            revision_head=artifact.content_hash,
            content=artifact.rendered,
            provenance_head=artifact.content_hash,
            label_head=run.policy_head,
            label=label,
            producer="promptstrings",
            surface="prompt",
        ),
        VisibilityMember(
            record_id=turn.turn_id + "/schema",
            revision_head=artifact.digest(),
            content=artifact.response_schema_json,
            provenance_head=artifact.digest(),
            label_head=run.policy_head,
            label=DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
            producer="agent_loop",
            surface="schema",
        ),
    )
    accumulated_turn = headed(turn.model_copy(update={"accumulator": members}), "execution-turn:")
    accumulated = advance(
        run, "TurnVisibilityAccumulated", turns=(*run.turns[:-1], accumulated_turn)
    )
    manifest_type = ExecutionVisibilityManifestV3 if v3 else ExecutionVisibilityManifest
    manifest = manifest_type.model_validate(
        dict(
            tenant=run.tenant,
            principal=run.principal,
            contour_head=run.contour_head,
            run_id=run.run_id,
            turn_id=turn.turn_id,
            generation=0,
            worker_session=run.worker_session,
            members=tuple(member.model_dump() for member in members),
            joined_label=join_labels(tuple(member.label for member in members)).model_dump(),
            artifact=artifact.model_dump(),
        )
    )
    request = json.dumps(
        {
            "prompt": artifact.rendered,
            "schema": artifact.response_schema_json,
            "manifest": manifest.digest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(request.encode()) > run.policy.max_request_bytes:
        raise LoopRejected("request exceeds physical transport bound")
    lineage = turn.turn_id + "/slot/0"
    attempt_type = ExecutionModelAttemptV3 if v3 else ExecutionModelAttempt
    live = run.policy.live_model
    attempt = headed(
        attempt_type.model_validate(
            dict(
                attempt_id=lineage + "/generation/0",
                lineage_id=lineage,
                generation=0,
                state="PREPARED_NOT_EMITTED",
                head="pending",
                manifest=manifest.model_dump(),
                request=request,
                worker_session=run.worker_session,
                provider_contract="hermetic-model.v1" if live is None else live.provider,
                recipient="hermetic-model" if live is None else live.recipient,
                live_model=None if live is None else live.model_dump(),
                response_base64=None,
                receipt=None,
                rejection=None,
            )
        ),
        "execution-attempt:",
    )
    prepared_turn = headed(
        accumulated_turn.model_copy(update={"attempts": (attempt,), "state": "CALL_ACTIVE"}),
        "execution-turn:",
    )
    return accumulated, advance(
        accumulated, "ModelAttemptPrepared", turns=(*run.turns[:-1], prepared_turn)
    )


def _selected(run: ExecutionRun, expected: str) -> ExecutionAttempt:
    current(run)
    if run.state != "ACTIVE" or not run.turns:
        raise LoopRejected("missing active Run")
    turn = run.turns[-1]
    if (
        turn.state != "CALL_ACTIVE"
        or not turn.attempts
        or turn.selector != len(turn.attempts) - 1
        or turn.response_seal is not None
    ):
        raise LoopRejected("missing current unsealed attempt")
    attempt = turn.attempts[turn.selector]
    if (
        attempt.state != expected
        or attempt.worker_session != run.worker_session
        or attempt.manifest.worker_session != run.worker_session
        or attempt.response_base64 is not None
        or attempt.receipt is not None
    ):
        raise LoopRejected("attempt state or worker binding differs")
    return attempt


def _attempt_transition(
    run: ExecutionRun, attempt: ExecutionAttempt, captured: bool
) -> ExecutionRun:
    turn = run.turns[-1]
    changed = headed(
        turn.model_copy(
            update={
                "state": "RESPONSE_AVAILABLE" if captured else "CALL_ACTIVE",
                "attempts": (*turn.attempts[:-1], headed(attempt, "execution-attempt:")),
            }
        ),
        "execution-turn:",
    )
    return advance(
        run,
        "ModelResponseCaptured" if captured else "ModelAttemptEmitted",
        turns=(*run.turns[:-1], changed),
    )


def emit(run: ExecutionRun) -> ExecutionRun:
    attempt = _selected(run, "PREPARED_NOT_EMITTED")
    return _attempt_transition(
        run, attempt.model_copy(update={"state": "EMITTED_OUTCOME_UNKNOWN"}), False
    )


def capture(run: ExecutionRun, raw: bytes, receipt: str) -> ExecutionRun:
    attempt = _selected(run, "EMITTED_OUTCOME_UNKNOWN")
    if not receipt or len(raw) > run.policy.max_response_bytes:
        raise LoopRejected("missing transport receipt or response byte bound exceeded")
    return _attempt_transition(
        run,
        attempt.model_copy(
            update={
                "state": "RESPONSE_CAPTURED",
                "response_base64": base64.b64encode(raw).decode(),
                "receipt": receipt,
            }
        ),
        True,
    )
