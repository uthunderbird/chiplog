import pytest

from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    decode_native_one_call_recovery_profile,
    decode_native_one_call_recovery_profile_v2,
    decode_native_one_call_recovery_profile_v3,
    decode_native_one_call_recovery_profile_v4,
    decode_native_one_call_recovery_profile_v5,
    native_one_call_recovery_profile,
    native_one_call_recovery_profile_v2,
    native_one_call_recovery_profile_v3,
    native_one_call_recovery_profile_v4,
    native_one_call_recovery_profile_v5,
)


def test_native_recovery_profile_v2_is_closed_and_distinct_from_v1() -> None:
    old = native_one_call_recovery_profile()
    current = native_one_call_recovery_profile_v2()

    assert decode_native_one_call_recovery_profile(old.canonical_bytes()) == old
    assert decode_native_one_call_recovery_profile_v2(current.canonical_bytes()) == current
    assert current.schema_id.endswith(".v2")
    assert current.registry.version == "2"
    assert current.call_branches == (
        "CANCELLED_BEFORE_ACCEPT",
        "CONSEQUENTIAL_ACCEPTED_RECOVERY",
    )
    assert current.obligation_states == ("OPEN", "CLOSED")
    assert "SEND_COMMITTED" in current.dispatch_record_kinds
    assert current.dispatch_outcome_record_kinds == ("EVIDENCE_RECORDED", "RECONCILED")
    with pytest.raises(ValueError):
        decode_native_one_call_recovery_profile_v2(old.canonical_bytes())
    with pytest.raises(ValueError):
        decode_native_one_call_recovery_profile(current.canonical_bytes())


def test_native_recovery_profile_v3_preserves_v2_and_registers_accepted_transitions() -> None:
    v2 = native_one_call_recovery_profile_v2()
    v3 = native_one_call_recovery_profile_v3()

    assert decode_native_one_call_recovery_profile_v2(v2.canonical_bytes()) == v2
    assert decode_native_one_call_recovery_profile_v3(v3.canonical_bytes()) == v3
    assert v3.schema_id.endswith(".v3")
    assert v3.registry == v2.registry
    assert v3.causal_transitions[: len(v2.causal_transitions)] == v2.causal_transitions
    assert "OriginalObligationResolved" in v3.causal_transitions
    assert "SemanticEvidenceReduced" in v3.causal_transitions
    with pytest.raises(ValueError):
        decode_native_one_call_recovery_profile_v2(v3.canonical_bytes())


def test_v5_adds_obligation_suspend_transitions_without_reinterpreting_v4() -> None:
    v4 = native_one_call_recovery_profile_v4()
    v5 = native_one_call_recovery_profile_v5()

    assert decode_native_one_call_recovery_profile_v4(v4.canonical_bytes()) == v4
    assert decode_native_one_call_recovery_profile_v5(v5.canonical_bytes()) == v5
    assert v5.causal_transitions[: len(v4.causal_transitions)] == v4.causal_transitions
    assert v5.causal_change_set != v4.causal_change_set
    assert "ExecutionSuspensionWithOriginalObligation" in v5.causal_transitions
    assert "ExecutionResumeAfterOriginalObligationResolution" in v5.causal_transitions
    with pytest.raises(ValueError):
        decode_native_one_call_recovery_profile_v4(v5.canonical_bytes())
    with pytest.raises(ValueError):
        decode_native_one_call_recovery_profile_v5(v4.canonical_bytes())
