"""Public non-tool consumers retain their real stream identity and exact bytes."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.effects.action_lifecycle_contracts import ActionClaimV1, ActionEvidenceV1


def test_non_tool_claim_has_real_stream_and_no_fabricated_call() -> None:
    value = ActionClaimV1(
        head="head",
        tenant_id="t",
        principal_id="p",
        effect_stream_id="plan-effect",
        send_head="send",
        worker="worker",
    )
    assert ActionClaimV1.model_validate_json(value.canonical_bytes()) == value
    with pytest.raises(ValidationError):
        ActionClaimV1.model_validate({**value.model_dump(), "original_call_id": "fake"})
    evidence = ActionEvidenceV1(
        head="e",
        tenant_id="t",
        principal_id="p",
        effect_stream_id="plan-effect",
        send_head="send",
        predecessor_evidence=None,
        raw_receipt=b"\x00\xff",
    )
    assert ActionEvidenceV1.model_validate_json(evidence.canonical_bytes()) == evidence
