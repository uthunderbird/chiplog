"""Exact V2 scheduler owner-batch wire measurements."""

from __future__ import annotations

import base64
import hashlib
import json

import pytest
from tests.support.scheduler_execution import DIGEST, bound_head, ordinary_seed_batch
from tests.support.scheduler_execution_batches import (
    finalized_result,
    finalized_result_with_two_non_envelope_members,
)

from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_execution_batch_wire import (
    SCHEDULED_WHOLE_RECORD_KIND_V2,
    ScheduledExecutionBatchValidationContextV2,
    scheduled_execution_batch_bytes,
    scheduled_execution_batch_bytes_within_bound,
)
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    PreparedFinalizedScheduledIntervalV2,
    PreparedPrimitiveFirstPublicationV2,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import (
    SchedulerCanonicalMember,
    _serialized,
)


def _physical_finalization(
    result: PreparedFinalizedScheduledIntervalV2,
) -> tuple[PreparedFinalizedScheduledIntervalV2, tuple[SchedulerCanonicalMember, ...]]:
    """Give the inert finalized result the fixed physical WHOLE identity."""

    whole_bytes = result.whole_envelope.canonical_envelope_bytes
    fingerprint = hashlib.sha256(whole_bytes).hexdigest()
    record_id = "scheduler-whole-envelope-v2:" + fingerprint
    wire = result.model_dump()
    wire["whole_envelope"]["proposed_record_id"] = record_id
    wire["whole_envelope"]["external_reference"] = Present(
        head=record_id, fingerprint=fingerprint
    ).model_dump()
    physical = PreparedFinalizedScheduledIntervalV2.model_validate(wire)
    whole = SchedulerCanonicalMember(
        record_kind=SCHEDULED_WHOLE_RECORD_KIND_V2,
        record_id=record_id,
        schema_id="chiplog.scheduler.whole-envelope.v2",
        canonical_base64=base64.b64encode(whole_bytes).decode(),
        fingerprint=fingerprint,
    )
    return physical, (*physical.complete_ordered_canonical_records, whole)


def test_v2_wire_is_byte_for_byte_the_registered_v1_serialized_domain() -> None:
    finalized, records = _physical_finalization(finalized_result(1))
    context = ScheduledExecutionBatchValidationContextV2.from_finalization(finalized)

    assert scheduled_execution_batch_bytes(records, context=context) == _serialized(records)


def test_full_nonempty_final_batch_including_whole_is_measured() -> None:
    finalized, records = _physical_finalization(finalized_result(1))
    context = ScheduledExecutionBatchValidationContextV2.from_finalization(finalized)

    encoded = scheduled_execution_batch_bytes(records, context=context)

    assert finalized.complete_ordered_canonical_records
    assert records[-1].record_kind == SCHEDULED_WHOLE_RECORD_KIND_V2
    assert base64.b64decode(records[-1].canonical_base64, validate=True) == (
        finalized.whole_envelope.canonical_envelope_bytes
    )
    assert json.loads(encoded)[-1] == records[-1].model_dump(mode="json")
    assert len(encoded) > sum(len(item) for item in finalized.complete_ordered_record_bytes)


def test_caller_can_reject_n_plus_one_final_batch_bytes_after_manifest_limits_pass() -> None:
    finalized, records = _physical_finalization(finalized_result(1))
    encoded = scheduled_execution_batch_bytes(
        records, context=ScheduledExecutionBatchValidationContextV2.from_finalization(finalized)
    )
    selected_bound = bound_head().model_copy(
        update={
            "bound": bound_head().bound.model_copy(
                update={
                    "max_manifest_bytes": 10_000,
                    "max_serialized_batch_bytes": len(encoded) - 1,
                }
            )
        }
    )
    source = ordinary_seed_batch(1).source
    assert isinstance(source, PreparedPrimitiveFirstPublicationV2)
    manifest = source.primitive_parent.manifest

    assert len(manifest.members) <= (selected_bound.bound.max_member_count)
    assert len(manifest.canonical_bytes()) <= (selected_bound.bound.max_manifest_bytes)
    assert len(encoded) == selected_bound.bound.max_serialized_batch_bytes + 1
    with pytest.raises(ValueError, match="serialized-byte bound"):
        scheduled_execution_batch_bytes_within_bound(
            records,
            selected_bound.bound,
            context=ScheduledExecutionBatchValidationContextV2.from_finalization(finalized),
        )


def test_rejects_omitted_permuted_or_invalid_whole_membership() -> None:
    finalized, records = _physical_finalization(finalized_result_with_two_non_envelope_members())
    context = ScheduledExecutionBatchValidationContextV2.from_finalization(finalized)

    with pytest.raises(ValueError, match="descriptor"):
        scheduled_execution_batch_bytes(records[1:], context=context)
    with pytest.raises(ValueError, match="descriptor"):
        scheduled_execution_batch_bytes((records[1], records[0], records[2]), context=context)
    invalid_whole = records[-1].model_copy(update={"record_id": "other-whole"})
    with pytest.raises(ValueError, match="record ID"):
        scheduled_execution_batch_bytes((*records[:-1], invalid_whole), context=context)


def test_rejects_noncanonical_member_base64_duplicate_identity_and_nonterminal_whole() -> None:
    finalized, records = _physical_finalization(finalized_result_with_two_non_envelope_members())
    context = ScheduledExecutionBatchValidationContextV2.from_finalization(finalized)
    noncanonical = records[0].model_copy(
        update={"canonical_base64": records[0].canonical_base64 + "\n"}
    )

    with pytest.raises(ValueError, match="base64"):
        scheduled_execution_batch_bytes((noncanonical, *records[1:]), context=context)
    duplicate = records[1].model_copy(update={"record_id": records[0].record_id})
    with pytest.raises(ValueError, match="duplicate"):
        scheduled_execution_batch_bytes((records[0], duplicate, records[-1]), context=context)
    with pytest.raises(ValueError, match="exactly one registered WHOLE member last"):
        scheduled_execution_batch_bytes((records[-1], *records[:-1]), context=context)


def test_rejects_an_embedded_registered_whole_before_descriptor_validation() -> None:
    finalized, records = _physical_finalization(finalized_result(1))
    context = ScheduledExecutionBatchValidationContextV2.from_finalization(finalized)
    earlier_body = finalized.whole_envelope.body.model_copy(
        update={"selected_clock_cut_fingerprint": "b" * 64}
    )
    earlier_bytes = earlier_body.canonical_bytes()
    earlier_fingerprint = hashlib.sha256(earlier_bytes).hexdigest()
    earlier_whole = SchedulerCanonicalMember(
        record_kind=SCHEDULED_WHOLE_RECORD_KIND_V2,
        record_id="scheduler-whole-envelope-v2:" + earlier_fingerprint,
        schema_id="chiplog.scheduler.whole-envelope.v2",
        canonical_base64=base64.b64encode(earlier_bytes).decode(),
        fingerprint=earlier_fingerprint,
    )

    with pytest.raises(ValueError, match="exactly one registered WHOLE member last"):
        scheduled_execution_batch_bytes(
            (*records[:-1], earlier_whole, records[-1]), context=context
        )


def test_context_binds_descriptor_subjects_and_fixed_whole_reference() -> None:
    finalized, records = _physical_finalization(finalized_result(1))
    context = ScheduledExecutionBatchValidationContextV2.from_finalization(finalized)
    wrong_subject = context.model_copy(update={"ordered_member_subject_ids": ("other",)})
    wrong_reference = context.model_copy(
        update={"whole_reference": Present(head="other", fingerprint=DIGEST)}
    )

    with pytest.raises(ValueError, match="subject"):
        scheduled_execution_batch_bytes(records, context=wrong_subject)
    with pytest.raises(ValueError, match="reference"):
        scheduled_execution_batch_bytes(records, context=wrong_reference)


def test_context_rejects_a_nonpresent_forged_whole_reference() -> None:
    with pytest.raises(ValueError):
        ScheduledExecutionBatchValidationContextV2.model_validate(
            {
                "ordered_member_subject_ids": ("command",),
                "whole_reference": {"kind": "ABSENT"},
            }
        )
