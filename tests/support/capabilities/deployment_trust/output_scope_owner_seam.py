"""Shared builders extracted without changing their behavior."""

import hashlib

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust import (
    IssueHermeticOutputScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
    SelectedHermeticResourceObservationRefV1,
    TrustReference,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputScopeAnchorV1,
    HermeticTrustObservationV1,
)
from chiplog.domain_primitives import PrincipalId, TenantId


def head(identity: str) -> ExactHead:
    return ExactHead(
        identity=identity, head=identity, fingerprint=hashlib.sha256(b"inert").hexdigest()
    )


def request() -> IssueHermeticOutputScopeV1:
    return IssueHermeticOutputScopeV1(
        slot_id="h1-cli-effects-origin",
        database_id="db",
        scope_id="scope",
        expected_trust_observation=HermeticTrustObservationV1(
            physical_journal_head=head("physical-journal"),
            logical_snapshot_head="logical-snapshot",
        ),
        expected_scope_predecessor=None,
        expected_revision=0,
        authenticated_cli_ref=TrustReference(
            TenantId("hermetic-tenant"),
            PrincipalId("hermetic-principal"),
            "CLI",
            "credential",
            "session",
            "source",
            hashlib.sha256(b"trust-binding").hexdigest(),
            "materialization",
            0,
            "peer",
        ),
        admitted_authentication_ref=head("selected-r17"),
        worker_session_id="worker",
        selected_resource_observation_ref=SelectedHermeticResourceObservationRefV1(
            signature_domain="dispatch-resources.v1",
            selected_initialization=head("initialization"),
            signed_observation_fingerprint=hashlib.sha256(b"inert signed observation").hexdigest(),
        ),
    )


def read_request() -> ReadCurrentHermeticExecutionScopeV1:
    issue = request()
    return ReadCurrentHermeticExecutionScopeV1(
        expected_trust_observation=issue.expected_trust_observation,
        source_anchor=HermeticOutputScopeAnchorV1(
            owner_id="deployment_trust",
            decision=head("physical-scope-decision"),
            record_ordinal=1,
            record_type_id="chiplog.deployment_trust.hermetic_output_scope",
            schema_id="chiplog.deployment_trust.record.v1",
            record=head("scope-record"),
            scope_revision=0,
            predecessor=None,
            selected_resource_observation_ref=issue.selected_resource_observation_ref,
        ),
        expected_revision=0,
        admitted_authentication_ref=issue.admitted_authentication_ref,
        authenticated_cli_ref=issue.authenticated_cli_ref,
        tenant_id="hermetic-tenant",
        database_id="db",
        scope_id="scope",
        expected_scope_ref=head("scope"),
        expected_worker_session_id="worker",
        selected_resource_observation_ref=issue.selected_resource_observation_ref,
    )
