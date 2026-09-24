"""Pure construction of the one H1 local Commentary effects receipt.

The caller supplies only a broker-attested, mounted owner call.  All durable
intent fields are recomputed from the first-path loop request/result and the
selected scope; this module has no SEND, provider, storage, or authority API.
"""

from __future__ import annotations

import hashlib
import json

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as LoopHead
from chiplog.capabilities.agent_loop.delivery_preparation import Commentary, DeliveryCompletion
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    first_path_completion_request_fingerprint,
)

from .contracts import ExactHead
from .h1_local_preparation_contracts import (
    H1LocalCommentaryOwnerCallV1,
    H1LocalCommentaryRejectedV1,
    H1LocalPreparedCommentaryIntentV1,
    PreparedH1LocalCommentaryV1,
)
from .h1_local_preparation_record_contracts import (
    h1_local_complete_owner_commitment,
    h1_local_intent_fingerprint,
    make_h1_local_prepared_commentary_member,
)
from .scoped_intent_contracts import PreparedDeliveryBasisV3


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _loop_head(subject: str, head: str, fingerprint: str) -> LoopHead:
    return LoopHead(identity=subject, head=head, fingerprint=fingerprint)


def _effects_head(value: LoopHead) -> ExactHead:
    return ExactHead(subject_id=value.identity, head=value.head, fingerprint=value.fingerprint)


def _intent_id(call: H1LocalCommentaryOwnerCallV1, delivery_id: str) -> str:
    preimage = [
        "chiplog.effects.h1-local-commentary-preparation.v1",
        call.request.identity.model_dump(mode="json"),
        delivery_id,
    ]
    return "h1-local-commentary:" + _sha(
        json.dumps(preimage, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    )


def _reject(reason: str) -> H1LocalCommentaryRejectedV1:
    return H1LocalCommentaryRejectedV1(disposition="DENIED", reason=reason)


def prepare_h1_local_commentary(
    call: H1LocalCommentaryOwnerCallV1,
) -> PreparedH1LocalCommentaryV1 | H1LocalCommentaryRejectedV1:
    """Derive a single local record or reject before any publication boundary."""
    try:
        # Reparse so copied/noncanonical objects cannot evade the registered loop
        # validators.  The mounted process supplies the outer route authentication.
        call = H1LocalCommentaryOwnerCallV1.model_validate_json(call.canonical_bytes())
        request = call.request
        first = request.original_completion_request
        prepared = prepare_first_path_execution_completion(first)
        if prepared != request.prepared_completion:
            return _reject("prepared completion differs from first-path loop result")
        if prepared.source_request_fingerprint != first_path_completion_request_fingerprint(first):
            return _reject("prepared completion source fingerprint differs")
        completion = DeliveryCompletion.model_validate_json(first.exact_captured_response)
        if completion.canonical_bytes() != first.exact_captured_response:
            return _reject("captured completion response is not canonical")
        if len(completion.deliveries) != 1 or len(completion.deliveries[0].payload) != 1:
            return _reject("H1 requires exactly one delivery with one payload")
        if not isinstance(completion.deliveries[0].payload[0], Commentary):
            return _reject("H1 requires NonAuthoritativeText Commentary")
        deliveries = prepared.delivery.manifest.ordered_deliveries
        if len(deliveries) != 1:
            return _reject("H1 accepted manifest must contain exactly one delivery")
        delivery = deliveries[0]
        scope = request.selected_scope
        run = first.run
        if (
            delivery.selection.kind != "ORIGIN_EXACT"
            or delivery.selection.recipient != scope.scope.recipient
            or delivery.policy != scope.scope.disclosure_policy.ref
            or request.fence.kind != "NON_SCHEDULER_NOT_APPLICABLE"
            or (request.fence.run_id, request.fence.run_head, request.fence.worker_session_id)
            != (run.run_id, run.head, run.worker_session)
            or (request.identity.command_id, request.identity.expected_tenant_head)
            != (call.route.request_id, first.source.tenant_commit_sequence)
            or request.intent_id != _intent_id(call, delivery.delivery_id)
        ):
            return _reject("H1 owner request does not bind selected native origin")
        attempt = first.selected_attempt
        source_cut = _effects_head(
            _loop_head(
                first.source.current_run.subject_id,
                first.source.current_run.revision.head,
                first.source.current_run.revision.fingerprint,
            )
        )
        basis = PreparedDeliveryBasisV3(
            source_cut=source_cut,
            acceptance=_effects_head(delivery.acceptance),
            completion_command_bytes=first.exact_captured_response,
            delivery_observation_bytes=first.delivery.canonical_bytes(),
            loop_proposal_bytes=prepared.delivery.canonical_bytes(),
        )
        unsigned = H1LocalPreparedCommentaryIntentV1(
            intent_id=request.intent_id,
            tenant_id=run.tenant,
            database_id=first.source.database_id,
            principal_id=run.principal,
            worker_session_id=run.worker_session,
            original_run=_loop_head(run.run_id, run.head, run.digest()),
            captured_attempt=_loop_head(
                attempt.subject_id, attempt.revision.head, attempt.revision.fingerprint
            ),
            delivery=delivery,
            basis=basis,
            scope_anchor=scope.anchor,
            scope_ref=scope.current_result.scope_ref,
            disclosure_policy_ref=scope.scope.disclosure_policy.ref,
            source_request_fingerprint=_sha(request.canonical_bytes()),
            fingerprint="0" * 64,
        )
        intent = unsigned.model_copy(update={"fingerprint": h1_local_intent_fingerprint(unsigned)})
        member = make_h1_local_prepared_commentary_member(intent)
        return PreparedH1LocalCommentaryV1(
            source_request_fingerprint=_sha(request.canonical_bytes()),
            intent=intent,
            complete_owner_commitment=h1_local_complete_owner_commitment(member),
        )
    except (TypeError, ValueError, IndexError, AttributeError) as error:
        return _reject(str(error))


__all__ = ["prepare_h1_local_commentary"]
