"""Pure preparation of the two scoped V3 pre-send transitions.

This owner only turns already selected, retained inputs into deterministic
records.  Authentication, writer-cut selection, publication, permits and
provider I/O remain outside this module.  The publication boundary must
authenticate each retained ``selected_decision`` against its journal/SQL
selection; this pure owner commits the supplied selected-source envelope only
through the exact request fingerprint and never reinterprets it as authority.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, cast

from .contracts import ExactHead, TransmissionAttempt
from .dispatch_authority_contracts import CapturedSource
from .dispatch_v2 import history_inventory, reference
from .dispatch_v2_contracts import CurrentDispatchInputsV2, DispatchBoundaryFailureV2
from .domain import EffectRuleViolation, require_semantics
from .fences import Absent
from .h1_scoped_dispatch_semantics import (
    PREPARE_SCOPED_AUTHORIZATION_V3,
    PREPARE_SCOPED_FIRST_SEND_V3,
    require_h1_scoped_dispatch_operation,
)
from .lifecycle_transition_contracts import SelectedEffectsSource
from .scoped_authorization_record_contracts import (
    AUTHORIZATION_SCHEMA,
    REVISION_SCHEMA,
    scoped_dispatch_authorization_head,
    scoped_pre_send_revision_head,
)
from .scoped_dispatch_contracts import (
    PreparedScopedAuthorization,
    PreparedScopedFirstSend,
    PrepareScopedAuthorization,
    PrepareScopedFirstSend,
    ScopedDispatchRequest,
    ScopedFirstSendDecision,
    ScopedFirstSendParent,
    ScopedPreSendRevision,
    SelectedScopedAuthorization,
)
from .scoped_intent_contracts import (
    ExternalActionIntentV3,
    ScopedAuthorityRecord,
    ScopedDispatchAuthorizationRecord,
)
from .scoped_intent_record_contracts import (
    decode_scoped_intent_member,
    make_scoped_intent_member,
)

_AUTHORIZATION_BATCH_DOMAIN = b"chiplog.effects.scoped-authorization-batch.v3\x00"
_FIRST_SEND_BATCH_DOMAIN = b"chiplog.effects.scoped-first-send-batch.v3\x00"
_FIRST_SEND_HEAD_DOMAIN = b"chiplog.effects.scoped-first-send.v3\x00"


class _Rejected(ValueError):
    pass


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Rejected(reason)


def _failure(error: Exception) -> DispatchBoundaryFailureV2:
    code = error.code if isinstance(error, EffectRuleViolation) else "DENIED"
    kinds: dict[
        str, Literal["DENIED", "STALE", "CONFLICT", "UNAVAILABLE", "DISPATCH_VERSION_HOLD"]
    ] = {
        "DENIED": "DENIED",
        "STALE": "STALE",
        "CONFLICT": "CONFLICT",
        "UNAVAILABLE": "UNAVAILABLE",
        "DISPATCH_VERSION_HOLD": "DISPATCH_VERSION_HOLD",
    }
    kind = kinds.get(code, "DENIED")
    return DispatchBoundaryFailureV2(
        schema_id="chiplog.effects.dispatch-boundary-failure.v2",
        kind=kind,
        source_role=None,
        reason=str(error) or "inconsistent scoped dispatch input",
    )


def _require_authority(source: ScopedAuthorityRecord) -> None:
    _require(
        source.head.fingerprint == _sha(source.canonical_record_bytes),
        "scoped authority source fingerprint differs",
    )


def _require_authorities(values: tuple[ScopedAuthorityRecord, ...]) -> None:
    _require(len(set(item.head for item in values)) == len(values), "duplicate current authority")
    for value in values:
        _require_authority(value)


def _require_selected(
    source: SelectedEffectsSource,
    *,
    schema_id: str | None = None,
    record_bytes: bytes | None = None,
    expected: ExactHead | None = None,
) -> None:
    _require(source.owner == "effects", "selected source owner differs")
    _require(source.subject == source.physical_record, "selected source physical record differs")
    _require(
        source.subject.fingerprint == _sha(source.canonical_record_bytes),
        "selected source fingerprint differs",
    )
    if schema_id is not None:
        _require(source.schema_id == schema_id, "selected source schema differs")
    if record_bytes is not None:
        _require(source.canonical_record_bytes == record_bytes, "selected source bytes differ")
    if expected is not None:
        _require(source.subject == expected, "selected source head differs")


def _require_current(
    request: PrepareScopedAuthorization | PrepareScopedFirstSend,
    intent: ExternalActionIntentV3,
    *,
    operation: str,
) -> None:
    current: CurrentDispatchInputsV2 = request.current
    mandate = intent.mandate
    _require(
        intent == decode_scoped_intent_member(make_scoped_intent_member(intent)), "intent differs"
    )
    _require(
        current.command_fingerprint == request.identity.fingerprint
        and current.observation.query_fingerprint == current.command_fingerprint,
        "current command fingerprint differs",
    )
    _require(
        current.immutable_mandate == reference(mandate.mandate_id, mandate.canonical_bytes())
        and current.observation.cut.tenant_id == mandate.tenant_id
        and current.observation.cut.tenant_frontier == request.identity.expected_tenant_head
        and current.observation.cut.source_role_registry == mandate.operation_profile
        and (current.clock_contract, current.clock_epoch)
        == (mandate.horizon.clock_contract, mandate.horizon.clock_epoch)
        and mandate.horizon.not_before_ns
        <= current.observed_time_ns
        < min(mandate.horizon.expires_at_ns, current.lease_expires_at_ns),
        "current mandate, cut, clock or lease differs",
    )
    require_semantics(mandate.semantics, current.supported_semantics, durable=True)
    original_sources = intent.acquisition.original_sources
    original_semantic_registry = getattr(original_sources, "semantic_registry", None)
    current_semantic_registry = current.observation.sources.semantic_registry
    if not isinstance(original_semantic_registry, CapturedSource) or not isinstance(
        current_semantic_registry, CapturedSource
    ):
        raise EffectRuleViolation(
            "DISPATCH_VERSION_HOLD", "scoped dispatch semantic registry is unavailable"
        )
    require_h1_scoped_dispatch_operation(
        semantics=mandate.semantics,
        operation=operation,
        original=original_semantic_registry,
        current=current_semantic_registry,
        clock_contract=current.clock_contract,
        clock_epoch=current.clock_epoch,
        valid_until_ns=current.lease_expires_at_ns,
    )
    for name in type(current.observation.sources).model_fields:
        source = getattr(current.observation.sources, name)
        _require(
            isinstance(source, CapturedSource), "required dispatch source unavailable: " + name
        )
        assert isinstance(source, CapturedSource)
        _require(
            source.clock_contract == current.clock_contract
            and source.clock_epoch == current.clock_epoch
            and source.valid_until_ns > current.observed_time_ns
            and source.head.fingerprint == _sha(source.canonical_value),
            "dispatch source lease or bytes differs: " + name,
        )
    sources = current.observation.sources
    assert isinstance(sources.runtime_and_fence, CapturedSource)
    assert isinstance(sources.normative_conflict_generation, CapturedSource)
    assert isinstance(sources.effects_history, CapturedSource)
    _require(
        json.loads(sources.runtime_and_fence.canonical_value)["fence"]
        == request.fence.model_dump(mode="json")
        and ExactHead.model_validate_json(sources.normative_conflict_generation.canonical_value)
        == mandate.normative_conflict_generation
        and sources.effects_history.canonical_value
        == history_inventory(current.observation.complete_effect_history)
        and _sha(
            json.dumps(
                [
                    item.model_dump(mode="json")
                    for item in current.observation.complete_effect_history
                ],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        )
        == current.observation.complete_history_fingerprint,
        "current fence, reducer source or complete history differs",
    )
    _require_authorities(request.complete_current_origin_sources)


def _intent_source(request: PrepareScopedAuthorization | PrepareScopedFirstSend) -> ExactHead:
    intent = request.observed.original_intent
    source = request.observed.selected_intent
    member = make_scoped_intent_member(intent)
    expected = ExactHead(
        subject_id=member.record_id,
        head=member.record_id,
        fingerprint=member.fingerprint,
    )
    _require_selected(
        source,
        schema_id="chiplog.effects.external-action-intent.v3",
        record_bytes=member.canonical_record_bytes,
        expected=expected,
    )
    return expected


def _initial_parent_source(
    request: PrepareScopedAuthorization, intent_head: ExactHead
) -> ExactHead:
    _require(
        request.observed.current_parent == request.observed.selected_intent,
        "initial parent is not the selected intent source",
    )
    return intent_head


def _authorization_head(record: ScopedDispatchAuthorizationRecord) -> ExactHead:
    return reference(record.authorization_id, record.canonical_bytes())


def _first_send_head(decision: ScopedFirstSendDecision) -> ExactHead:
    fingerprint = _sha(_FIRST_SEND_HEAD_DOMAIN + decision.canonical_bytes())
    return ExactHead(
        subject_id=decision.identity.command_id,
        head="scoped-first-send:" + fingerprint,
        fingerprint=fingerprint,
    )


def _authorization_record(
    request: PrepareScopedAuthorization,
    intent_head: ExactHead,
    parent_head: ExactHead,
) -> ScopedDispatchAuthorizationRecord:
    return ScopedDispatchAuthorizationRecord(
        authorization_id="scoped-authorization/" + request.identity.command_id,
        command=request.identity,
        original_intent=intent_head,
        expected_parent=parent_head,
        immutable_mandate=reference(
            request.observed.original_intent.mandate.mandate_id,
            request.observed.original_intent.mandate.canonical_bytes(),
        ),
        semantics=request.observed.original_intent.mandate.semantics,
        complete_current_origin_sources=request.complete_current_origin_sources,
        current_inputs_fingerprint=_sha(request.current.canonical_bytes()),
        fence=request.fence,
    )


def prepare_scoped_authorization(
    request: PrepareScopedAuthorization,
) -> PreparedScopedAuthorization | DispatchBoundaryFailureV2:
    """Prepare authorization then its DISPATCH_AUTHORIZED parent, without publication."""
    try:
        request = PrepareScopedAuthorization.model_validate_json(request.canonical_bytes())
        _require(
            request.observed.state == "INTENT_RECORDED", "authorization requires initial parent"
        )
        _require(
            isinstance(request.observed.authorization, Absent), "authorization already selected"
        )
        intent_head = _intent_source(request)
        previous_parent = _initial_parent_source(request, intent_head)
        _require_current(
            request, request.observed.original_intent, operation=PREPARE_SCOPED_AUTHORIZATION_V3
        )
        authorization = _authorization_record(request, intent_head, previous_parent)
        authorization_head = _authorization_head(authorization)
        parent = ScopedPreSendRevision(
            original_intent=intent_head,
            predecessor=previous_parent,
            state="DISPATCH_AUTHORIZED",
            authorization=authorization_head,
            retired_authorizations=(),
            current_inputs_fingerprint=_sha(request.current.canonical_bytes()),
            complete_current_origin_sources=request.complete_current_origin_sources,
        )
        request_fingerprint = _sha(request.canonical_bytes())
        return PreparedScopedAuthorization(
            source_request_fingerprint=request_fingerprint,
            authorization=authorization,
            parent=parent,
            complete_batch_fingerprint=_sha(
                _AUTHORIZATION_BATCH_DOMAIN
                + request_fingerprint.encode()
                + authorization.canonical_bytes()
                + parent.canonical_bytes()
            ),
        )
    except (TypeError, ValueError, AttributeError, KeyError, json.JSONDecodeError) as error:
        return _failure(error)


def _selected_authorization(
    request: PrepareScopedFirstSend, intent_head: ExactHead
) -> ScopedDispatchAuthorizationRecord:
    selected = request.observed.authorization
    _require(
        isinstance(selected, type(request.observed.authorization))
        and not isinstance(selected, Absent),
        "authorization absent",
    )
    _require(isinstance(selected, SelectedScopedAuthorization), "selected authorization differs")
    selected = cast(SelectedScopedAuthorization, selected)
    record = selected.record
    expected = scoped_dispatch_authorization_head(record)
    source = selected.source
    _require(
        source.owner == "effects"
        and source.schema_id == AUTHORIZATION_SCHEMA
        and source.subject == expected
        and source.canonical_record_bytes == record.canonical_bytes()
        and source.subject.fingerprint == _sha(source.canonical_record_bytes),
        "selected authorization semantic source differs",
    )
    expected_physical = ExactHead(
        subject_id=expected.head,
        head=expected.head,
        fingerprint=expected.fingerprint,
    )
    _require(
        source.physical_record == expected_physical,
        "selected authorization physical row differs",
    )
    _require(request.selected_authorization == expected, "requested authorization differs")
    _require(
        record.original_intent == intent_head
        and record.immutable_mandate
        == reference(
            request.observed.original_intent.mandate.mandate_id,
            request.observed.original_intent.mandate.canonical_bytes(),
        )
        and record.semantics == request.observed.original_intent.mandate.semantics,
        "authorization intent, parent, mandate or semantics differs",
    )
    _require_authorities(record.complete_current_origin_sources)
    return record


def _authorized_parent_source(
    request: PrepareScopedFirstSend,
    intent_head: ExactHead,
    authorization: ScopedDispatchAuthorizationRecord,
) -> ExactHead:
    source = request.observed.current_parent
    _require(source.owner == "effects", "selected parent owner differs")
    try:
        parent = ScopedPreSendRevision.model_validate_json(source.canonical_record_bytes)
    except ValueError as error:
        raise _Rejected("selected parent is not a scoped pre-send revision") from error
    _require(
        parent.canonical_bytes() == source.canonical_record_bytes,
        "selected parent bytes are not canonical",
    )
    _require(
        source.schema_id == REVISION_SCHEMA
        and source.subject == scoped_pre_send_revision_head(parent),
        "selected parent schema or head differs",
    )
    expected_physical = ExactHead(
        subject_id=source.subject.head,
        head=source.subject.head,
        fingerprint=source.subject.fingerprint,
    )
    _require(
        source.physical_record == expected_physical
        and source.physical_record.fingerprint == _sha(source.canonical_record_bytes),
        "selected parent physical row differs",
    )
    authorization_head = _authorization_head(authorization)
    _require(
        parent.state == "DISPATCH_AUTHORIZED"
        and parent.original_intent == intent_head
        and parent.authorization == authorization_head
        and parent.predecessor == authorization.expected_parent
        and parent.current_inputs_fingerprint == authorization.current_inputs_fingerprint
        and parent.complete_current_origin_sources == authorization.complete_current_origin_sources,
        "selected authorized parent differs from authorization",
    )
    return source.subject


def prepare_scoped_first_send(
    request: PrepareScopedFirstSend,
) -> PreparedScopedFirstSend | DispatchBoundaryFailureV2:
    """Prepare ordinal-zero decision, transmission and SEND_COMMITTED parent."""
    try:
        request = PrepareScopedFirstSend.model_validate_json(request.canonical_bytes())
        _require(
            request.observed.state == "DISPATCH_AUTHORIZED", "first send requires authorized parent"
        )
        _require(request.ordinal == 0, "first send ordinal differs")
        intent_head = _intent_source(request)
        _require_current(
            request, request.observed.original_intent, operation=PREPARE_SCOPED_FIRST_SEND_V3
        )
        authorization = _selected_authorization(request, intent_head)
        prior_parent = _authorized_parent_source(request, intent_head, authorization)
        request_fingerprint = _sha(request.canonical_bytes())
        decision = ScopedFirstSendDecision(
            identity=request.identity,
            source_request_fingerprint=request_fingerprint,
            original_intent=intent_head,
            authorization=request.selected_authorization,
            prior_parent=prior_parent,
            ordinal=0,
            current_inputs_fingerprint=_sha(request.current.canonical_bytes()),
            complete_current_origin_sources=request.complete_current_origin_sources,
            fence=request.fence,
        )
        decision_head = _first_send_head(decision)
        mandate = request.observed.original_intent.mandate
        child = TransmissionAttempt(
            transmission=reference(
                intent_head.subject_id + "/transmission/0", decision.canonical_bytes()
            ),
            intent=intent_head,
            ordinal=0,
            semantics=mandate.semantics,
            dispatch_time_ns=request.current.observed_time_ns,
            payload_fingerprint=mandate.effect_fingerprint,
            recipient=mandate.recipient,
            idempotency_fence_key=mandate.idempotency_fence_key,
            send_commit=decision_head,
            coverage_proof=None,
        )
        child_head = reference(child.transmission.subject_id, child.canonical_bytes())
        parent = ScopedFirstSendParent(
            original_intent=intent_head,
            predecessor=prior_parent,
            state="SEND_COMMITTED",
            send_decision=decision_head,
            first_child=child_head,
        )
        return PreparedScopedFirstSend(
            source_request_fingerprint=request_fingerprint,
            decision=decision,
            child=child,
            parent=parent,
            complete_batch_fingerprint=_sha(
                _FIRST_SEND_BATCH_DOMAIN
                + decision.canonical_bytes()
                + child.canonical_bytes()
                + parent.canonical_bytes()
            ),
        )
    except (TypeError, ValueError, AttributeError, KeyError, json.JSONDecodeError) as error:
        return _failure(error)


def prepare_scoped_dispatch(
    request: ScopedDispatchRequest,
) -> PreparedScopedAuthorization | PreparedScopedFirstSend | DispatchBoundaryFailureV2:
    """Route the two implemented pre-send requests; hold every other scoped variant."""
    if isinstance(request, PrepareScopedAuthorization):
        return prepare_scoped_authorization(request)
    if isinstance(request, PrepareScopedFirstSend):
        return prepare_scoped_first_send(request)
    return _failure(_Rejected("scoped request is not implemented by pre-send preparation"))


__all__ = [
    "prepare_scoped_authorization",
    "prepare_scoped_dispatch",
    "prepare_scoped_first_send",
]
