"""Selected closed-outcome acquisition avoids a duplicate R14 replay."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
)
from chiplog.composition import r14_native_selected_accounting_source as accounting_module
from chiplog.composition.r14_native_selected_accounting_source import (
    R14NativeSelectedAccountingSource,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chiplog.adapters.driven.effects_queries import StoredEffectRow
    from chiplog.capabilities.agent_loop.contracts import RunRecord
    from chiplog.capabilities.effects.dispatch_authority_contracts import (
        Identity,
        SelectedEffectHistoryMember,
    )
    from chiplog.capabilities.effects.dispatch_outcome_contracts import (
        DispatchOutcomePreparationV2,
        DispatchOutcomeRecordV2,
    )
    from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2


def test_open_cut_validation_reuses_authenticated_history_without_skipping_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unit doubles probe forwarding, not authenticated producer conformance."""
    from unittest.mock import create_autospec

    from chiplog.composition import r14_accepted_open_accounting_publication as publication
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.r14_loop_history import ExecutionRecoveryHistory
    from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
    from chiplog.composition.r14_native_recovery_source_subset import R14NativeRecoverySourceSubset
    from chiplog.composition.r14_native_selected_accounting_source import (
        R14NativeAcceptedOpenOutcome,
    )

    class ReachedAccounting(BaseException):
        pass

    history: ExecutionRecoveryHistory = create_autospec(ExecutionRecoveryHistory, instance=True)
    opened: R14NativeAcceptedOpenOutcome = create_autospec(
        R14NativeAcceptedOpenOutcome, instance=True
    )
    subset: R14NativeRecoverySourceSubset = create_autospec(
        R14NativeRecoverySourceSubset, instance=True
    )
    monkeypatch.setattr(subset, "accepted_closed_outcome", None)
    monkeypatch.setattr(subset, "accepted_open_outcome", opened)
    monkeypatch.setattr(subset, "authenticated_execution_history", history)
    events: list[str] = []

    class Gate:
        held = False

        def require_held(self) -> None:
            assert self.held
            events.append("held")

        @contextmanager
        def hold(self) -> Iterator[None]:
            was_held = self.held
            self.held = True
            try:
                yield
            finally:
                self.held = was_held

    gate = Gate()
    runtime: CommonCliExecutionRuntime = create_autospec(CommonCliExecutionRuntime, instance=True)
    monkeypatch.setattr(runtime, "_authority_gate", lambda: gate)
    source = object.__new__(NativeRecoveryCutSource)

    def frontier(
        actual_runtime: CommonCliExecutionRuntime,
        *,
        _authenticated_history: ExecutionRecoveryHistory | None = None,
    ) -> None:
        assert actual_runtime is runtime
        assert gate.held
        assert _authenticated_history is history
        events.append("frontier")
        raise ReachedAccounting

    monkeypatch.setattr(publication, "read_selected_accepted_open_frontier", frontier)
    with gate.hold(), pytest.raises(ReachedAccounting):
        source._selected_accepted_recovery_inputs(runtime, subset)
    assert events == ["held", "frontier"]


def test_closed_outcome_collects_once_under_gate_after_capturing_baseline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unit doubles prove one collection reaches the R16 read under the gate."""
    from sqlite3 import Connection
    from typing import NoReturn
    from unittest.mock import create_autospec

    from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
        CallLifecycleObservation,
        InitializedCallRecord,
    )
    from chiplog.capabilities.agent_loop.execution_preparation_bindings_contracts import (
        NativeExecutionPreparationBindingsV2,
    )
    from chiplog.composition import r16_effects
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.r14_native_selected_accounting_source import (
        R14NativeOneCallAcceptedFacts,
        R14NativeSelectedAccountingSourceFacts,
        R14NativeSelectedResponseSealFacts,
    )
    from chiplog.composition.r14_original_prepare_source import R14OriginalNativePrepareProjection
    from chiplog.composition.r14_runtime import AnchoredOwnerDecisionJournal
    from chiplog.platform.authority_reads import AuthorityCommitmentJournal
    from chiplog.platform.owner_decision_journal import (
        IndependentOwnerDecisionJournal,
        OwnerJournalSnapshot,
    )

    events: list[str] = []

    class Gate:
        held = False

        @contextmanager
        def hold(self) -> Iterator[None]:
            assert not self.held
            self.held = True
            try:
                yield
            finally:
                self.held = False

    gate = Gate()
    journal = OwnerJournalSnapshot("tenant", None, (), frozenset())
    owner_decisions: AnchoredOwnerDecisionJournal = create_autospec(
        AnchoredOwnerDecisionJournal, instance=True
    )
    monkeypatch.setattr(owner_decisions, "snapshot", lambda: journal)
    runtime = object.__new__(CommonCliExecutionRuntime)
    runtime._tenant_id = "tenant"
    runtime._database = tmp_path / "runtime.sqlite"
    runtime._database_identity = ("database", 1, 2)
    runtime._commitment_journal = create_autospec(AuthorityCommitmentJournal, instance=True)

    def load_commitment(tenant: str) -> str:
        assert tenant == "tenant"
        return "commitment"

    def check_identity() -> None:
        events.append("identity")

    monkeypatch.setattr(runtime._commitment_journal, "load", load_commitment)
    monkeypatch.setattr(runtime, "_authority_gate", lambda: gate)
    monkeypatch.setattr(runtime, "_check_database_identity", check_identity)
    monkeypatch.setattr(runtime, "_owner_decisions", lambda: owner_decisions)
    branch = ConsequentialAcceptedCall(
        initialized=Present(head="initialized", fingerprint="1" * 64),
        accepted=Present(head="accepted", fingerprint="2" * 64),
        execution_intent=Present(head="execution", fingerprint="3" * 64),
        external_effect_intent=Present(head="effect", fingerprint="4" * 64),
        complete_acceptance_manifest=(
            Present(head="accepted", fingerprint="2" * 64),
            Present(head="execution", fingerprint="3" * 64),
        ),
    )
    initialized: InitializedCallRecord = create_autospec(InitializedCallRecord, instance=True)
    monkeypatch.setattr(initialized, "original_call_id", "call", raising=False)
    row: CallLifecycleObservation = create_autospec(CallLifecycleObservation, instance=True)
    monkeypatch.setattr(row, "original_call_id", "call", raising=False)
    monkeypatch.setattr(row, "acceptance", branch, raising=False)
    seal: R14NativeSelectedResponseSealFacts = create_autospec(
        R14NativeSelectedResponseSealFacts, instance=True
    )
    monkeypatch.setattr(seal, "initialized_records", (initialized,))
    facts: R14NativeSelectedAccountingSourceFacts = create_autospec(
        R14NativeSelectedAccountingSourceFacts, instance=True
    )
    monkeypatch.setattr(facts, "selected_native_seals", (seal,))
    monkeypatch.setattr(facts, "target_inventory_rows", (row,))
    accepted: R14NativeOneCallAcceptedFacts = create_autospec(
        R14NativeOneCallAcceptedFacts, instance=True
    )
    monkeypatch.setattr(accepted, "source", facts)
    monkeypatch.setattr(accepted, "selected_seal", seal)
    monkeypatch.setattr(accepted, "original_call_id", "call")
    source = object.__new__(R14NativeSelectedAccountingSource)
    source._runtime = runtime
    source._original_prepare = create_autospec(R14OriginalNativePrepareProjection, instance=True)
    source._preparation_bindings = create_autospec(
        NativeExecutionPreparationBindingsV2, instance=True
    )

    def collect_one_accepted(*_args: object, **_kwargs: object) -> R14NativeOneCallAcceptedFacts:
        assert gate.held
        events.append("collect-one")
        return accepted

    def duplicate_collect(
        *_args: object, **_kwargs: object
    ) -> R14NativeSelectedAccountingSourceFacts:
        events.append("duplicate-collect")
        return facts

    monkeypatch.setattr(source, "collect_one_accepted", collect_one_accepted)
    monkeypatch.setattr(source, "collect", duplicate_collect)
    connection_double: Connection = create_autospec(Connection, instance=True)

    @contextmanager
    def connection(database: Path) -> Iterator[Connection]:
        assert database == runtime._database
        yield connection_double

    def capture_baseline(actual: Connection, tenant: str) -> str:
        assert actual is connection_double and tenant == "tenant"
        events.append("baseline")
        return "commitment"

    monkeypatch.setattr(accounting_module, "read_connection", connection)
    monkeypatch.setattr(
        accounting_module, "capture_authority_snapshot_commitment", capture_baseline
    )

    def stop_after_selected_source(
        actual_runtime: CommonCliExecutionRuntime,
        actual_journal: IndependentOwnerDecisionJournal,
        **_kwargs: object,
    ) -> NoReturn:
        assert actual_runtime is runtime and actual_journal is owner_decisions
        assert gate.held
        events.append("r16-held")
        raise RuntimeError("stop after source collection")

    monkeypatch.setattr(
        r16_effects, "_read_materialized_effects_with_history", stop_after_selected_source
    )
    result = source._collect_one_accepted_outcome(current_obligation_state="CLOSED")

    assert result is None
    assert events.index("baseline") < events.index("collect-one")
    assert "duplicate-collect" not in events
    assert events.count("collect-one") == 1
    assert events.count("r16-held") == 1
    assert events.index("collect-one") < events.index("r16-held")


@pytest.mark.parametrize(
    "prefix_kind",
    [
        "native-direct",
        "pending-open",
        "pending-resolver-invocation",
        "pending-original-resolution",
        "rejected-open",
    ],
)
def test_historical_suffix_admits_authenticated_accepted_intents_from_both_run_seals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prefix_kind: str
) -> None:
    """Unit doubles probe forwarding; source authentication is tested elsewhere."""
    from collections.abc import Callable
    from sqlite3 import Connection
    from typing import NoReturn
    from unittest.mock import create_autospec

    from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
        CallLifecycleObservation,
        InitializedCallRecord,
    )
    from chiplog.composition import r14_historical_suffix as suffix
    from chiplog.composition import r14_original_call_resolution_source as resolution
    from chiplog.composition import r16_dispatch_history, r16_effects
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
    from chiplog.composition.r13_planning import R13PlanningRuntime
    from chiplog.composition.r14_native_selected_accounting_source import (
        R14NativeOneCallAcceptedFacts,
        R14NativeSelectedAccountingSourceFacts,
        R14NativeSelectedResponseSealFacts,
    )
    from chiplog.composition.r14_runtime import AnchoredOwnerDecisionJournal
    from chiplog.composition.r16_dispatch_history import VerifiedDispatchHistory
    from chiplog.composition.r16_effects import MaterializedEffectsCut
    from chiplog.platform.authority_reads import AuthorityCommitmentJournal
    from chiplog.platform.owner_decision_journal import (
        IndependentOwnerDecisionJournal,
        OwnerJournalSnapshot,
    )

    class ReachedSemanticVerifier(BaseException):
        pass

    class ReachedPrefixVerdict(BaseException):
        pass

    class Gate:
        @contextmanager
        def hold(self) -> Iterator[None]:
            yield

    gate = Gate()
    journal = OwnerJournalSnapshot("tenant", None, (), frozenset())
    owners: AnchoredOwnerDecisionJournal = create_autospec(
        AnchoredOwnerDecisionJournal, instance=True
    )
    monkeypatch.setattr(owners, "snapshot", lambda: journal)
    runtime = object.__new__(CommonCliExecutionRuntime)
    runtime._tenant_id = "tenant"
    runtime._database = tmp_path / "runtime.sqlite"
    runtime._database_identity = ("database", 1, 2)
    runtime._commitment_journal = create_autospec(AuthorityCommitmentJournal, instance=True)

    def load_commitment(tenant: str) -> str:
        assert tenant == "tenant"
        return "anchored"

    def pending() -> tuple[dict[str, object], ...]:
        if prefix_kind in {"pending-resolver-invocation", "pending-original-resolution"}:
            return (
                {
                    "operation_kind": {
                        "pending-resolver-invocation": "agent_loop.original-resolver-invocation.v1",
                        "pending-original-resolution": "agent_loop.original-call-resolution.v1",
                    }[prefix_kind]
                },
            )
        return ()

    monkeypatch.setattr(runtime._commitment_journal, "load", load_commitment)
    monkeypatch.setattr(runtime, "_authority_gate", lambda: gate)
    monkeypatch.setattr(runtime, "_check_database_identity", lambda: None)
    monkeypatch.setattr(runtime, "_owner_decisions", lambda: owners)
    monkeypatch.setattr(runtime, "_pending", pending)

    def row(call_id: str, intent_id: str) -> CallLifecycleObservation:
        result: CallLifecycleObservation = create_autospec(CallLifecycleObservation, instance=True)
        monkeypatch.setattr(result, "original_call_id", call_id, raising=False)
        monkeypatch.setattr(
            result,
            "acceptance",
            ConsequentialAcceptedCall(
                initialized=Present(head="initialized-" + call_id, fingerprint="1" * 64),
                accepted=Present(head="accepted-" + call_id, fingerprint="2" * 64),
                execution_intent=Present(head="execution-" + call_id, fingerprint="3" * 64),
                external_effect_intent=Present(head=intent_id, fingerprint="4" * 64),
                complete_acceptance_manifest=(
                    Present(head="accepted-" + call_id, fingerprint="2" * 64),
                    Present(head="execution-" + call_id, fingerprint="3" * 64),
                ),
            ),
            raising=False,
        )
        return result

    def seal(call_id: str) -> R14NativeSelectedResponseSealFacts:
        initialized: InitializedCallRecord = create_autospec(InitializedCallRecord, instance=True)
        monkeypatch.setattr(initialized, "original_call_id", call_id, raising=False)
        result: R14NativeSelectedResponseSealFacts = create_autospec(
            R14NativeSelectedResponseSealFacts, instance=True
        )
        monkeypatch.setattr(result, "initialized_records", (initialized,))
        return result

    first, second = row("call-first", "effect-first"), row("call-second", "effect-second")
    first_seal, second_seal = seal("call-first"), seal("call-second")
    facts: R14NativeSelectedAccountingSourceFacts = create_autospec(
        R14NativeSelectedAccountingSourceFacts, instance=True
    )
    monkeypatch.setattr(
        facts, "target_inventory_rows", (first, second, row("foreign-call", "foreign-effect"))
    )
    monkeypatch.setattr(facts, "selected_native_seals", (first_seal, second_seal))
    accepted: R14NativeOneCallAcceptedFacts = create_autospec(
        R14NativeOneCallAcceptedFacts, instance=True
    )
    monkeypatch.setattr(accepted, "original_call_id", "call-first")
    monkeypatch.setattr(accepted, "selected_seal", first_seal)
    monkeypatch.setattr(accepted, "source", facts)
    source = object.__new__(R14NativeSelectedAccountingSource)
    source._runtime = runtime
    connection_double: Connection = create_autospec(Connection, instance=True)

    @contextmanager
    def connection(database: Path) -> Iterator[Connection]:
        assert database == runtime._database
        yield connection_double

    def baseline(actual_connection: Connection, tenant: str) -> str:
        assert actual_connection is connection_double and tenant == "tenant"
        return "actual"

    monkeypatch.setattr(accounting_module, "read_connection", connection)
    monkeypatch.setattr(accounting_module, "capture_authority_snapshot_commitment", baseline)
    monkeypatch.setattr(
        resolution, "authenticate_selected_resolution_prefix_held", lambda *_: False
    )
    monkeypatch.setattr(
        suffix,
        "authenticate_selected_r14_native_terminal_suffix_held",
        lambda *_, **kw: prefix_kind == "native-direct",
    )
    monkeypatch.setattr(
        suffix, "authenticate_selected_open_prefix_held", lambda *_: prefix_kind == "pending-open"
    )
    pending_suffix_calls: list[tuple[str, str]] = []

    def authenticate_pending_suffix(
        actual_runtime: CommonCliExecutionRuntime, actual: str, anchored: str
    ) -> bool:
        assert actual_runtime is runtime
        pending_suffix_calls.append((actual, anchored))
        return prefix_kind in {"pending-resolver-invocation", "pending-original-resolution"}

    monkeypatch.setattr(
        suffix, "authenticate_selected_pending_r14_suffix_held", authenticate_pending_suffix
    )
    cut: MaterializedEffectsCut = create_autospec(MaterializedEffectsCut, instance=True)
    monkeypatch.setattr(cut, "materialization_commitment", "actual")

    def effects_read(
        actual_runtime: R13PlanningRuntime,
        actual_owners: IndependentOwnerDecisionJournal,
        *,
        authenticate_later_suffix: Callable[[R13PlanningRuntime, str, str], bool] | None,
        **kwargs: object,
    ) -> tuple[MaterializedEffectsCut, OwnerJournalSnapshot]:
        assert actual_runtime is runtime and actual_owners is owners
        assert authenticate_later_suffix is not None
        assert authenticate_later_suffix(actual_runtime, "actual", "anchored") is (
            prefix_kind != "rejected-open"
        )
        if prefix_kind != "native-direct":
            raise ReachedPrefixVerdict
        return cut, journal

    monkeypatch.setattr(r16_effects, "_read_materialized_effects_with_history", effects_read)
    verified = VerifiedDispatchHistory((), ())
    monkeypatch.setattr(r16_dispatch_history, "verify_dispatch_history", lambda *_: verified)

    def semantic_verifier(
        actual_runtime: CommonCliExecutionRuntime,
        actual_journal: OwnerJournalSnapshot,
        call_id: str,
        *,
        allowed_intent_ids: frozenset[str],
    ) -> NoReturn:
        assert actual_runtime is runtime and actual_journal is journal
        assert call_id == "call-first"
        assert allowed_intent_ids == frozenset({"effect-first", "effect-second"})
        raise ReachedSemanticVerifier

    monkeypatch.setattr(suffix, "validate_native_terminal_suffix_semantics", semantic_verifier)
    with pytest.raises(
        ReachedSemanticVerifier if prefix_kind == "native-direct" else ReachedPrefixVerdict
    ):
        source._collect_one_accepted_outcome(
            current_obligation_state="CLOSED",
            allow_authenticated_later_suffix=True,
            accepted_facts=accepted,
        )
    assert len(pending_suffix_calls) == int(prefix_kind != "pending-open")


def _outcome_request(
    previous: DispatchRecordV2 | DispatchOutcomeRecordV2,
    send: DispatchRecordV2,
    positive: tuple[Identity, ...] = (),
    negative: tuple[Identity, ...] = (),
    *,
    close: bool = False,
    name: str = "receipt",
) -> DispatchOutcomePreparationV2:
    from chiplog.capabilities.effects.contracts import CommandIdentity
    from chiplog.capabilities.effects.dispatch_outcome_contracts import (
        DispatchEvidenceV2,
        DispatchOutcomeCommandV2,
        DispatchOutcomePreparationV2,
        DispatchOutcomeRecordV2,
    )
    from chiplog.capabilities.effects.dispatch_v2 import digest, reference

    evidence = (
        None
        if close
        else DispatchEvidenceV2(
            evidence_id=name,
            source_identity="source",
            transmission=send.snapshot.transmissions[0].transmission,
            raw_bytes=name.encode(),
            raw_digest=digest(name.encode()),
            observation="PROVIDER_RECEIPT",
            occurred_members=positive,
            permanently_incapable_members=negative,
        )
    )
    command = DispatchOutcomeCommandV2(
        schema_id="chiplog.effects.dispatch-outcome-command.v2",
        identity=CommandIdentity(
            command_id=name, fingerprint=digest(name.encode()), expected_tenant_head=1
        ),
        operation="RESOLVE_OBLIGATION" if close else "APPEND_EVIDENCE",
        intent=reference(send.snapshot.intent.intent_id, send.snapshot.intent.canonical_bytes()),
        original_send=send.record,
        expected_attempt=previous.snapshot.attempt,
        complete_children=tuple(child.transmission for child in send.snapshot.transmissions),
        prior_evidence=previous.snapshot.evidence_heads
        if isinstance(previous, DispatchOutcomeRecordV2)
        else (),
        evidence=evidence,
        resolver=reference("principal", b"principal") if close else None,
    )
    return DispatchOutcomePreparationV2(
        schema_id="chiplog.effects.dispatch-outcome-preparation.v2",
        original_send=send,
        previous=previous,
        command=command,
    )


def _selected_outcome_tuple[SelectedRecord: (DispatchRecordV2, DispatchOutcomeRecordV2)](
    outcome: SelectedRecord, *, ordinal: int = 1, commit_sequence: int = 1
) -> tuple[SelectedRecord, SelectedEffectHistoryMember, StoredEffectRow]:
    import hashlib

    from chiplog.adapters.driven.effects_queries import StoredEffectRow
    from chiplog.capabilities.effects.dispatch_authority_contracts import (
        SelectedEffectHistoryMember,
    )
    from chiplog.capabilities.effects.dispatch_v2 import reference
    from chiplog.platform._owner_publication_contracts import OwnerRecordBytes

    raw = outcome.canonical_bytes()
    member = SelectedEffectHistoryMember(
        kind=outcome.kind,
        selected_decision=reference("decision", b"selected decision"),
        publication_ordinal=ordinal,
        original_selected_cut=reference("cut", b"selected cut"),
        record=outcome.record,
        predecessor=outcome.predecessor,
        intent=reference(
            outcome.snapshot.intent.intent_id, outcome.snapshot.intent.canonical_bytes()
        ),
        semantics=outcome.snapshot.intent.mandate.semantics,
        command_bytes=outcome.command.canonical_bytes(),
        record_bytes=raw,
    )
    physical = OwnerRecordBytes(
        owner="effects",
        record_kind="effects." + outcome.kind,
        record_id=outcome.record.head,
        schema_id=outcome.schema_id,
        canonical_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )
    return (
        outcome,
        member,
        StoredEffectRow(
            commit_sequence=commit_sequence,
            publication_ordinal=ordinal,
            batch_record_ids=(outcome.record.head,),
            record=physical,
        ),
    )


async def _open_cut_probe(monkeypatch, case):
    """Inert source-selection probe; mounted source authentication is separate."""
    from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
    from chiplog.composition import r14_native_accepted_open_cut as cuts
    from chiplog.composition.r14_execution_transition_records import ExecutionHistorySnapshot
    from tests.capabilities.effects.support.dispatch_outcomes import original_send
    from tests.support.acceptance_v2 import prepared_acceptance
    from tests.support.execution_fan_out import fixture

    acceptance = prepared_acceptance()
    intent = acceptance.effects_proposal.snapshot.intent
    send = original_send()
    send = send.model_copy(update={"snapshot": send.snapshot.model_copy(update={"intent": intent})})
    outcome, member, row = _selected_outcome_tuple(
        prepare_outcome(_outcome_request(send, send)), commit_sequence=2
    )
    run = (await fixture()).captured_run
    run = run.model_copy(update={"tenant": intent.mandate.tenant_id})
    run_ref = cuts.CallSubjectHead(
        subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
    )
    initialized = acceptance.loop_request.binding.initialized
    manifest = tuple(
        item.revision for item in acceptance.loop_proposal.complete_acceptance_manifest
    )
    branch = ConsequentialAcceptedCall(
        initialized=initialized.revision,
        accepted=manifest[0],
        execution_intent=manifest[1],
        external_effect_intent=manifest[2],
        complete_acceptance_manifest=manifest,
    )
    call_id = acceptance.loop_request.binding.original_call_id
    first = SimpleNamespace(
        original_call_id="earlier-call",
        acceptance=branch.model_copy(
            update={"accepted": Present(head="earlier-accepted", fingerprint="9" * 64)}
        ),
    )
    target = SimpleNamespace(original_call_id=call_id, acceptance=branch)
    rows = (first, target) if case == "second-member" else (first,)
    if case == "duplicate":
        rows = (first, target, target)
    selected = SimpleNamespace(
        accepted=SimpleNamespace(
            source=SimpleNamespace(target_run_lineage=(run_ref,), target_inventory_rows=rows),
            acceptance=acceptance,
            initialized=initialized,
            original_call_id=call_id,
            selected_seal=SimpleNamespace(response_seal=SimpleNamespace(subject_id="seal")),
        ),
        database_identity=("database", 1, 2),
        tenant_id=run.tenant,
        tenant_commit_sequence=9,
        materialization_commitment="a" * 64,
        outcome=outcome,
        selected_member=member,
        outcome_record_id=row.record.record_id,
        commit_sequence=row.commit_sequence,
        publication_ordinal=row.publication_ordinal,
        batch_record_ids=row.batch_record_ids,
    )

    @contextmanager
    def held() -> Iterator[None]:
        yield

    class Runtime:
        _tenant_id = run.tenant
        _database = object()
        _database_identity = selected.database_identity
        _commitment_journal = SimpleNamespace(load=lambda _: "a" * 64)

        def _authority_gate(self):
            return SimpleNamespace(hold=held)

        def _check_database_identity(self) -> None:
            pass

    monkeypatch.setattr(cuts, "CommonCliExecutionRuntime", Runtime)
    snapshot = ExecutionHistorySnapshot(tenant_head=9, records=(run,))
    monkeypatch.setattr(cuts, "read_execution_history", lambda _: snapshot)

    @contextmanager
    def connection(_database):
        yield object()

    monkeypatch.setattr(cuts, "read_connection", connection)
    monkeypatch.setattr(cuts, "capture_authority_snapshot_commitment", lambda *_: "a" * 64)
    result = cuts.build_accepted_open_recovery_cut(Runtime(), selected)
    return result, branch, call_id, selected, snapshot, acceptance, send


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["second-member", "missing", "duplicate"])
async def test_open_cut_uses_exact_member_instead_of_first_inventory_row(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    result, branch, call_id, *_ = await _open_cut_probe(monkeypatch, case)
    if case == "second-member":
        assert result is not None
        assert result.original_call_id == call_id
        assert result.accepted.revision == branch.accepted
        assert result.execution_intent.revision == branch.execution_intent
    else:
        assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "history", "actual", "anchor-after"])
async def test_historical_open_cut_consumes_exact_history_proof_before_anchor_gate(
    monkeypatch: pytest.MonkeyPatch, damage: str | None
) -> None:
    """Real cut builder; source facts and outer history acquisition are inert."""
    from chiplog.composition import r14_native_accepted_open_cut as cuts
    from chiplog.composition import r14_original_call_resolution_source as resolutions

    _, _, call_id, facts, snapshot, *_ = await _open_cut_probe(monkeypatch, "second-member")
    runtime = cuts.CommonCliExecutionRuntime()
    acquired = False

    def history(*args, **kwargs):
        nonlocal acquired
        assert kwargs == {"selected_only": True, "exact_historical_sources": True}
        acquired = True
        if damage == "history":
            raise ValueError("unverified physical suffix")
        return snapshot, None, None, None, None, None

    @contextmanager
    def historical_connection(_database):
        run = snapshot.records[-1]

        def execute(sql, parameters):
            assert "commit_sequence<=?" in sql
            assert parameters == (runtime._tenant_id, 8, run.head)
            return SimpleNamespace(
                fetchall=lambda: (
                    (run.head, "agent_loop", run.schema_id, run.canonical_bytes(), 1),
                )
            )

        yield SimpleNamespace(execute=execute)

    monkeypatch.setattr(cuts, "read_connection", historical_connection)
    runtime._commitment_journal = SimpleNamespace(
        load=lambda _: "c" * 64 if acquired and damage == "anchor-after" else "b" * 64
    )
    monkeypatch.setattr(cuts, "read_execution_recovery_history", history)
    monkeypatch.setattr(
        cuts,
        "capture_authority_snapshot_commitment",
        lambda *_: "c" * 64 if damage == "actual" else facts.materialization_commitment,
    )
    monkeypatch.setattr(
        resolutions, "authenticate_selected_resolution_prefix_held", lambda *a: False
    )
    result = cuts.build_accepted_open_recovery_cut(
        runtime,
        facts,
        source_materialization_commitment="b" * 64,
        source_tenant_commit_sequence=8,
    )
    if damage is None:
        assert result is not None
        assert acquired
        assert result.original_call_id == call_id
        assert result.materialization_commitment == "b" * 64
        assert result.tenant_commit_sequence == 8
    else:
        assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage",
    [
        None,
        "acceptance",
        "effect",
        "seal",
        "future-outcome",
        "future-run",
        "closed-at-source",
        "later-closed",
    ],
)
async def test_open_causal_source_joins_captured_prefix_and_excludes_future_closure(
    monkeypatch: pytest.MonkeyPatch, damage: str | None
) -> None:
    """Inert preauthenticated inputs isolate the causal join, not IPC provenance."""
    from dataclasses import replace

    from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
    from chiplog.composition import r14_historical_suffix as suffix
    from chiplog.composition.r14_native_accepted_open_cut import _fingerprint
    from chiplog.composition.r16_dispatch_history import VerifiedDispatchHistory

    cut, _, _, selected, snapshot, acceptance, send = await _open_cut_probe(
        monkeypatch, "second-member"
    )
    assert cut is not None
    initialized = acceptance.loop_request.initialized_record
    fanout = SimpleNamespace(
        proposal=SimpleNamespace(
            fan_out=SimpleNamespace(
                initialized_records=(initialized,),
                response_seal=SimpleNamespace(
                    response_seal_id="seal",
                    original_run_id=snapshot.records[-1].run_id,
                    original_turn_id=initialized.call.original.original_turn_id,
                ),
            )
        )
    )
    send_tuple = _selected_outcome_tuple(send, commit_sequence=1)
    outcome_tuple = _selected_outcome_tuple(selected.outcome, commit_sequence=2)
    observed = [send_tuple, outcome_tuple]
    updates = {}
    if damage == "acceptance":
        updates["accepted"] = cut.accepted.model_copy(
            update={"revision": Present(head="foreign-acceptance", fingerprint="9" * 64)}
        )
    elif damage == "effect":
        updates["external_effect_intent"] = cut.external_effect_intent.model_copy(
            update={"revision": Present(head="foreign-effect", fingerprint="9" * 64)}
        )
    elif damage == "seal":
        updates["response_id"] = "other-seal"
    elif damage == "future-run":
        updates["current_run"] = cut.current_run.model_copy(
            update={"revision": Present(head="future-run", fingerprint="9" * 64)}
        )
    elif damage == "future-outcome":
        observed[1] = (*outcome_tuple[:2], replace(outcome_tuple[2], commit_sequence=10))
        updates["outcome_commit_sequence"] = 10
    elif damage in ("closed-at-source", "later-closed"):
        members = tuple(item.subject_id for item in send.snapshot.intent.mandate.bundle_members)
        confirmed = prepare_outcome(
            _outcome_request(selected.outcome, send, positive=members, name="confirmed")
        )
        closed = prepare_outcome(_outcome_request(confirmed, send, close=True, name="closed"))
        first_sequence = 3 if damage == "closed-at-source" else 10
        observed.extend(
            (
                _selected_outcome_tuple(confirmed, commit_sequence=first_sequence),
                _selected_outcome_tuple(closed, commit_sequence=first_sequence + 1),
            )
        )
    cut = cut.model_copy(update=updates)
    cut = cut.model_copy(update={"cut_fingerprint": _fingerprint(cut)})
    effects_cut = SimpleNamespace(tenant_id=cut.tenant_id, rows=tuple(item[2] for item in observed))
    verified = VerifiedDispatchHistory(
        members=tuple(item[1] for item in observed), v2_records=tuple(item[0] for item in observed)
    )
    runtime = SimpleNamespace(
        _tenant_id=cut.tenant_id, _database_identity=selected.database_identity
    )

    def reentry(*args, **kwargs) -> None:
        raise AssertionError("causal join must consume captured history")

    monkeypatch.setattr(accounting_module, "read_execution_recovery_history", reentry)
    kwargs = dict(
        snapshot=snapshot,
        fanouts=(fanout,),
        acceptances=(acceptance,),
        effects_cut=effects_cut,
        verified_effects=verified,
    )
    if damage in (None, "later-closed"):
        suffix.validate_native_open_causal_source_prefix(runtime, cut, **kwargs)
    else:
        with pytest.raises(ValueError, match="OPEN causal source"):
            suffix.validate_native_open_causal_source_prefix(runtime, cut, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["execution-suspension", "execution-successor"])
@pytest.mark.parametrize(
    "state",
    [
        "ABSENT",
        "COMPLETE",
        "unselected",
        "conflict",
        "orphan",
        "future-head",
        "predecessor",
    ],
)
async def test_open_effects_history_keeps_only_exact_physical_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, family: str, state: str
) -> None:
    """Real physical readback; owner transition semantics are authenticated by the caller."""
    import hashlib
    import sqlite3

    from chiplog.composition import r14_historical_suffix as suffix
    from chiplog.composition.r14_execution_fanout_contracts import EXECUTION_RUN_SCHEMA
    from chiplog.platform._sqlite import (
        PhysicalPublicationCommand,
        PhysicalRecord,
        SQLiteMaterializer,
    )
    from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
    from chiplog.platform.workspace_snapshot import read_connection

    _, _, _, _, snapshot, _, _ = await _open_cut_probe(monkeypatch, "second-member")
    original = snapshot.records[0]
    later = original.model_copy(update={"predecessor": original.head, "head": "loop:later"})
    assert later.head != original.head

    def command(
        record: RunRecord | ExecutionRunRecord, sequence: int, operation: str
    ) -> PhysicalPublicationCommand:
        raw = record.canonical_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        return PhysicalPublicationCommand(
            tenant_id=original.tenant,
            operation_kind="agent_loop." + operation + ".v1",
            idempotency_key=record.head,
            request_fingerprint=fingerprint,
            expected_head=sequence - 1,
            fence_generation="r6",
            expected_fence_frontier=0,
            minimum_fence_frontier=0,
            records=(
                PhysicalRecord(record.head, "agent_loop", EXECUTION_RUN_SCHEMA, raw, fingerprint),
            ),
        )

    first, pending = (
        command(original, 1, "execution-transition"),
        command(later, 3 if state == "future-head" else 2, family),
    )
    database = tmp_path / "physical-history.sqlite"
    with SQLiteMaterializer(database, record_contracts={"agent_loop": EXECUTION_RUN_SCHEMA}):
        pass
    with sqlite3.connect(database) as db:
        physical_head = 2 if state in ("COMPLETE", "conflict") else 1
        db.execute("INSERT INTO tenant_heads VALUES (?, ?)", (original.tenant, physical_head))

        def publish(cmd: PhysicalPublicationCommand) -> None:
            record = cmd.records[0]
            db.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
                (
                    original.tenant,
                    record.record_id,
                    record.owner,
                    record.schema_id,
                    record.canonical_bytes,
                    cmd.expected_head + 1,
                ),
            )
            db.execute(
                "INSERT INTO publications VALUES (?, ?, ?, ?, ?, ?)",
                (
                    original.tenant,
                    cmd.operation_kind,
                    cmd.idempotency_key,
                    cmd.request_fingerprint,
                    cmd.expected_head + 1,
                    record.record_id,
                ),
            )

        publish(first)
        if state in ("COMPLETE", "conflict"):
            publish(pending)
        if state == "conflict":
            db.execute(
                "UPDATE records SET canonical_bytes=? WHERE record_id=?", (b"bad", later.head)
            )
        if state == "orphan":
            db.execute(
                "INSERT INTO records VALUES (?, 'orphan', 'agent_loop', ?, ?, 1)",
                (
                    original.tenant,
                    EXECUTION_RUN_SCHEMA,
                    b"orphan",
                ),
            )
    with read_connection(database) as connection:
        commitment = capture_authority_snapshot_commitment(connection, original.tenant)
    pending_entry = dict(
        operation_id=later.head, predecessor="wrong" if state == "predecessor" else commitment
    )
    runtime = SimpleNamespace(
        _tenant_id=original.tenant,
        _authority_gate=lambda: SimpleNamespace(require_held=lambda: None),
        _pending=lambda: () if state == "unselected" else (pending_entry,),
        _publication=lambda entry: pending,
        _commitment_journal=SimpleNamespace(load=lambda _: commitment),
    )
    selected_snapshot = snapshot.__class__(tenant_head=physical_head, records=(original, later))
    with read_connection(database) as connection:
        kwargs = dict(
            snapshot=selected_snapshot,
            selected=((first, {}), (pending, pending_entry)),
            expected_ids={original.head, later.head},
            expected_publications={
                (cmd.operation_kind, cmd.idempotency_key) for cmd in (first, pending)
            },
        )
        if state in ("ABSENT", "COMPLETE"):
            result = suffix.native_open_physical_execution_history_held(
                runtime, connection, **kwargs
            )
            assert result.records == ((original,) if state == "ABSENT" else (original, later))
            assert selected_snapshot.records == (original, later)
        else:
            with pytest.raises(ValueError, match="OPEN physical"):
                suffix.native_open_physical_execution_history_held(runtime, connection, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "absent-prefix-run", "future-prefix-run"])
async def test_open_outer_fold_reads_effects_once_with_physical_history(
    monkeypatch: pytest.MonkeyPatch, damage: str | None
) -> None:
    """Boundary wiring probe; actual source causality is exercised separately."""
    from chiplog.composition import r14_historical_suffix as suffix
    from chiplog.composition import r16_dispatch_history, r16_effects

    cut, _, _, _, snapshot, *_ = await _open_cut_probe(monkeypatch, "second-member")
    assert cut is not None
    original = snapshot.records[0]
    later = original.model_copy(update={"head": "loop:pending", "predecessor": original.head})
    current_selected = snapshot.__class__(tenant_head=10, records=(original, later))
    physical = snapshot.__class__(tenant_head=10, records=(original,))
    prefix = (
        snapshot
        if damage != "absent-prefix-run"
        else snapshot.__class__(tenant_head=snapshot.tenant_head, records=(original, later))
    )
    calls = []
    runtime = SimpleNamespace(_tenant_id=cut.tenant_id, _owner_decisions=lambda: "owners")
    monkeypatch.setattr(
        suffix, "native_open_physical_execution_history_held", lambda *a, **kw: physical
    )
    monkeypatch.setattr(suffix, "capture_authority_snapshot_commitment", lambda *_: "actual")
    effects_cut = SimpleNamespace(materialization_commitment="actual")

    def read_effects(
        runtime, journal, *, authenticated_execution_history, authenticate_later_suffix
    ):
        assert journal == "owners"
        assert authenticated_execution_history is physical
        assert authenticated_execution_history is not current_selected
        calls.append("effects")
        return effects_cut, "retained-journal"

    monkeypatch.setattr(r16_effects, "_read_materialized_effects_with_history", read_effects)
    monkeypatch.setattr(r16_dispatch_history, "verify_dispatch_history", lambda *args: "verified")
    monkeypatch.setattr(
        suffix,
        "validate_native_open_causal_source_prefix",
        lambda runtime, cut, **kwargs: calls.append((cut, kwargs)),
    )
    sequence = 10 if damage == "future-prefix-run" else 1
    connection = SimpleNamespace(execute=lambda *args: [(original.head, sequence)])
    sources = tuple((SimpleNamespace(), {}, cut, prefix, (), ()) for _ in range(2))
    kwargs = dict(
        sources=sources,
        snapshot=current_selected,
        selected=(),
        expected_ids=(),
        expected_publications=(),
        acceptances=(),
    )
    if damage is None:
        suffix.validate_native_open_sources_held(runtime, connection, **kwargs)
        assert calls[0] == "effects" and len(calls) == 3
        assert all(value[1]["snapshot"] is prefix for value in calls[1:])
        assert all(value[1]["verified_effects"] == "verified" for value in calls[1:])
    else:
        with pytest.raises(ValueError, match="unmaterialized or future Run"):
            suffix.validate_native_open_sources_held(runtime, connection, **kwargs)
        assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pending_operation",
    [
        None,
        "agent_loop.accepted-open-accounting.v1",
        "agent_loop.accepted-open-recovery.v1",
        "agent_loop.execution-suspension.v1",
        "agent_loop.execution-accounting-readiness.v1",
    ],
)
async def test_open_historical_cut_selects_call_seal_among_two_native_executions(
    monkeypatch: pytest.MonkeyPatch, pending_operation: str | None
) -> None:
    from chiplog.composition import r14_accepted_open_accounting_publication as publication

    cut, _, call_id, _, snapshot, acceptance, *_ = await _open_cut_probe(
        monkeypatch, "second-member"
    )
    assert cut is not None
    executions = tuple(
        SimpleNamespace(
            proposal=SimpleNamespace(
                fan_out=SimpleNamespace(
                    initialized_records=(SimpleNamespace(original_call_id=member),),
                    response_seal=SimpleNamespace(response_seal_id=seal, digest=lambda: "b" * 64),
                )
            )
        )
        for member, seal in (("earlier-call", "earlier-seal"), (call_id, cut.response_id))
    )
    history = (snapshot, None, (), executions, (), (acceptance,))
    monkeypatch.setattr(publication, "read_execution_recovery_history", lambda *a, **kw: history)
    projections = []

    def projection(ref):
        projections.append(ref.subject_id)
        return SimpleNamespace(selected_response_seal=ref)

    monkeypatch.setattr(
        publication,
        "R14OriginalNativePrepareSource",
        lambda runtime: SimpleNamespace(collect=projection),
    )
    monkeypatch.setattr(publication, "bind_native_execution_preparation_v2", lambda _: object())
    facts = object()
    physical_snapshot = object()
    recovery_history = history
    from chiplog.composition import r14_historical_suffix as suffix

    monkeypatch.setattr(
        suffix,
        "_materialized_native_open_run_history_held",
        lambda *a, **kw: physical_snapshot,
        raising=False,
    )
    monkeypatch.setattr(publication, "read_connection", lambda _: nullcontext(object()))

    def collect(run, **kwargs):
        assert kwargs["original_call_id"] == call_id
        assert kwargs["expected_outcome_record_id"] == cut.selected_outcome_record_id
        assert kwargs.get("authenticated_effects_history") is (
            snapshot
            if pending_operation
            in (
                "agent_loop.accepted-open-accounting.v1",
                "agent_loop.accepted-open-recovery.v1",
                "agent_loop.execution-accounting-readiness.v1",
            )
            else physical_snapshot
            if pending_operation == "agent_loop.execution-suspension.v1"
            else None
        )
        assert kwargs.get("authenticated_recovery_history") is recovery_history
        return facts

    monkeypatch.setattr(
        publication,
        "R14NativeSelectedAccountingSource",
        lambda *a: SimpleNamespace(collect_one_accepted_open_outcome=collect),
    )
    monkeypatch.setattr(publication, "execution_run_head", lambda _: cut.current_run)
    monkeypatch.setattr(publication, "build_accepted_open_recovery_cut", lambda *a, **kw: cut)
    runtime = SimpleNamespace(
        _database="inert-probe",
        _pending=lambda: (
            () if pending_operation is None else ({"operation_kind": pending_operation},)
        ),
        _pending_owners=lambda: (),
        _pending_gate_publications=lambda: (),
        _authority_gate=lambda: SimpleNamespace(require_held=lambda: None),
    )
    assert publication._current_cut(
        runtime,
        selected_only=True,
        expected_source_cut=cut,
        authenticated_history=recovery_history,
    ) == (facts, cut)
    assert projections == [cut.response_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("damage", [None, "missing-run", "run-bytes", "frontier"])
async def test_effects_run_snapshot_reuse_keeps_exact_sql_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, supplied: bool, damage: str | None
) -> None:
    """Real Run reconstruction and SQL; outer history acquisition is a counted control."""
    import sqlite3

    from chiplog.composition import r14_loop_history as history
    from chiplog.composition import r16_effects as effects
    from chiplog.composition.r14_runtime import R14PlanningRuntime
    from chiplog.platform._sqlite import SCHEMA_SQL
    from tests.support.capabilities.agent_loop.execution_recovery_preparation import _cut

    cut = await _cut()
    runs = cast(tuple[ExecutionRunRecord, ...], cut.complete_ordered_run_lineage)
    assert len(runs) >= 2
    snapshot = history.ExecutionHistorySnapshot(tenant_head=1, records=runs)
    database = tmp_path / "effects-runs.sqlite"
    with sqlite3.connect(database) as connection:
        connection.executescript(SCHEMA_SQL)
        for run in runs:
            connection.execute(
                "INSERT INTO records VALUES(?,?,?,?,?,?)",
                (
                    cut.tenant_id,
                    run.head,
                    "agent_loop",
                    run.schema_id,
                    run.canonical_bytes(),
                    1,
                ),
            )
        if damage == "missing-run":
            connection.execute("DELETE FROM records WHERE record_id=?", (runs[0].head,))
        elif damage == "run-bytes":
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE record_id=?", (b"changed", runs[0].head)
            )
        elif damage == "frontier":
            snapshot = snapshot.model_copy(update={"tenant_head": 2})
        runtime = object.__new__(R14PlanningRuntime)
        runtime._record_schema_variants = (("agent_loop", runs[0].schema_id),)
        reads = []

        def acquire(actual_runtime, **kwargs):
            assert actual_runtime is runtime
            assert kwargs == {"selected_only": True, "exact_historical_sources": True}
            reads.append(True)
            return snapshot, None, None, (), (), ()

        monkeypatch.setattr(history, "read_execution_recovery_history", acquire)
        kwargs = dict(
            historical=True, authenticated_execution_history=snapshot if supplied else None
        )
        journal = SimpleNamespace(decisions=())
        if damage is None:
            assert effects._reconstruct_registered_runs(
                runtime, connection, cut.tenant_id, journal, 1, **kwargs
            ) == (runs[-1],)
        else:
            with pytest.raises(
                ValueError, match="execution selected history and effects physical cut"
            ):
                effects._reconstruct_registered_runs(
                    runtime, connection, cut.tenant_id, journal, 1, **kwargs
                )
        assert len(reads) == (0 if supplied else 1)


def test_open_unqualified_cut_holds_for_multiple_accepted_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.composition import r14_accepted_open_accounting_publication as publication

    acceptances = tuple(
        SimpleNamespace(
            loop_request=SimpleNamespace(binding=SimpleNamespace(original_call_id=call_id))
        )
        for call_id in ("first", "second")
    )
    history = (
        SimpleNamespace(records=(object(),)),
        None,
        (),
        (object(), object()),
        (),
        acceptances,
    )
    monkeypatch.setattr(publication, "read_execution_recovery_history", lambda *a, **kw: history)
    assert publication._current_cut(object()) is None


@pytest.mark.asyncio
async def test_open_startup_validation_does_not_project_incomplete_whole_seal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validator boundary: selected owner/source acquisition is inert here."""
    import json

    from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
        PrepareAcceptedOpenRecoveryV1,
    )
    from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
        prepare_accepted_open_recovery_v1,
    )
    from chiplog.composition import r14_accepted_open_accounting_publication as publication

    cut, _, _, _, _, acceptance, *_ = await _open_cut_probe(monkeypatch, "second-member")
    assert cut is not None
    request = PrepareAcceptedOpenRecoveryV1(
        command_id="accepted-open-recovery:" + cut.cut_fingerprint, cut=cut
    )
    result = prepare_accepted_open_recovery_v1(request)
    assert result.kind == "PREPARED_ACCEPTED_OPEN_RECOVERY_V1"
    evidence = SimpleNamespace(request=request, result=result)
    command = SimpleNamespace(
        operation_kind=publication.ACCEPTED_OPEN_RECOVERY_OPERATION,
        idempotency_key=request.command_id,
        expected_head=cut.tenant_commit_sequence,
        request_fingerprint="selected-fingerprint",
        records=tuple(SimpleNamespace(schema_id="member") for _ in range(3)),
    )
    entry = dict(
        kind="DECIDED",
        operation_kind=command.operation_kind,
        operation_id=command.idempotency_key,
        predecessor=cut.materialization_commitment,
        expected_head=command.expected_head,
        fingerprint=command.request_fingerprint,
    )
    facts = SimpleNamespace(
        accepted=SimpleNamespace(acceptance=acceptance),
        materialization_commitment=cut.materialization_commitment,
    )
    runtime = SimpleNamespace(
        _tenant_id=cut.tenant_id,
        _database=object(),
        _authority_gate=lambda: SimpleNamespace(hold=nullcontext),
        _loop_decisions=lambda: SimpleNamespace(
            entries=lambda: (("decision", b"pred", json.dumps(entry)),)
        ),
        _publication=lambda entry: command,
        _commitment_journal=SimpleNamespace(load=lambda _: cut.materialization_commitment),
        _selected_physical_state=lambda command: (
            "COMPLETE",
            cut.materialization_commitment,
            cut.materialization_commitment,
        ),
    )

    @contextmanager
    def connection(_):
        yield object()

    monkeypatch.setattr(publication, "read_connection", connection)
    monkeypatch.setattr(
        publication,
        "capture_authority_snapshot_commitment",
        lambda *_: cut.materialization_commitment,
    )
    monkeypatch.setattr(publication, "retained_accepted_open_recovery", lambda _: evidence)
    monkeypatch.setattr(publication, "accepted_open_recovery_command", lambda _: command)
    monkeypatch.setattr(publication, "_current_cut", lambda *a, **kw: (facts, cut))

    def incomplete_projection(*args) -> None:
        raise AssertionError("startup must validate the exact call, not an incomplete whole seal")

    monkeypatch.setattr(publication, "_accepted_open_sealed_accounting", incomplete_projection)
    publication.validate_selected_accepted_open_recovery(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "publish_accepted_open_accounting",
        "publish_accepted_open_recovery",
        "read_selected_accepted_open_accounting",
        "read_selected_accepted_open_recovery",
    ],
)
async def test_public_open_api_forwards_exact_original_call(
    monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    from chiplog.composition import r14_accepted_open_accounting_publication as publication
    from chiplog.composition import r14_loop_history as history
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

    runtime = object()
    result = object()
    calls: list[tuple[object, str, str] | tuple[object, str]] = []

    async def write(actual_runtime: object, peer: str, *, original_call_id: str) -> object:
        calls.append((actual_runtime, peer, original_call_id))
        return result

    def read(actual_runtime: object, *, original_call_id: str) -> object:
        calls.append((actual_runtime, original_call_id))
        return result

    if method.startswith("publish"):
        monkeypatch.setattr(publication, method, write)
        assert (
            await getattr(CommonCliExecutionRuntime, method)(
                runtime, "hermetic-ingress", original_call_id="target-call"
            )
            is result
        )
        assert calls == [(runtime, "hermetic-ingress", "target-call")]
    else:
        monkeypatch.setattr(history, method, read)
        assert (
            getattr(CommonCliExecutionRuntime, method)(runtime, original_call_id="target-call")
            is result
        )
        assert calls == [(runtime, "target-call")]


@pytest.mark.asyncio
@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize("damage", [None, "other-envelope", "duplicate-call"])
async def test_open_writer_replays_requested_call_after_validating_all_marked_envelopes(
    monkeypatch: pytest.MonkeyPatch, recovery: bool, damage: str | None
) -> None:
    """Prior selection boundary; actual owner builders are replaced explicitly."""
    import json

    from chiplog.capabilities.agent_loop.contracts import LoopRejected
    from chiplog.composition import r14_accepted_open_accounting_publication as publication

    operation = (
        publication.ACCEPTED_OPEN_RECOVERY_OPERATION
        if recovery
        else publication.ACCEPTED_OPEN_ACCOUNTING_OPERATION
    )
    entries, commands, evidence = [], {}, {}
    checked, finished = [], []
    from unittest.mock import create_autospec

    from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
        AcceptedOpenAccountingRecordV1,
        PreparedAcceptedOpenRecoveryV1,
    )

    prepared = create_autospec(
        PreparedAcceptedOpenRecoveryV1 if recovery else AcceptedOpenAccountingRecordV1,
        instance=True,
        spec_set=True,
    )
    for label in ("A", "B"):
        call = "call-B" if damage == "duplicate-call" else "call-" + label
        seal = "seal-B" if damage == "duplicate-call" else "seal-" + label
        entry = dict(
            kind="DECIDED",
            operation_kind=operation,
            operation_id=label,
            expected_head=1,
            fingerprint="fingerprint-" + label,
            predecessor="source",
        )
        if label == "A" and damage == "other-envelope":
            entry["fingerprint"] = "changed"
        entries.append(entry)
        commands[label] = SimpleNamespace(
            operation_kind=operation,
            idempotency_key=label,
            expected_head=1,
            request_fingerprint="fingerprint-" + label,
            records=tuple(SimpleNamespace(schema_id="inert") for _ in range(3 if recovery else 1)),
        )
        evidence[label] = SimpleNamespace(
            label=label,
            request=SimpleNamespace(
                cut=SimpleNamespace(
                    original_call_id=call, response_id=seal, materialization_commitment="source"
                )
            ),
        )

    @contextmanager
    def held() -> Iterator[None]:
        yield

    async def actor(_):
        return object()

    runtime = SimpleNamespace(
        _execution_actor=actor,
        _check_execution_actor=lambda _: None,
        _authority_gate=lambda: SimpleNamespace(hold=held),
        _pending=lambda: (),
        _loop_decisions=lambda: SimpleNamespace(
            entries=lambda: tuple(
                (entry["operation_id"], b"pred", json.dumps(entry)) for entry in entries
            )
        ),
        _publication=lambda entry: commands[entry["operation_id"]],
    )

    def retained(entry):
        checked.append(entry["operation_id"])
        return evidence[entry["operation_id"]]

    async def finish(actual_runtime, entry):
        assert actual_runtime is runtime
        finished.append(entry["operation_id"])
        return prepared

    retained_name = (
        "retained_accepted_open_recovery" if recovery else "retained_accepted_open_accounting"
    )
    builder_name = (
        "accepted_open_recovery_command" if recovery else "accepted_open_accounting_command"
    )
    monkeypatch.setattr(publication, retained_name, retained)
    monkeypatch.setattr(publication, builder_name, lambda value: commands[value.label])
    monkeypatch.setattr(publication, "_finish_recovery" if recovery else "_finish", finish)
    write = (
        publication.publish_accepted_open_recovery
        if recovery
        else publication.publish_accepted_open_accounting
    )
    if damage is None:
        assert await write(runtime, original_call_id="call-B") is prepared
        assert checked == ["A", "B"] and finished == ["B"]
    else:
        with pytest.raises(LoopRejected, match=r"envelope|duplicate"):
            await write(runtime, original_call_id="call-B")
        assert finished == []


@pytest.mark.parametrize("state", ["COMPLETE", "ABSENT", "PARTIAL"])
@pytest.mark.parametrize("physical_ahead", [False, True])
@pytest.mark.parametrize("damage", [None, "source", "anchor"])
def test_open_source_authentication_binds_current_physical_cut(
    monkeypatch: pytest.MonkeyPatch, state: str, physical_ahead: bool, damage: str | None
) -> None:
    """Source acquisition is inert; this checks its current-cut trust boundary."""
    from chiplog.capabilities.agent_loop.contracts import LoopRejected
    from chiplog.composition import r14_accepted_open_accounting_publication as publication

    actual = "current" if physical_ahead else "source"
    anchor = "source"
    gate_held = False
    acquired = False

    @contextmanager
    def hold() -> Iterator[None]:
        nonlocal gate_held
        assert not gate_held
        gate_held = True
        try:
            yield
        finally:
            gate_held = False

    @contextmanager
    def connection(_):
        assert gate_held
        yield object()

    cut = SimpleNamespace(materialization_commitment="source", tenant_commit_sequence=7)

    def collect(_runtime, **kwargs):
        nonlocal acquired
        assert gate_held
        assert kwargs["expected_source_cut"] is cut
        assert kwargs["source_materialization_commitment"] == "source"
        assert kwargs["source_tenant_commit_sequence"] == 7
        acquired = True
        return SimpleNamespace(
            materialization_commitment="wrong" if damage == "source" else actual
        ), cut

    runtime = SimpleNamespace(
        _database=object(),
        _tenant_id="tenant",
        _authority_gate=lambda: SimpleNamespace(hold=hold),
        _commitment_journal=SimpleNamespace(
            load=lambda _: "changed" if acquired and damage == "anchor" else anchor
        ),
        _selected_physical_state=lambda _: (state, actual, anchor),
    )
    monkeypatch.setattr(publication, "read_connection", connection)
    monkeypatch.setattr(publication, "capture_authority_snapshot_commitment", lambda *_: actual)
    monkeypatch.setattr(publication, "_current_cut", collect)
    valid = damage is None and (state == "COMPLETE" or (state == "ABSENT" and not physical_ahead))
    if valid:
        assert (
            publication._authenticate_selected_open_source(
                runtime, {"predecessor": "source"}, object(), cut
            )[1]
            is cut
        )
    else:
        with pytest.raises(LoopRejected):
            publication._authenticate_selected_open_source(
                runtime, {"predecessor": "source"}, object(), cut
            )


def test_open_current_cut_reuses_history_but_keeps_original_historical_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.composition import r14_accepted_open_accounting_publication as publication

    class ReachedBuilder(BaseException):
        pass

    seal = SimpleNamespace(response_seal_id="seal", digest=lambda: "a" * 64)
    history = (
        SimpleNamespace(records=(object(),)),
        None,
        None,
        (
            SimpleNamespace(
                proposal=SimpleNamespace(
                    fan_out=SimpleNamespace(
                        initialized_records=(SimpleNamespace(original_call_id="call"),),
                        response_seal=seal,
                    )
                )
            ),
        ),
        None,
        (),
    )
    retained = SimpleNamespace(
        original_call_id="call",
        current_run=object(),
        selected_outcome_record_id="original",
        selected_decision=object(),
        tenant_commit_sequence=7,
    )
    events = []
    facts = object()

    class PrepareSource:
        def __init__(self, runtime) -> None:
            pass

        def collect(self, seal_ref):
            events.append("prepare")
            return SimpleNamespace(selected_response_seal=seal_ref)

    class AccountingSource:
        def __init__(self, *args) -> None:
            pass

        def collect_one_accepted_open_outcome(self, target, **kwargs):
            assert target is retained.current_run
            assert kwargs["expected_outcome_record_id"] == "original"
            assert kwargs["expected_selected_decision"] is retained.selected_decision
            assert kwargs["expected_tenant_commit_sequence"] == 7
            assert "_accepted_facts" not in kwargs
            events.append("historical-selection")
            return facts

    def duplicate(*args, **kwargs) -> None:
        pytest.fail("fresh held history was read again")

    def build(runtime, selected, **kwargs) -> None:
        assert selected is facts and kwargs["authenticated_history"] is history
        assert events == ["held", "prepare", "held", "historical-selection"]
        raise ReachedBuilder

    runtime = SimpleNamespace(
        _authority_gate=lambda: SimpleNamespace(require_held=lambda: events.append("held")),
        _pending=lambda: (),
    )
    monkeypatch.setattr(publication, "read_execution_recovery_history", duplicate)
    monkeypatch.setattr(publication, "R14OriginalNativePrepareSource", PrepareSource)
    monkeypatch.setattr(publication, "R14NativeSelectedAccountingSource", AccountingSource)
    monkeypatch.setattr(publication, "bind_native_execution_preparation_v2", lambda *_: object())
    monkeypatch.setattr(publication, "build_accepted_open_recovery_cut", build)
    with pytest.raises(ReachedBuilder):
        publication._current_cut(
            runtime,
            selected_only=True,
            expected_source_cut=retained,
            source_materialization_commitment="historical",
            source_tenant_commit_sequence=7,
            authenticated_history=history,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("damage", [None, "initial-sql", "final-sql", "final-anchor", "history"])
async def test_current_cut_and_real_builder_read_historical_sources_once(
    monkeypatch: pytest.MonkeyPatch, supplied: bool, damage: str | None
) -> None:
    """Real caller/builder seam; producer facts and physical rows are controls."""
    from chiplog.composition import r14_accepted_open_accounting_publication as publication
    from chiplog.composition import r14_native_accepted_open_cut as cuts

    retained, _, call_id, facts, snapshot, *_ = await _open_cut_probe(monkeypatch, "second-member")
    runtime = cuts.CommonCliExecutionRuntime()

    class Gate:
        depth = 0

        def require_held(self) -> None:
            assert self.depth > 0

        @contextmanager
        def hold(self) -> Iterator[None]:
            self.depth += 1
            try:
                yield
            finally:
                self.depth -= 1

    gate = Gate()
    runtime._authority_gate = lambda: gate
    runtime._pending = lambda: ()
    seal = SimpleNamespace(response_seal_id="seal", digest=lambda: "a" * 64)
    history = (
        snapshot,
        None,
        None,
        (
            SimpleNamespace(
                proposal=SimpleNamespace(
                    fan_out=SimpleNamespace(
                        initialized_records=(SimpleNamespace(original_call_id=call_id),),
                        response_seal=seal,
                    )
                )
            ),
        ),
        None,
        (),
    )
    reads, source_calls, builder_proofs = [], [], []

    def acquire(actual_runtime, **kwargs):
        gate.require_held()
        assert actual_runtime is runtime
        assert kwargs == {"selected_only": True, "exact_historical_sources": True}
        reads.append(True)
        if damage == "history":
            raise ValueError("historical source refused")
        return history

    class PrepareSource:
        def __init__(self, actual_runtime) -> None:
            assert actual_runtime is runtime

        def collect(self, seal_ref):
            gate.require_held()
            return SimpleNamespace(selected_response_seal=seal_ref)

    class AccountingSource:
        def __init__(self, *args) -> None:
            pass

        def collect_one_accepted_open_outcome(self, target, **kwargs):
            gate.require_held()
            assert kwargs["authenticated_recovery_history"] is history
            assert target == retained.current_run
            assert kwargs["expected_outcome_record_id"] == retained.selected_outcome_record_id
            assert kwargs["expected_selected_decision"] == retained.selected_decision
            source_calls.append(True)
            return facts

    @contextmanager
    def connection(_path):
        gate.require_held()
        run = snapshot.records[-1]

        def execute(sql, parameters):
            assert "commit_sequence<=?" in sql
            assert parameters == (runtime._tenant_id, 8, run.head)
            return SimpleNamespace(
                fetchall=lambda: (
                    (run.head, "agent_loop", run.schema_id, run.canonical_bytes(), 1),
                )
            )

        yield SimpleNamespace(execute=execute)

    hashes, anchors = [], []

    def commitment(*args):
        gate.require_held()
        hashes.append(True)
        changed = damage == "initial-sql" or (damage == "final-sql" and len(hashes) == 2)
        return "c" * 64 if changed else facts.materialization_commitment

    def anchor(*args):
        gate.require_held()
        anchors.append(True)
        return "c" * 64 if damage == "final-anchor" and len(anchors) == 2 else "b" * 64

    real_builder = cuts.build_accepted_open_recovery_cut

    def build(*args, **kwargs):
        gate.require_held()
        builder_proofs.append(kwargs["authenticated_history"])
        return real_builder(*args, **kwargs)

    runtime._commitment_journal = SimpleNamespace(load=anchor)
    monkeypatch.setattr(publication, "read_execution_recovery_history", acquire)
    monkeypatch.setattr(cuts, "read_execution_recovery_history", acquire)
    monkeypatch.setattr(publication, "R14OriginalNativePrepareSource", PrepareSource)
    monkeypatch.setattr(publication, "R14NativeSelectedAccountingSource", AccountingSource)
    monkeypatch.setattr(publication, "bind_native_execution_preparation_v2", lambda *_: object())
    monkeypatch.setattr(publication, "build_accepted_open_recovery_cut", build)
    monkeypatch.setattr(cuts, "read_connection", connection)
    monkeypatch.setattr(cuts, "capture_authority_snapshot_commitment", commitment)
    kwargs = dict(
        selected_only=True,
        expected_source_cut=retained,
        source_materialization_commitment="b" * 64,
        source_tenant_commit_sequence=8,
        authenticated_history=history if supplied else None,
    )
    with gate.hold():
        if damage == "history" and not supplied:
            with pytest.raises(ValueError, match="historical source refused"):
                publication._current_cut(runtime, **kwargs)
            assert source_calls == builder_proofs == []
            assert len(reads) == 1
            return
        result = publication._current_cut(runtime, **kwargs)
    assert gate.depth == 0
    assert len(reads) == (0 if supplied else 1)
    assert builder_proofs == [history]
    if damage in {None, "history"}:
        assert result is not None
        assert result[0] is facts
        assert result[1].original_call_id == call_id
        assert result[1].current_run == retained.current_run
        assert result[1].tenant_commit_sequence == 8
        assert result[1].materialization_commitment == "b" * 64
    else:
        assert result is None


@pytest.mark.parametrize("damage", [None, "commitment", "identity", "tenant"])
def test_open_builder_reuses_held_history_after_current_source_checks(
    monkeypatch: pytest.MonkeyPatch, damage: str | None
) -> None:
    from chiplog.composition import r14_native_accepted_open_cut as builder

    class ReachedRunJoin(BaseException):
        pass

    class Records:
        def __iter__(self):
            raise ReachedRunJoin

    @contextmanager
    def connection(_path):
        yield object()

    runtime = SimpleNamespace(
        _authority_gate=lambda: SimpleNamespace(
            hold=lambda: connection(None), require_held=lambda: None
        ),
        _check_database_identity=lambda: None,
        _database="database",
        _tenant_id="tenant",
        _database_identity=("database", 1, 2),
        _commitment_journal=SimpleNamespace(load=lambda *_: "current"),
    )
    selected = SimpleNamespace(
        materialization_commitment="wrong" if damage == "commitment" else "current",
        database_identity=("wrong", 1, 2) if damage == "identity" else runtime._database_identity,
        tenant_id="wrong" if damage == "tenant" else "tenant",
        accepted=SimpleNamespace(source=SimpleNamespace(target_run_lineage=())),
    )

    def duplicate(*args, **kwargs) -> None:
        pytest.fail("builder read an already authenticated history again")

    monkeypatch.setattr(builder, "CommonCliExecutionRuntime", SimpleNamespace)
    monkeypatch.setattr(builder, "read_connection", connection)
    monkeypatch.setattr(builder, "capture_authority_snapshot_commitment", lambda *_: "current")
    monkeypatch.setattr(builder, "read_execution_recovery_history", duplicate)
    kwargs = dict(
        source_materialization_commitment="historical",
        source_tenant_commit_sequence=7,
        authenticated_history=(SimpleNamespace(records=Records()), None, None, (), (), ()),
    )
    if damage is None:
        with pytest.raises(ReachedRunJoin):
            builder.build_accepted_open_recovery_cut(runtime, selected, **kwargs)
    else:
        assert builder.build_accepted_open_recovery_cut(runtime, selected, **kwargs) is None
