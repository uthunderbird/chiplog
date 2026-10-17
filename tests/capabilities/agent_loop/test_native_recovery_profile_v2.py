import sqlite3
from types import SimpleNamespace

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
from chiplog.composition.r14_recovery_profile_publication import (
    RetainedNativeProfilePublication,
    _assert_v2_precedes_covered_lineage,
    profile_command,
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


def test_profile_command_keeps_exact_v1_decoder_and_selects_v2_schema() -> None:
    old = RetainedNativeProfilePublication(
        tenant_id="tenant",
        database_id="db",
        record_bytes=native_one_call_recovery_profile().canonical_bytes(),
        expected_head=0,
        predecessor="0" * 64,
    )
    current = old.model_copy(
        update={"record_bytes": native_one_call_recovery_profile_v2().canonical_bytes()}
    )

    old_command = profile_command(old)
    current_command = profile_command(current)

    assert old_command.operation_kind == "agent_loop.native-one-call-recovery-profile.v1"
    assert old_command.records[0].schema_id.endswith(".v1")
    assert current_command.operation_kind == "agent_loop.native-one-call-recovery-profile.v2"
    assert current_command.records[0].schema_id.endswith(".v2")


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

    retained = RetainedNativeProfilePublication(
        tenant_id="tenant",
        database_id="db",
        record_bytes=v3.canonical_bytes(),
        expected_head=0,
        predecessor="0" * 64,
    )
    command = profile_command(retained)
    assert command.operation_kind == "agent_loop.native-one-call-recovery-profile.v3"
    assert command.records[0].schema_id.endswith(".v3")


def test_v2_selection_guard_rejects_v1_rows_and_late_lineage(tmp_path):
    database = tmp_path / "profile.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE records (tenant_id TEXT, schema_id TEXT, commit_sequence INTEGER)"
        )
        connection.execute("CREATE TABLE publications (tenant_id TEXT, operation_kind TEXT)")
    journal = SimpleNamespace(entries=lambda: ())
    runtime = SimpleNamespace(
        _tenant_id="tenant", _database=database, _loop_decisions=lambda: journal
    )

    _assert_v2_precedes_covered_lineage(runtime)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            ("tenant", "chiplog.call.initialized.v1", 4),
        )
    with pytest.raises(ValueError, match="must precede"):
        _assert_v2_precedes_covered_lineage(runtime)
    _assert_v2_precedes_covered_lineage(runtime, profile_sequence=3)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?)",
            ("tenant", "chiplog.execution.native-one-call-recovery-profile.v1", 2),
        )
    with pytest.raises(ValueError, match="V1 recovery profile"):
        _assert_v2_precedes_covered_lineage(runtime, profile_sequence=1)


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

    for profile, version in ((v4, "v4"), (v5, "v5")):
        retained = RetainedNativeProfilePublication(
            tenant_id="tenant",
            database_id="db",
            record_bytes=profile.canonical_bytes(),
            expected_head=0,
            predecessor="0" * 64,
        )
        command = profile_command(retained)
        assert command.operation_kind.endswith(version)
        assert command.records[0].schema_id.endswith(version)
