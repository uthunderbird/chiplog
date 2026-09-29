"""Pure H1 basis derivation from native loop completion facts.

This module only validates and translates loop-owned values.  It does not
authenticate a caller, select scope, or grant delivery authority.
"""

from __future__ import annotations

import hashlib

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as LoopHead
from chiplog.capabilities.agent_loop.delivery_preparation import Commentary, DeliveryCompletion
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
    decode_first_path_completion_request,
    first_path_completion_request_fingerprint,
)

from .contracts import ExactHead
from .scoped_intent_contracts import PreparedDeliveryBasisV3


def _effects_head(value: LoopHead) -> ExactHead:
    return ExactHead(subject_id=value.identity, head=value.head, fingerprint=value.fingerprint)


def derive_h1_prepared_delivery_basis(
    first: PrepareExecutionCompletionFirstPathV2,
    prepared: PreparedExecutionCompletion,
) -> PreparedDeliveryBasisV3:
    """Return the exact loop-only basis for one H1 Commentary delivery.

    Inputs are reparsed before use so a copied model cannot bypass first-path
    validation.  Invalid inputs raise ``ValueError`` for the enclosing owner
    to turn into its own rejection result.
    """
    first = decode_first_path_completion_request(first.canonical_bytes())
    prepared = PreparedExecutionCompletion.model_validate_json(prepared.canonical_bytes())

    expected = prepare_first_path_execution_completion(first)
    if not isinstance(expected, PreparedExecutionCompletion) or expected != prepared:
        raise ValueError("prepared completion differs from first-path loop result")
    if prepared.source_request_fingerprint != first_path_completion_request_fingerprint(first):
        raise ValueError("prepared completion source fingerprint differs")

    completion = DeliveryCompletion.model_validate_json(first.exact_captured_response)
    if completion.canonical_bytes() != first.exact_captured_response:
        raise ValueError("captured completion response is not canonical")
    if len(completion.deliveries) != 1 or len(completion.deliveries[0].payload) != 1:
        raise ValueError("H1 requires exactly one delivery with one payload")
    if not isinstance(completion.deliveries[0].payload[0], Commentary):
        raise ValueError("H1 requires NonAuthoritativeText Commentary")

    deliveries = prepared.delivery.manifest.ordered_deliveries
    if len(deliveries) != 1:
        raise ValueError("H1 accepted manifest must contain exactly one delivery")
    delivery = deliveries[0]
    if delivery.selection.kind != "ORIGIN_EXACT":
        raise ValueError("H1 requires ORIGIN_EXACT accepted delivery")
    if delivery.render_digest != hashlib.sha256(delivery.rendered_bytes).hexdigest():
        raise ValueError("accepted delivery render digest differs")

    source = first.source.current_run
    return PreparedDeliveryBasisV3(
        source_cut=ExactHead(
            subject_id=source.subject_id,
            head=source.revision.head,
            fingerprint=source.revision.fingerprint,
        ),
        acceptance=_effects_head(delivery.acceptance),
        completion_command_bytes=first.exact_captured_response,
        delivery_observation_bytes=first.delivery.canonical_bytes(),
        loop_proposal_bytes=prepared.delivery.canonical_bytes(),
    )


__all__ = ["derive_h1_prepared_delivery_basis"]
