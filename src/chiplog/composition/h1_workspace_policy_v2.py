"""Inert, canonical H1 V2 workspace-policy and original-issuance evidence.

These wires bind bytes already selected by broker-owned readers.  They do not
authenticate deployment custody, an issuance journal reference, or currentness;
only the future authority-gate-held source adapter can make those checks.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Literal, Self

from pydantic import model_validator

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO, UInt64
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.capabilities.projections.workspace_boundary import SourceReference

from .r14_h1_workspace_issuance_contracts import (
    H1CalendarOriginalReadV1,
    H1DashboardIssuanceRefV1,
    H1PlanningSourcesV1,
    H1QueryProofV1,
    H1RetainedDecisionV1,
    H1WorkspaceIssuanceRefV1,
    H1WorkspaceSnapshotV1,
)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _base64(value: str, reason: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except ValueError as error:
        raise ValueError("H1 V2 " + reason) from error


class _H1ConversationRegistrationFields(RecoveryDTO):
    """Shared fields; each public wire supplies its own schema discriminator."""

    registration_id: Identity
    generation: UInt64
    origin_recipient_id: Identity
    accepted_policy: ExactHead
    conversation_id: Identity
    visible_channels: tuple[Identity, ...]

    @model_validator(mode="after")
    def canonical_visible_channels(self) -> Self:
        if (
            not self.visible_channels
            or self.visible_channels != tuple(sorted(self.visible_channels))
            or len(set(self.visible_channels)) != len(self.visible_channels)
        ):
            raise ValueError("H1 V2 visible channels must be nonempty, sorted, and unique")
        return self


class H1ConversationRegistrationV1(_H1ConversationRegistrationFields):
    """Canonical registration assertion, inert until custody authenticates it."""

    schema_id: Literal["chiplog.execution.h1-conversation-registration.v1"] = (
        "chiplog.execution.h1-conversation-registration.v1"
    )


class H1PreissuanceRegistrationV1(_H1ConversationRegistrationFields):
    """Resolved selection retained with V2 policy bytes; never a live capability."""

    schema_id: Literal["chiplog.execution.h1-preissuance-registration.v1"] = (
        "chiplog.execution.h1-preissuance-registration.v1"
    )
    deployment_id: Identity
    database_id: Identity
    database_genesis_digest: Digest
    tenant_id: Identity
    principal_id: Identity
    channel_id: Identity
    custody_entry_digest: Digest
    accepted_policy_selector: Literal["H1_OWNER_ISSUED_ORIGIN_EXACT_V1"]
    accepted_policy_bytes_base64: str
    output_scope_anchor: HermeticOutputScopeAnchorV1
    output_scope_ref: ExactHead
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1
    admitted_authentication_ref: ExactHead

    @model_validator(mode="after")
    def exact_owner_policy(self) -> Self:
        if self.channel_id not in self.visible_channels:
            raise ValueError("H1 V2 visible channels omit original channel")
        raw = _base64(self.accepted_policy_bytes_base64, "accepted policy bytes are invalid")
        try:
            policy = HermeticOutputPolicyV1.model_validate_json(raw)
        except ValueError as error:
            raise ValueError("H1 V2 accepted policy bytes do not decode") from error
        if policy.canonical_bytes() != raw:
            raise ValueError("H1 V2 accepted policy bytes are noncanonical")
        fingerprint = _digest(raw)
        expected = ExactHead(
            identity="h1-disclosure-policy",
            head="h1-disclosure-policy/" + fingerprint,
            fingerprint=fingerprint,
        )
        if self.accepted_policy != expected:
            raise ValueError("H1 V2 accepted policy head differs from owner policy bytes")
        if (
            policy.selected_resource_observation_ref != self.selected_resource_observation_ref
            or self.output_scope_anchor.selected_resource_observation_ref
            != self.selected_resource_observation_ref
        ):
            raise ValueError("H1 V2 accepted policy selected resource differs")
        return self


class H1WorkspacePolicyHeadsV1(RecoveryDTO):
    """The existing V1 workspace heads excluding the journal counter."""

    policy: Identity
    credential: Identity
    session: Identity
    contour: Identity
    deletion: Identity


class H1WorkspacePolicyV2(RecoveryDTO):
    schema_id: Literal["chiplog.workspace.policy.v2"] = "chiplog.workspace.policy.v2"
    tenant: Identity
    principal: Identity
    channel: Identity
    database: Identity
    endpoint: Identity
    heads: H1WorkspacePolicyHeadsV1
    sources: tuple[SourceReference, ...]
    registration: H1PreissuanceRegistrationV1

    @model_validator(mode="after")
    def registration_joins_workspace(self) -> Self:
        registration = self.registration
        if (
            registration.tenant_id != self.tenant
            or registration.principal_id != self.principal
            or registration.channel_id != self.channel
            or registration.database_id != self.database
        ):
            raise ValueError("H1 V2 registration differs from workspace binding")
        # This reference is a delivery disclosure policy.  The workspace-policy
        # physical record is checked only when a V2 original issuance is decoded.
        if registration.accepted_policy.identity == "workspace-policy":
            raise ValueError("H1 V2 accepted policy substitutes workspace policy")
        return self


def decode_h1_workspace_policy_v2(raw: bytes) -> H1WorkspacePolicyV2:
    """Decode one exact canonical V2 policy; this establishes no authority."""
    try:
        policy = H1WorkspacePolicyV2.model_validate_json(raw)
    except ValueError as error:
        raise ValueError("H1 V2 workspace policy schema differs") from error
    if policy.canonical_bytes() != raw:
        raise ValueError("H1 V2 workspace policy bytes are noncanonical")
    return policy


class H1WorkspaceSourcesV2(RecoveryDTO):
    """V1 source cut plus the exact physical V2 workspace-policy identity."""

    trusted_ingress_json: str
    conversation_bindings_json: tuple[str, ...]
    selected_loop_decisions: tuple[H1RetainedDecisionV1, ...]
    planning_sources: tuple[H1PlanningSourcesV1, ...]
    policy_record_id: Identity
    policy_payload_base64: str
    endpoint: Identity
    policy_owner: Literal["workspace_policy"]
    policy_schema_id: Literal["chiplog.workspace.policy.v2"]
    policy_payload_digest: Digest


class H1OriginalWorkspaceIssuanceV2(RecoveryDTO):
    """V2 counterpart of the original V1 cut, retaining all V1-shaped fields."""

    schema_id: Literal["chiplog.execution.h1-original-workspace-issuance.v2"] = (
        "chiplog.execution.h1-original-workspace-issuance.v2"
    )
    profile_id: Literal["chiplog.execution.h1-zero-call-recovery-frontier"] = (
        "chiplog.execution.h1-zero-call-recovery-frontier"
    )
    profile_version: Literal[2] = 2
    tenant: Identity
    run_id: Identity
    started_run_head: Identity
    turn_id: Identity
    worker_session: Identity
    workspace_member_json: str
    proposal_context_json: str
    snapshot: H1WorkspaceSnapshotV1
    queries: tuple[H1QueryProofV1, ...]
    calendar: H1CalendarOriginalReadV1
    sources: H1WorkspaceSourcesV2
    dashboard: H1DashboardIssuanceRefV1

    @model_validator(mode="after")
    def closed_query_slots(self) -> Self:
        expected = (
            ("history", "conversation.context_read.v1"),
            ("planning", "planning.workspace.read.v1"),
            ("journal", "journal.workspace.read.v1"),
            ("calendar", "calendar.workspace.read.v1"),
        )
        if tuple((proof.slot, proof.reader_id) for proof in self.queries) != expected:
            raise ValueError("H1 V2 original workspace query slots differ")
        return self

    @property
    def payload_digest(self) -> str:
        return _digest(self.canonical_bytes())


def _validate_snapshot_membership(issuance: H1OriginalWorkspaceIssuanceV2) -> None:
    snapshot = issuance.snapshot
    if tuple(row.commit_sequence for row in snapshot.publications) != tuple(
        range(1, snapshot.tenant_head + 1)
    ):
        raise ValueError("H1 V2 publication frontier differs")
    records = {row.record_id: row for row in snapshot.records}
    if len(records) != len(snapshot.records) or any(
        row.tenant_id != issuance.tenant for row in records.values()
    ):
        raise ValueError("H1 V2 record inventory differs")
    seen: set[str] = set()
    for publication in snapshot.publications:
        if publication.tenant_id != issuance.tenant:
            raise ValueError("H1 V2 publication tenant differs")
        members = publication.record_ids_json.splitlines()
        if not members or any(not member for member in members):
            raise ValueError("H1 V2 publication membership differs")
        for record_id in members:
            record = records.get(record_id)
            if (
                record is None
                or record.commit_sequence != publication.commit_sequence
                or record_id in seen
            ):
                raise ValueError("H1 V2 publication membership differs")
            seen.add(record_id)
    if seen != set(records):
        raise ValueError("H1 V2 unpublished physical member")


def _batch_id(issuance: H1OriginalWorkspaceIssuanceV2) -> str:
    try:
        value = json.loads(issuance.proposal_context_json)
        batch_id = value["batch"]["batch_id"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("H1 V2 original workspace batch differs") from error
    if not isinstance(batch_id, str) or not batch_id:
        raise ValueError("H1 V2 original workspace batch differs")
    return batch_id


def _validate_policy_member(issuance: H1OriginalWorkspaceIssuanceV2) -> None:
    source = issuance.sources
    raw = _base64(source.policy_payload_base64, "policy bytes are invalid")
    if _digest(raw) != source.policy_payload_digest:
        raise ValueError("H1 V2 policy digest differs")
    policy = decode_h1_workspace_policy_v2(raw)
    if policy.tenant != issuance.tenant or policy.endpoint != source.endpoint:
        raise ValueError("H1 V2 policy context differs")
    if policy.registration.accepted_policy.fingerprint == source.policy_payload_digest:
        raise ValueError("H1 V2 accepted policy substitutes workspace policy")
    records = {row.record_id: row for row in issuance.snapshot.records}
    row = records.get(source.policy_record_id)
    if row is None:
        raise ValueError("H1 V2 policy member is absent")
    if row.owner != source.policy_owner:
        raise ValueError("H1 V2 policy owner differs")
    if row.schema_id != source.policy_schema_id:
        raise ValueError("H1 V2 policy schema differs")
    if row.canonical_record_bytes() != raw:
        raise ValueError("H1 V2 policy member bytes differ")
    expected_record_id = (
        "workspace-policy:"
        + _digest(json.dumps([policy.tenant, policy.principal, policy.channel]).encode())
        + ":"
        + source.policy_payload_digest
    )
    if row.record_id != expected_record_id:
        raise ValueError("H1 V2 policy identity differs")
    publications = tuple(
        publication
        for publication in issuance.snapshot.publications
        if publication.record_ids_json == source.policy_record_id
    )
    if len(publications) != 1:
        raise ValueError("H1 V2 policy publication differs")
    publication = publications[0]
    if (
        publication.tenant_id != issuance.tenant
        or publication.operation_kind != "workspace.policy.h1.v2"
        or publication.idempotency_key != expected_record_id
        or publication.request_fingerprint != source.policy_payload_digest
        or publication.commit_sequence != row.commit_sequence
    ):
        raise ValueError("H1 V2 policy publication differs")


def decode_h1_original_workspace_issuance_v2(
    raw: bytes, reference: H1WorkspaceIssuanceRefV1
) -> H1OriginalWorkspaceIssuanceV2:
    """Decode retained V2 evidence with exact ref and physical member joins.

    ``reference`` remains an unauthenticated locator here.  Its journal entry
    and source-currentness must be re-opened by an issuer-held adapter.
    """
    try:
        issuance = H1OriginalWorkspaceIssuanceV2.model_validate_json(raw)
    except ValueError as error:
        raise ValueError("H1 V2 original workspace schema differs") from error
    if issuance.canonical_bytes() != raw:
        raise ValueError("H1 V2 original workspace bytes are noncanonical")
    if (
        reference.tenant != issuance.tenant
        or reference.payload_digest != _digest(raw)
        or reference.batch_id != _batch_id(issuance)
    ):
        raise ValueError("H1 V2 original workspace reference differs")
    _validate_snapshot_membership(issuance)
    _validate_policy_member(issuance)
    return issuance
