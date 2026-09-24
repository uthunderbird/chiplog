"""Focused representation checks for retained successor Run ancestry reads.

The helper checks only retained bytes and joins.  Broker reader authentication is
an explicitly separate runtime concern.
"""

from __future__ import annotations

import json
from typing import Literal

import pytest
from tests.support.successor_records import repeated_scheduler_successors

from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.successor_ancestry_contracts import (
    ReadSuccessorRunAncestryResultV1,
    ReadSuccessorRunAncestryV1,
    SuccessorAncestryIntegrityError,
    SuccessorAncestryReadFailureV1,
    validate_successor_run_ancestry_exchange,
)
from chiplog.capabilities.agent_loop.successor_record_contracts import (
    PriorSuccessorObservationLineageWitness,
)


async def _exchange(
    version: Literal["v2", "v3"],
) -> tuple[ReadSuccessorRunAncestryV1, ReadSuccessorRunAncestryResultV1]:
    chain = await repeated_scheduler_successors(version)
    witness = chain.second.scheduler_inputs.continuity
    assert isinstance(witness, PriorSuccessorObservationLineageWitness)
    return witness.ancestry_request, witness.ancestry


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_native_successor_ancestry_exchange_validates(version: Literal["v2", "v3"]) -> None:
    request, result = await _exchange(version)
    decoded = validate_successor_run_ancestry_exchange(request, result)
    assert len(decoded) == 4
    assert decoded[0].run.state == "CREATED"
    assert decoded[-1].run.state == "SUSPENDED"


async def test_ancestry_exchange_rejects_typed_failure_and_empty_forged_success() -> None:
    request, result = await _exchange("v2")
    failure = SuccessorAncestryReadFailureV1(
        source_request_fingerprint=result.source_request_fingerprint,
        code="NOT_SELECTED",
        reason="unselected",
    )
    with pytest.raises(SuccessorAncestryIntegrityError, match="did not return success"):
        validate_successor_run_ancestry_exchange(request, failure)
    forged = result.model_copy(update={"ordered_runs": ()})
    with pytest.raises(SuccessorAncestryIntegrityError, match="invalid retained"):
        validate_successor_run_ancestry_exchange(request, forged)


@pytest.mark.parametrize(
    ("field", "reason"),
    (("fingerprint", "request fingerprint"), ("cut", "source context"), ("prior", "prior lineage")),
)
async def test_ancestry_exchange_rejects_typed_context_mutations(field: str, reason: str) -> None:
    request, result = await _exchange("v3")
    if field == "fingerprint":
        changed = result.model_copy(update={"source_request_fingerprint": "0" * 64})
    elif field == "cut":
        changed = result.model_copy(
            update={"cut": result.cut.model_copy(update={"materialization_commitment": "0" * 64})}
        )
    else:
        changed = result.model_copy(
            update={
                "prior_lineage_advance": result.prior_lineage_advance.model_copy(
                    update={
                        "revision": Present(
                            head=result.prior_lineage_advance.revision.head,
                            fingerprint="0" * 64,
                        )
                    }
                )
            }
        )
    with pytest.raises(SuccessorAncestryIntegrityError, match=reason):
        validate_successor_run_ancestry_exchange(request, changed)


@pytest.mark.parametrize("mutation", ("omit", "swap", "duplicate", "sequence", "bool_sequence"))
async def test_ancestry_exchange_rejects_chain_mutations(mutation: str) -> None:
    request, result = await _exchange("v2")
    values = result.ordered_runs
    if mutation == "omit":
        changed = result.model_copy(update={"ordered_runs": (values[0], *values[2:])})
    elif mutation == "swap":
        changed = result.model_copy(
            update={"ordered_runs": (values[0], values[2], values[1], values[3])}
        )
    elif mutation == "duplicate":
        changed = result.model_copy(
            update={"ordered_runs": (values[0], values[1], values[1], values[3])}
        )
    elif mutation == "sequence":
        changed = result.model_copy(
            update={
                "ordered_runs": (
                    values[0],
                    values[1].model_copy(update={"selected_commit_sequence": 1}),
                    *values[2:],
                )
            }
        )
    else:
        raw = json.loads(result.canonical_bytes())
        raw["ordered_runs"][0]["selected_commit_sequence"] = True
        object.__setattr__(
            result, "canonical_bytes", lambda: json.dumps(raw, separators=(",", ":")).encode()
        )
        changed = result
    with pytest.raises(SuccessorAncestryIntegrityError):
        validate_successor_run_ancestry_exchange(request, changed)
