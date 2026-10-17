"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from tests.support.capabilities.agent_loop.original_call_resolution_preparation import (
    _source,
)
from tests.support.capabilities.agent_loop.original_call_resolution_preparation import (
    request as resolution_request,
)

from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
    prepare_original_call_resolution,
)
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    LoopRecoveryStream,
    PrepareLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent


def request() -> PrepareLoopSemanticReduction:
    source_request = resolution_request()
    prepared = prepare_original_call_resolution(source_request)
    assert prepared.kind == "PREPARED_ORIGINAL_CALL_RESOLUTION_V1"
    cut = source_request.cut
    records = (
        (
            prepared.basis,
            "chiplog.loop.original-resolution-basis.v1",
            prepared.basis.basis_id,
        ),
        (
            prepared.closure,
            prepared.closure.schema_id,
            prepared.closure.closure_id,
        ),
        (
            prepared.recovered_outcome,
            prepared.recovered_outcome.schema_id,
            prepared.recovered_outcome.outcome_id,
        ),
        (
            prepared.batch,
            "chiplog.loop.original-resolver-batch.v1",
            prepared.batch.batch_id,
        ),
    )
    anchors = (
        *(
            _source(record, record_id=record_id, owner="agent_loop", schema=schema)
            for record, schema, record_id in records
        ),
        cut.selected_witness,
    )
    binding = cut.original
    return PrepareLoopSemanticReduction(
        command_id="reduce-original-semantic-evidence",
        tenant_id=cut.tenant_id,
        database_id=cut.database_id,
        tenant_commit_sequence=cut.tenant_commit_sequence + 1,
        materialization_commitment="a" * 64,
        stream=LoopRecoveryStream(
            stream_id=binding.evidence_stream_id,
            registry=cut.obligation_stream.registry,
            subject=cut.current_obligation.subject,
            schema_id="chiplog.loop.semantic-reduction.v1",
        ),
        expected=Absent(),
        original=binding,
        complete_ordered_evidence=cut.complete_ordered_evidence,
        complete_anchor_sources=anchors,
        reducer_policy=cut.reducer_policy,
        authority=cut.authority,
    )
