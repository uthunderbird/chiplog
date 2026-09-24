"""Public wire discrimination for loop-owned rejected terminalization."""

import asyncio
import json

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.completion_assembly import rejected_completion_fixture

from chiplog.capabilities.agent_loop import (
    rejected_completion_terminalization_contracts as terminal,
)


def test_rejected_terminalization_result_is_closed_and_has_no_caller_manifest_slot() -> None:
    adapter: TypeAdapter[terminal.RejectedTerminalizationPreparationResultV1] = TypeAdapter(
        terminal.RejectedTerminalizationPreparationResultV1
    )
    mapping = adapter.json_schema()["discriminator"]["mapping"]
    assert set(mapping) == {
        "PREPARED_REJECTED_COMPLETION_TERMINALIZATION_V1",
        "REJECTED_COMPLETION_TERMINALIZATION_FAILURE_V1",
    }
    with pytest.raises(ValidationError):
        adapter.validate_json(
            json.dumps(
                {
                    "kind": "PREPARED_REJECTED_COMPLETION_TERMINALIZATION_V1",
                    "terminal_manifest": {"target": "ABORTED"},
                    "caller_manifest": {"target": "ABORTED"},
                }
            )
        )


def test_exchange_revalidates_a_direct_model_copy_request() -> None:
    async def exercise() -> None:
        fixture = await rejected_completion_fixture("v3")
        request = fixture.assembly.rejected_terminalization_request
        result = fixture.assembly.rejected_terminalization_result
        terminal.validate_rejected_terminalization_exchange(request, result)
        forged = request.model_copy(update={"source_cut_fingerprint": "0" * 64})
        with pytest.raises(ValueError, match="source cut fingerprint"):
            terminal.validate_rejected_terminalization_exchange(forged, result)

    asyncio.run(exercise())
