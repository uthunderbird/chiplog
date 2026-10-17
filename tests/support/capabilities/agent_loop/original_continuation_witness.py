"""Shared builders extracted without changing their behavior."""

from types import SimpleNamespace

from tests.support.capabilities.agent_loop.original_call_resolution_preparation import _source
from tests.support.capabilities.agent_loop.original_semantic_reduction_preparation import request
from tests.support.capabilities.agent_loop.recovery_accounting import recovery_call

from chiplog.capabilities.agent_loop.execution_recovery_observations import OriginalRecoveredClosure
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    OriginalObligationClosureRecord,
    OriginalResolutionBasis,
    PreparedLoopSemanticReduction,
)
from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
    prepare_loop_semantic_reduction,
)
from chiplog.capabilities.agent_loop.recovery_domain import (
    sealed_call_accounting,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import EvidenceReduction


def _original_cut_accounting():
    source_request = request()
    prepared = prepare_loop_semantic_reduction(source_request)
    assert isinstance(prepared, PreparedLoopSemanticReduction)
    record = prepared.record
    anchors = {source.schema_id: source for source in source_request.complete_anchor_sources}
    closure = OriginalObligationClosureRecord.model_validate_json(
        anchors["chiplog.loop.original-obligation-closure.v1"].canonical_record_bytes
    )
    basis = OriginalResolutionBasis.model_validate_json(
        anchors["chiplog.loop.original-resolution-basis.v1"].canonical_record_bytes
    )
    assert record.anchor.kind == "RESOLVED"
    assert record.disposition.kind == "CONSUMABLE"
    assert record.anchor.accepted_witness == closure.accepted_witness
    assert closure.accepted_witness not in record.complete_ordered_evidence
    assert record.complete_ordered_evidence == tuple(
        item.physical_record for item in source_request.complete_ordered_evidence
    )
    reduced_source = _source(
        record,
        record_id=record.schema_id + ":" + record.digest(),
        owner="agent_loop",
        schema=record.schema_id,
    )
    summary = OriginalRecoveredClosure(
        original=closure.original,
        terminal_disposition=basis.original_terminal.revision,
        recovered_outcome=record.anchor.recovered_outcome.revision,
        accepted_evidence=closure.accepted_witness.revision,
        closure=record.anchor.closure.revision,
        resolver_batch=record.anchor.resolver_batch.revision,
        reduction_id=record.reduction_id,
        accepted_semantic_class=record.disposition.semantic_class,
    )
    reduction = EvidenceReduction(
        stream_id=record.stream.stream_id,
        reduction_id=record.reduction_id,
        reducer_id=record.reducer_id,
        reducer_version=record.reducer_version,
        current_reduction=reduced_source.subject.revision,
        ordered_consumed_evidence=tuple(item.revision for item in record.complete_ordered_evidence),
        accepted_witness=record.anchor.accepted_witness.revision,
        accepted_outcome=record.anchor.recovered_outcome.revision,
        obligation_closure=record.anchor.closure.revision,
        resolver_batch=record.anchor.resolver_batch.revision,
        semantic_class=record.disposition.semantic_class,
        consumability="CONSUMABLE",
    )
    call = recovery_call(recovered=True)
    call = call.model_copy(
        update={
            "original_call_id": closure.original.original_call_id,
            "response_id": "response-original",
            "disposition": call.disposition.model_copy(
                update={
                    "terminal": basis.original_terminal.revision,
                    "obligation": closure.original,
                    "recovered_outcome": call.disposition.recovered_outcome.model_copy(
                        update={
                            "original_call_id": closure.original.original_call_id,
                            "recovery_obligation_id": closure.original.obligation_id,
                            "outcome": record.anchor.recovered_outcome.revision,
                        }
                    ),
                }
            ),
        }
    )
    accounting = sealed_call_accounting(
        call.response_id, "f" * 64, (call.original_call_id,), (call,)
    )
    cut = SimpleNamespace(
        original_closures=(summary,),
        current_reductions=(reduction,),
        complete_sources=(
            *source_request.complete_anchor_sources,
            *source_request.complete_ordered_evidence,
            source_request.reducer_policy,
            reduced_source,
            source_request.authority.authenticated_invocation,
        ),
    )
    from chiplog.capabilities.agent_loop.original_recovery_contracts import (
        OriginalResolverInvocationV1,
    )

    invocation = OriginalResolverInvocationV1.model_validate_json(
        source_request.authority.authenticated_invocation.canonical_record_bytes
    )
    cut.complete_sources += (
        invocation.accepted_terminal_source,
        invocation.accepted_opening_source,
    )
    return cut, accounting
