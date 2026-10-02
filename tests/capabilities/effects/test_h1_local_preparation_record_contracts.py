"""Closed, inert H1 local preparation record contracts."""

from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError
from tests.support.h1_selected_scope_source import build_h1_selected_scope_source

from chiplog.capabilities.agent_loop.delivery_contracts import (
    AcceptedDelivery,
    OriginSelection,
)
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as LoopHead
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.h1_local_preparation_contracts import (
    H1LocalPreparedCommentaryIntentV1,
    H1SelectedScopeSourceV1,
)
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    H1LocalPreparationRecordIntegrityError,
    decode_h1_local_prepared_commentary_member,
    h1_local_complete_owner_commitment,
    h1_local_intent_fingerprint,
    make_h1_local_prepared_commentary_member,
)
from chiplog.capabilities.effects.lifecycle_transition_contracts import SelectedEffectsSource
from chiplog.capabilities.effects.scoped_delivery_source_contracts import (
    decode_selected_delivery_intent,
)
from chiplog.capabilities.effects.scoped_intent_contracts import PreparedDeliveryBasisV3


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _loop_head(name: str) -> LoopHead:
    return LoopHead(identity=name, head=name + "/head", fingerprint=_sha(name.encode()))


def _effects_head(name: str) -> ExactHead:
    return ExactHead(subject_id=name, head=name + "/head", fingerprint=_sha(name.encode()))


def _scope_source() -> H1SelectedScopeSourceV1:
    return build_h1_selected_scope_source()


def _intent() -> H1LocalPreparedCommentaryIntentV1:
    source = _scope_source()
    delivery = AcceptedDelivery(
        delivery_id="delivery",
        acceptance=_loop_head("acceptance"),
        selection=OriginSelection(
            ingress_binding=_loop_head("ingress"), recipient=source.scope.recipient
        ),
        rendered_bytes=b"commentary",
        render_digest=_sha(b"commentary"),
        manifest_digest=_sha(b"manifest"),
        visibility=(_loop_head("visibility"),),
        provenance=(_loop_head("provenance"),),
        disclosure=(_loop_head("disclosure"),),
        narrowing=(),
        policy=source.scope.disclosure_policy.ref,
    )
    unsigned = H1LocalPreparedCommentaryIntentV1(
        intent_id="h1-local-commentary:intent",
        tenant_id="hermetic-tenant",
        database_id="database",
        principal_id="hermetic-principal",
        worker_session_id="worker",
        original_run=_loop_head("run"),
        captured_attempt=_loop_head("attempt"),
        delivery=delivery,
        basis=PreparedDeliveryBasisV3(
            source_cut=_effects_head("cut"),
            acceptance=_effects_head("acceptance"),
            completion_command_bytes=b"completion",
            delivery_observation_bytes=b"observation",
            loop_proposal_bytes=b"proposal",
        ),
        scope_anchor=source.anchor,
        scope_ref=source.current_result.scope_ref,
        disclosure_policy_ref=source.scope.disclosure_policy.ref,
        source_request_fingerprint=_sha(b"request"),
        fingerprint="0" * 64,
    )
    return unsigned.model_copy(update={"fingerprint": h1_local_intent_fingerprint(unsigned)})


def test_local_member_is_deterministic_and_closed_to_external_decoder() -> None:
    member = make_h1_local_prepared_commentary_member(_intent())
    assert decode_h1_local_prepared_commentary_member(member) == _intent()
    assert h1_local_complete_owner_commitment(member) == h1_local_complete_owner_commitment(member)
    source = SelectedEffectsSource(
        owner="effects",
        subject=_effects_head(member.record_id),
        schema_id=member.schema_id,
        canonical_record_bytes=member.canonical_record_bytes,
        selected_decision=_effects_head("decision"),
        physical_record=_effects_head("physical"),
    )
    with pytest.raises(ValueError, match="invalid selected delivery intent schema"):
        decode_selected_delivery_intent(source)


def test_local_member_rejects_noncanonical_or_changed_semantic_bytes() -> None:
    member = make_h1_local_prepared_commentary_member(_intent())
    with pytest.raises(H1LocalPreparationRecordIntegrityError, match="fingerprint"):
        decode_h1_local_prepared_commentary_member(
            member.model_copy(update={"fingerprint": "0" * 64})
        )
    with pytest.raises(H1LocalPreparationRecordIntegrityError, match="semantic"):
        changed = _intent().model_copy(update={"tenant_id": "other", "fingerprint": "0" * 64})
        raw = changed.canonical_bytes()
        decode_h1_local_prepared_commentary_member(
            member.model_copy(update={"canonical_record_bytes": raw, "fingerprint": _sha(raw)})
        )


def test_selected_scope_rejects_physical_and_logical_join_tampering() -> None:
    source = _scope_source()
    with pytest.raises(ValidationError, match="physical anchor"):
        H1SelectedScopeSourceV1(
            anchor=source.anchor,
            scope=source.scope,
            selected_decision_bytes=b"forged",
            selected_record_bytes=source.selected_record_bytes,
            current_request=source.current_request,
            current_result=source.current_result,
        )
    with pytest.raises(ValidationError, match="logical join"):
        H1SelectedScopeSourceV1(
            anchor=source.anchor,
            scope=source.scope,
            selected_decision_bytes=source.selected_decision_bytes,
            selected_record_bytes=source.selected_record_bytes,
            current_request=source.current_request,
            current_result=source.current_result.model_copy(
                update={"scope_ref": _loop_head("other")}
            ),
        )
