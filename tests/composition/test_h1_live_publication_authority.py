"""Fail-closed admission boundary for the live H1 writer authority.

These are deliberately only the pre-B negative witnesses.  A schema-valid
``CompleteDeliveryBatchV2`` is not a substitute for the nonserializable
capability issued by the live preparation session.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

import chiplog.composition.h1_completion_issuance as historical_issuance
import chiplog.composition.h1_historical_selected_sources as historical_sources
from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority
from chiplog.composition.r14_execution_completion_records import (
    RetainedCompleteAcceptanceExchangeV1,
    complete_acceptance_command,
)
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    CompleteDeliveryBatchV2,
    ExactReplayQuery,
    InvocationProofRef,
    JournalSelectedPublication,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    PublicationRejected,
    WorkerAuthentication,
)
from chiplog.platform._sqlite import (
    PhysicalPublicationCommand,
    PhysicalRecord,
    StoreAdmissionError,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    OwnerDecisionJournal,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
    source_commands,
)
from tests.support.completion_assembly import accepted_completion_fixture


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _unissued_batch() -> CompleteDeliveryBatchV2:
    def command(
        owner: Literal[
            "agent_loop",
            "effects",
            "planning",
            "broker_ingress",
            "broker_dispatch",
            "conversation",
        ],
    ) -> OwnerCommandBytes:
        raw = f"{owner}-command".encode()
        return OwnerCommandBytes(
            owner=owner,
            schema_id="test.command.v1",
            canonical_bytes=raw,
            fingerprint=_digest(raw),
        )

    record = b"terminal-record"
    return CompleteDeliveryBatchV2(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id="h1-command",
            command_fingerprint=_digest(b"h1-command"),
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        authentication=WorkerAuthentication(
            invocation=InvocationProofRef(
                issuance_id="unissued",
                issuance_fingerprint=_digest(b"unissued"),
                broker_epoch="epoch",
                broker_session="session",
                runtime_generation="generation",
                operation_subject="h1-command",
            ),
            applicability_schema="chiplog.composition.h1-completion-issuance.v1",
            applicability_bytes=b"forged",
            applicability_fingerprint=_digest(b"forged"),
        ),
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=0,
            expected_materialization_commitment="0" * 64,
            registry_head="registry",
            registry_fingerprint="0" * 64,
            ordered_heads=(),
            complete_manifest_fingerprint="0" * 64,
        ),
        loop_command=command("agent_loop"),
        conversation_command=command("conversation"),
        terminal_work_command=command("agent_loop"),
        prepared_effects_commands=(command("effects"),),
        complete_records=(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind="TERMINAL",
                record_id="terminal",
                schema_id="test.terminal.v1",
                canonical_bytes=record,
                fingerprint=_digest(record),
            ),
        ),
        complete_batch_fingerprint="0" * 64,
    )


class _UnexpectedAppender:
    async def submit(self, _: object) -> object:  # pragma: no cover - must not run
        raise AssertionError("unissued H1 batch reached physical writer admission")


@dataclass
class _Journal:
    selected: list[object] = field(default_factory=list)

    def lookup(self, _: str, __: str) -> None:
        return None

    def select(self, prepared: object, _: str) -> object:  # pragma: no cover - must not run
        self.selected.append(prepared)
        raise AssertionError("unissued H1 batch reached owner decision selection")

    def materialized(self, _: object) -> None:  # pragma: no cover - must not run
        raise AssertionError("unissued H1 batch was marked materialized")


@pytest.mark.asyncio
async def test_unissued_h1_batch_is_rejected_before_historical_validation_or_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DTO forged from the public wire cannot bootstrap first selection."""
    monkeypatch.setattr(
        historical_issuance,
        "validate_h1_completion_issuance",
        lambda *_: pytest.fail("live authority used historical issuance validation"),
    )
    monkeypatch.setattr(
        historical_sources,
        "bind_selected_h1_completion",
        lambda *_: pytest.fail("live authority bound selected H1 before selection"),
    )
    journal = _Journal()
    coordinator = BrokerPublicationCoordinator(
        _UnexpectedAppender(),  # type: ignore[arg-type]
        H1LivePublicationAuthority(),
        cast(OwnerDecisionJournal, journal),
    )

    result = await coordinator.commit(_unissued_batch())

    assert isinstance(result, PublicationRejected)
    assert result.kind == "DENIED"
    assert journal.selected == []


def test_non_h1_prepared_object_is_denied_without_a_current_check() -> None:
    authority = H1LivePublicationAuthority()

    assert authority.check_prepared(object()) == "DENIED"  # type: ignore[arg-type]


def test_unissued_h1_physical_resolver_fails_closed() -> None:
    authority = H1LivePublicationAuthority()

    with pytest.raises(StoreAdmissionError, match="capability is not mounted"):
        authority.verify(object(), "ABSENT")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_selected_v2_closure_failure_precedes_recovery_decode_and_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recovery cannot consume corrupt V2 selection evidence before closure readback."""
    from chiplog.composition.h1_completion_issuance import V2_SCHEMA

    batch = CompleteDeliveryBatchV2.model_construct(
        authentication=SimpleNamespace(applicability_schema=V2_SCHEMA)
    )
    decision = SelectedOwnerDecision(
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
        tenant_commit_sequence=1,
    )
    gate = AuthorityGate(tmp_path / "recovery-closure-order.sqlite3")
    authority = H1LivePublicationAuthority()
    authority._runtime = cast(
        Any,
        SimpleNamespace(
            _authority_gate=lambda: gate,
            _check_database_identity=lambda: None,
            _owner_decisions=lambda: SimpleNamespace(
                snapshot=lambda: SimpleNamespace(decisions=(decision,))
            ),
        ),
    )
    monkeypatch.setattr(
        historical_sources,
        "_verify_selected_v2_delivery_closure",
        lambda *_: (_ for _ in ()).throw(ValueError("closure is corrupt")),
    )
    monkeypatch.setattr(
        historical_issuance,
        "decode_h1_completion_issuance",
        lambda *_: pytest.fail("issuance decode reached before selected closure"),
    )
    monkeypatch.setattr(
        authority,
        "_retained_h0",
        lambda *_: pytest.fail("retained H0 read reached before selected closure"),
    )

    with pytest.raises(RuntimeError, match="selected evidence is malformed"):
        await authority._recover_finalization_held(
            identity=cast(Any, object()),
            original_fingerprint="fingerprint",
            lease=SimpleNamespace(require_owned=lambda: None),
        )


def _verify_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[
    H1LivePublicationAuthority,
    PhysicalPublicationCommand,
    PreparedOwnerPublication,
    SimpleNamespace,
]:
    """Build one guarded physical command and its callback-free projection."""
    authority = H1LivePublicationAuthority()
    batch = _unissued_batch()
    gate = AuthorityGate(tmp_path / "verify-authority.sqlite3")
    prepared = PreparedOwnerPublication(
        request=batch,
        issuance_id="issued",
        fence_generation="r6",
        fence_frontier=0,
        predecessor_commitment=batch.expected.expected_materialization_commitment,
    )
    journal = SimpleNamespace(selected=None)
    runtime = SimpleNamespace(
        _authority_gate=lambda: gate,
        _owner_decisions=lambda: SimpleNamespace(
            lookup=lambda _tenant, _command: journal.selected
        ),
    )
    authority._runtime = cast(Any, runtime)
    authority._prepared[batch.identity.command_id] = (prepared, SimpleNamespace(batch=batch))
    command = PhysicalPublicationCommand(
        tenant_id=batch.identity.tenant_id,
        operation_kind=batch.operation,
        idempotency_key=batch.identity.command_id,
        request_fingerprint=batch.identity.command_fingerprint,
        expected_head=batch.expected.tenant_frontier,
        fence_generation=prepared.fence_generation,
        expected_fence_frontier=prepared.fence_frontier,
        minimum_fence_frontier=prepared.fence_frontier,
        records=tuple(
            PhysicalRecord(
                record.record_id,
                record.owner,
                record.schema_id,
                record.canonical_bytes,
                record.fingerprint,
            )
            for record in batch.complete_records
        ),
        admission_guard=lambda: None,
        decision_guard=lambda _commitment: None,
    )
    monkeypatch.setattr(historical_issuance, "h1_completion_exchange", lambda _batch: _batch)
    from chiplog.composition import r14_execution_completion_records

    monkeypatch.setattr(
        r14_execution_completion_records,
        "complete_acceptance_command",
        lambda _exchange: replace(command, admission_guard=None, decision_guard=None),
    )
    return authority, command, prepared, journal


def test_h1_verify_fresh_modes_check_only_absent_and_returns_precommit_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, command, prepared, journal = _verify_fixture(tmp_path, monkeypatch)
    checks: list[str] = []
    monkeypatch.setattr(
        authority, "check_prepared", lambda value: checks.append("prepared") or None
    )
    monkeypatch.setattr(
        authority,
        "check_selected_predecessor",
        lambda _decision: pytest.fail("fresh verification used selected predecessor"),
    )

    assert authority.verify(command, "ABSENT").selected_identity is None
    assert checks == ["prepared"]
    assert authority.verify(command, "PRESELECT").selected_identity is None
    assert checks == ["prepared"]

    journal.selected = SimpleNamespace(
        prepared=prepared,
        decision_id="decision",
        decision_fingerprint="a" * 64,
        resulting_commitment="b" * 64,
        tenant_commit_sequence=1,
    )
    monkeypatch.setattr(authority, "materialization_state", lambda _decision: "COMPLETE")
    verified = authority.verify(command, "PRECOMMIT")

    assert checks == ["prepared"]
    assert (
        verified.selected_identity,
        verified.selected_fingerprint,
        verified.expected_resulting,
        verified.expected_commit_sequence,
    ) == ("decision", "a" * 64, "b" * 64, 1)


def test_h1_verify_selected_recovery_checks_predecessor_only_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, command, prepared, journal = _verify_fixture(tmp_path, monkeypatch)
    authority._prepared.clear()
    journal.selected = SimpleNamespace(
        prepared=prepared,
        decision_id="decision",
        decision_fingerprint="a" * 64,
        resulting_commitment="b" * 64,
        tenant_commit_sequence=1,
    )
    checks: list[str] = []
    monkeypatch.setattr(
        authority, "check_prepared", lambda _prepared: pytest.fail("recovery used fresh check")
    )
    monkeypatch.setattr(
        authority,
        "check_selected_predecessor",
        lambda _decision: checks.append("selected") or True,
    )
    monkeypatch.setattr(authority, "materialization_state", lambda _decision: "ABSENT")

    assert authority.verify(command, "ABSENT").selected_identity == "decision"
    assert checks == ["selected"]
    assert authority.verify(command, "PRESELECT").selected_identity == "decision"
    assert authority.verify(command, "PRECOMMIT").selected_identity == "decision"
    monkeypatch.setattr(authority, "materialization_state", lambda _decision: "COMPLETE")
    assert (
        authority.verify(
            replace(command, admission_guard=None, decision_guard=None), "REPLAY"
        ).selected_identity
        == "decision"
    )
    assert checks == ["selected"]


@pytest.mark.parametrize(
    "changed",
    (
        {"admission_guard": None},
        {"decision_guard": None},
        {"fault": "before_commit"},
        {"authority_checkpoint_guard": lambda _commitment, _snapshot: None},
    ),
)
def test_h1_verify_rejects_unsafe_new_write_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: dict[str, object]
) -> None:
    authority, command, _prepared, _journal = _verify_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(authority, "check_prepared", lambda _prepared: None)

    with pytest.raises(StoreAdmissionError):
        authority.verify(replace(command, **changed), "ABSENT")


@pytest.mark.asyncio
async def test_h1_verify_projects_real_completion_command_without_callback_equality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Projection admission retains real batch-to-command reconstruction."""
    fixture = await accepted_completion_fixture("v3", "empty")
    batch = fixture.batch
    exchange = RetainedCompleteAcceptanceExchangeV1(
        assembly=fixture.assembly,
        batch=batch,
        expected_head=batch.expected.tenant_frontier,
        predecessor_commitment=batch.expected.expected_materialization_commitment,
    )
    callback_free = complete_acceptance_command(exchange)
    authority = H1LivePublicationAuthority()
    gate = AuthorityGate(tmp_path / "projection-authority.sqlite3")
    prepared = PreparedOwnerPublication(
        request=batch,
        issuance_id="issued",
        fence_generation=callback_free.fence_generation,
        fence_frontier=callback_free.expected_fence_frontier,
        predecessor_commitment=batch.expected.expected_materialization_commitment,
    )
    runtime = SimpleNamespace(
        _authority_gate=lambda: gate,
        _owner_decisions=lambda: SimpleNamespace(lookup=lambda _tenant, _command: None),
    )
    authority._runtime = cast(Any, runtime)
    authority._prepared[batch.identity.command_id] = (prepared, SimpleNamespace(batch=batch))
    monkeypatch.setattr(
        historical_issuance,
        "h1_completion_exchange",
        lambda candidate: exchange if candidate is batch else pytest.fail("foreign batch"),
    )
    monkeypatch.setattr(authority, "check_prepared", lambda value: None)
    guarded = replace(
        callback_free,
        admission_guard=lambda: None,
        decision_guard=lambda _commitment: None,
    )

    assert authority.verify(guarded, "ABSENT").projection.records == guarded.records
    with pytest.raises(StoreAdmissionError):
        authority.verify(replace(guarded, expected_head=1), "ABSENT")
    changed_record = replace(
        guarded.records[0],
        canonical_bytes=b"changed",
        fingerprint=_digest(b"changed"),
    )
    with pytest.raises(StoreAdmissionError):
        authority.verify(replace(guarded, records=(changed_record, *guarded.records[1:])), "ABSENT")


def _active_replay_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[
    H1LivePublicationAuthority, ExactReplayQuery
]:
    """Build only the private active-commit join required by replay authentication."""
    from chiplog.composition.h1_live_completion_enrollment import (
        _H1LiveCompletionEnrollment,
        _H1LiveCompletionIssuance,
        _IssuanceRecord,
        _RecoveryEnrollmentRecord,
    )

    authority = H1LivePublicationAuthority()
    batch = _unissued_batch()
    gate = AuthorityGate(tmp_path / "authority.sqlite3")

    class _Runtime:
        def _authority_gate(self) -> AuthorityGate:
            return gate

    runtime = _Runtime()
    enrollment = object.__new__(_H1LiveCompletionEnrollment)
    source = object.__new__(_H1LiveCompletionIssuance)
    recovery = _RecoveryEnrollmentRecord(
        session=cast(Any, object()),
        source=object(),
        context=object(),
        lease=object(),
        preflight=object(),
    )
    record = _IssuanceRecord(
        enrollment=recovery,
        source=source,
        authority=authority,
        issuance=object(),
        batch=batch,
        readplan_capture=object(),
        state="CONSUMED",
    )
    private_runtime = cast(Any, runtime)
    private_enrollment = cast(Any, enrollment)
    authority._runtime = cast(Any, runtime)
    private_runtime._h1_live_publication_authority = authority
    private_runtime._h1_live_completion_enrollment = enrollment
    private_enrollment._runtime = runtime
    private_enrollment._authority = authority
    private_enrollment._gate = gate
    private_enrollment._issuances = {id(source): record}
    monkeypatch.setattr(enrollment, "_require_recovery_record_current", lambda _record: None)
    authority._prepared[batch.identity.command_id] = (None, record)
    return authority, ExactReplayQuery(
        identity=batch.identity,
        operation=batch.operation,
        current_invocation=batch.authentication.invocation,
        original_commands=source_commands(batch),
    )


def test_h1_replay_authentication_requires_active_exact_issued_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, query = _active_replay_scope(tmp_path, monkeypatch)

    assert authority.authenticate_replay(query) is None
    for changed in (
        query.model_copy(
            update={
                "current_invocation": query.current_invocation.model_copy(
                    update={"issuance_id": "foreign"}
                )
            }
        ),
        query.model_copy(
            update={"identity": query.identity.model_copy(update={"command_id": "foreign"})}
        ),
        query.model_copy(update={"operation": "effects.accept_call"}),
        query.model_copy(
            update={
                "original_commands": (
                    query.original_commands[0].model_copy(
                        update={"canonical_bytes": b"foreign"}
                    ),
                )
            }
        ),
    ):
        result = authority.authenticate_replay(changed)
        assert isinstance(result, PublicationRejected)
        assert result.kind == "DENIED"

    authority._prepared.clear()
    copied_outside_scope = query.model_copy()
    result = authority.authenticate_replay(copied_outside_scope)
    assert isinstance(result, PublicationRejected)
    assert result.kind == "DENIED"


def test_h1_replay_authentication_rejects_stale_or_revoked_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, query = _active_replay_scope(tmp_path, monkeypatch)
    enrollment = cast(Any, authority._runtime)._h1_live_completion_enrollment
    monkeypatch.setattr(
        enrollment,
        "_require_recovery_record_current",
        lambda _record: (_ for _ in ()).throw(RuntimeError("stale")),
    )

    stale = authority.authenticate_replay(query)
    assert isinstance(stale, PublicationRejected)
    assert stale.kind == "DENIED"

    authority._revoke_all()
    revoked = authority.authenticate_replay(query)
    assert isinstance(revoked, PublicationRejected)
    assert revoked.kind == "DENIED"


def test_h1_active_replay_scope_keeps_broker_exact_replay_and_conflict_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, query = _active_replay_scope(tmp_path, monkeypatch)
    batch = cast(Any, authority._prepared[query.identity.command_id][1]).batch
    prepared = SimpleNamespace(request=batch, predecessor_commitment="0" * 64)
    decision = SimpleNamespace(
        prepared=prepared,
        decision_id="decision",
        decision_head="head",
        decision_fingerprint="a" * 64,
        resulting_commitment="b" * 64,
        tenant_commit_sequence=1,
    )

    class _SelectedJournal:
        def lookup(self, _tenant: str, _command: str) -> object:
            return decision

        def materialized(self, observed: object) -> None:
            assert observed is decision

    coordinator = BrokerPublicationCoordinator(
        _UnexpectedAppender(),  # type: ignore[arg-type]
        authority,
        cast(OwnerDecisionJournal, _SelectedJournal()),
    )
    monkeypatch.setattr(authority, "materialization_state", lambda _decision: "COMPLETE")

    replay = coordinator.lookup_exact(query)
    assert type(replay) is JournalSelectedPublication
    assert replay.kind == "EXACT_REPLAY"

    prepared.request = batch.model_copy(
        update={
            "loop_command": batch.loop_command.model_copy(
                update={"canonical_bytes": b"changed-command"}
            )
        }
    )
    conflict = coordinator.lookup_exact(query)
    assert type(conflict) is PublicationRejected
    assert conflict.kind == "CONFLICT"

    authority._prepared.clear()
    assert type(coordinator.lookup_exact(query)) is PublicationRejected


def _retained_prepare_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[H1LivePublicationAuthority, CompleteDeliveryBatchV2, object]:
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV2

    authority, query = _active_replay_scope(tmp_path, monkeypatch)
    record = cast(Any, authority._prepared[query.identity.command_id][1])
    issued_bytes = b"issued-v2-canonical-bytes"
    issuance = object.__new__(H1CompletionIssuanceV2)
    object.__setattr__(issuance, "_test_canonical_bytes", issued_bytes)
    authentication = record.batch.authentication.model_copy(
        update={
            "applicability_bytes": issued_bytes,
            "applicability_fingerprint": _digest(issued_bytes),
        }
    )
    batch = record.batch.model_copy(update={"authentication": authentication})
    record.batch = batch
    record.issuance = issuance
    authority._prepared[batch.identity.command_id] = (None, record)
    monkeypatch.setattr(
        H1CompletionIssuanceV2,
        "canonical_bytes",
        lambda value: cast(bytes, value._test_canonical_bytes),
    )
    monkeypatch.setattr(
        historical_issuance, "decode_h1_completion_issuance", lambda _batch: issuance
    )
    return authority, batch, record


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason="the local fixture reconstructs only structural V2 bytes, not a decodable V2 issuance",
)
async def test_h1_prepare_accepts_exact_private_batch_after_v2_decoder_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, batch, _record = _retained_prepare_candidate(tmp_path, monkeypatch)

    prepared = await authority.prepare(batch)

    assert isinstance(prepared, PreparedOwnerPublication)
    assert authority._prepared[batch.identity.command_id][0] is prepared


@pytest.mark.asyncio
async def test_h1_prepare_rejects_a_foreign_delivery_evidence_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A structural V2 reconstruction cannot substitute for a mounted root issuer."""
    from chiplog.composition.h1_live_readplan_source import H1LiveReadPlanSource

    authority, batch, _record = _retained_prepare_candidate(tmp_path, monkeypatch)
    runtime = cast(Any, authority._runtime)
    runtime._h1_live_readplan_source = object.__new__(H1LiveReadPlanSource)
    monkeypatch.setattr(
        H1LiveReadPlanSource,
        "_admit_issued_manifest",
        lambda *_args, **_kw: batch.expected,
    )
    runtime._h1_delivery_evidence_journal = object()

    prepared = await authority.prepare(batch)

    assert isinstance(prepared, PublicationRejected)
    assert prepared.kind == "DENIED"
    assert authority._prepared[batch.identity.command_id][0] is None


@pytest.mark.asyncio
async def test_h1_prepare_rejects_a_stale_issued_readplan_before_evidence_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exact source type reporting a changed manifest denies before root evidence."""
    from chiplog.composition.h1_live_readplan_source import H1LiveReadPlanSource

    authority, batch, _record = _retained_prepare_candidate(tmp_path, monkeypatch)
    runtime = cast(Any, authority._runtime)
    runtime._h1_live_readplan_source = object.__new__(H1LiveReadPlanSource)
    stale = batch.expected.model_copy(
        update={"tenant_frontier": batch.expected.tenant_frontier + 1}
    )
    monkeypatch.setattr(
        H1LiveReadPlanSource,
        "_admit_issued_manifest",
        lambda *_args, **_kw: stale,
    )

    prepared = await authority.prepare(batch)

    assert isinstance(prepared, PublicationRejected)
    assert prepared.kind == "DENIED"
    assert authority._prepared[batch.identity.command_id][0] is None


@pytest.mark.asyncio
async def test_h1_prepare_rejects_copied_or_noncurrent_private_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority, batch, record = _retained_prepare_candidate(tmp_path, monkeypatch)
    private_record = cast(Any, record)

    copied = await authority.prepare(batch.model_copy())
    assert isinstance(copied, PublicationRejected)
    assert copied.kind == "DENIED"
    assert authority._prepared[batch.identity.command_id][0] is None

    private_record.state = "ISSUED"
    nonconsumed = await authority.prepare(batch)
    assert isinstance(nonconsumed, PublicationRejected)
    assert nonconsumed.kind == "DENIED"
    assert authority._prepared[batch.identity.command_id][0] is None


@pytest.mark.asyncio
async def test_h1_prepare_rejects_different_v2_bytes_or_applicability_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV2

    authority, batch, _record = _retained_prepare_candidate(tmp_path, monkeypatch)
    different = object.__new__(H1CompletionIssuanceV2)
    object.__setattr__(different, "_test_canonical_bytes", b"different-v2-canonical-bytes")
    monkeypatch.setattr(
        historical_issuance, "decode_h1_completion_issuance", lambda _batch: different
    )

    different_bytes = await authority.prepare(batch)
    assert isinstance(different_bytes, PublicationRejected)
    assert different_bytes.kind == "DENIED"
    assert authority._prepared[batch.identity.command_id][0] is None

    authority, batch, record = _retained_prepare_candidate(tmp_path, monkeypatch)
    bad_batch = batch.model_copy(
        update={
            "authentication": batch.authentication.model_copy(
                update={"applicability_fingerprint": "0" * 64}
            )
        }
    )
    private_record = cast(Any, record)
    private_record.batch = bad_batch
    authority._prepared[bad_batch.identity.command_id] = (None, private_record)

    bad_digest = await authority.prepare(bad_batch)
    assert isinstance(bad_digest, PublicationRejected)
    assert bad_digest.kind == "DENIED"
    assert authority._prepared[bad_batch.identity.command_id][0] is None
