"""Independent v2 acceptance byte graph; no owner calls or publication authority."""

import base64
import hashlib
import json
from typing import Literal

from chiplog.capabilities.agent_loop.execution_contracts import ConsequentialToolCall
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.dispatch_v2 import canonical, digest, reference
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    InitializedCallOrigin,
    PublishDispatchIntentV2,
)

from ._pure_bytes import reuse_exact_bytes
from .r14_acceptance_contracts import (
    ACCEPTED_SCHEMA,
    EXECUTION_SCHEMA,
    MAX_ACCEPTANCE_BYTES,
    AcceptancePhysicalMember,
)
from .r14_acceptance_records import _require, _translate, _without, verify_loop_preparation
from .r14_acceptance_v2_contracts import (
    AcceptancePhysicalEnvelopeV2,
    RetainedAcceptancePreparationV2,
)
from .r14_call_acceptance_port import CallAcceptanceTarget
from .r14_call_dispatch_policy import (
    CLOCK,
    MAX_HORIZON_NS,
    SEMANTICS,
    bundle_references,
    loop_dispatch_semantics,
    policy_reference,
)

EFFECT_SCHEMA = "chiplog.effects.dispatch-record.v2"


def _verify_effects(retained: RetainedAcceptancePreparationV2) -> None:
    request, record = retained.effects_request, retained.effects_proposal
    command = request.command
    _require(isinstance(command, PublishDispatchIntentV2), "not initial intent publication")
    assert isinstance(command, PublishDispatchIntentV2)
    binding = retained.loop_request.binding
    intent, current = command.intent, request.current
    mandate, acquisition = intent.mandate, intent.acquisition
    origin = mandate.origin
    _require(isinstance(origin, InitializedCallOrigin), "not an initialized call origin")
    assert isinstance(origin, InitializedCallOrigin)
    raw = base64.b64decode(
        retained.loop_request.initialized_record.call.canonical_call_base64, validate=True
    )
    call = ConsequentialToolCall.model_validate_json(raw)
    _require(
        call.call_id == binding.original.model_call_label
        and origin.original_call_id == binding.original_call_id
        and origin.original_run_id == binding.original.original_run_id
        and origin.initialization_run_head.subject_id == origin.original_run_id
        and origin.initialized_head == _translate(binding.initialized)
        and origin.tool_name == call.tool
        and origin.tool_schema == "chiplog.request-self-effect.v2"
        and origin.tool_policy == _translate(binding.tool_policy)
        and origin.sealed_arguments == call.arguments.model_dump_json().encode()
        and mandate.payload == call.arguments.payload
        and mandate.bundle_members == bundle_references(binding.original_call_id, call.arguments),
        "original call, payload or complete bundle differs",
    )
    _require(
        mandate.semantics == SEMANTICS
        and current.supported_semantics == SEMANTICS
        and mandate.operation_profile == policy_reference()
        and mandate.horizon.clock_contract == CLOCK
        and mandate.effect_fingerprint == digest(mandate.payload)
        and 0 < mandate.horizon.expires_at_ns - mandate.horizon.not_before_ns <= MAX_HORIZON_NS
        and mandate.dependencies == ()
        and mandate.factual_assertion_evidence == ()
        and mandate.tenant_id == binding.cut.tenant_id == "hermetic-tenant"
        and mandate.principal_id == mandate.actor_id == "hermetic-principal"
        and mandate.recipient.provider == "hermetic-effects"
        and mandate.recipient.account == "hermetic-account"
        and mandate.recipient.recipient == "hermetic-principal"
        and mandate.recipient.canonical_address == b"hermetic://effects/hermetic-principal",
        "unregistered call policy or scope",
    )
    expected_intent_fingerprint = digest(
        b"chiplog.effects.intent.v2\x00"
        + canonical(intent.model_dump(mode="json", exclude={"fingerprint"}))
    )
    precursor, result, adoption = (
        acquisition.precursor_request,
        acquisition.precursor_result,
        acquisition.adoption,
    )
    target = CallAcceptanceTarget(
        original_call_id=binding.original_call_id,
        initialized=binding.initialized,
        current_run=binding.cut.current_run,
    )
    expected_display = json.dumps(
        {
            "kind": "CALL_ACCEPTANCE_DISPLAY_V2",
            "target": json.loads(target.canonical_bytes()),
            "mandate": json.loads(mandate.canonical_bytes()),
        },
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
    ).encode()
    _require(
        intent.fingerprint == expected_intent_fingerprint
        and adoption.mandate_bytes == mandate.canonical_bytes()
        and adoption.display_bytes == expected_display
        and adoption.display.fingerprint == digest(adoption.display_bytes)
        and adoption.ingress.fingerprint == digest(adoption.ingress_bytes)
        and precursor.mandate_digest == digest(mandate.canonical_bytes())
        and precursor.interpretation_policy == mandate.operation_profile
        and mandate.preexisting_authority_basis in precursor.preexisting_source_heads
        and result.request_digest == digest(precursor.canonical_bytes())
        and result.mandate_digest == precursor.mandate_digest
        and result.interpretation_policy == precursor.interpretation_policy
        and result.evaluation_evidence == precursor.preexisting_source_heads,
        "original acquisition or intent digest graph differs",
    )
    _require(
        _translate(binding.external_intent) == reference(intent.intent_id, intent.canonical_bytes())
        and command.complete_publication_manifest
        == tuple(_translate(ref) for ref in retained.loop_proposal.complete_acceptance_manifest)
        and command.identity.expected_tenant_head == binding.cut.tenant_commit_sequence
        and command.fence.canonical_bytes() == binding.cut.fence.canonical_bytes()
        and current.command_fingerprint == digest(command.canonical_bytes())
        and current.immutable_mandate == reference(mandate.mandate_id, mandate.canonical_bytes())
        and current.observation.query_fingerprint == current.command_fingerprint
        and current.observation.cut.tenant_id == mandate.tenant_id
        and current.observation.cut.tenant_frontier == command.identity.expected_tenant_head
        and current.observation.cut.materialization_commitment
        == binding.cut.materialization_commitment
        and current.observation.cut.source_role_registry == mandate.operation_profile
        and (current.clock_contract, current.clock_epoch)
        == (mandate.horizon.clock_contract, mandate.horizon.clock_epoch)
        and mandate.horizon.not_before_ns
        <= current.observed_time_ns
        < min(mandate.horizon.expires_at_ns, current.lease_expires_at_ns),
        "cross-owner manifest, cut, original intent or current input differs",
    )
    sources = current.observation.sources
    for name in type(sources).model_fields:
        source = getattr(sources, name)
        _require(isinstance(source, CapturedSource), "unavailable current source: " + name)
        assert isinstance(source, CapturedSource)
        _require(
            source.clock_contract == current.clock_contract
            and source.clock_epoch == current.clock_epoch
            and source.valid_until_ns > current.observed_time_ns
            and source.head.fingerprint == digest(source.canonical_value),
            "current source bytes or lease differs: " + name,
        )
    assert isinstance(sources.runtime_and_fence, CapturedSource)
    assert isinstance(sources.normative_conflict_generation, CapturedSource)
    assert isinstance(sources.effects_history, CapturedSource)
    history = current.observation.complete_effect_history
    history_bytes = canonical(
        [
            {
                **member.model_dump(mode="json", exclude={"command_bytes", "record_bytes"}),
                "command_digest": digest(member.command_bytes),
                "record_digest": digest(member.record_bytes),
            }
            for member in history
        ]
    )
    _require(
        json.loads(sources.runtime_and_fence.canonical_value)["fence"]
        == command.fence.model_dump(mode="json")
        and ExactHead.model_validate_json(sources.normative_conflict_generation.canonical_value)
        == mandate.normative_conflict_generation
        and sources.effects_history.canonical_value == history_bytes
        and current.observation.complete_history_fingerprint
        == digest(canonical([member.model_dump(mode="json") for member in history])),
        "current fence, normative generation or complete history differs",
    )
    snapshot = record.snapshot
    _require(
        request.previous is None
        and record.predecessor is None
        and record.kind == "INTENT_ACCEPTED"
        and record.command == command
        and snapshot.intent == intent
        and snapshot.state == "INTENT_RECORDED"
        and snapshot.authorizations == ()
        and snapshot.transmissions == ()
        and snapshot.attempt
        == reference("attempt/" + command.identity.command_id, request.canonical_bytes()),
        "not the exact initial owner result",
    )
    body = canonical(
        {
            "command": command.model_dump(mode="json"),
            "predecessor": None,
            "snapshot": snapshot.model_dump(mode="json"),
            "kind": "INTENT_ACCEPTED",
        }
    )
    _require(
        record.record == reference("effect-record/" + command.identity.command_id, body),
        "effects record preimage differs",
    )


def _member(
    identity: str,
    owner: Literal["agent_loop", "effects"],
    schema: str,
    raw: bytes,
) -> AcceptancePhysicalMember:
    return AcceptancePhysicalMember(
        record_id=identity,
        owner=owner,
        schema_id=schema,
        canonical_payload_base64=base64.b64encode(raw).decode(),
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def build_acceptance_envelope(
    retained: RetainedAcceptancePreparationV2,
) -> AcceptancePhysicalEnvelopeV2:
    return AcceptancePhysicalEnvelopeV2.model_validate_json(
        _envelope_bytes(retained.canonical_bytes())
    )


@reuse_exact_bytes
def _envelope_bytes(raw: bytes) -> bytes:
    retained = RetainedAcceptancePreparationV2.model_validate_json(raw)
    # Each owner has its own canonicalization domain, including bytes and key order.
    for value in (
        retained.loop_request,
        retained.loop_proposal,
        retained.effects_request,
        retained.effects_proposal,
    ):
        type(value).model_validate_json(value.canonical_bytes())
    verify_loop_preparation(
        retained.loop_request, retained.loop_proposal, loop_dispatch_semantics()
    )
    _verify_effects(retained)
    proposal = retained.loop_proposal
    effect = retained.effects_proposal
    members = (
        _member(
            proposal.accepted.accepted_id,
            "agent_loop",
            ACCEPTED_SCHEMA,
            proposal.accepted.canonical_bytes(),
        ),
        _member(
            proposal.execution_intent.execution_intent_id,
            "agent_loop",
            EXECUTION_SCHEMA,
            proposal.execution_intent.canonical_bytes(),
        ),
        _member(effect.record.head, "effects", EFFECT_SCHEMA, effect.canonical_bytes()),
    )
    _require(len({row.record_id for row in members}) == 3, "duplicate physical identity")
    _require(
        all("\n" not in row.record_id and "\r" not in row.record_id for row in members),
        "physical identity contains a membership delimiter",
    )
    envelope = AcceptancePhysicalEnvelopeV2(
        tenant_id=retained.loop_request.binding.cut.tenant_id,
        original_call_id=retained.loop_request.binding.original_call_id,
        expected_tenant_head=retained.loop_request.binding.cut.tenant_commit_sequence,
        retained_preparation_fingerprint=retained.digest(),
        complete_records=members,
        physical_batch_fingerprint="0" * 64,
    )
    envelope = envelope.model_copy(
        update={"physical_batch_fingerprint": _without(envelope, "physical_batch_fingerprint")}
    )
    _require(
        len(envelope.canonical_bytes()) <= MAX_ACCEPTANCE_BYTES, "physical byte bound exceeded"
    )
    return envelope.canonical_bytes()


def verify_acceptance_envelope(
    retained: RetainedAcceptancePreparationV2,
    envelope: AcceptancePhysicalEnvelopeV2,
) -> None:
    envelope = AcceptancePhysicalEnvelopeV2.model_validate_json(envelope.canonical_bytes())
    _require(envelope == build_acceptance_envelope(retained), "physical envelope differs")
