"""Shared builders extracted without changing their behavior."""

import hashlib

from tests.support.capabilities.deployment_trust.hermetic_output_scope_contracts import scope
from tests.support.capabilities.deployment_trust.output_scope_owner_seam import (
    request,
)

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    BrokerSelectedH1EvidenceV1,
    H1BrokerRouteBindingV1,
    H1OwnerCandidateCallV1,
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    IssueHermeticOutputScopeV1,
)


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
        selected_admitted_record_ref=ExactHead(
            identity="selected-command/record",
            head="selected-command/record/"
            + hashlib.sha256(b"inert retained R17 record").hexdigest(),
            fingerprint=hashlib.sha256(b"inert retained R17 record").hexdigest(),
        ),
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
