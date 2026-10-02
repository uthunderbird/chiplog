"""Isolated mandate compilation; these tests grant no administrative authority."""

import base64
import hashlib
import json

import pytest
from tests.support.scheduler_mandate_lifecycle import issue

from chiplog.capabilities.agent_loop._scheduler_process import ROUTES, dispatch
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerContextRef
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BATCH_SCHEMA,
    BatchPreparationCut,
    MandateIssuancePreparationRequest,
    PreparedSchedulerBatch,
    prepare_mandate_issuance,
)
from chiplog.capabilities.agent_loop.scheduler_startup import (
    MaterializedSchedulerRow,
    SchedulerStartupHold,
    SelectedSchedulerBatch,
    validate_selected_scheduler_records,
)


def _request() -> MandateIssuancePreparationRequest:
    command = issue()
    raw = command.canonical_bytes()
    context = SchedulerContextRef(
        tenant_id=command.administrative_source.tenant_id,
        service_identity=command.proposed_mandate.mandate.scope.service_identity,
        session_id="isolated-test-session",
        mandate_head="administrative-mandate-reference",
        issuance_id="isolated-test-issuance",
        issuance_fingerprint="a" * 64,
    )
    return MandateIssuancePreparationRequest(
        context=context,
        cut=BatchPreparationCut(
            tenant_id=context.tenant_id,
            tenant_frontier=0,
            materialization_commitment="b" * 64,
            registry_head="registry",
            registry_fingerprint="c" * 64,
            submission_id="submission",
            authorized_context=context,
            authorized_command_fingerprint=hashlib.sha256(raw).hexdigest(),
            authority_proof=Present(head="proof", fingerprint="d" * 64),
        ),
        command_bytes=raw,
    )


def test_isolated_route_compiles_exact_mandate_and_zero_budget_pair() -> None:
    request = _request()
    response = dispatch("scheduler.prepare_mandate_issuance", request.canonical_bytes())
    assert response["schema_id"] == BATCH_SCHEMA
    actual = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    assert actual == prepare_mandate_issuance(request)
    assert tuple(row.schema_id for row in actual.records) == (
        "chiplog.scheduler.system-mandate.v2",
        "chiplog.scheduler.mandate-budget.v1",
    )
    assert actual.result.budget_genesis.budget.generation == 0
    assert actual.result.budget_genesis.budget.cumulative_consequential_calls == 0
    assert ROUTES[-1][0] == "scheduler.prepare_mandate_issuance"


def test_isolated_route_rejects_substituted_scope_cut_and_noncanonical_command() -> None:
    request = _request()
    command = issue()
    wrong_scope = command.proposed_mandate.mandate.scope.model_copy(
        update={"service_identity": "another-service"}
    )
    wrong_mandate = command.proposed_mandate.mandate.model_copy(update={"scope": wrong_scope})
    wrong_proposal = command.proposed_mandate.model_copy(
        update={
            "mandate": wrong_mandate,
            "canonical_mandate_bytes": wrong_mandate.canonical_bytes(),
        }
    )
    cases = (
        request.model_copy(
            update={"context": request.context.model_copy(update={"tenant_id": "other"})}
        ),
        request.model_copy(
            update={
                "command_bytes": command.model_copy(
                    update={"proposed_mandate": wrong_proposal}
                ).canonical_bytes()
            }
        ),
        request.model_copy(
            update={"command_bytes": json.dumps(json.loads(request.command_bytes)).encode()}
        ),
    )
    for changed in cases:
        response = dispatch("scheduler.prepare_mandate_issuance", changed.canonical_bytes())
        assert response["failure"] == "PROTOCOL_REJECTED"
        assert "payload" not in response


def _selected() -> tuple[SelectedSchedulerBatch, tuple[MaterializedSchedulerRow, ...]]:
    request = _request()
    prepared = prepare_mandate_issuance(request)
    decision = Present(head="selected-decision", fingerprint="e" * 64)
    batch = SelectedSchedulerBatch(
        tenant_id="tenant",
        physical_database_id="physical-db",
        publication_ordinal=1,
        decision=decision,
        historical_validator="chiplog.scheduler.selected-records.v1",
        context=request.context,
        command_id=issue().command_id,
        operation="scheduler.issue_mandate",
        records=prepared.records,
    )
    rows = tuple(
        MaterializedSchedulerRow(
            tenant_id="tenant",
            physical_database_id="physical-db",
            publication_ordinal=1,
            member_ordinal=ordinal,
            decision=decision,
            record=member,
        )
        for ordinal, member in enumerate(prepared.records)
    )
    return batch, rows


def test_selected_readback_requires_exact_two_record_zero_genesis() -> None:
    batch, rows = _selected()
    index = validate_selected_scheduler_records(
        tenant_id="tenant",
        physical_database_id="physical-db",
        selected=(batch,),
        materialized=rows,
    )
    selected = index.mandates[0]
    assert selected.mandate.scope.mandate_id == selected.budget.mandate_id
    assert selected.budget.generation == 0
    assert index.selected_record_ids == tuple(member.record_id for member in batch.records)

    for changed in (
        batch.model_copy(update={"records": batch.records[:1]}),
        batch.model_copy(update={"records": batch.records[::-1]}),
        batch.model_copy(update={"command_id": "rival"}),
        batch.model_copy(update={"physical_database_id": "other-db"}),
    ):
        with pytest.raises(SchedulerStartupHold):
            validate_selected_scheduler_records(
                tenant_id="tenant",
                physical_database_id="physical-db",
                selected=(changed,),
                materialized=rows,
            )
