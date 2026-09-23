"""Exact display/identity interpretation; selected source authentication is separate."""

import hashlib
import json

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition.r14_call_acceptance_preview import (
    build_call_acceptance_preview,
    decode_call_acceptance_preview,
)
from tests.support.call_acceptance import preview_inputs


def test_display_retains_entire_original_mandate_and_current_target() -> None:
    target, mandate = preview_inputs()
    preview = build_call_acceptance_preview(target, mandate)
    assert decode_call_acceptance_preview(preview.canonical_bytes()) == (preview, mandate)
    display = json.loads(preview.display_bytes)
    assert display["mandate"] == json.loads(mandate.canonical_bytes())
    assert display["target"] == json.loads(target.canonical_bytes())
    assert preview.mandate_bytes == mandate.canonical_bytes()
    # A valid successor can have a different current Run; authenticating that
    # lineage is the runtime's job, not a guess based on equal string identifiers.
    assert display["mandate"]["origin"]["original_run_id"] != target.current_run.subject_id
    body = json.loads(preview.canonical_bytes())
    del body["preview_fingerprint"]
    expected = hashlib.sha256(
        b"chiplog.call.acceptance-preview.v2\x00"
        + json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert preview.preview_fingerprint == expected
    del body["preview_id"]
    identity = hashlib.sha256(
        b"chiplog.call.acceptance-preview-identity.v2\x00"
        + json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    assert preview.preview_id == "call-acceptance-preview:" + identity


@pytest.mark.parametrize("field", ["subject_id", "head", "fingerprint"])
def test_each_initialized_reference_component_must_match(field: str) -> None:
    target, mandate = preview_inputs()
    wire = mandate.model_dump(mode="json")
    wire["origin"]["initialized_head"][field] = "b" * 64
    changed = type(mandate).model_validate_json(json.dumps(wire))
    with pytest.raises(ValueError):
        build_call_acceptance_preview(target, changed)


@pytest.mark.parametrize("field", ["display_bytes", "preview_id", "preview_fingerprint"])
def test_changed_preview_cannot_be_reused(field: str) -> None:
    target, mandate = preview_inputs()
    preview = build_call_acceptance_preview(target, mandate)
    value = b"different display" if field == "display_bytes" else "d" * 64
    changed = preview.model_copy(update={field: value})
    with pytest.raises(LoopRejected):
        decode_call_acceptance_preview(changed.canonical_bytes())


def test_noncanonical_outer_or_mandate_bytes_reject() -> None:
    target, mandate = preview_inputs()
    preview = build_call_acceptance_preview(target, mandate)
    with pytest.raises(LoopRejected):
        decode_call_acceptance_preview(b" " + preview.canonical_bytes())
    changed = preview.model_copy(update={"mandate_bytes": b" " + preview.mandate_bytes})
    with pytest.raises(LoopRejected):
        decode_call_acceptance_preview(changed.canonical_bytes())


def test_changed_current_run_changes_preview_identity_without_renewing_mandate() -> None:
    target, mandate = preview_inputs()
    first = build_call_acceptance_preview(target, mandate)
    revision = target.current_run.revision.model_copy(update={"head": "later-head"})
    later = target.model_copy(
        update={"current_run": target.current_run.model_copy(update={"revision": revision})}
    )
    second = build_call_acceptance_preview(later, mandate)
    assert second.preview_id != first.preview_id
    assert second.preview_fingerprint != first.preview_fingerprint
    assert second.mandate_bytes == first.mandate_bytes
