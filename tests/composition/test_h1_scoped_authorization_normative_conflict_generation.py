"""Focused lifecycle checks for the inert V22 prepublication-generation source."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from chiplog.capabilities.effects.h1_normative_conflict_generation import (
    H1EffectsHistoryV1,
    h1_effects_history_digest,
)


class _Gate:
    held = False

    @contextmanager
    def hold(self):
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def require_held(self) -> None:
        if not self.held:
            raise RuntimeError("gate is not held")


class _Runtime:
    def __init__(self) -> None:
        self._gate = _Gate()

    def _authority_gate(self) -> _Gate:
        return self._gate


def _installed(monkeypatch: pytest.MonkeyPatch):
    import chiplog.composition.h1_scoped_authorization_normative_conflict_generation as subject

    runtime = _Runtime()
    selected = _Selected()
    issuance = _V4()
    predecessor = SimpleNamespace(
        tenant_id="tenant", head="predecessor", decisions=(), materialized_command_ids=frozenset()
    )
    history = H1EffectsHistoryV1(
        tenant_id="tenant",
        owner_journal_head="predecessor",
        ordered_members=(),
        digest=h1_effects_history_digest("tenant", "predecessor", ()),
    )
    effects_capture = object()
    joined_capture = object.__new__(subject.H1ScopedAuthorizationDeliveryWorkCaptureV1)
    effects = SimpleNamespace(
        selected=SimpleNamespace(selected_h1=SimpleNamespace(decision=selected, issuance=issuance))
    )

    class _Joined:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self._effects = SimpleNamespace(_issued_record_held=lambda _: effects)
            self.changed = False

        def recheck_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            if self.changed:
                raise subject.H1ScopedAuthorizationDeliveryWorkHold("changed")
            assert capture is joined_capture
            return capture

        def _issued_record_held(self, capture: object) -> object:
            assert capture is joined_capture
            return SimpleNamespace(effects_capture=effects_capture)

    class _Clock:
        def __init__(self, installed_runtime: object) -> None:
            self._runtime = installed_runtime
            self.released: list[object] = []

        def read(self, capture: object) -> object:
            assert capture is joined_capture
            return object()

        def role_held(self, capture: object) -> object:
            runtime._authority_gate().require_held()
            return SimpleNamespace(
                clock_contract="chiplog.dispatch.monotonic.v2",
                clock_epoch="epoch",
                valid_until_ns=50,
            )

        def release(self, capture: object) -> None:
            self.released.append(capture)

    joined, clock = _Joined(runtime), _Clock(runtime)
    runtime._h1_scoped_authorization_delivery_work_reader_v1 = joined
    runtime._h1_scoped_authorization_clock_reader_v1 = clock
    runtime._owner_decisions = lambda: SimpleNamespace(
        predecessor_snapshot_for_selected=lambda value: predecessor
    )
    monkeypatch.setattr(subject, "CommonCliExecutionRuntime", _Runtime)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationDeliveryWorkReaderV1", _Joined)
    monkeypatch.setattr(subject, "H1ScopedAuthorizationClockReaderV1", _Clock)
    monkeypatch.setattr(subject, "SelectedOwnerDecision", _Selected)
    monkeypatch.setattr(subject, "H1CompletionIssuanceV4", _V4)
    monkeypatch.setattr(
        subject,
        "_read_materialized_effects_with_history",
        lambda *_: (SimpleNamespace(), SimpleNamespace(decisions=(selected,))),
    )
    verified = SimpleNamespace(commitment="commitment", tenant_head=lambda _: 1)

    def prechecks(*_args: object, **_kwargs: object) -> tuple[object, object]:
        return verified, predecessor

    monkeypatch.setattr(subject, "_require_v2_read_plan_prechecks", prechecks)
    monkeypatch.setattr(
        subject.H1ScopedAuthorizationNormativeConflictGenerationReaderV1,
        "_prefix_history",
        staticmethod(lambda *_, **__: history),
    )
    return subject, runtime, joined_capture, joined, clock


class _Selected:
    tenant_commit_sequence = 2
    prepared = SimpleNamespace(
        request=SimpleNamespace(
            expected=SimpleNamespace(
                expected_materialization_commitment="commitment",
                tenant_frontier=1,
                tenant_id="tenant",
            )
        )
    )


class _V4:
    pass


def test_prefix_history_rejects_later_unrelated_effects_member() -> None:
    import chiplog.composition.h1_scoped_authorization_normative_conflict_generation as subject

    prefix = SimpleNamespace(owner="effects", record_id="prefix")
    target = SimpleNamespace(owner="effects", record_id="target")
    later = SimpleNamespace(owner="effects", record_id="later")
    predecessor = SimpleNamespace(
        decisions=(
            SimpleNamespace(
                tenant_commit_sequence=1,
                prepared=SimpleNamespace(request=SimpleNamespace(complete_records=(prefix,))),
            ),
        )
    )
    selected = SimpleNamespace(
        tenant_commit_sequence=2,
        prepared=SimpleNamespace(request=SimpleNamespace(complete_records=(target,))),
    )
    cut = SimpleNamespace(
        rows=(
            SimpleNamespace(commit_sequence=1, record=prefix),
            SimpleNamespace(commit_sequence=2, record=target),
            SimpleNamespace(commit_sequence=3, record=later),
        )
    )

    with pytest.raises(ValueError, match="prepublication effects prefix differs"):
        subject.H1ScopedAuthorizationNormativeConflictGenerationReaderV1._prefix_history(
            cut, predecessor, selected, object(), runtime=object()
        )


def test_prefix_history_passes_installed_runtime_to_prior_scoped_binder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chiplog.composition.h1_scoped_authorization_normative_conflict_generation as subject

    prior = SimpleNamespace(
        owner="effects", record_id="prior", schema_id="schema", canonical_bytes=b"prior"
    )
    target = SimpleNamespace(owner="effects", record_id="target")
    decision = SimpleNamespace(
        tenant_commit_sequence=1,
        prepared=SimpleNamespace(request=SimpleNamespace(complete_records=(prior,))),
    )
    predecessor = SimpleNamespace(tenant_id="tenant", head="head", decisions=(decision,))
    selected = SimpleNamespace(
        tenant_commit_sequence=2,
        prepared=SimpleNamespace(request=SimpleNamespace(complete_records=(target,))),
    )
    cut = SimpleNamespace(
        rows=(
            SimpleNamespace(commit_sequence=1, record=prior),
            SimpleNamespace(commit_sequence=2, record=target),
        )
    )
    verified_rows = SimpleNamespace(physical_record=lambda **_: ("effects", "schema", b"prior", 1))
    runtime = object()
    interpreted = object()

    def interpret(*args: object, runtime: object | None = None) -> object:
        assert args[1] is predecessor
        assert runtime is installed_runtime
        return interpreted

    installed_runtime = runtime
    monkeypatch.setattr(
        subject, "replace", lambda value, **changes: SimpleNamespace(**(vars(value) | changes))
    )
    monkeypatch.setattr(subject, "interpret_h1_producer_effects_history", interpret)
    monkeypatch.setattr(subject, "_effects_history", lambda value: value)
    assert (
        subject.H1ScopedAuthorizationNormativeConflictGenerationReaderV1._prefix_history(
            cut, predecessor, selected, verified_rows, runtime=runtime
        )
        is interpreted
    )


def test_normative_source_is_reader_issued_rechecked_and_revoked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, joined, clock = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationNormativeConflictGenerationReaderV1(runtime)

    capture = reader.read(joined_capture)
    with runtime._authority_gate().hold():
        source = reader.source_held(capture)
    assert source.clock_epoch == "epoch"

    joined.changed = True
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationNormativeConflictGenerationHold,
            match="prefix is unavailable",
        ),
    ):
        reader.recheck_held(capture)
    joined.changed = False
    reader.release(capture)
    assert len(clock.released) == 1
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationNormativeConflictGenerationHold, match="not reader-issued"
        ),
    ):
        reader.source_held(capture)


def test_normative_source_rejects_forged_capture_and_close_revokes_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, _, clock = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationNormativeConflictGenerationReaderV1(runtime)

    forged = object.__new__(subject.H1ScopedAuthorizationNormativeConflictGenerationCaptureV1)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationNormativeConflictGenerationHold, match="not reader-issued"
        ),
    ):
        reader.source_held(forged)

    capture = reader.read(joined_capture)
    reader.close()
    assert len(clock.released) == 1
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationNormativeConflictGenerationHold, match="reader is closed"
        ),
    ):
        reader.source_held(capture)


def test_normative_source_release_survives_a_revoked_clock_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject, runtime, joined_capture, _, clock = _installed(monkeypatch)
    reader = subject.H1ScopedAuthorizationNormativeConflictGenerationReaderV1(runtime)
    capture = reader.read(joined_capture)

    def revoked(_: object) -> None:
        raise subject.H1ScopedAuthorizationClockHold("clock reader is closed")

    clock.release = revoked
    reader.release(capture)
    with (
        runtime._authority_gate().hold(),
        pytest.raises(
            subject.H1ScopedAuthorizationNormativeConflictGenerationHold, match="not reader-issued"
        ),
    ):
        reader.source_held(capture)
