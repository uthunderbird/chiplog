"""Only wire consistency: self-consistent attestation is deliberately not proof."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1_CALL_MAX_BYTES,
    BrokerSelectedH1EvidenceV1,
    H1BrokerRouteBindingV1,
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1RetainedSelectedWrapperV1,
    decode_h1_candidate_call,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputScopeV1,
    IssueHermeticOutputScopeV1,
)
from tests.capabilities.deployment_trust.test_hermetic_output_scope_contracts import scope
from tests.capabilities.deployment_trust.test_output_scope_owner_seam import request


def call() -> H1OwnerCandidateCallV1:
    proposed = scope()
    intent = request().model_dump()
    intent.update(
        authenticated_cli_ref=request().authenticated_cli_ref,
        database_id=proposed.database_id,
        scope_id=proposed.scope_id,
        admitted_authentication_ref=proposed.admitted_authentication,
        selected_resource_observation_ref=proposed.selected_resource_observation_ref,
    )
    selected = IssueHermeticOutputScopeV1.model_validate(intent)
    retained = H1RetainedSelectedWrapperV1(
        initialization_envelope_bytes=b"claimed source",
        admitted_record_bytes=b"inert retained R17 record",
        authentication_result_bytes=b"claimed source",
        admitted_record_digest=hashlib.sha256(b"inert retained R17 record").hexdigest(),
    )
    evidence = BrokerSelectedH1EvidenceV1(
        route=H1BrokerRouteBindingV1(
            tenant_id="hermetic-tenant",
            database_id=selected.database_id,
            worker_session_id=selected.worker_session_id,
            broker_epoch=1,
            runtime_generation="generation",
            broker_session_id="broker-session",
            owner_session_id="owner-session",
            request_id="request",
        ),
        selected_request_bytes=selected.canonical_bytes(),
        request_digest=hashlib.sha256(selected.canonical_bytes()).hexdigest(),
        retained=retained,
        retained_wrapper_digest=hashlib.sha256(retained.canonical_bytes()).hexdigest(),
        recipient=proposed.recipient,
        trust_observation=selected.expected_trust_observation,
        trust_snapshot_digest=hashlib.sha256(b"inert snapshot").hexdigest(),
    )
    return H1OwnerCandidateCallV1(
        evidence=evidence, evidence_digest=hashlib.sha256(evidence.canonical_bytes()).hexdigest()
    )


def candidate(value: H1OwnerCandidateCallV1) -> H1OwnerCandidateV1:
    return H1OwnerCandidateV1(
        route=value.evidence.route,
        request_digest=value.evidence.request_digest,
        evidence_digest=value.evidence_digest,
        scope=scope(),
    )


def test_inert_roundtrip_and_pinned_candidate() -> None:
    value = call()
    assert decode_h1_candidate_call(value.canonical_bytes()) == value
    candidate(value).check_pinned_call(value)


def test_rehashed_splice_is_not_proof_and_differs_from_pinned_call() -> None:
    original = call()
    changed_retained = original.evidence.retained.model_dump()
    changed_retained.update(
        admitted_record_bytes=b"caller replacement",
        admitted_record_digest=hashlib.sha256(b"caller replacement").hexdigest(),
    )
    retained = H1RetainedSelectedWrapperV1.model_validate(changed_retained)
    changed = original.evidence.model_dump()
    changed.update(
        retained=retained,
        retained_wrapper_digest=hashlib.sha256(retained.canonical_bytes()).hexdigest(),
    )
    evidence = BrokerSelectedH1EvidenceV1.model_validate(changed)
    forged = H1OwnerCandidateCallV1(
        evidence=evidence, evidence_digest=hashlib.sha256(evidence.canonical_bytes()).hexdigest()
    )
    # A fully recomputed caller assertion still parses. Only a separately pinned
    # broker exchange catches this substitution; the DTO is no authenticity gate.
    with pytest.raises(ValueError, match="pinned broker call"):
        candidate(forged).check_pinned_call(original)


def test_physical_logical_swap_rejected_against_exact_request() -> None:
    data = call().evidence.model_dump()
    observation = data["trust_observation"]
    physical = observation["physical_journal_head"]["head"]
    observation["physical_journal_head"]["head"] = observation["logical_snapshot_head"]
    observation["logical_snapshot_head"] = physical
    with pytest.raises(ValidationError, match="context differs"):
        BrokerSelectedH1EvidenceV1.model_validate(data)


@pytest.mark.parametrize("field", ["policy", "ordered_mandates", "recipient"])
def test_caller_cannot_add_policy_mandates_or_recipient_to_intent(field: str) -> None:
    data = request().model_dump()
    data["authenticated_cli_ref"] = request().authenticated_cli_ref
    data[field] = "caller authority"
    with pytest.raises(ValidationError):
        IssueHermeticOutputScopeV1.model_validate(data)


def test_unknown_profile_and_oversized_retained_bytes_rejected() -> None:
    data = call().evidence.model_dump()
    data["source_profile"] = "unknown"
    with pytest.raises(ValidationError):
        BrokerSelectedH1EvidenceV1.model_validate(data)
    retained = call().evidence.retained.model_dump()
    retained["initialization_envelope_bytes"] = b"x" * 524_289
    with pytest.raises(ValidationError):
        H1RetainedSelectedWrapperV1.model_validate(retained)
    with pytest.raises(ValueError, match="wire bound"):
        decode_h1_candidate_call(b" " * (H1_CALL_MAX_BYTES + 1))


def test_noncanonical_wire_and_changed_fixed_policy_are_rejected() -> None:
    with pytest.raises(ValueError, match="not canonical"):
        decode_h1_candidate_call(call().canonical_bytes() + b" ")
    data = scope().model_dump()
    data["ordered_mandates"] = (scope().admitted_authentication,)
    with pytest.raises(ValidationError):
        HermeticOutputScopeV1.model_validate(data)
    policy = json.loads(scope().disclosure_policy.canonical_source_bytes)
    policy["external_delivery"] = True
    data = scope().model_dump()
    data["disclosure_policy"]["canonical_source_bytes"] = json.dumps(policy).encode()
    with pytest.raises(ValidationError):
        HermeticOutputScopeV1.model_validate(data)
