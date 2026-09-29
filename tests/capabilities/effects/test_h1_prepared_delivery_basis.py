"""Pure H1 loop-basis derivation remains byte-for-byte stable."""

from __future__ import annotations

import pytest
from tests.capabilities.agent_loop.test_execution_first_path_completion_contracts import (
    request as first_path_request,
)

from chiplog.capabilities.agent_loop.delivery_preparation import DeliveryCompletion
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.h1_prepared_delivery_basis import (
    derive_h1_prepared_delivery_basis,
)
from chiplog.capabilities.effects.scoped_intent_contracts import PreparedDeliveryBasisV3


async def _valid_completion() -> tuple[
    PrepareExecutionCompletionFirstPathV2, PreparedExecutionCompletion
]:
    first = await first_path_request(canonical_response=True)
    prepared = prepare_first_path_execution_completion(first)
    assert isinstance(prepared, PreparedExecutionCompletion)
    return first, prepared


def _previous_local_basis(
    first: PrepareExecutionCompletionFirstPathV2, prepared: PreparedExecutionCompletion
) -> PreparedDeliveryBasisV3:
    source = first.source.current_run
    delivery = prepared.delivery.manifest.ordered_deliveries[0]
    return PreparedDeliveryBasisV3(
        source_cut=ExactHead(
            subject_id=source.subject_id,
            head=source.revision.head,
            fingerprint=source.revision.fingerprint,
        ),
        acceptance=ExactHead(
            subject_id=delivery.acceptance.identity,
            head=delivery.acceptance.head,
            fingerprint=delivery.acceptance.fingerprint,
        ),
        completion_command_bytes=first.exact_captured_response,
        delivery_observation_bytes=first.delivery.canonical_bytes(),
        loop_proposal_bytes=prepared.delivery.canonical_bytes(),
    )


async def test_basis_is_exactly_the_prior_local_construction() -> None:
    first, prepared = await _valid_completion()

    assert derive_h1_prepared_delivery_basis(first, prepared) == _previous_local_basis(
        first, prepared
    )


async def test_basis_rejects_a_copied_mismatched_prepared_result() -> None:
    first, prepared = await _valid_completion()
    copied = prepared.model_copy(update={"source_request_fingerprint": "0" * 64})

    with pytest.raises(ValueError, match="first-path loop result"):
        derive_h1_prepared_delivery_basis(first, copied)


@pytest.mark.parametrize("mutation", ["tampered-response", "extra-delivery"])
async def test_basis_rejects_tampered_captured_completion(mutation: str) -> None:
    first, prepared = await _valid_completion()
    if mutation == "tampered-response":
        changed = first.model_copy(update={"exact_captured_response": b"forged"})
    else:
        completion = DeliveryCompletion.model_validate_json(first.exact_captured_response)
        changed_completion = completion.model_copy(
            update={"deliveries": completion.deliveries + completion.deliveries}
        )
        changed = first.model_copy(
            update={"exact_captured_response": changed_completion.canonical_bytes()}
        )

    with pytest.raises(ValueError):
        derive_h1_prepared_delivery_basis(changed, prepared)
