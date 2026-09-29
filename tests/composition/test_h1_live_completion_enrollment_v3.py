"""Producer/schema binding at the private H1 recovery enrollment boundary."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any, Literal

import pytest

from chiplog.composition.h1_completion_issuance import (
    V2_SCHEMA,
    V3_SCHEMA,
    H1CompletionIssuanceV2,
    H1CompletionIssuanceV3,
)
from chiplog.composition.h1_live_completion_enrollment import (
    H1LiveCompletionEnrollmentUnavailable,
    _H1LiveCompletionEnrollment,
)
from chiplog.composition.h1_postseal_recovery_coordinator import (
    _H1CompleteChainPreflight,
    _H1PostSealRecoveryCoordinator,
)
from chiplog.composition.h1_recovery_execution_fence import _H1RecoveryExecutionLease
from chiplog.composition.h1_recovery_stage_source import H1RecoveryStageSource
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2
from chiplog.platform.authority_gate import AuthorityGate


def _issuance(producer: Literal["LOCAL_V2", "SCOPED_V3"]) -> object:
    if producer == "LOCAL_V2":
        return H1CompletionIssuanceV2.model_construct()
    return H1CompletionIssuanceV3.model_construct()


def _batch(issuance: object, *, schema: str) -> CompleteDeliveryBatchV2:
    raw = issuance.canonical_bytes()  # type: ignore[union-attr]
    return CompleteDeliveryBatchV2.model_construct(
        authentication=SimpleNamespace(
            applicability_schema=schema,
            applicability_bytes=raw,
            applicability_fingerprint=hashlib.sha256(raw).hexdigest(),
        )
    )


@pytest.mark.parametrize(
    ("producer", "issuance_producer", "schema", "accepted"),
    (
        ("LOCAL_V2", "LOCAL_V2", V2_SCHEMA, True),
        ("SCOPED_V3", "SCOPED_V3", V3_SCHEMA, True),
        ("LOCAL_V2", "SCOPED_V3", V3_SCHEMA, False),
        ("SCOPED_V3", "LOCAL_V2", V2_SCHEMA, False),
        ("LOCAL_V2", "LOCAL_V2", V3_SCHEMA, False),
        ("SCOPED_V3", "SCOPED_V3", V2_SCHEMA, False),
    ),
)
def test_recovery_enrollment_binds_root_producer_to_exact_issuance_schema(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    producer: Literal["LOCAL_V2", "SCOPED_V3"],
    issuance_producer: Literal["LOCAL_V2", "SCOPED_V3"],
    schema: str,
    accepted: bool,
) -> None:
    """A V2/V3 applicability wire cannot cross a differently selected ROOT."""
    enrollment = object.__new__(_H1LiveCompletionEnrollment)
    enrollment._gate = AuthorityGate.for_database(tmp_path / "authority.sqlite3")
    enrollment._issuances = {}
    enrollment._authority = object()
    record = SimpleNamespace(preflight=object())
    issuance = _issuance(issuance_producer)
    batch = _batch(issuance, schema=schema)

    monkeypatch.setattr(enrollment, "_require_recovery_record", lambda _: record)
    monkeypatch.setattr(enrollment, "_require_recovery_record_current", lambda _: None)
    monkeypatch.setattr(enrollment, "_selected_recovery_producer", lambda _: producer)

    if accepted:
        source = enrollment._issue_recovery_issuance(
            session=object(), issuance=issuance, batch=batch, readplan_capture=object()
        )
        retained = enrollment._issuances[id(source)]
        assert retained.issuance is issuance
        assert retained.batch is batch
    else:
        with pytest.raises(
            H1LiveCompletionEnrollmentUnavailable,
            match="producer, schema, or payload differs",
        ):
            enrollment._issue_recovery_issuance(
                session=object(), issuance=issuance, batch=batch, readplan_capture=object()
            )


def test_recovery_enrollment_rejects_a_changed_applicability_fingerprint(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    enrollment = object.__new__(_H1LiveCompletionEnrollment)
    enrollment._gate = AuthorityGate.for_database(tmp_path / "authority.sqlite3")
    enrollment._issuances = {}
    enrollment._authority = object()
    record = SimpleNamespace(preflight=object())
    issuance = _issuance("SCOPED_V3")
    batch = _batch(issuance, schema=V3_SCHEMA)
    batch.authentication.applicability_fingerprint = "0" * 64

    monkeypatch.setattr(enrollment, "_require_recovery_record", lambda _: record)
    monkeypatch.setattr(enrollment, "_require_recovery_record_current", lambda _: None)
    monkeypatch.setattr(enrollment, "_selected_recovery_producer", lambda _: "SCOPED_V3")

    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="producer, schema, or payload"):
        enrollment._issue_recovery_issuance(
            session=object(), issuance=issuance, batch=batch, readplan_capture=object()
        )


def test_selected_recovery_producer_requires_the_exact_held_root_context_and_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The V3 choice is read from the retained coordinator preflight, not a flag."""
    runtime = SimpleNamespace()
    coordinator = object.__new__(_H1PostSealRecoveryCoordinator)
    source = object.__new__(H1RecoveryStageSource)
    lease = object.__new__(_H1RecoveryExecutionLease)
    context = object()
    root = object()
    state = SimpleNamespace(root=root, selected_producer="SCOPED_V3")
    preflight = _H1CompleteChainPreflight(coordinator, source, lease, state, context)
    record = SimpleNamespace(
        preflight=preflight,
        source=source,
        context=context,
        lease=lease,
    )
    enrollment = object.__new__(_H1LiveCompletionEnrollment)
    enrollment._runtime = runtime
    runtime._h1_postseal_recovery_coordinator = coordinator

    monkeypatch.setattr(
        H1RecoveryStageSource,
        "_context_state",
        lambda self, candidate: (
            SimpleNamespace(root=root)
            if self is source and candidate is context
            else (_ for _ in ()).throw(ValueError("foreign recovery context"))
        ),
    )
    monkeypatch.setattr(
        H1RecoveryStageSource,
        "_require_current",
        lambda self, candidate: (
            None
            if self is source and candidate is context
            else (_ for _ in ()).throw(ValueError("stale recovery context"))
        ),
    )
    monkeypatch.setattr(_H1RecoveryExecutionLease, "require_owned", lambda self: None)

    assert enrollment._selected_recovery_producer(record) == "SCOPED_V3"

    record.context = object()
    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="producer preflight differs"):
        enrollment._selected_recovery_producer(record)

    record.context = context
    state.root = object()
    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="producer ROOT is not current"):
        enrollment._selected_recovery_producer(record)
