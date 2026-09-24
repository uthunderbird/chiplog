"""Physical completion-batch reconstruction from retained owner exchanges."""

import asyncio

import pytest

from chiplog.composition import r14_execution_completion_records as records
from tests.support.completion_assembly import accepted_completion_fixture


async def _evidence() -> records.RetainedCompleteAcceptanceExchangeV1:
    fixture = await accepted_completion_fixture("v3", "empty")
    return records.RetainedCompleteAcceptanceExchangeV1(
        assembly=fixture.assembly,
        batch=fixture.batch,
        expected_head=fixture.batch.expected.tenant_frontier,
        predecessor_commitment=fixture.batch.expected.expected_materialization_commitment,
    )


def test_complete_batch_reconstructs_one_exact_ordered_physical_command() -> None:
    async def exercise() -> None:
        evidence = await _evidence()
        command = records.complete_acceptance_command(evidence)

        assert command.tenant_id == evidence.batch.identity.tenant_id
        assert command.operation_kind == evidence.batch.operation
        assert command.idempotency_key == evidence.batch.identity.command_id
        assert command.request_fingerprint == evidence.batch.identity.command_fingerprint
        assert command.expected_head == evidence.expected_head
        assert command.fence_generation == "r6"
        assert command.expected_fence_frontier == command.minimum_fence_frontier == 0
        assert tuple(
            (row.record_id, row.owner, row.schema_id, row.canonical_bytes, row.fingerprint)
            for row in command.records
        ) == tuple(
            (row.record_id, row.owner, row.schema_id, row.canonical_bytes, row.fingerprint)
            for row in evidence.batch.complete_records
        )
        owners = [row.owner for row in command.records]
        assert owners[:3] == ["agent_loop", "agent_loop", "agent_loop"]
        assert (
            owners.count("conversation")
            == owners.count("effects")
            == len(evidence.assembly.ordered_effects)
        )

        reconstructed = records.RetainedCompleteAcceptanceExchangeV1.model_validate_json(
            evidence.canonical_bytes()
        )
        assert records.complete_acceptance_command(reconstructed) == command

    asyncio.run(exercise())


@pytest.mark.parametrize("omitted_owner", ["conversation", "effects"])
def test_complete_batch_rejects_missing_required_record_before_physical_publication(
    omitted_owner: str,
) -> None:
    async def exercise() -> None:
        evidence = await _evidence()
        batch = evidence.batch.model_copy(
            update={
                "complete_records": tuple(
                    row for row in evidence.batch.complete_records if row.owner != omitted_owner
                )
            }
        )
        with pytest.raises(ValueError, match="completion batch"):
            records.complete_acceptance_command(evidence.model_copy(update={"batch": batch}))

    asyncio.run(exercise())


@pytest.mark.parametrize("mutated_field", ["source", "result"])
def test_complete_batch_rejects_altered_retained_source_or_result_before_publication(
    mutated_field: str,
) -> None:
    async def exercise() -> None:
        evidence = await _evidence()
        if mutated_field == "source":
            source = evidence.assembly.work_source.model_copy(
                update={"original_completion_request_bytes": b"altered-source"}
            )
            assembly = evidence.assembly.model_copy(update={"work_source": source})
        else:
            result = evidence.assembly.prepared_completion.model_copy(
                update={"source_request_fingerprint": "0" * 64}
            )
            assembly = evidence.assembly.model_copy(update={"prepared_completion": result})
        with pytest.raises(ValueError):
            records.complete_acceptance_command(evidence.model_copy(update={"assembly": assembly}))

    asyncio.run(exercise())


def test_complete_batch_rejects_retained_head_or_predecessor_mismatch() -> None:
    async def exercise() -> None:
        evidence = await _evidence()
        with pytest.raises(ValueError, match="expected head"):
            records.complete_acceptance_command(evidence.model_copy(update={"expected_head": 1}))
        with pytest.raises(ValueError, match="predecessor"):
            records.complete_acceptance_command(
                evidence.model_copy(update={"predecessor_commitment": "0" * 64})
            )

    asyncio.run(exercise())
