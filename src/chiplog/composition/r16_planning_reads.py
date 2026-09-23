"""Historical planning projection from independently selected atomic PlanEffects.

The caller holds the authority gate across legacy reads and this bridge. Exact
selected bytes establish history; this code never renews historical authority.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from chiplog.adapters.driven.effects_queries import _decode_row
from chiplog.adapters.driven.planning_sqlite import PlanningProjectionIntegrityError, _publication
from chiplog.capabilities.effects.contracts import (
    EffectPreparationRequest,
    ExactHead,
    PublishPlanEffectCommand,
)
from chiplog.capabilities.planning._planning import _PlanningPublication
from chiplog.capabilities.planning._r8_authority import decode_trace
from chiplog.composition.r16_effects import _decode_request, read_materialized_effects
from chiplog.domain_primitives import TenantId
from chiplog.platform._owner_publication_contracts import PlanEffectBatch
from chiplog.platform.owner_publications import SelectedOwnerDecision

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _Tenant(_Closed):
    value: str


class _ResultId(_Closed):
    tenant_id: _Tenant
    value: str


class _Result(_Closed):
    command_id: _ResultId
    result_id: _ResultId
    intention_line_id: _ResultId
    revision_id: _ResultId
    authorization_evidence_id: _ResultId
    commit_sequence: int = Field(gt=0)
    allocation_manifest: tuple[tuple[int, str, _ResultId], ...]
    record_manifest: tuple[tuple[_ResultId, str, str], ...]
    batch_fingerprint: str
    result_fingerprint: str
    publication_fingerprint: str


class _ResultRecord(_Closed):
    canonical_bytes: str
    fingerprint: str
    record_id: str
    record_type_id: str


class _OwnerResult(_Closed):
    commit_sequence: int = Field(gt=0)
    operation_kind: Literal["CREATE_INTENTION_LINE"]
    records: tuple[_ResultRecord, ...] = Field(min_length=5, max_length=5)
    request_fingerprint: str
    result: _Result


class _Command(_Closed):
    command_id: str
    intention_line_id: str
    revision_id: str
    purpose: str
    authority_act_id: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _project(decision: SelectedOwnerDecision, tenant: str) -> _PlanningPublication:
    batch = decision.prepared.request
    if not isinstance(batch, PlanEffectBatch):
        raise ValueError("foreign or non-PlanEffect selection")
    return validate_plan_effect_records(batch, tenant, decision.tenant_commit_sequence)


def validate_plan_effect_records(
    batch: PlanEffectBatch,
    tenant: str,
    commit_sequence: int,
) -> _PlanningPublication:
    """Check exact cross-owner bytes before admission or historical projection.

    This pure check issues no authority and does not consult live owners or renew
    old leases. The broker independently authenticates the supplied batch.
    """
    if batch.effects_command.schema_id == "chiplog.effects.dispatch-preparation.v2":
        from chiplog.composition.r16_dispatch_history import validate_dispatch_plan_effect

        return validate_dispatch_plan_effect(batch, tenant, commit_sequence)
    if not isinstance(batch, PlanEffectBatch) or batch.identity.tenant_id != tenant:
        raise ValueError("foreign or non-PlanEffect selection")
    planning, effects = batch.planning_command, batch.effects_command
    if (
        planning.owner != "planning"
        or planning.schema_id != "chiplog.planning.public.create.v2"
        or effects.owner != "effects"
        or effects.schema_id != "chiplog.effects.prepare.v1"
        or planning.fingerprint != _digest(planning.canonical_bytes)
        or effects.fingerprint != _digest(effects.canonical_bytes)
    ):
        raise ValueError("selected PlanEffect command schemas or bytes differ")
    request = _decode_request(planning.canonical_bytes)
    command = _Command.model_validate_json(request.command_bytes)
    if _canonical(command.model_dump(mode="json")) != request.command_bytes:
        raise ValueError("noncanonical planning command")
    trace = decode_trace(request.authority_trace_bytes)
    planning_reads = [read for read in trace.reads if read.kind == "PLANNING"]
    trust_reads = [read for read in trace.reads if read.kind == "TRUST"]
    if len(planning_reads) != 1 or len(trust_reads) != 1:
        raise ValueError("original planning trace lacks unique planning/trust sources")
    original_snapshot = json.loads(planning_reads[0].canonical_value)
    original_trust = json.loads(trust_reads[0].canonical_value)
    if (
        not isinstance(original_snapshot, dict)
        or type(original_snapshot.get("head")) is not int
        or original_snapshot["head"] != commit_sequence - 1
        or not isinstance(original_trust, dict)
    ):
        raise ValueError("original planning read head differs from selected predecessor")
    preparation = EffectPreparationRequest.model_validate_json(effects.canonical_bytes)
    effect = PublishPlanEffectCommand.model_validate_json(preparation.command_bytes)
    if (
        preparation.canonical_bytes() != effects.canonical_bytes
        or preparation.operation != "effects.publish_plan_effect"
        or effect.canonical_bytes() != preparation.command_bytes
        or effect.identity.command_id != batch.identity.command_id
        or preparation.expected.tenant_id != tenant
        or trace.tenant_id != tenant
        or trace.principal_id != effect.intent.authority.principal_id
        or effect.intent.authority.tenant_id != tenant
        or preparation.expected.tenant_head != commit_sequence - 1
        or effect.identity.expected_tenant_head != preparation.expected.tenant_head
        or batch.expected.tenant_frontier != preparation.expected.tenant_head
    ):
        raise ValueError("selected PlanEffect original command/cut differs")
    proposal = _OwnerResult.model_validate_json(effect.planning_owner_bytes)
    if _canonical(proposal.model_dump(mode="json")) != effect.planning_owner_bytes:
        raise ValueError("noncanonical original planning owner result")
    if len(batch.complete_records) != 6:
        raise ValueError("PlanEffect requires exact planning records and one effects record")
    raw_records = batch.complete_records[:5]
    for raw, owner in zip(raw_records, proposal.records, strict=True):
        payload = base64.b64decode(owner.canonical_bytes, validate=True)
        if (
            raw.owner != "planning"
            or raw.schema_id != "chiplog.planning.record.v1"
            or raw.record_id != owner.record_id
            or raw.record_kind != owner.record_type_id
            or raw.canonical_bytes != payload
            or raw.fingerprint != _digest(payload)
            or base64.b64encode(payload).decode() != owner.canonical_bytes
        ):
            raise ValueError("selected planning records differ from original owner result")
    original_result = proposal.result
    expected_ids = (
        command.command_id,
        command.command_id + ".authorization",
        command.intention_line_id,
        command.revision_id,
        command.command_id + ".result",
    )
    result_ids = (
        original_result.command_id,
        original_result.authorization_evidence_id,
        original_result.intention_line_id,
        original_result.revision_id,
        original_result.result_id,
    )
    result_envelope = json.loads(raw_records[-1].canonical_bytes)
    result_fields = result_envelope.get("fields") if isinstance(result_envelope, dict) else None
    if (
        proposal.commit_sequence != commit_sequence
        or original_result.commit_sequence != commit_sequence
        or tuple(row.record_id for row in raw_records) != expected_ids
        or tuple(item.value for item in result_ids) != expected_ids
        or any(item.tenant_id.value != tenant for item in result_ids)
        or not isinstance(result_fields, dict)
        or type(result_fields.get("commit_sequence")) is not int
        or result_fields.get("commit_sequence") != commit_sequence
        or result_fields.get("command_id") != {"tenant_id": tenant, "value": command.command_id}
    ):
        raise ValueError("original planning result identity or commit sequence differs")
    publication = _publication(
        TenantId(tenant),
        command.command_id,
        proposal.request_fingerprint,
        commit_sequence,
        tuple((row.record_id, row.canonical_bytes) for row in raw_records),
    )
    result = publication.result
    if (
        proposal.commit_sequence != commit_sequence
        or result.commit_sequence != commit_sequence
        or result.command_id.value != command.command_id
        or result.result_id.value != command.command_id + ".result"
        or result.intention_line_id.value != command.intention_line_id
        or result.revision_id.value != command.revision_id
        or result.authorization_evidence_id.value != command.command_id + ".authorization"
        or proposal.result.model_dump(mode="json") != json.loads(_canonical(asdict(result)))
        or tuple(row.fingerprint for row in publication.records)
        != tuple(row.fingerprint for row in proposal.records)
    ):
        raise ValueError("selected planning result identity, head or manifest differs")
    expected_kinds = (
        "chiplog.planning.planning_command",
        "chiplog.planning.authorization_evidence",
        "chiplog.planning.intention_line",
        "chiplog.planning.intention_line_revision",
        "chiplog.planning.committed_result",
    )
    if tuple(row.record_type_id for row in publication.records) != expected_kinds:
        raise ValueError("selected planning record kinds/order differ")
    revision = raw_records[3]
    revision_ref = ExactHead(
        subject_id=revision.record_id, head=revision.record_id, fingerprint=revision.fingerprint
    )
    intent = effect.intent
    manifest = (
        *(
            ExactHead(subject_id=row.record_id, head=row.record_id, fingerprint=row.fingerprint)
            for row in raw_records
        ),
        ExactHead(
            subject_id=intent.intent_id,
            head=intent.intent_id + "/" + intent.fingerprint,
            fingerprint=intent.fingerprint,
        ),
    )
    record = _decode_row(batch.complete_records[-1], tenant)
    if (
        effect.planning_publication != revision_ref
        or intent.authority.planning_revision != revision_ref
        or effect.complete_publication_manifest != manifest
        or record.kind != "PLAN_EFFECT_PUBLISHED"
        or record.source_command != preparation.command_bytes
        or record.command != effect.identity
        or record.snapshot.intent != intent
        or record.predecessor is not None
    ):
        raise ValueError("selected effects record does not bind exact planning companion")
    common = {
        "authority_act_id": command.authority_act_id,
        "principal_id": trace.principal_id,
        "tenant_id": tenant,
    }
    trust = {**original_trust, "tenant_id": tenant, "principal_id": trace.principal_id}

    def rid(value: str) -> dict[str, str]:
        return {"tenant_id": tenant, "value": value}

    allocation = [
        {"ordinal": i, "record_id": rid(identifier), "record_type_id": kind}
        for i, identifier, kind in (
            (0, command.intention_line_id, expected_kinds[2]),
            (1, command.revision_id, expected_kinds[3]),
        )
    ]
    committed_manifest = [
        {
            "record_id": rid(row.record_id.value),
            "record_type_id": row.record_type_id,
            "fingerprint": row.fingerprint,
        }
        for row in publication.records[:4]
    ]
    expected_fields = (
        {
            **common,
            "allocation_manifest": allocation,
            "operation": "CREATE_INTENTION_LINE",
            "permission_scope": "planning.create_intention_line",
            "trust_reference": trust,
        },
        {**common, "operation": "CREATE_INTENTION_LINE", "trust_reference": trust},
        {**common, "initial_revision_id": rid(command.revision_id)},
        {
            **common,
            "activity": "ACTIVE",
            "intention_line_id": rid(command.intention_line_id),
            "ordinal": 0,
            "personal_outcome": "OPEN",
            "purpose": command.purpose,
            "predecessor_revision_id": None,
        },
        {
            "allocation_manifest": allocation,
            "authorization_evidence_id": rid(expected_ids[1]),
            "command_id": rid(command.command_id),
            "commit_sequence": commit_sequence,
            "batch_fingerprint": _digest(_canonical(committed_manifest)),
            "record_manifest": committed_manifest,
            "tenant_id": tenant,
        },
    )
    # Verify the registered initial wire contract, including every envelope field.
    # Never run the owner factory here or replace the owner's returned bytes.
    for row, fields in zip(raw_records, expected_fields, strict=True):
        namespace, name = row.record_kind.rsplit(".", 1)
        envelope = {
            "fields": fields,
            "owner": "planning",
            "record_id": rid(row.record_id),
            "record_type": {"namespace": namespace, "name": name},
            "versions": {
                "canonicalization": 1,
                "codec": 1,
                "schema": {"namespace": "chiplog.planning", "name": "record", "version": 1},
            },
        }
        expected_bytes = json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        if row.canonical_bytes != expected_bytes:
            raise ValueError("selected planning command differs from recorded meaning")
    request_value = {
        "command": {
            "authority_act_id": command.authority_act_id,
            "command_id": rid(command.command_id),
            "intention_line_id": rid(command.intention_line_id),
            "purpose": command.purpose,
            "revision_id": rid(command.revision_id),
        },
        "context": {
            "permission_scope": "planning.create_intention_line",
            "principal_id": trace.principal_id,
            "tenant_id": tenant,
            "trust_reference": trust,
        },
        "operation": "CREATE_INTENTION_LINE",
        "predecessor_result_id": None,
    }
    if proposal.request_fingerprint != _digest(_canonical(request_value)):
        raise ValueError("original planning request fingerprint differs from recorded meaning")
    return publication


def read_plan_effect_publications(
    runtime: R14PlanningRuntime,
) -> tuple[tuple[_PlanningPublication, ...], int]:
    """Read the complete authenticated PlanEffect planning subset at one gate cut."""
    runtime._authority_gate().require_held()
    try:
        journal = runtime._owner_decisions()
        before = journal.snapshot()
        cut = read_materialized_effects(runtime, journal)
        if journal.snapshot() != before:
            raise ValueError("owner journal changed across planning projection cut")
        publications = tuple(
            _project(decision, runtime._tenant_id)
            for decision in before.decisions
            if isinstance(decision.prepared.request, PlanEffectBatch)
        )
        return publications, cut.tenant_frontier
    except (ValueError, TypeError, KeyError) as error:
        raise PlanningProjectionIntegrityError(
            "selected PlanEffect planning projection integrity failure"
        ) from error


def merge_planning_publications(
    legacy: tuple[_PlanningPublication, ...],
    effects: tuple[_PlanningPublication, ...],
) -> tuple[_PlanningPublication, ...]:
    """Disjoint authenticated subsets may interleave, never duplicate identities."""
    result = tuple(sorted((*legacy, *effects), key=lambda row: row.commit_sequence))
    records = [record.record_id for row in result for record in row.records]
    if (
        len({row.commit_sequence for row in result}) != len(result)
        or len({row.command_id for row in result}) != len(result)
        or len(set(records)) != len(records)
    ):
        raise PlanningProjectionIntegrityError("planning projection subsets overlap")
    return result
