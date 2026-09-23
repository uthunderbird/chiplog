"""Public consumer wire checks; no claim of authentication or runtime acceptance."""

import json

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptancePreview,
    CallAcceptanceTarget,
)


def test_consumer_can_retain_exact_preview_without_decoding_binary_fields() -> None:
    initialized = CallSubjectHead(
        subject_id="original-call",
        revision=Present(head="initialized-head", fingerprint="a" * 64),
    )
    current = CallSubjectHead(
        subject_id="run", revision=Present(head="active-head", fingerprint="b" * 64)
    )
    preview = CallAcceptancePreview(
        preview_id="preview",
        target=CallAcceptanceTarget(
            original_call_id="original-call", initialized=initialized, current_run=current
        ),
        display_bytes=b"exact display\x00\xff",
        mandate_bytes=b"original mandate\x00\xfe",
        preview_fingerprint="c" * 64,
    )
    submitted = CallAcceptanceAdoption(act_id="adopt-once", preview_bytes=preview.canonical_bytes())
    restored = CallAcceptanceAdoption.model_validate_json(submitted.canonical_bytes())
    assert restored.preview_bytes == preview.canonical_bytes()
    decoded = CallAcceptancePreview.model_validate_json(restored.preview_bytes)
    assert decoded == preview
    assert decoded.target.current_run != decoded.target.initialized


@pytest.mark.parametrize(
    "field",
    ["authenticated", "source_cut", "prepared_batch", "lease_proof", "send_authorized"],
)
def test_adoption_rejects_caller_supplied_authority(field: str) -> None:
    adoption = CallAcceptanceAdoption(act_id="act", preview_bytes=b"untrusted")
    wire = json.loads(adoption.canonical_bytes())
    wire[field] = True
    with pytest.raises(ValidationError):
        CallAcceptanceAdoption.model_validate_json(json.dumps(wire))


def test_malformed_preview_is_retained_for_runtime_rejection_not_normalized() -> None:
    raw = b"\xff\x00not-json\r\n"
    adoption = CallAcceptanceAdoption(act_id="act", preview_bytes=raw)
    assert (
        CallAcceptanceAdoption.model_validate_json(adoption.canonical_bytes()).preview_bytes == raw
    )
    with pytest.raises(ValidationError):
        CallAcceptancePreview.model_validate_json(raw)
