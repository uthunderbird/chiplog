"""Complete ordered effects history for the separately versioned dispatch route.

Callers must supply a cut acquired by read_materialized_effects. This verifier
does not turn a caller-constructed cut into authenticated storage evidence.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from chiplog.adapters.driven.effects_queries import StoredEffectRow
from chiplog.capabilities.effects.contracts import EffectRecord, ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import SelectedEffectHistoryMember
from chiplog.capabilities.effects.dispatch_outcome_contracts import (
    DispatchOutcomePreparationV2,
    DispatchOutcomeRecordV2,
)
from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
from chiplog.capabilities.effects.dispatch_v2 import (
    DispatchPreparationV2,
    DispatchRecordV2,
    canonical,
    digest,
    prepare_dispatch,
    reference,
)
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    ExternalActionIntentV2,
    PlanEffectOrigin,
    PublishDispatchIntentV2,
)
from chiplog.capabilities.planning._planning import _PlanningPublication
from chiplog.composition.r16_effects import MaterializedEffectsCut
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import SelectedOwnerDecision

if TYPE_CHECKING:
    from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime

PREPARATION_SCHEMA = "chiplog.effects.dispatch-preparation.v2"
RECORD_SCHEMA = "chiplog.effects.dispatch-record.v2"


@dataclass(frozen=True)
class VerifiedDispatchHistory:
    members: tuple[SelectedEffectHistoryMember, ...]
    v2_records: tuple[DispatchRecordV2 | DispatchOutcomeRecordV2, ...]


def _request(decision: SelectedOwnerDecision) -> DispatchPreparationV2:
    batch = decision.prepared.request
    if batch.kind == "PLAN_EFFECT_ATOMIC":
        command = batch.effects_command
    elif batch.kind == "SINGLE_OWNER":
        command = batch.command
    elif batch.kind == "CALL_EFFECT_ATOMIC":
        command = batch.effects_command
    else:
        raise ValueError("v2 dispatch record has unregistered publication envelope")
    if command.owner != "effects" or command.schema_id != PREPARATION_SCHEMA:
        raise ValueError("v2 dispatch record has another owner or command schema")
    request = DispatchPreparationV2.model_validate_json(command.canonical_bytes)
    if (
        request.canonical_bytes() != command.canonical_bytes
        or command.fingerprint != digest(command.canonical_bytes)
        or request.command.identity.command_id != batch.identity.command_id
        or request.command.identity.expected_tenant_head != batch.expected.tenant_frontier
        or request.current.observation.cut.tenant_frontier != batch.expected.tenant_frontier
        or request.current.observation.cut.materialization_commitment
        != batch.expected.expected_materialization_commitment
    ):
        raise ValueError("v2 retained request differs from selected original cut")
    return request


def verify_dispatch_history(
    cut: MaterializedEffectsCut, journal: OwnerJournalSnapshot
) -> VerifiedDispatchHistory:
    if cut.owner_journal_head != journal.head or cut.tenant_id != journal.tenant_id:
        raise ValueError("effects history and authenticated selection cuts differ")
    selected = {
        record.record_id: (decision, ordinal)
        for decision in journal.decisions
        for ordinal, record in enumerate(decision.prepared.request.complete_records)
        if record.owner == "effects"
    }
    if len(selected) != sum(
        record.owner == "effects"
        for decision in journal.decisions
        for record in decision.prepared.request.complete_records
    ):
        raise ValueError("duplicate effect record across selected batches")
    members: list[SelectedEffectHistoryMember] = []
    records: list[DispatchRecordV2 | DispatchOutcomeRecordV2] = []
    latest: dict[str, DispatchRecordV2 | DispatchOutcomeRecordV2] = {}
    seen: set[str] = set()
    order: tuple[int, int] | None = None
    for row in cut.rows:
        raw = row.record
        if raw.record_id not in selected or raw.record_id in seen:
            raise ValueError("effect record omitted, duplicated or outside selected history")
        seen.add(raw.record_id)
        decision, ordinal = selected[raw.record_id]
        position = (decision.tenant_commit_sequence, ordinal)
        if order is not None and position <= order:
            raise ValueError("effect selected history reordered")
        order = position
        batch = decision.prepared.request
        if raw != batch.complete_records[ordinal] or raw.fingerprint != digest(raw.canonical_bytes):
            raise ValueError("effect canonical selected bytes substituted")
        selected_cut = reference(
            "selected-cut/" + batch.identity.command_id,
            canonical(batch.expected.model_dump(mode="json")),
        )
        if raw.schema_id == RECORD_SCHEMA:
            record = DispatchRecordV2.model_validate_json(raw.canonical_bytes)
            request = _request(decision)
            intent = record.snapshot.intent
            if (
                record.canonical_bytes() != raw.canonical_bytes
                or request.previous != latest.get(intent.intent_id)
                or prepare_dispatch(request) != record
                or raw.record_id != record.record.head
                or raw.record_kind != "effects." + record.kind
                or request.current.observation.complete_effect_history != tuple(members)
            ):
                raise ValueError("v2 original owner output/predecessor interpretation differs")
            latest[intent.intent_id] = record
            records.append(record)
            member = SelectedEffectHistoryMember(
                kind=record.kind,
                selected_decision=ExactHead(
                    subject_id=decision.decision_id,
                    head=decision.decision_head,
                    fingerprint=decision.decision_fingerprint,
                ),
                publication_ordinal=ordinal,
                original_selected_cut=selected_cut,
                record=record.record,
                predecessor=record.predecessor,
                intent=reference(intent.intent_id, intent.canonical_bytes()),
                semantics=intent.mandate.semantics,
                command_bytes=request.command.canonical_bytes(),
                record_bytes=raw.canonical_bytes,
            )
        elif raw.schema_id == "chiplog.effects.dispatch-outcome-record.v2":
            outcome = DispatchOutcomeRecordV2.model_validate_json(raw.canonical_bytes)
            if batch.kind != "SINGLE_OWNER":
                raise ValueError("outcome has another publication envelope")
            preparation = DispatchOutcomePreparationV2.model_validate_json(
                batch.command.canonical_bytes
            )
            if (
                outcome.canonical_bytes() != raw.canonical_bytes
                or batch.command.owner != "effects"
                or batch.command.schema_id != preparation.schema_id
                or batch.command.fingerprint != digest(batch.command.canonical_bytes)
                or preparation.canonical_bytes() != batch.command.canonical_bytes
                or preparation.command.identity.command_id != batch.identity.command_id
                or preparation.command.identity.expected_tenant_head
                != batch.expected.tenant_frontier
                or preparation.previous != latest.get(outcome.snapshot.intent.intent_id)
                or preparation.original_send not in records
                or prepare_outcome(preparation) != outcome
                or raw.record_id != outcome.record.head
                or raw.record_kind != "effects." + outcome.kind
            ):
                raise ValueError(
                    "outcome original owner/predecessor/evidence interpretation differs"
                )
            latest[outcome.snapshot.intent.intent_id] = outcome
            records.append(outcome)
            member = SelectedEffectHistoryMember(
                kind=outcome.kind,
                selected_decision=ExactHead(
                    subject_id=decision.decision_id,
                    head=decision.decision_head,
                    fingerprint=decision.decision_fingerprint,
                ),
                publication_ordinal=ordinal,
                original_selected_cut=selected_cut,
                record=outcome.record,
                predecessor=outcome.predecessor,
                intent=reference(
                    outcome.snapshot.intent.intent_id, outcome.snapshot.intent.canonical_bytes()
                ),
                semantics=outcome.snapshot.intent.mandate.semantics,
                command_bytes=preparation.command.canonical_bytes(),
                record_bytes=raw.canonical_bytes,
            )
        elif raw.schema_id == "chiplog.effects.record.v1":
            legacy = EffectRecord.model_validate_json(raw.canonical_bytes)
            if (
                legacy.canonical_bytes() != raw.canonical_bytes
                or raw.record_id != legacy.record.head
            ):
                raise ValueError("legacy effect canonical identity differs")
            member = SelectedEffectHistoryMember(
                kind=legacy.kind,
                selected_decision=ExactHead(
                    subject_id=decision.decision_id,
                    head=decision.decision_head,
                    fingerprint=decision.decision_fingerprint,
                ),
                publication_ordinal=ordinal,
                original_selected_cut=selected_cut,
                record=legacy.record,
                predecessor=legacy.predecessor,
                intent=ExactHead(
                    subject_id=legacy.snapshot.intent.intent_id,
                    head=legacy.snapshot.intent.intent_id
                    + "/"
                    + legacy.snapshot.intent.fingerprint,
                    fingerprint=legacy.snapshot.intent.fingerprint,
                ),
                semantics=legacy.snapshot.intent.semantics,
                command_bytes=legacy.source_command,
                record_bytes=raw.canonical_bytes,
            )
        else:
            raise ValueError("unregistered effects history schema")
        members.append(member)
    if seen != set(selected):
        raise ValueError("complete effect history omits selected members")
    return VerifiedDispatchHistory(tuple(members), tuple(records))


def normative_generation(
    history: VerifiedDispatchHistory, own: ExternalActionIntentV2 | None
) -> ExactHead:
    """Exclude only reinterpreted exact own initial/pre-send successors, never rivals."""
    exempt: set[ExactHead] = set()
    if own is not None:
        for record in history.v2_records:
            if record.snapshot.intent == own and record.kind in {
                "PLAN_EFFECT_PUBLISHED",
                "INTENT_ACCEPTED",
                "DISPATCH_AUTHORIZED",
            }:
                exempt.add(record.record)
    events = tuple(member for member in history.members if member.record not in exempt)
    return reference(
        "effects.normative-conflict-generation.v2",
        canonical([item.model_dump(mode="json") for item in events]),
    )


def validate_dispatch_plan_effect(
    batch: object, tenant: str, sequence: int
) -> _PlanningPublication:
    """Project exact original planning bytes without changing v1 interpretation."""
    from chiplog.adapters.driven.planning_sqlite import _publication
    from chiplog.domain_primitives import TenantId
    from chiplog.platform._owner_publication_contracts import PlanEffectBatch

    if not isinstance(batch, PlanEffectBatch) or batch.identity.tenant_id != tenant:
        raise ValueError("foreign dispatch PlanEffect batch")
    request = DispatchPreparationV2.model_validate_json(batch.effects_command.canonical_bytes)
    command = request.command
    if not isinstance(command, PublishDispatchIntentV2) or not isinstance(
        command.intent.mandate.origin, PlanEffectOrigin
    ):
        raise ValueError("dispatch PlanEffect has another origin")
    origin = command.intent.mandate.origin
    proposal = json.loads(origin.original_planning_result)
    original_request = json.loads(origin.original_planning_request)
    original_command = json.loads(
        base64.b64decode(original_request["command_bytes"], validate=True)
    )
    planned = batch.complete_records[:-1]
    if (
        len(planned) != 5
        or batch.planning_command.owner != "planning"
        or batch.planning_command.schema_id != "chiplog.planning.public.create.v2"
        or batch.planning_command.canonical_bytes != origin.original_planning_request
        or batch.planning_command.fingerprint != digest(origin.original_planning_request)
        or batch.effects_command.schema_id != PREPARATION_SCHEMA
        or request.canonical_bytes() != batch.effects_command.canonical_bytes
        or sequence != batch.expected.tenant_frontier + 1
        or request.command.identity.expected_tenant_head != sequence - 1
        or proposal["commit_sequence"] != sequence
    ):
        raise ValueError("v2 PlanEffect original planning command/cut differs")
    for row, original in zip(planned, proposal["records"], strict=True):
        raw = base64.b64decode(original["canonical_bytes"], validate=True)
        if (
            row.owner,
            row.schema_id,
            row.record_id,
            row.record_kind,
            row.canonical_bytes,
            row.fingerprint,
        ) != (
            "planning",
            "chiplog.planning.record.v1",
            original["record_id"],
            original["record_type_id"],
            raw,
            digest(raw),
        ):
            raise ValueError("v2 PlanEffect planning companions differ from original owner result")
    projected = _publication(
        TenantId(tenant),
        original_command["command_id"],
        proposal["request_fingerprint"],
        sequence,
        tuple((r.record_id, r.canonical_bytes) for r in planned),
    )
    revision = planned[3]
    revision_ref = ExactHead(
        subject_id=revision.record_id, head=revision.record_id, fingerprint=revision.fingerprint
    )
    record = DispatchRecordV2.model_validate_json(batch.complete_records[-1].canonical_bytes)
    expected_manifest = (
        *(
            ExactHead(subject_id=r.record_id, head=r.record_id, fingerprint=r.fingerprint)
            for r in planned
        ),
        reference(command.intent.intent_id, command.intent.canonical_bytes()),
    )
    if (
        origin.proposed_planning_revision != revision_ref
        or command.intent.mandate.planning_revision != revision_ref
        or command.complete_publication_manifest != expected_manifest
        or record != prepare_dispatch(request)
        or record.kind != "PLAN_EFFECT_PUBLISHED"
        or projected.result.revision_id.value != original_command["revision_id"]
        or projected.result.intention_line_id.value != original_command["intention_line_id"]
    ):
        raise ValueError("v2 PlanEffect exact revision/intent/manifest binding differs")
    return projected


def validate_selected_dispatch_sources(runtime: R16DispatchRuntime) -> None:
    """Interpret authenticated selected history before recovery; no physical-read claim."""
    from chiplog.composition.r16_dispatch_authority import validate_issuance

    journal = runtime._owner_decisions().snapshot()
    rows: list[StoredEffectRow] = []
    for decision in journal.decisions:
        batch = decision.prepared.request
        effects = tuple(record for record in batch.complete_records if record.owner == "effects")
        if any(record.schema_id == RECORD_SCHEMA for record in effects):
            validate_issuance(batch, runtime)
        elif any(
            record.schema_id == "chiplog.effects.dispatch-outcome-record.v2" for record in effects
        ):
            from chiplog.composition.r16_dispatch_outcomes import validate_outcome_issuance

            validate_outcome_issuance(batch, runtime)
        elif (
            getattr(batch.authentication, "applicability_schema", None)
            == "chiplog.effects.dispatch-issuance.v2"
        ):
            raise ValueError("v2 dispatch issuance lost its exact effects output")
        rows.extend(
            StoredEffectRow(
                decision.tenant_commit_sequence,
                ordinal,
                tuple(item.record_id for item in batch.complete_records),
                record,
            )
            for ordinal, record in enumerate(batch.complete_records)
            if record.owner == "effects"
        )
    # Only selected-byte interpretation is performed here. The parent runtime
    # independently proves ABSENT/COMPLETE SQL membership before recovery writes.
    selected_cut = MaterializedEffectsCut(
        runtime._tenant_id,
        max((decision.tenant_commit_sequence for decision in journal.decisions), default=0),
        runtime._commitment_journal.load(runtime._tenant_id) or "",
        journal.head,
        str(runtime._database),
        0,
        0,
        tuple(rows),
        None,
        (),
    )
    verify_dispatch_history(selected_cut, journal)
