"""Bounded raw H0/R17 provenance reader for a retained H1 source wrapper.

This is deliberately a small historical seam.  It authenticates the two raw
journal selections which the wrapper merely *claims* to retain; it neither
opens current dispatch resources nor reads joined history.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1RetainedSelectedWrapperV1,
)
from chiplog.composition.common_execution_driver_contracts import DriveInputRequestV1
from chiplog.composition.r14_execution_inbox_records import (
    EXECUTION_INBOX_INITIALIZATION_OPERATION,
    RetainedInboxExecutionInitialization,
    inbox_initialization_command,
)
from chiplog.composition.r17_authenticated_records import (
    COMMAND_SCHEMA,
    decode_authentication,
)
from chiplog.composition.r17_ingress_history import record_head, wire_record
from chiplog.platform._owner_publication_contracts import SingleOwnerBatch
from chiplog.platform.ingress_authenticated_contracts import (
    AuthenticatedCustodyCommand,
    AuthenticatedCustodyRecord,
)
from chiplog.platform.ingress_custody_records import canonical
from chiplog.platform.owner_publications import SelectedOwnerDecision

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


@dataclass(frozen=True, slots=True)
class HistoricalH0R17Selection:
    """Authenticated raw selections; no fresh authority is represented here."""

    initialization: RetainedInboxExecutionInitialization
    initialization_decision_id: str
    admitted_record: AuthenticatedCustodyRecord
    admitted_decision: SelectedOwnerDecision


def _raw_h0(
    runtime: R14PlanningRuntime, retained: H1RetainedSelectedWrapperV1
) -> tuple[RetainedInboxExecutionInitialization, str, DriveInputRequestV1]:
    raw = retained.initialization_envelope_bytes
    matches: list[tuple[RetainedInboxExecutionInitialization, str, DriveInputRequestV1]] = []
    for decision_id, _, candidate in runtime._loop_decisions().entries():
        if candidate != raw:
            continue
        entry = json.loads(candidate)
        if (
            not isinstance(entry, dict)
            or entry.get("kind") != "DECIDED"
            or entry.get("operation_kind") != EXECUTION_INBOX_INITIALIZATION_OPERATION
        ):
            continue
        evidence_raw = entry.get("inbox_initialization")
        if not isinstance(evidence_raw, str):
            raise ValueError("selected H0 envelope lacks inbox initialization")
        evidence = RetainedInboxExecutionInitialization.model_validate_json(evidence_raw)
        command = inbox_initialization_command(evidence)
        if (
            evidence.canonical_bytes().decode() != evidence_raw
            or runtime._publication(entry) != command
            or command.idempotency_key != evidence.proposal.run.head
            or entry.get("operation_id") != command.idempotency_key
            or entry.get("expected_head") != evidence.expected_head
            or entry.get("predecessor") != evidence.predecessor_commitment
            or len(command.records) != 1
            or command.records[0].canonical_bytes != evidence.proposal.run.canonical_bytes()
        ):
            raise ValueError("selected H0 command or native Run membership differs")
        wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
        if wire.canonical_bytes() != evidence.driver_request_bytes:
            raise ValueError("selected H0 driver request is noncanonical")
        matches.append((evidence, decision_id, wire))
    if len(matches) != 1:
        raise ValueError("retained H0 envelope is missing or ambiguous in the raw journal")
    return matches[0]


def _raw_r17(
    runtime: R14PlanningRuntime,
    retained: H1RetainedSelectedWrapperV1,
    wire: DriveInputRequestV1,
) -> tuple[AuthenticatedCustodyRecord, SelectedOwnerDecision]:
    record = AuthenticatedCustodyRecord.model_validate_json(retained.admitted_record_bytes)
    if canonical(record) != retained.admitted_record_bytes:
        raise ValueError("retained R17 record is noncanonical")
    matches: list[tuple[AuthenticatedCustodyRecord, SelectedOwnerDecision]] = []
    for decision in runtime._owner_decisions().snapshot().decisions:
        batch = decision.prepared.request
        if not isinstance(batch, SingleOwnerBatch) or batch.command.schema_id != COMMAND_SCHEMA:
            continue
        selected_decision = wire.selected_source.selected_ingress_decision
        if (
            selected_decision.identity != decision.decision_id
            or selected_decision.head != decision.decision_head
            or selected_decision.fingerprint != decision.decision_fingerprint
        ):
            continue
        command = AuthenticatedCustodyCommand.model_validate_json(batch.command.canonical_bytes)
        if canonical(command) != batch.command.canonical_bytes:
            raise ValueError("selected R17 command is noncanonical")
        if record.command != command:
            raise ValueError("retained R17 record differs from its selected owner command")
        row = wire_record(record)
        selected = retained.selected_admitted_record_ref
        if (
            row.canonical_bytes != retained.admitted_record_bytes
            or record_head(record).model_dump() != selected.model_dump()
            or record.command.authentication_result_bytes != retained.authentication_result_bytes
            or command.token.token_id != wire.identity.original_ingress_identity.command_id
            or command.profile.tenant_id != wire.identity.tenant_id
            or batch.identity.tenant_id != command.profile.tenant_id
            or decision.tenant_commit_sequence != command.tenant_frontier + 1
        ):
            continue
        owner_call, authenticated = decode_authentication(command)
        if (
            authenticated.reference.tenant_id != command.profile.tenant_id
            or not authenticated.reference.principal_id
            or owner_call.request.offer.scope.replay_identity != command.command_id
        ):
            continue
        if batch.complete_records != (row,):
            raise ValueError("selected R17 physical record membership differs")
        matches.append((record, decision))
    if len(matches) != 1:
        raise ValueError(
            "retained R17 record is missing or ambiguous in the selected owner journal"
        )
    return matches[0]


def read_historical_h0_r17(
    runtime: R14PlanningRuntime, retained: H1RetainedSelectedWrapperV1
) -> HistoricalH0R17Selection:
    """Authenticate exact retained H0 and R17 sources under the registered gate."""
    if type(retained) is not H1RetainedSelectedWrapperV1:
        raise TypeError("historical H0/R17 reader requires H1RetainedSelectedWrapperV1")
    gate = runtime._authority_gate()
    with gate.hold():
        h0, h0_id, wire = _raw_h0(runtime, retained)
        record, decision = _raw_r17(runtime, retained, wire)
    return HistoricalH0R17Selection(h0, h0_id, record, decision)
