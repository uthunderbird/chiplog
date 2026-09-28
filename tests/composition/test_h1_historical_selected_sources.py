"""Closure tests for the H1 historical selected-source boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import chiplog.composition.h1_historical_selected_sources as historical_sources
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import (
    V2_SCHEMA,
    H1CompletionIssuanceV1,
    H1CompletionIssuanceV2,
)
from chiplog.composition.h1_delivery_evidence_contracts import H1DeliverySelectionClosureV2
from chiplog.composition.h1_historical_selected_sources import (
    _open_historical_ports,
    _require_historical_ports,
    _verify_historical_scope,
    bind_selected_h1_completion,
    verify_h1_historical_sources,
)
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.h1_delivery_binding_contracts import H1DeliveryBinding
from chiplog.platform.owner_decision_journal import OwnerJournalSnapshot
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from chiplog.platform.r7_trust import TrustOwnerCall


class _FrameDTO:
    """Tiny JSON-mode DTO double that exposes wire, rather than object, equality."""

    def __init__(self, value: dict[str, object]) -> None:
        self._value = value

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return self._value


def _selected_recovery_seam_inputs(*, recovery_head: str = "head") -> tuple[object, object, object]:
    """Minimal raw-source doubles for the read-only recovery join prechecks."""
    identity = SimpleNamespace()
    h0 = SimpleNamespace(
        initialization=SimpleNamespace(
            driver_request_bytes=b"h0", driver_request_fingerprint="f" * 64
        )
    )
    issuance = SimpleNamespace(
        recovery=SimpleNamespace(
            root_id="r" * 64,
            journal_instance_id="journal",
            completed_chain_head=recovery_head,
        )
    )
    return issuance, h0, identity


def _install_selected_recovery_precheck_doubles(
    monkeypatch: pytest.MonkeyPatch,
    *,
    recovery_head: str = "head",
    state_head: str = "head",
    matching_root: bool = True,
) -> tuple[object, object, object]:
    issuance, h0, identity = _selected_recovery_seam_inputs(recovery_head=recovery_head)
    root = SimpleNamespace(
        root_id=lambda: "r" * 64,
        journal_instance_id="journal",
    )
    state = SimpleNamespace(
        root=root if matching_root else SimpleNamespace(root_id=lambda: "s" * 64),
        head=state_head,
        inputs=(
            ("COMPLETION", b"", None),
            ("CONVERSATION", b"", None),
            ("EFFECTS", b"", "effects"),
            ("TERMINAL_WORK", b"", None),
        ),
        results=(
            ("COMPLETION", b""),
            ("CONVERSATION", b""),
            ("EFFECTS", b""),
            ("TERMINAL_WORK", b""),
        ),
    )

    class Driver:
        @staticmethod
        def model_validate_json(raw: bytes) -> object:
            assert raw == b"h0"
            return SimpleNamespace(identity=identity, canonical_bytes=lambda: raw)

    class Native:
        def __init__(self, _runtime: object) -> None:
            pass

        def locate_selected_seal(self, **_kwargs: object) -> object:
            return object()

    class RootSource:
        def __init__(self, _runtime: object) -> None:
            pass

        def derive_on_restart(self, *_args: object) -> object:
            return root

    class Journal:
        def __init__(self) -> None:
            self._mount = object()

        def scan(self) -> object:
            return SimpleNamespace(state_for_root=lambda _: state)

    runtime = SimpleNamespace(_h1_recovery_mount=object())
    journal = Journal()
    journal._mount = runtime._h1_recovery_mount
    runtime._h1_postseal_recovery_journal = journal
    monkeypatch.setattr(historical_sources, "DriveInputRequestV1", Driver)
    monkeypatch.setattr(historical_sources, "H1V2RecoveryNativeSource", Native)
    monkeypatch.setattr(historical_sources, "H1PostSealRecoveryRootSource", RootSource)
    monkeypatch.setattr(historical_sources, "H1PostSealRecoveryJournal", Journal)
    return issuance, h0, runtime


def test_v2_selected_recovery_rejects_a_co_mutated_journal_and_assembly_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recovery head is independently authenticated, not trusted from issuance."""
    issuance, h0, runtime = _install_selected_recovery_precheck_doubles(
        monkeypatch, recovery_head="co-mutated", state_head="head"
    )

    with pytest.raises(ValueError, match="selected recovery reference differs"):
        historical_sources._verify_v2_selected_recovery(issuance, runtime, h0)


def test_v2_selected_recovery_precheck_never_reaches_live_recovery_apis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source-owned read-only seam rejects before any live recovery path."""
    issuance, h0, runtime = _install_selected_recovery_precheck_doubles(
        monkeypatch, recovery_head="wrong", state_head="head"
    )
    for name in (
        "_require_current",
        "_issue_historical_recovery_source",
        "_assert_launch_and_trust",
        "_validate_complete_chain_held",
    ):
        setattr(
            runtime,
            name,
            lambda *_args, name=name, **_kwargs: pytest.fail(f"live API reached: {name}"),
        )

    with pytest.raises(ValueError, match="selected recovery reference differs"):
        historical_sources._verify_v2_selected_recovery(issuance, runtime, h0)


def test_v2_selected_recovery_rejects_a_durable_root_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuance, h0, runtime = _install_selected_recovery_precheck_doubles(
        monkeypatch, matching_root=False
    )

    with pytest.raises(ValueError, match="selected recovery reference differs"):
        historical_sources._verify_v2_selected_recovery(issuance, runtime, h0)


def _v2_selected_scope_frame_inputs() -> tuple[object, object, object]:
    issue_sent = _FrameDTO({"request_id": "issue", "metadata": "selected"})
    issue_returned = _FrameDTO({"request_id": "issue", "result": "selected"})
    current_sent = _FrameDTO({"request_id": "current", "metadata": "selected"})
    current_returned = _FrameDTO({"request_id": "current", "result": "selected"})
    issuance = SimpleNamespace(
        scope_issue_exchange=SimpleNamespace(
            sent=issue_sent,
            returned=issue_returned,
            sent_at_ns=10,
            returned_at_ns=11,
        ),
        scope_current_exchange=SimpleNamespace(
            sent=current_sent,
            returned=current_returned,
            sent_at_ns=12,
            returned_at_ns=13,
        ),
    )
    native = SimpleNamespace(
        seal=SimpleNamespace(
            raw_bytes=json.dumps(
                {
                    "h1_preseal_pe_anchor": "anchor",
                    "h1_preseal_p_scope_frames_v1": {"frames": "selected"},
                    "h1_preseal_p_scope_wires_v1": {"wires": "selected"},
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
    )
    frames = SimpleNamespace(
        wires=lambda **_: (
            SimpleNamespace(
                sent=issue_sent,
                returned=issue_returned,
                sent_at_ns=10,
                returned_at_ns=11,
            ),
            SimpleNamespace(
                sent=current_sent,
                returned=current_returned,
                sent_at_ns=12,
                returned_at_ns=13,
            ),
        )
    )
    return issuance, native, frames


def _install_selected_scope_frame_codecs(
    monkeypatch: pytest.MonkeyPatch, frames: object
) -> None:
    anchor = SimpleNamespace(
        binding={"bound": "anchor"},
        as_dict=lambda: {
            "p": {
                "accepted_issue_wire_digest": "issue-digest",
                "accepted_current_wire_digest": "current-digest",
            }
        },
    )
    monkeypatch.setattr(
        historical_sources, "decode_h1_preseal_pe_anchor_record", lambda _: anchor
    )
    monkeypatch.setattr(
        historical_sources, "decode_h1_preseal_p_scope_wires", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        historical_sources, "decode_h1_preseal_p_scope_frames", lambda *_args, **_kwargs: frames
    )


def test_v2_selected_preseal_scope_frames_require_exact_full_dto_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuance, native, frames = _v2_selected_scope_frame_inputs()
    _install_selected_scope_frame_codecs(monkeypatch, frames)

    historical_sources._require_v2_selected_preseal_scope_frames(issuance, native)


@pytest.mark.parametrize(
    "substitute_frame, substitute_timestamp",
    ((True, False), (False, True)),
)
def test_v2_selected_preseal_scope_frames_reject_payload_preserving_metadata_substitution(
    monkeypatch: pytest.MonkeyPatch, substitute_frame: bool, substitute_timestamp: bool
) -> None:
    issuance, native, _frames = _v2_selected_scope_frame_inputs()
    substituted_sent = (
        _FrameDTO({"request_id": "issue", "metadata": "substituted"})
        if substitute_frame
        else issuance.scope_issue_exchange.sent
    )
    frames = SimpleNamespace(
        wires=lambda **_: (
            SimpleNamespace(
                sent=substituted_sent,
                returned=issuance.scope_issue_exchange.returned,
                sent_at_ns=(
                    issuance.scope_issue_exchange.sent_at_ns - 1
                    if substitute_timestamp
                    else issuance.scope_issue_exchange.sent_at_ns
                ),
                returned_at_ns=issuance.scope_issue_exchange.returned_at_ns,
            ),
            SimpleNamespace(
                sent=issuance.scope_current_exchange.sent,
                returned=issuance.scope_current_exchange.returned,
                sent_at_ns=issuance.scope_current_exchange.sent_at_ns,
                returned_at_ns=issuance.scope_current_exchange.returned_at_ns,
            ),
        )
    )
    _install_selected_scope_frame_codecs(monkeypatch, frames)

    with pytest.raises(ValueError, match="full-frame scope issue differs"):
        historical_sources._require_v2_selected_preseal_scope_frames(issuance, native)


def test_v2_selected_preseal_scope_frames_marks_a_missing_legacy_sibling_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuance, native, frames = _v2_selected_scope_frame_inputs()
    decision = json.loads(native.seal.raw_bytes)
    del decision["h1_preseal_p_scope_frames_v1"]
    native.seal.raw_bytes = json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
    _install_selected_scope_frame_codecs(monkeypatch, frames)

    with pytest.raises(
        historical_sources.H1V2SelectedPresealFullFrameUnavailable,
        match="lacks full-frame sibling",
    ):
        historical_sources._require_v2_selected_preseal_scope_frames(issuance, native)


def test_v2_selected_preseal_scope_frames_fails_closed_for_a_malformed_present_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuance, native, frames = _v2_selected_scope_frame_inputs()
    decision = json.loads(native.seal.raw_bytes)
    decision["h1_preseal_p_scope_frames_v1"] = {"frames": "malformed"}
    native.seal.raw_bytes = json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
    _install_selected_scope_frame_codecs(monkeypatch, frames)
    monkeypatch.setattr(
        historical_sources,
        "decode_h1_preseal_p_scope_frames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("malformed")),
    )

    with pytest.raises(ValueError, match="full-frame sibling is malformed"):
        historical_sources._require_v2_selected_preseal_scope_frames(issuance, native)


def _v2_readplan_inputs() -> tuple[object, object, object, object, object]:
    """Small raw-cut fixture for V2-only precheck negatives."""
    from chiplog.composition.h1_completion_readplan_registry import current_registry

    registry = current_registry()
    expected = SimpleNamespace(
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        expected_materialization_commitment="a" * 64,
        tenant_frontier=3,
    )
    checkpoint = SimpleNamespace(authority_surface_digest="b" * 64)
    read_plan = SimpleNamespace(
        registry_bytes=registry.canonical_bytes,
        predecessor_checkpoint=checkpoint,
        predecessor_owner_head="materialized-before",
    )
    issuance = SimpleNamespace(read_plan=read_plan, capture=SimpleNamespace(expected=expected))
    batch = SimpleNamespace(identity=SimpleNamespace(tenant_id="tenant"), expected=expected)
    decision = SimpleNamespace(
        decision_id="selected",
        prepared=SimpleNamespace(
            request=SimpleNamespace(identity=SimpleNamespace(command_id="old"))
        ),
    )
    return batch, issuance, decision, checkpoint, read_plan


def _install_readplan_checkpoint(
    monkeypatch: pytest.MonkeyPatch, *, commitment: str = "a" * 64, frontier: int = 3
) -> tuple[object, list[tuple[object, str, str]]]:
    calls: list[tuple[object, str, str]] = []

    class Store:
        def resolve_verified(
            self, ref: object, *, expected_resulting: str, expected_surface_digest: str
        ) -> object:
            calls.append((ref, expected_resulting, expected_surface_digest))
            return object()

    class Rows:
        def __init__(self) -> None:
            self.commitment = commitment

        def tenant_head(self, tenant_id: str) -> int:
            assert tenant_id == "tenant"
            return frontier

    monkeypatch.setattr(
        historical_sources.H1VerifiedSnapshotRows,
        "from_verified",
        staticmethod(lambda _: Rows()),
    )
    return Store(), calls


def test_v2_readplan_requires_raw_immediate_owner_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch, issuance, decision, _checkpoint, read_plan = _v2_readplan_inputs()
    store, _calls = _install_readplan_checkpoint(monkeypatch)
    owner = SimpleNamespace(
        _raw=SimpleNamespace(entries=lambda: (("selected", "not-the-head", b"selected"),)),
        snapshot_at=lambda _: pytest.fail("predecessor mismatch reached snapshot_at"),
    )
    runtime = SimpleNamespace(_h1_checkpoint_store=lambda: store)

    with pytest.raises(ValueError, match="owner predecessor"):
        historical_sources._require_v2_read_plan_prechecks(
            batch, issuance, runtime=runtime, owner_journal=owner, decision=decision
        )
    assert read_plan.predecessor_owner_head == "materialized-before"


def test_v2_readplan_rejects_a_missing_checkpoint_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch, issuance, decision, _checkpoint, _read_plan = _v2_readplan_inputs()
    owner = SimpleNamespace(
        _raw=SimpleNamespace(entries=lambda: (("selected", "materialized-before", b"selected"),)),
        snapshot_at=lambda head: OwnerJournalSnapshot("tenant", head, (), frozenset()),
    )

    with pytest.raises(ValueError, match="lacks checkpoint store"):
        historical_sources._require_v2_read_plan_prechecks(
            batch, issuance, runtime=object(), owner_journal=owner, decision=decision
        )


@pytest.mark.parametrize(
    "registry_bytes",
    (
        b'{"roles":[],"schema":"chiplog.h1-completion-read-plan-registry.v1","version":1}',
        b'{"roles":[],"roles":[],"schema":"chiplog.h1-completion-read-plan-registry.v1","version":1}',
    ),
)
def test_v2_readplan_rejects_unknown_or_duplicate_frozen_registry_before_checkpoint(
    monkeypatch: pytest.MonkeyPatch, registry_bytes: bytes
) -> None:
    batch, issuance, decision, _checkpoint, read_plan = _v2_readplan_inputs()
    store, calls = _install_readplan_checkpoint(monkeypatch)
    read_plan.registry_bytes = registry_bytes
    owner = SimpleNamespace(
        _raw=SimpleNamespace(entries=lambda: (("selected", "materialized-before", b"selected"),)),
        snapshot_at=lambda _: pytest.fail("invalid registry reached owner journal"),
    )
    runtime = SimpleNamespace(_h1_checkpoint_store=lambda: store)

    with pytest.raises(ValueError, match="read-plan registry"):
        historical_sources._require_v2_read_plan_prechecks(
            batch, issuance, runtime=runtime, owner_journal=owner, decision=decision
        )
    assert calls == []


def test_v2_readplan_resolves_checkpoint_with_batch_commitment_and_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch, issuance, decision, checkpoint, _read_plan = _v2_readplan_inputs()
    store, calls = _install_readplan_checkpoint(monkeypatch)
    owner = SimpleNamespace(
        _raw=SimpleNamespace(entries=lambda: (("selected", "materialized-before", b"selected"),)),
        snapshot_at=lambda head: OwnerJournalSnapshot("tenant", head, (), frozenset()),
    )
    runtime = SimpleNamespace(_h1_checkpoint_store=lambda: store)

    historical_sources._require_v2_read_plan_prechecks(
        batch, issuance, runtime=runtime, owner_journal=owner, decision=decision
    )

    assert calls == [
        (
            checkpoint,
            batch.expected.expected_materialization_commitment,
            historical_sources.AUTHORITY_STORAGE_SURFACE_DIGEST,
        )
    ]


@pytest.mark.parametrize("commitment, frontier", (("b" * 64, 3), ("a" * 64, 4)))
def test_v2_readplan_rejects_stale_checkpoint_commitment_or_frontier(
    monkeypatch: pytest.MonkeyPatch, commitment: str, frontier: int
) -> None:
    batch, issuance, decision, _checkpoint, _read_plan = _v2_readplan_inputs()
    store, _calls = _install_readplan_checkpoint(
        monkeypatch, commitment=commitment, frontier=frontier
    )
    owner = SimpleNamespace(
        _raw=SimpleNamespace(entries=lambda: (("selected", "materialized-before", b"selected"),)),
        snapshot_at=lambda head: OwnerJournalSnapshot("tenant", head, (), frozenset()),
    )
    runtime = SimpleNamespace(_h1_checkpoint_store=lambda: store)

    with pytest.raises(ValueError, match="checkpoint frontier"):
        historical_sources._require_v2_read_plan_prechecks(
            batch, issuance, runtime=runtime, owner_journal=owner, decision=decision
        )


class _NoHistoricalPorts:
    """Looks deliberately unlike an executable/current H1 runtime."""

    class _Gate:
        def hold(self) -> object:
            raise AssertionError("historical verifier must not enter a partial gate")

    def _authority_gate(self) -> _Gate:
        return self._Gate()

    def _require_dispatch_resources(self) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used current dispatch resources")

    def read_admitted_inbox(self, _: str) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used the ingress history facade")

    def _find(self, *_: object) -> object:  # pragma: no cover - must not be called
        raise AssertionError("historical verifier used the current H1 reader")


def test_verifier_rejects_untyped_batch_before_any_runtime_access() -> None:
    runtime = _NoHistoricalPorts()

    with pytest.raises(TypeError, match="CompleteDeliveryBatchV2"):
        verify_h1_historical_sources(object(), object(), runtime)  # type: ignore[arg-type]


def test_binder_rejects_untyped_batch_before_any_runtime_access() -> None:
    runtime = _NoHistoricalPorts()

    with pytest.raises(TypeError, match="CompleteDeliveryBatchV2"):
        bind_selected_h1_completion(object(), runtime)  # type: ignore[arg-type]


def test_historical_reader_accepts_the_exact_v2_issuance_type() -> None:
    """V2 reaches the same historical source boundary; subclasses never do."""
    batch = CompleteDeliveryBatchV2.model_construct()
    issuance = H1CompletionIssuanceV2.model_construct()

    historical_sources._require_typed_inputs(batch, issuance)


def test_historical_ports_are_not_silently_replaced_by_current_h1_services() -> None:
    """A runtime missing the registered raw seam fails before any current fallback."""
    runtime = _NoHistoricalPorts()

    # Deliberately bypass the typed wire boundary: this exercises only the
    # fail-closed runtime capability check and proves no current fallback runs.
    with pytest.raises(ValueError, match="historical custody"):
        _require_historical_ports(runtime)


class _NullCustodyPorts:
    class _Gate:
        def hold(self) -> object:
            raise AssertionError("test opens raw ports directly")

    def _authority_gate(self) -> _Gate:
        return self._Gate()

    def _h1_historical_custody(self) -> object:
        return None

    def _loop_decisions(self) -> object:
        return type("Loop", (), {"entries": lambda self: ()})()

    def _owner_decisions(self) -> object:
        return type("Owners", (), {"snapshot": lambda self: object()})()

    def _h1_historical_trust_reader(self) -> object:
        return object()


def test_raw_port_open_rejects_missing_custody_without_current_fallback() -> None:
    """The gate-held opener refuses a null custody binding rather than substituting resources."""
    ports = _require_historical_ports(_NullCustodyPorts())

    with pytest.raises(ValueError, match="unsupported raw selected-source reader"):
        _open_historical_ports(ports)


class _MissingTrustPorts(_NullCustodyPorts):
    def _h1_historical_custody(self) -> object:
        return object()


def test_raw_port_open_requires_historical_trust_methods() -> None:
    """An opaque trust object cannot be treated as a historical raw reader."""
    ports = _require_historical_ports(_MissingTrustPorts())

    with pytest.raises(ValueError, match="unsupported raw selected-source reader"):
        _open_historical_ports(ports)


def test_historical_scope_rejects_a_trust_reader_on_a_different_gate() -> None:
    """Raw trust facts from another authority cut cannot be joined to H1 evidence."""
    reader = SimpleNamespace(authority_gate=object())

    with pytest.raises(ValueError, match="different authority gate"):
        _verify_historical_scope(
            SimpleNamespace(),  # type: ignore[arg-type]
            trust_reader=reader,
            gate=object(),
        )


def _unvalidated_historical_inputs() -> tuple[CompleteDeliveryBatchV2, H1CompletionIssuanceV1]:
    """Build only exact outer types; raw authority is supplied by the test seam."""
    source = object()
    retained = SimpleNamespace(initialization_envelope_bytes=b"retained-initialization")
    issuance = H1CompletionIssuanceV1.model_construct(
        assembly=SimpleNamespace(
            original_completion_request=SimpleNamespace(source=source),
            ordered_effects=(
                SimpleNamespace(
                    owner_call=SimpleNamespace(request=SimpleNamespace(retained_origin=retained))
                ),
            ),
        )
    )
    return CompleteDeliveryBatchV2.model_construct(), issuance


def test_historical_verifier_requires_the_exact_common_cli_runtime_before_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duck-typed R14 runtime cannot enter the raw historical boundary."""
    batch, issuance = _unvalidated_historical_inputs()

    monkeypatch.setattr(
        historical_sources,
        "_require_historical_ports",
        lambda _: pytest.fail("wrong runtime reached historical ports"),
    )

    with pytest.raises(TypeError, match="canonical common CLI runtime"):
        verify_h1_historical_sources(batch, issuance, object())


def test_selected_owner_decision_returns_from_the_single_outer_historical_cut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Binding uses the owner decision read beside native validation, never a later cut."""
    batch, issuance = _unvalidated_historical_inputs()
    runtime = object.__new__(CommonCliExecutionRuntime)
    events: list[str] = []
    decision = object()

    class Gate:
        @contextmanager
        def hold(self) -> Iterator[None]:
            events.append("enter")
            try:
                yield
            finally:
                events.append("exit")

    ports = SimpleNamespace(
        gate=Gate(),
        custody_factory=object(),
        loop_factory=object(),
        owner_factory=object(),
        trust_factory=object(),
    )

    class Sources:
        def __init__(self, actual_runtime: object) -> None:
            assert actual_runtime is runtime

        def validate_historical(
            self, source: object, *, initialization_envelope_bytes: bytes
        ) -> None:
            assert source is issuance.assembly.original_completion_request.source
            assert initialization_envelope_bytes == b"retained-initialization"
            events.append("native")

    monkeypatch.setattr(historical_sources, "_require_historical_ports", lambda _: ports)
    monkeypatch.setattr(
        historical_sources,
        "_open_historical_ports",
        lambda _: (object(), object(), object(), object()),
    )
    monkeypatch.setattr(historical_sources, "read_historical_h0_r17", lambda *_: object())
    monkeypatch.setattr(
        historical_sources, "_verify_historical_r16", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        historical_sources, "_verify_historical_scope", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(historical_sources, "H1FirstPathSources", Sources)
    monkeypatch.setattr(
        historical_sources,
        "_selected_owner_decision",
        lambda actual_batch, _owner: (
            decision if actual_batch is batch else pytest.fail("batch changed")
        ),
    )
    monkeypatch.setattr(
        "chiplog.composition.h1_completion_issuance.decode_h1_completion_issuance",
        lambda actual_batch: issuance if actual_batch is batch else pytest.fail("batch changed"),
    )

    verify_h1_historical_sources(batch, issuance, runtime)
    bound = bind_selected_h1_completion(batch, runtime)

    assert bound.decision is decision
    assert bound.issuance is issuance
    assert events == ["enter", "native", "exit", "enter", "native", "exit"]


def test_v2_selected_closure_failure_precedes_all_historical_source_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A selected V2 closure is authenticated before H0 or native replay starts."""
    batch = CompleteDeliveryBatchV2.model_construct()
    issuance = object.__new__(H1CompletionIssuanceV2)
    runtime = object.__new__(CommonCliExecutionRuntime)
    gate = AuthorityGate(tmp_path / "historical-order.sqlite3")
    ports = SimpleNamespace(
        gate=gate,
        custody_factory=object(),
        loop_factory=object(),
        owner_factory=object(),
        trust_factory=object(),
    )
    native_reads: list[str] = []

    class Sources:
        def __init__(self, _runtime: object) -> None:
            native_reads.append("constructed")

    monkeypatch.setattr(historical_sources, "_require_historical_ports", lambda _: ports)
    monkeypatch.setattr(
        historical_sources,
        "_open_historical_ports",
        lambda _: (object(), object(), object(), object()),
    )
    monkeypatch.setattr(historical_sources, "_selected_owner_decision", lambda *_: object())
    monkeypatch.setattr(
        historical_sources,
        "_verify_selected_v2_delivery_closure",
        lambda *_: (_ for _ in ()).throw(ValueError("closure is corrupt")),
    )
    monkeypatch.setattr(historical_sources, "H1FirstPathSources", Sources)
    monkeypatch.setattr(
        historical_sources,
        "read_historical_h0_r17",
        lambda *_: pytest.fail("H0 read reached before selected V2 closure"),
    )

    with pytest.raises(ValueError, match="closure is corrupt"):
        historical_sources._verify_historical_selection(batch, issuance, runtime)

    assert native_reads == []


def test_selected_v2_closure_rejects_missing_or_changed_selected_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retained closure must bind the exact selected request and frontier."""
    from chiplog.composition import h1_delivery_evidence_journal as delivery_journal
    from chiplog.platform import owner_decision_journal

    request_bytes = b"selected-request"
    batch = CompleteDeliveryBatchV2.model_construct(
        authentication=SimpleNamespace(applicability_schema=V2_SCHEMA),
        identity=SimpleNamespace(
            tenant_id="tenant",
            command_id="command",
            command_fingerprint=hashlib.sha256(b"command").hexdigest(),
        ),
        expected=SimpleNamespace(
            tenant_frontier=4,
            expected_materialization_commitment="a" * 64,
        ),
    )
    binding = H1DeliveryBinding(
        deployment_id="deployment",
        database_id="database",
        database_genesis_digest=hashlib.sha256(b"genesis").hexdigest(),
        tenant_id="tenant",
        journal_instance_id="journal",
        closure_entry_id=hashlib.sha256(b"entry").hexdigest(),
        closure_payload_digest=hashlib.sha256(b"payload").hexdigest(),
        closure_schema_id="chiplog.execution.h1-delivery-selection-closure.v2",
        command_id="command",
        command_fingerprint=hashlib.sha256(b"command").hexdigest(),
        request_digest=hashlib.sha256(request_bytes).hexdigest(),
    )
    prepared = PreparedOwnerPublication(
        request=batch,
        issuance_id="issued",
        fence_generation="r6",
        fence_frontier=0,
        predecessor_commitment="a" * 64,
        h1_delivery_binding=binding,
    )
    decision = SelectedOwnerDecision(
        prepared=prepared,
        decision_id="b" * 64,
        decision_head="b" * 64,
        decision_fingerprint="b" * 64,
        resulting_commitment="c" * 64,
        tenant_commit_sequence=5,
    )
    gate = AuthorityGate(tmp_path / "closure-gate.sqlite3")
    runtime = SimpleNamespace(_authority_gate=lambda: gate)
    monkeypatch.setattr(
        owner_decision_journal, "canonical_owner_publication_bytes", lambda _: request_bytes
    )

    class Journal:
        def __init__(self, value: dict[str, object]) -> None:
            self.value = value

        def read_closure(self, actual_binding: object) -> object:
            assert actual_binding is binding
            return SimpleNamespace(record=H1DeliverySelectionClosureV2(b"", self.value))

    base = {
        "deployment_id": "deployment",
        "database_id": "database",
        "database_genesis_digest": hashlib.sha256(b"genesis").hexdigest(),
        "tenant_id": "tenant",
        "principal_id": "principal",
        "journal_instance_id": "journal",
        "command_id": "command",
        "command_fingerprint": hashlib.sha256(b"command").hexdigest(),
        "request_digest": hashlib.sha256(request_bytes).hexdigest(),
        "predecessor_commitment": "a" * 64,
        "expected_tenant_frontier": 4,
    }
    monkeypatch.setattr(delivery_journal, "H1DeliveryEvidenceJournal", Journal)
    runtime._h1_delivery_evidence_journal = Journal(base)
    with gate.hold():
        historical_sources._verify_selected_v2_delivery_closure(decision, runtime)

    issuance = object.__new__(H1CompletionIssuanceV2)
    object.__setattr__(
        issuance,
        "assembly",
        SimpleNamespace(
            original_completion_request=SimpleNamespace(
                source=SimpleNamespace(
                    selected_admitted_input=SimpleNamespace(principal_id="principal")
                )
            )
        ),
    )
    with gate.hold():
        historical_sources._verify_selected_v2_delivery_closure(decision, runtime, issuance)

    for changed in (
        {**base, "request_digest": hashlib.sha256(b"other").hexdigest()},
        {**base, "tenant_id": "other-tenant"},
        {**base, "command_id": "other-command"},
        {**base, "command_fingerprint": hashlib.sha256(b"other-command").hexdigest()},
        {**base, "principal_id": "other-principal"},
        {**base, "predecessor_commitment": "d" * 64},
        {**base, "expected_tenant_frontier": 3},
    ):
        runtime._h1_delivery_evidence_journal = Journal(changed)
        with gate.hold(), pytest.raises(ValueError, match="closure"):
            historical_sources._verify_selected_v2_delivery_closure(decision, runtime, issuance)

    missing = SelectedOwnerDecision(
        prepared=PreparedOwnerPublication(
            request=batch,
            issuance_id="issued",
            fence_generation="r6",
            fence_frontier=0,
            predecessor_commitment="a" * 64,
        ),
        decision_id="b" * 64,
        decision_head="b" * 64,
        decision_fingerprint="b" * 64,
        resulting_commitment="c" * 64,
        tenant_commit_sequence=5,
    )
    with gate.hold(), pytest.raises(ValueError, match="closure is unavailable"):
        historical_sources._verify_selected_v2_delivery_closure(missing, runtime)


@dataclass(frozen=True)
class _Scope:
    database_id: str
    scope_id: str
    revision: int
    predecessor: object | None = None


class _ScopeCodec:
    @staticmethod
    def model_validate(value: dict[str, object]) -> _Scope:
        return _Scope(
            database_id=cast(str, value["database_id"]),
            scope_id=cast(str, value["scope_id"]),
            revision=cast(int, value["revision"]),
            predecessor=value.get("predecessor"),
        )


def _scope_entry(decision_id: str, scope: _Scope) -> tuple[str, None, bytes]:
    return (
        decision_id,
        None,
        json.dumps(
            {
                "kind": "HERMETIC_OUTPUT_SCOPE_V1",
                "payload": {"scope": scope.__dict__},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    )


def test_scope_lineage_rejects_a_later_self_consistent_scope_substitution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer same-lineage scope cannot replace the historical selected row."""
    monkeypatch.setattr(historical_sources, "HermeticOutputScopeV1", _ScopeCodec)
    selected = _Scope("db", "scope", 0)
    substituted = _Scope("db", "scope", 1)
    prefix = SimpleNamespace(
        physical_entries=(_scope_entry("selected", selected), _scope_entry("later", substituted)),
        observation=SimpleNamespace(physical_journal_head=SimpleNamespace(head="before")),
    )

    with pytest.raises(ValueError, match="supersedes"):
        historical_sources._verify_historical_scope_lineage(
            scope=selected,  # type: ignore[arg-type]
            selected_decision_id="selected",
            selected_predecessor="before",
            issue_prefix=prefix,
            current_prefix=prefix,
        )


def test_scope_lineage_rejects_new_scope_with_substituted_physical_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recomputed scope body cannot detach a new decision from the issue cut."""
    monkeypatch.setattr(historical_sources, "HermeticOutputScopeV1", _ScopeCodec)
    selected = _Scope("db", "scope", 0)
    current = SimpleNamespace(
        physical_entries=(_scope_entry("selected", selected),),
        observation=SimpleNamespace(physical_journal_head=SimpleNamespace(head="issue-head")),
    )
    issue = SimpleNamespace(
        physical_entries=(),
        observation=SimpleNamespace(physical_journal_head=SimpleNamespace(head="issue-head")),
    )

    with pytest.raises(ValueError, match="wrong physical predecessor"):
        historical_sources._verify_historical_scope_lineage(
            scope=selected,  # type: ignore[arg-type]
            selected_decision_id="selected",
            selected_predecessor="substituted-head",
            issue_prefix=issue,
            current_prefix=current,
        )


def test_historical_scope_rejects_a_recomputed_owner_response_on_a_different_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-consistent owner response cannot be moved to a different broker route."""
    route = SimpleNamespace(request_id="issue")
    substituted_route = SimpleNamespace(request_id="issue", replacement=True)
    issue_wire = TrustOwnerCall(
        mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
        snapshot_bytes=b"snapshot",
        request_bytes=b"issue-call",
    )
    current_wire = TrustOwnerCall(
        mode="READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
        snapshot_bytes=b"snapshot",
        request_bytes=b"current-call",
    )
    issue_call = SimpleNamespace(
        evidence=SimpleNamespace(route=route, selected_request_bytes=b"issue-intent")
    )
    current_call = SimpleNamespace(
        route=SimpleNamespace(request_id="current"), read_request_bytes=b"current-intent"
    )
    decoded_nested_calls: list[bytes] = []

    def decode_issue_call(raw: bytes) -> object:
        decoded_nested_calls.append(raw)
        return issue_call

    def decode_current_call(raw: bytes) -> object:
        decoded_nested_calls.append(raw)
        return current_call

    prefix = SimpleNamespace(snapshot_bytes=b"snapshot")
    reader = SimpleNamespace(
        authority_gate=None,
        historical_prefix=lambda _: prefix,
    )

    def exchange(payload: bytes, request_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            sent=SimpleNamespace(
                canonical_payload=payload,
                request_id=request_id,
                caller=SimpleNamespace(owner_id="broker"),
                callee=SimpleNamespace(owner_id="deployment_trust"),
                held_resources=(),
                budget=SimpleNamespace(
                    remaining_calls=1,
                    remaining_depth=1,
                    policy_version=1,
                    absolute_deadline_ns=2,
                ),
            ),
            returned=SimpleNamespace(
                request_id=request_id,
                responder=SimpleNamespace(owner_id="deployment_trust"),
                canonical_payload=b"owner-response",
            ),
            sent_at_ns=1,
            returned_at_ns=1,
        )

    issuance = SimpleNamespace(
        scope_issue_exchange=exchange(issue_wire.canonical_bytes(), "issue"),
        scope_current_exchange=exchange(current_wire.canonical_bytes(), "current"),
    )
    monkeypatch.setattr(
        historical_sources.H1OwnerCandidateCallV1,  # type: ignore[attr-defined]
        "model_validate_json",
        decode_issue_call,
    )
    monkeypatch.setattr(
        historical_sources.H1OwnerCurrentCallV1,  # type: ignore[attr-defined]
        "model_validate_json",
        decode_current_call,
    )
    monkeypatch.setattr(
        historical_sources.H1OwnerCandidateV1,  # type: ignore[attr-defined]
        "model_validate_json",
        lambda _: SimpleNamespace(route=substituted_route),
    )
    monkeypatch.setattr(
        historical_sources.H1OwnerCurrentCandidateV1,  # type: ignore[attr-defined]
        "model_validate_json",
        lambda _: SimpleNamespace(route=current_call.route),
    )
    monkeypatch.setattr(historical_sources, "PublicPortSuccess", object)
    monkeypatch.setattr(
        historical_sources.IssueHermeticOutputScopeV1,  # type: ignore[attr-defined]
        "model_validate_json",
        lambda _: SimpleNamespace(expected_trust_observation=object()),
    )
    monkeypatch.setattr(
        historical_sources.ReadCurrentHermeticExecutionScopeV1,  # type: ignore[attr-defined]
        "model_validate_json",
        lambda _: SimpleNamespace(expected_trust_observation=object()),
    )

    with pytest.raises(ValueError, match="prefix differs"):
        _verify_historical_scope(
            cast(H1CompletionIssuanceV1, issuance),
            trust_reader=reader,
            gate=None,
        )
    assert decoded_nested_calls == [b"issue-call", b"current-call"]
