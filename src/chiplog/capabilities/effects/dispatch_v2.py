"""Effects-owned v2 first-send preparation; no I/O or authentication is performed here."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field

from .contracts import DispatchSemanticBinding, ExactHead, TransmissionAttempt
from .dispatch_authority_contracts import (
    CapturedSource,
    DispatchObservationDTO,
    SelectedEffectHistoryMember,
)
from .dispatch_v2_contracts import (
    AuthorizeDispatchV2,
    CommitFirstSendV2,
    CurrentDispatchInputsV2,
    DispatchMandateV2,
    DispatchPrecursorRequestV2,
    DispatchPrecursorResultV2,
    ExternalActionIntentV2,
    InitializedCallOrigin,
    PublishDispatchIntentV2,
)
from .domain import EffectRuleViolation, require_semantics
from .fences import WorkerFence


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def reference(subject: str, raw: bytes) -> ExactHead:
    fingerprint = digest(raw)
    return ExactHead(subject_id=subject, head=subject + "/" + fingerprint, fingerprint=fingerprint)


def intent_fingerprint(intent: ExternalActionIntentV2) -> str:
    body = intent.model_dump(mode="json", exclude={"fingerprint"})
    return digest(b"chiplog.effects.intent.v2\x00" + canonical(body))


def require_mandate(mandate: DispatchMandateV2) -> None:
    if digest(mandate.payload) != mandate.effect_fingerprint:
        raise EffectRuleViolation("DENIED", "mandate payload fingerprint differs")
    if mandate.horizon.not_before_ns >= mandate.horizon.expires_at_ns:
        raise EffectRuleViolation("DENIED", "empty or reversed adopted horizon")
    if len(set(mandate.bundle_members)) != len(mandate.bundle_members):
        raise EffectRuleViolation("DENIED", "duplicate inseparable bundle member")
    origin = mandate.origin
    if isinstance(origin, InitializedCallOrigin) and (
        origin.initialized_head.subject_id != origin.original_call_id
        or origin.initialization_run_head.subject_id != origin.original_run_id
    ):
        raise EffectRuleViolation("DENIED", "original subject and initialized revision differ")


def evaluate_precursor(
    request: DispatchPrecursorRequestV2, mandate: DispatchMandateV2
) -> DispatchPrecursorResultV2:
    require_mandate(mandate)
    if (
        request.mandate_digest != digest(mandate.canonical_bytes())
        or request.interpretation_policy != mandate.operation_profile
        or mandate.preexisting_authority_basis not in request.preexisting_source_heads
    ):
        raise EffectRuleViolation("DENIED", "precursor substitutes mandate or authority basis")
    return DispatchPrecursorResultV2(
        schema_id="chiplog.effects.dispatch-precursor-result.v2",
        request_digest=digest(request.canonical_bytes()),
        mandate_digest=request.mandate_digest,
        interpretation_policy=request.interpretation_policy,
        evaluation_evidence=request.preexisting_source_heads,
    )


def require_intent(intent: ExternalActionIntentV2) -> None:
    require_mandate(intent.mandate)
    acquisition = intent.acquisition
    adoption = acquisition.adoption
    if (
        intent_fingerprint(intent) != intent.fingerprint
        or adoption.mandate_bytes != intent.mandate.canonical_bytes()
        or adoption.display.fingerprint != digest(adoption.display_bytes)
        or adoption.ingress.fingerprint != digest(adoption.ingress_bytes)
        or acquisition.precursor_result
        != evaluate_precursor(acquisition.precursor_request, intent.mandate)
    ):
        raise EffectRuleViolation("DENIED", "intent/adoption/precursor digest graph differs")


class DispatchAuthorizationV2(DispatchObservationDTO):
    authorization: ExactHead
    intent: ExactHead
    expected_attempt: ExactHead
    mandate: ExactHead
    semantics: DispatchSemanticBinding
    fence: WorkerFence
    observation_fingerprint: str = Field(min_length=1)


class DispatchAttemptV2(DispatchObservationDTO):
    intent: ExternalActionIntentV2
    attempt: ExactHead
    state: Literal["INTENT_RECORDED", "DISPATCH_AUTHORIZED", "SEND_COMMITTED"]
    authorizations: tuple[DispatchAuthorizationV2, ...]
    transmissions: tuple[TransmissionAttempt, ...]


DispatchCommandV2 = Annotated[
    PublishDispatchIntentV2 | AuthorizeDispatchV2 | CommitFirstSendV2,
    Field(discriminator="schema_id"),
]


class DispatchRecordV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-record.v2"]
    record: ExactHead
    predecessor: ExactHead | None
    kind: Literal[
        "PLAN_EFFECT_PUBLISHED", "INTENT_ACCEPTED", "DISPATCH_AUTHORIZED", "SEND_COMMITTED"
    ]
    command: DispatchCommandV2
    snapshot: DispatchAttemptV2


class DispatchPreparationV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.dispatch-preparation.v2"]
    command: DispatchCommandV2
    previous: DispatchRecordV2 | None
    current: CurrentDispatchInputsV2


class PrecursorPreparationV2(DispatchObservationDTO):
    schema_id: Literal["chiplog.effects.precursor-preparation.v2"]
    request: DispatchPrecursorRequestV2
    mandate: DispatchMandateV2


def history_inventory(members: tuple[SelectedEffectHistoryMember, ...]) -> bytes:
    """Bind the complete ordered evidence once; full preimages remain in members."""
    return canonical(
        [
            {
                **member.model_dump(mode="json", exclude={"command_bytes", "record_bytes"}),
                "command_digest": digest(member.command_bytes),
                "record_digest": digest(member.record_bytes),
            }
            for member in members
        ]
    )


def _current(request: DispatchPreparationV2, intent: ExternalActionIntentV2) -> None:
    command, current, mandate = request.command, request.current, intent.mandate
    require_intent(intent)
    if (
        current.command_fingerprint != digest(command.canonical_bytes())
        or current.immutable_mandate != reference(mandate.mandate_id, mandate.canonical_bytes())
        or current.observation.query_fingerprint != current.command_fingerprint
        or current.observation.cut.tenant_id != mandate.tenant_id
        or current.observation.cut.tenant_frontier != command.identity.expected_tenant_head
        or current.observation.cut.source_role_registry != mandate.operation_profile
        or (current.clock_contract, current.clock_epoch)
        != (mandate.horizon.clock_contract, mandate.horizon.clock_epoch)
        or not mandate.horizon.not_before_ns
        <= current.observed_time_ns
        < min(mandate.horizon.expires_at_ns, current.lease_expires_at_ns)
    ):
        raise EffectRuleViolation("STALE", "mandate, operation, clock or fresh lease differs")
    require_semantics(
        mandate.semantics, current.supported_semantics, durable=request.previous is not None
    )
    for name in type(current.observation.sources).model_fields:
        source = getattr(current.observation.sources, name)
        if not isinstance(source, CapturedSource):
            raise EffectRuleViolation(
                "RECOVERY_HOLD", "required dispatch source unavailable: " + name
            )
        if (
            source.clock_contract != current.clock_contract
            or source.clock_epoch != current.clock_epoch
            or source.valid_until_ns <= current.observed_time_ns
            or source.head.fingerprint != digest(source.canonical_value)
        ):
            raise EffectRuleViolation("STALE", "dispatch source lease/bytes differs: " + name)
    sources = current.observation.sources
    assert isinstance(sources.runtime_and_fence, CapturedSource)
    assert isinstance(sources.normative_conflict_generation, CapturedSource)
    assert isinstance(sources.effects_history, CapturedSource)
    if (
        json.loads(sources.runtime_and_fence.canonical_value)["fence"]
        != command.fence.model_dump(mode="json")
        or ExactHead.model_validate_json(sources.normative_conflict_generation.canonical_value)
        != mandate.normative_conflict_generation
        or sources.effects_history.canonical_value
        != history_inventory(current.observation.complete_effect_history)
        or digest(
            canonical(
                [
                    member.model_dump(mode="json")
                    for member in current.observation.complete_effect_history
                ]
            )
        )
        != current.observation.complete_history_fingerprint
    ):
        raise EffectRuleViolation(
            "STALE", "current fence, normative generation or complete history differs"
        )


def prepare_dispatch(request: DispatchPreparationV2) -> DispatchRecordV2:
    command, previous = request.command, request.previous
    if isinstance(command, PublishDispatchIntentV2):
        if previous is not None:
            raise EffectRuleViolation("CONFLICT", "initial publication has a predecessor")
        intent = command.intent
        state: Literal["INTENT_RECORDED", "DISPATCH_AUTHORIZED", "SEND_COMMITTED"] = (
            "INTENT_RECORDED"
        )
        authorizations: tuple[DispatchAuthorizationV2, ...] = ()
        transmissions: tuple[TransmissionAttempt, ...] = ()
        kind: Literal[
            "PLAN_EFFECT_PUBLISHED", "INTENT_ACCEPTED", "DISPATCH_AUTHORIZED", "SEND_COMMITTED"
        ] = (
            "INTENT_ACCEPTED"
            if isinstance(intent.mandate.origin, InitializedCallOrigin)
            else "PLAN_EFFECT_PUBLISHED"
        )
    else:
        if previous is None:
            raise EffectRuleViolation("STALE", "dispatch predecessor absent")
        snapshot = previous.snapshot
        intent = snapshot.intent
        expected_intent = reference(intent.intent_id, intent.canonical_bytes())
        expected_mandate = reference(intent.mandate.mandate_id, intent.mandate.canonical_bytes())
        if (
            command.intent != expected_intent
            or command.immutable_mandate != expected_mandate
            or command.expected_attempt != snapshot.attempt
            or command.semantics != intent.mandate.semantics
        ):
            raise EffectRuleViolation("STALE", "dispatch intent, mandate or attempt differs")
        authorizations, transmissions = snapshot.authorizations, snapshot.transmissions
        if isinstance(command, AuthorizeDispatchV2):
            if snapshot.state != "INTENT_RECORDED":
                raise EffectRuleViolation("CONFLICT", "authorization requires initial attempt")
            authorization = DispatchAuthorizationV2(
                authorization=reference(
                    "authorization/" + command.identity.command_id, command.canonical_bytes()
                ),
                intent=expected_intent,
                expected_attempt=snapshot.attempt,
                mandate=expected_mandate,
                semantics=command.semantics,
                fence=command.fence,
                observation_fingerprint=digest(request.current.observation.canonical_bytes()),
            )
            authorizations = (*authorizations, authorization)
            state, kind = "DISPATCH_AUTHORIZED", "DISPATCH_AUTHORIZED"
        else:
            if (
                snapshot.state != "DISPATCH_AUTHORIZED"
                or transmissions
                or not authorizations
                or command.authorization != authorizations[-1].authorization
                or command.fence != authorizations[-1].fence
            ):
                raise EffectRuleViolation("STALE", "first send authorization is absent or retired")
            child_id = intent.intent_id + "/transmission/0"
            child_ref = reference(child_id, command.canonical_bytes())
            transmissions = (
                TransmissionAttempt(
                    transmission=child_ref,
                    intent=expected_intent,
                    ordinal=0,
                    semantics=command.semantics,
                    dispatch_time_ns=request.current.observed_time_ns,
                    payload_fingerprint=intent.mandate.effect_fingerprint,
                    recipient=intent.mandate.recipient,
                    idempotency_fence_key=intent.mandate.idempotency_fence_key,
                    send_commit=reference(command.identity.command_id, command.canonical_bytes()),
                    coverage_proof=None,
                ),
            )
            state, kind = "SEND_COMMITTED", "SEND_COMMITTED"
    _current(request, intent)
    # Attempt identity commits to the complete owner input without requiring the
    # resulting snapshot/record to contain its own hash preimage.
    attempt = reference("attempt/" + command.identity.command_id, request.canonical_bytes())
    snapshot = DispatchAttemptV2(
        intent=intent,
        attempt=attempt,
        state=state,
        authorizations=authorizations,
        transmissions=transmissions,
    )
    body = canonical(
        {
            "command": command.model_dump(mode="json"),
            "predecessor": None if previous is None else previous.record.model_dump(mode="json"),
            "snapshot": snapshot.model_dump(mode="json"),
            "kind": kind,
        }
    )
    return DispatchRecordV2(
        schema_id="chiplog.effects.dispatch-record.v2",
        record=reference("effect-record/" + command.identity.command_id, body),
        predecessor=None if previous is None else previous.record,
        kind=kind,
        command=command,
        snapshot=snapshot,
    )
