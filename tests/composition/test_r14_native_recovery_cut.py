"""Mounted selected native full-cut boundary; inert DTOs never grant authority."""

import hashlib
import json
import operator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallPreparationRejected,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.execution_recovery_contracts import PrepareExecutionAccounting
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionRecoveryCut,
    RecoverySourceRecord,
)
from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    prepare_execution_accounting,
)
from chiplog.capabilities.agent_loop.native_recovery_cut_validation import (
    validate_native_recovery_cut,
)
from chiplog.capabilities.agent_loop.native_recovery_profile_contracts import (
    native_recovery_fingerprint,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    CancelledBeforeAcceptResult,
    ConsequentialAcceptedCall,
    InitializedCall,
    RecoveryFrontier,
    RecoveryRequiredResult,
    SuccessResult,
    TerminalCallFrontier,
    UnknownResult,
)
from chiplog.capabilities.effects.contracts import ExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import SelectedEffectHistoryMember
from chiplog.capabilities.effects.dispatch_v2 import reference as effects_reference
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_native_recovery_cut import (
    NativeRecoveryCutSource,
    _selected_accepted_open_recovery_preimages,
    _selected_external_intent_preimage,
    _selected_no_retry_sources,
    _selected_reduction_sources,
)
from chiplog.composition.r14_native_recovery_historical import (
    _authenticated_loop_cut_matches,
    _authenticated_scope_sources,
    _AuthenticatedLoopDecisionCut,
    _external_intent_matches_dispatch_carrier,
    _historical_publications,
    _make_authenticated_loop_decision_cut,
    _selected_decision_matches_source,
    _selected_source_carrier_id,
    _v3_accepted_cut_route,
)
from chiplog.composition.r14_recovery_profile_publication import publish_native_recovery_profile
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._sqlite import PhysicalPublicationCommand
from tests.capabilities.effects.support.dispatch_outcomes import original_intent, original_send
from tests.support.native_cancelled import native_response, selected_native_cancelled
from tests.support.recovery_candidate import inert_recovery_candidate


def _historical_command(expected_head: int) -> PhysicalPublicationCommand:
    return PhysicalPublicationCommand(
        tenant_id="tenant",
        operation_kind="agent_loop.test",
        idempotency_key="selected",
        request_fingerprint="fingerprint",
        expected_head=expected_head,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(),
    )


def test_historical_loop_cut_rechecks_every_fresh_journal_row() -> None:
    selected_command = _historical_command(2)
    decisions: tuple[
        tuple[str, str | None, bytes, dict[str, object], PhysicalPublicationCommand | None], ...
    ] = (
        (
            "selected-id",
            None,
            b"selected-raw",
            {"kind": "DECIDED", "operation_id": "selected"},
            selected_command,
        ),
        (
            "marker-id",
            "selected-id",
            b"materialized-raw",
            {"kind": "MATERIALIZED", "operation_id": "selected"},
            None,
        ),
    )
    runtime = SimpleNamespace(
        _loop_decisions=lambda: SimpleNamespace(
            entries=lambda: tuple((row[0], row[1], row[2]) for row in decisions)
        )
    )

    cut = _make_authenticated_loop_decision_cut(runtime, decisions)
    assert _authenticated_loop_cut_matches(runtime, cut)
    foreign_runtime = SimpleNamespace(
        _loop_decisions=lambda: SimpleNamespace(
            entries=lambda: tuple((row[0], row[1], row[2]) for row in decisions)
        )
    )
    assert not _authenticated_loop_cut_matches(foreign_runtime, cut)
    assert not _authenticated_loop_cut_matches(runtime, decisions)
    # Deliberately invoke unsupported mutations dynamically to check runtime immutability.
    mutation = "setitem"
    with pytest.raises(TypeError):
        getattr(operator, mutation)(cut.decisions[0].entry, "kind", "MATERIALIZED")
    field = "publication"
    with pytest.raises((AttributeError, TypeError)):
        setattr(cut.decisions[0], field, _historical_command(999))
    with pytest.raises(TypeError):
        _AuthenticatedLoopDecisionCut(None, runtime, decisions)

    changed_marker = (*decisions[1][:2], b"changed-marker", *decisions[1][3:])
    changed_decisions = (decisions[0], changed_marker)
    runtime._loop_decisions = lambda: SimpleNamespace(
        entries=lambda: tuple((row[0], row[1], row[2]) for row in changed_decisions)
    )
    assert not _authenticated_loop_cut_matches(runtime, cut)

    runtime._loop_decisions = lambda: SimpleNamespace(
        entries=lambda: tuple((row[0], row[1], row[2]) for row in decisions)
    )
    assert _authenticated_loop_cut_matches(runtime, cut)
    runtime._loop_decisions = lambda: SimpleNamespace(entries=lambda: ())
    assert not _authenticated_loop_cut_matches(runtime, cut)


def test_historical_publications_reuse_exact_commands_and_keep_all_decisions() -> None:
    cached = _historical_command(2)
    equal_to_cut = _historical_command(3)
    non_agent_loop: dict[str, object] = {
        "kind": "DECIDED",
        "operation_id": "workspace-policy",
        "operation_kind": "workspace.policy",
    }
    fallback_command = _historical_command(1)
    runtime = SimpleNamespace(
        _publication=lambda entry: (
            fallback_command
            if entry.get("operation_id") == "workspace-policy"
            else pytest.fail("cached command should not be rebuilt")
        )
    )
    decisions: tuple[
        tuple[str, str | None, bytes, dict[str, object], PhysicalPublicationCommand | None], ...
    ] = (
        (
            "agent-loop-id",
            None,
            b"agent-loop-raw",
            {"kind": "DECIDED", "operation_id": "agent-loop"},
            cached,
        ),
        (
            "workspace-id",
            "agent-loop-id",
            b"workspace-raw",
            non_agent_loop,
            None,
        ),
        (
            "at-cut-id",
            "workspace-id",
            b"at-cut-raw",
            {"kind": "DECIDED", "operation_id": "at-cut"},
            equal_to_cut,
        ),
    )

    cut = _make_authenticated_loop_decision_cut(runtime, decisions)
    selections = _historical_publications(runtime, cut, 3)

    assert selections == [
        ("agent-loop-id", b"agent-loop-raw", decisions[0][3], cached),
        ("workspace-id", b"workspace-raw", non_agent_loop, fallback_command),
    ]


def test_inherited_no_retry_references_require_one_exact_inactive_source() -> None:
    reference = CallSubjectHead(
        subject_id="effect:original",
        revision=Present(head="record:original", fingerprint="a" * 64),
    )
    selected_decision = CallSubjectHead(
        subject_id="publication:source",
        revision=Present(head="decision:source", fingerprint="b" * 64),
    )
    source = RecoverySourceRecord(
        owner="effects",
        subject=reference,
        schema_id="chiplog.effects.original-effect.v1",
        canonical_record_bytes=b"selected original effect",
        selected_decision=selected_decision,
        physical_record=reference,
    )

    assert _selected_no_retry_sources((reference,), (source,), ())
    assert not _selected_no_retry_sources((reference,), (), ())
    assert not _selected_no_retry_sources((reference,), (source, source), ())
    assert not _selected_no_retry_sources((reference, reference), (source,), ())
    assert not _selected_no_retry_sources((reference,), (source,), (reference,))


def test_selected_reduction_source_projection_keeps_the_complete_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.composition import r14_loop_semantic_reduction_publication as publication

    sources = {"opening": object(), "successor": object()}
    selected_entries = ("opening", "successor")
    monkeypatch.setattr(
        publication,
        "_selected_value",
        lambda runtime, entry: (None, SimpleNamespace(source=sources[entry])),
    )

    assert _selected_reduction_sources(object(), selected_entries) == (
        sources["opening"],
        sources["successor"],
    )


def test_closed_recovery_source_projection_keeps_resolver_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Source acquisition is inert; the real projection must retain authority proofs."""
    from chiplog.capabilities.agent_loop.original_call_resolution_preparation import (
        prepare_original_call_resolution,
    )
    from chiplog.capabilities.agent_loop.original_semantic_reduction_preparation import (
        prepare_loop_semantic_reduction,
    )
    from chiplog.composition import r14_loop_semantic_reduction_publication as reductions
    from chiplog.composition import r14_original_call_resolution_publication as resolutions
    from tests.support.capabilities.agent_loop.original_call_resolution_preparation import (
        _source,
    )
    from tests.support.capabilities.agent_loop.original_call_resolution_preparation import (
        request as resolution_request,
    )
    from tests.support.capabilities.agent_loop.original_semantic_reduction_preparation import (
        request as reduction_request,
    )
    from tests.support.capabilities.agent_loop.recovery_accounting import recovery_call

    resolver_request = resolution_request()
    resolution = prepare_original_call_resolution(resolver_request)
    request = reduction_request()
    reduced = prepare_loop_semantic_reduction(request)
    reduction_source = _source(
        reduced.record,
        record_id=reduced.record.reduction_id,
        owner="agent_loop",
        schema=reduced.record.schema_id,
    )
    selected_reduction = SimpleNamespace(record=reduced.record, source=reduction_source)
    retained_resolution = SimpleNamespace(request=resolver_request, prepared=resolution)
    monkeypatch.setattr(
        resolutions, "read_selected_original_call_resolution_source_held", lambda _: resolution
    )
    monkeypatch.setattr(
        resolutions,
        "_read_selected_original_call_resolution_evidence_held",
        lambda _: retained_resolution,
        raising=False,
    )
    monkeypatch.setattr(reductions, "_selected_entries", lambda _: ("selected",))
    monkeypatch.setattr(
        reductions, "validate_selected_command", lambda *args: SimpleNamespace(request=request)
    )
    monkeypatch.setattr(reductions, "_selected_value", lambda *args: (None, selected_reduction))
    monkeypatch.setattr(reductions, "capture_request", lambda _: request)
    runtime = SimpleNamespace(
        read_selected_accepted_open_frontier=lambda: SimpleNamespace(
            calls=(recovery_call(recovered=True),)
        ),
        read_selected_loop_semantic_reduction=lambda: selected_reduction,
    )
    subset = SimpleNamespace(
        accepted_closed_outcome=SimpleNamespace(
            complete_ordered_evidence_sources=request.complete_ordered_evidence
        ),
        accepted_open_outcome=None,
    )
    result = NativeRecoveryCutSource._selected_accepted_recovery_inputs(runtime, subset)
    assert result is not None
    authority_sources = (
        resolver_request.cut.resolver_policy,
        resolver_request.cut.authority.authenticated_invocation,
    )
    for source in authority_sources:
        assert source in result[3]
    assert resolver_request.cut.current_obligation in result[3]
    assert resolver_request.cut.call_terminal in result[3]

    from chiplog.composition import r14_native_recovery_cut as cuts
    from chiplog.composition.h1_inventory_leaf_contracts import H1RawInventoryItem

    monkeypatch.setattr(
        cuts,
        "AuthenticatedCustodyRecord",
        SimpleNamespace(model_validate_json=lambda _: object()),
    )
    scope = SimpleNamespace(selected_admission_bytes=b"inert custody acquisition")
    known = {source.physical_record.subject_id: source for source in result[3]}
    occurrences = tuple(
        H1RawInventoryItem(
            surface="PHYSICAL",
            locator="records/" + source.physical_record.subject_id,
            tenant=resolver_request.cut.tenant_id,
            owner=source.owner,
            schema=source.schema_id,
            record_kind=None,
            record_id=source.physical_record.subject_id,
            fingerprint=source.physical_record.revision.fingerprint,
            raw=source.canonical_record_bytes,
        )
        for source in authority_sources
    )
    assert cuts.classify_native_occurrences(occurrences, {}, known, scope)
    for occurrence in occurrences:
        omitted = {key: source for key, source in known.items() if key != occurrence.record_id}
        assert not cuts.classify_native_occurrences(occurrences, {}, omitted, scope)
        substituted = replace(occurrence, raw=occurrence.raw + b" ")
        assert not cuts.classify_native_occurrences((substituted,), {}, known, scope)


@pytest.mark.parametrize(
    "damage", [None, "head", "fingerprint", "physical", "bytes", "owner", "schema"]
)
def test_original_resolver_source_requires_exact_independent_selection(damage) -> None:
    from chiplog.composition.r14_native_recovery_cut import _original_source_matches_selection
    from tests.support.capabilities.agent_loop.original_call_resolution_preparation import request

    source = request().cut.current_obligation
    record = SimpleNamespace(
        record_id=source.physical_record.subject_id,
        owner=source.owner,
        schema_id=source.schema_id,
        canonical_bytes=source.canonical_record_bytes,
    )
    decision = source.selected_decision.model_copy(update={"subject_id": "command-id"})
    if damage in {"head", "fingerprint"}:
        revision = source.selected_decision.revision.model_copy(update={damage: "f" * 64})
        source = source.model_copy(
            update={
                "selected_decision": source.selected_decision.model_copy(
                    update={"revision": revision}
                )
            }
        )
    elif damage == "physical":
        source = source.model_copy(
            update={
                "physical_record": source.physical_record.model_copy(
                    update={"subject_id": "foreign"}
                )
            }
        )
    elif damage == "bytes":
        source = source.model_copy(
            update={"canonical_record_bytes": source.canonical_record_bytes + b" "}
        )
    elif damage in {"owner", "schema"}:
        source = source.model_copy(
            update={"schema_id" if damage == "schema" else "owner": "foreign"}
        )
    assert _original_source_matches_selection(source, record, decision) is (damage is None)


@pytest.mark.parametrize(
    "variant",
    [
        "carrier-and-marker",
        "marker-only",
        "duplicate-carrier",
        "logical-child",
    ],
)
def test_accepted_effect_join_distinguishes_carrier_and_body_fingerprints(variant: str) -> None:
    from chiplog.capabilities.effects.dispatch_outcomes import prepare_outcome
    from chiplog.composition.r14_native_recovery_cut import _accepted_source_preimages
    from tests.support.capabilities.effects.dispatch_outcomes import request

    send = original_send()
    record = prepare_outcome(request(send, send))
    raw = record.canonical_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    marker = CallSubjectHead(
        subject_id=record.record.subject_id,
        revision=Present(head=record.record.head, fingerprint=record.record.fingerprint),
    )
    selected = RecoverySourceRecord(
        owner="effects",
        schema_id=record.schema_id,
        subject=marker,
        canonical_record_bytes=raw,
        physical_record=marker,
        selected_decision=CallSubjectHead(
            subject_id="decision", revision=Present(head="decision", fingerprint="d" * 64)
        ),
    )
    physical = CallSubjectHead(
        subject_id=record.record.head,
        revision=Present(head="record:" + digest, fingerprint=digest),
    )
    carrier = selected.model_copy(
        update={
            "subject": marker.model_copy(
                update={"revision": marker.revision.model_copy(update={"fingerprint": digest})}
            ),
            "physical_record": physical,
        }
    )
    body_alias = selected.model_copy(update={"physical_record": physical})
    assert carrier.subject != body_alias.subject
    if variant == "logical-child":
        child = selected.model_copy(
            update={"subject": marker.model_copy(update={"subject_id": "nested-evidence"})}
        )
        retained_child = child.model_copy(update={"physical_record": physical})
        wrong_child = retained_child.model_copy(
            update={
                "subject": retained_child.subject.model_copy(
                    update={"revision": marker.revision.model_copy(update={"fingerprint": digest})}
                )
            }
        )
        assert _accepted_source_preimages((child,), (retained_child, wrong_child)) == (
            retained_child,
        )
        with pytest.raises(ValueError, match="lacks one exact recovery preimage"):
            _accepted_source_preimages((child,), (wrong_child,))
    elif variant == "carrier-and-marker":
        assert _accepted_source_preimages((selected,), (carrier, body_alias)) == (carrier,)
    else:
        sources = (body_alias,) if variant == "marker-only" else (carrier, carrier, body_alias)
        with pytest.raises(ValueError, match="lacks one exact recovery preimage"):
            _accepted_source_preimages((selected,), sources)


@pytest.mark.parametrize("conflict", [False, True])
def test_original_terminal_aliases_share_one_exact_selected_decision(conflict) -> None:
    from chiplog.capabilities.agent_loop.execution_recovery_preparation import _source_for_revision
    from chiplog.capabilities.agent_loop.recovery_domain import RecoveryHold
    from chiplog.composition.r14_native_recovery_cut import _original_source_decisions
    from tests.support.capabilities.agent_loop.original_call_resolution_preparation import request

    original = request().cut.call_terminal
    physical = original.physical_record
    record = SimpleNamespace(
        record_id=physical.subject_id,
        owner=original.owner,
        schema_id=original.schema_id,
        canonical_bytes=original.canonical_record_bytes,
    )
    generic_decision = original.selected_decision.model_copy(update={"subject_id": "command-id"})
    raw = original.model_copy(
        update={
            "subject": physical,
            "selected_decision": generic_decision,
        }
    )
    cut = SimpleNamespace(complete_sources=(raw, original))
    with pytest.raises(RecoveryHold, match="missing or duplicate accepted-recovery source"):
        _source_for_revision(cut, original.subject.revision, original.schema_id)
    source_rows = (original, raw) if conflict else (original,)
    decisions = _original_source_decisions(
        source_rows, {record.record_id: (record, generic_decision)}
    )
    if conflict:
        assert decisions is None
    else:
        assert decisions == {record.record_id: original.selected_decision}
        raw = raw.model_copy(update={"selected_decision": decisions[record.record_id]})
        cut.complete_sources = (raw, original)
        assert _source_for_revision(cut, original.subject.revision, original.schema_id) == original


def test_closed_accepted_recovery_retains_only_exact_selected_open_preimages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.composition import r14_accepted_open_accounting_records as records
    from chiplog.composition import r14_original_obligation_sources as sources_module

    obligation = object()
    selected_command = SimpleNamespace()
    physical = tuple(
        SimpleNamespace(
            record_id=f"member-{index}",
            owner="agent_loop",
            schema=f"schema-{index}",
            raw=f"bytes-{index}".encode(),
        )
        for index in range(3)
    )
    evidence = SimpleNamespace(result=SimpleNamespace(opening=SimpleNamespace(original=obligation)))
    source_rows = (object(), object())
    monkeypatch.setattr(records, "ACCEPTED_OPEN_RECOVERY_OPERATION", "accepted-open-recovery")
    monkeypatch.setattr(
        records, "accepted_open_recovery_command", lambda _evidence: selected_command
    )
    monkeypatch.setattr(records, "retained_accepted_open_recovery", lambda _entry: evidence)
    monkeypatch.setattr(
        sources_module,
        "accepted_open_recovery_sources",
        lambda _evidence, **_kwargs: source_rows,
    )

    class Decisions:
        def entries(self):
            return (
                (
                    "decision-1",
                    "unused",
                    b'{"kind":"DECIDED","operation_kind":"accepted-open-recovery"}',
                ),
            )

    class Runtime:
        def _loop_decisions(self):
            return Decisions()

        def _publication(self, _entry):
            return selected_command

    selected_command.records = tuple(
        SimpleNamespace(
            record_id=row.record_id,
            owner=row.owner,
            schema_id=row.schema,
            canonical_bytes=row.raw,
        )
        for row in physical
    )
    inventory = SimpleNamespace(records=physical)

    result = _selected_accepted_open_recovery_preimages(Runtime(), inventory, obligation)

    assert result == (source_rows, frozenset({"member-0", "member-1", "member-2"}))
    tampered_row = SimpleNamespace(**(vars(physical[2]) | {"raw": b"tampered"}))
    tampered = SimpleNamespace(records=(physical[0], physical[1], tampered_row))
    assert _selected_accepted_open_recovery_preimages(Runtime(), tampered, obligation) is None

    class DuplicateDecisions(Decisions):
        def entries(self):
            return super().entries() * 2

    class DuplicateRuntime(Runtime):
        def _loop_decisions(self):
            return DuplicateDecisions()

    assert (
        _selected_accepted_open_recovery_preimages(DuplicateRuntime(), inventory, obligation)
        is None
    )


def _v3_route_cut(
    acceptance_type, disposition_type, *, closures=(), reductions=()
) -> ExecutionRecoveryCut:
    # Partial DTOs exercise only route selection; mounted tests authenticate full cuts.
    call = TerminalCallFrontier.model_construct(
        acceptance=acceptance_type.model_construct(),
        disposition=disposition_type.model_construct(),
    )
    return ExecutionRecoveryCut.model_construct(
        frontier=RecoveryFrontier.model_construct(ordered_calls=(call,)),
        original_closures=closures,
        current_reductions=reductions,
    )


def test_v3_readback_does_not_select_a_terminal_route_for_pending_retry() -> None:
    from tests.support.successor_records import pending

    cut = ExecutionRecoveryCut.model_construct(
        frontier=RecoveryFrontier.model_construct(ordered_calls=(pending(),)),
        original_closures=(),
        current_reductions=(),
    )
    assert _v3_accepted_cut_route(cut) is None


def test_v3_readback_routes_only_complete_direct_or_accepted_open_cuts() -> None:
    proof = (object(),)
    assert (
        _v3_accepted_cut_route(_v3_route_cut(ConsequentialAcceptedCall, SuccessResult))
        == "direct-terminal"
    )
    assert (
        _v3_accepted_cut_route(
            _v3_route_cut(
                ConsequentialAcceptedCall,
                RecoveryRequiredResult,
                closures=proof,
                reductions=proof,
            )
        )
        == "accepted-open"
    )
    assert (
        _v3_accepted_cut_route(_v3_route_cut(InitializedCall, CancelledBeforeAcceptResult))
        == "legacy-cancellation"
    )
    assert (
        _v3_accepted_cut_route(_v3_route_cut(ConsequentialAcceptedCall, RecoveryRequiredResult))
        is None
    )
    assert (
        _v3_accepted_cut_route(
            _v3_route_cut(
                ConsequentialAcceptedCall,
                RecoveryRequiredResult,
                closures=proof,
            )
        )
        is None
    )
    assert (
        _v3_accepted_cut_route(
            _v3_route_cut(
                ConsequentialAcceptedCall,
                RecoveryRequiredResult,
                reductions=proof,
            )
        )
        is None
    )
    assert _v3_accepted_cut_route(_v3_route_cut(ConsequentialAcceptedCall, UnknownResult)) is None


@pytest.mark.parametrize(
    "schema",
    [
        "chiplog.execution.accepted-open-recovery-opening.v1",
        "chiplog.execution.accepted-open-terminal-disposition.v1",
        "chiplog.loop.original-effect-evidence-reducer-policy.v1",
        "chiplog.loop.original-obligation-closure.v1",
        "chiplog.loop.original-resolution-basis.v1",
        "chiplog.loop.original-resolver-authority-policy.v1",
        "chiplog.loop.original-resolver-batch.v1",
        "chiplog.loop.original-resolver-invocation.v1",
        "chiplog.loop.recovered-call-outcome.v1",
        "chiplog.loop.semantic-reduction.v1",
    ],
)
def test_original_source_producer_joins_exact_selected_decision_head(schema: str) -> None:
    """Real source constructor, not proof of journal or native authority."""
    from chiplog.composition.r14_original_obligation_sources import selected_recovery_source
    from chiplog.platform._sqlite import PhysicalRecord

    raw = b'{"control":"selected-original"}'
    digest = hashlib.sha256(raw).hexdigest()
    decision = b'{"kind":"DECIDED","operation_id":"original-command"}'
    physical = CallSubjectHead(
        subject_id="original-record", revision=Present(head="record:" + digest, fingerprint=digest)
    )
    source = selected_recovery_source(
        PhysicalRecord("original-record", "agent_loop", schema, raw, digest),
        subject=physical,
        selected_decision_id="selected-decision",
        selected_decision_bytes=decision,
    )
    selected = CallSubjectHead(
        subject_id="original-command",
        revision=Present(
            head="selected-decision", fingerprint=hashlib.sha256(decision).hexdigest()
        ),
    )
    assert source.selected_decision.subject_id == selected.revision.head
    assert _selected_decision_matches_source(source, selected)


@pytest.mark.parametrize("damage", ["head", "fingerprint", "subject", "owner", "unknown"])
def test_original_source_decision_alias_rejects_substitutions(damage: str) -> None:
    selected = CallSubjectHead(
        subject_id="original-command",
        revision=Present(head="selected-decision", fingerprint="a" * 64),
    )
    source = SimpleNamespace(
        owner="agent_loop",
        schema_id="chiplog.execution.accepted-open-terminal-disposition.v1",
        selected_decision=selected.model_copy(update={"subject_id": "selected-decision"}),
    )
    if damage in {"head", "fingerprint"}:
        revision = source.selected_decision.revision.model_copy(
            update={damage: "foreign-head" if damage == "head" else "b" * 64}
        )
        source.selected_decision = source.selected_decision.model_copy(
            update={"revision": revision}
        )
    elif damage == "subject":
        source.selected_decision = source.selected_decision.model_copy(
            update={"subject_id": "foreign"}
        )
    elif damage == "owner":
        source.owner = "effects"
    else:
        source.schema_id = "chiplog.unknown.record.v1"
    # The partial source tests only the selected-decision alias, not source authentication.
    assert not _selected_decision_matches_source(
        RecoverySourceRecord.model_construct(**vars(source)), selected
    )


def test_original_resolver_anchor_producer_joins_selected_command_decision() -> None:
    from chiplog.composition.r14_loop_semantic_reduction_publication import _selected_anchor_sources
    from chiplog.composition.r14_original_call_resolution_publication import command
    from tests.support.composition.r14_original_call_resolution_publication import (
        publication_evidence,
    )

    retained = publication_evidence()
    batch = command(retained)
    raw = b'{"kind":"DECIDED","operation_id":"original-resolution"}'
    selected = CallSubjectHead(
        subject_id=batch.idempotency_key,
        revision=Present(head="selected-resolution", fingerprint=hashlib.sha256(raw).hexdigest()),
    )
    sources = _selected_anchor_sources(
        SimpleNamespace(), ("selected-resolution", raw, {}, batch), retained
    )
    assert len(sources) == 5
    for source in sources[:-1]:
        assert source.selected_decision.subject_id == selected.revision.head
        assert _selected_decision_matches_source(source, selected)


def test_r16_effect_source_joins_selected_decision_by_exact_head() -> None:
    journal_decision = CallSubjectHead(
        subject_id="dispatch-command",
        revision=Present(head="owner-decision", fingerprint="a" * 64),
    )
    semantic_decision = CallSubjectHead(
        subject_id="owner-decision",
        revision=Present(head="owner-decision", fingerprint="a" * 64),
    )
    # Unused source fields stay absent so this remains an isolated alias-join test.
    source = RecoverySourceRecord.model_construct(
        schema_id="chiplog.effects.dispatch-record.v2",
        selected_decision=semantic_decision,
    )

    assert _selected_decision_matches_source(source, journal_decision)
    assert _selected_decision_matches_source(
        RecoverySourceRecord.model_construct(
            schema_id="chiplog.effects.external-action-intent.v2",
            selected_decision=semantic_decision,
        ),
        journal_decision,
    )
    assert not _selected_decision_matches_source(
        RecoverySourceRecord.model_construct(
            schema_id="chiplog.effects.dispatch-record.v2",
            selected_decision=semantic_decision.model_copy(
                update={"revision": Present(head="other", fingerprint="a" * 64)}
            ),
        ),
        journal_decision,
    )
    assert not _selected_decision_matches_source(
        RecoverySourceRecord.model_construct(
            schema_id="chiplog.other.record.v1",
            selected_decision=semantic_decision,
        ),
        journal_decision,
    )


def test_external_intent_preimage_binds_nested_bytes_to_selected_dispatch_carrier() -> None:
    dispatch = original_send()
    raw = dispatch.canonical_bytes()
    physical = CallSubjectHead(
        subject_id=dispatch.record.subject_id,
        revision=Present(
            head=dispatch.record.head,
            fingerprint=dispatch.record.fingerprint,
        ),
    )
    selected_decision = CallSubjectHead(
        subject_id="selected-dispatch-decision",
        revision=Present(head="decision-head", fingerprint="a" * 64),
    )
    carrier = RecoverySourceRecord(
        owner="effects",
        subject=physical,
        schema_id=dispatch.schema_id,
        canonical_record_bytes=raw,
        selected_decision=selected_decision,
        physical_record=physical,
    )
    intent = dispatch.snapshot.intent
    expected = effects_reference(intent.intent_id, intent.canonical_bytes())
    reference = CallSubjectHead(
        subject_id=expected.subject_id,
        revision=Present(head=expected.head, fingerprint=expected.fingerprint),
    )
    member = SelectedEffectHistoryMember(
        kind=dispatch.kind,
        selected_decision=ExactHead(
            subject_id=selected_decision.subject_id,
            head=selected_decision.revision.head,
            fingerprint=selected_decision.revision.fingerprint,
        ),
        publication_ordinal=0,
        original_selected_cut=ExactHead(
            subject_id="selected-cut", head="cut-head", fingerprint="f" * 64
        ),
        record=ExactHead(
            subject_id=dispatch.record.subject_id,
            head=dispatch.record.head,
            fingerprint=dispatch.record.fingerprint,
        ),
        predecessor=dispatch.predecessor,
        intent=expected,
        semantics=intent.mandate.semantics,
        command_bytes=b"selected command",
        record_bytes=raw,
    )

    source = _selected_external_intent_preimage(reference, dispatch, (carrier,), (member,))

    assert source is not None
    assert source.subject == reference
    assert source.schema_id == intent.schema_id
    assert source.canonical_record_bytes == intent.canonical_bytes()
    assert source.selected_decision == selected_decision
    assert source.physical_record == physical
    row = (dispatch.record.head, "effects", dispatch.schema_id, raw, 3)
    assert _selected_source_carrier_id(source) == dispatch.record.head
    assert _external_intent_matches_dispatch_carrier(source, row)

    wrong_reference = reference.model_copy(
        update={"revision": Present(head=reference.revision.head, fingerprint="b" * 64)}
    )
    assert (
        _selected_external_intent_preimage(wrong_reference, dispatch, (carrier,), (member,)) is None
    )
    assert (
        _selected_external_intent_preimage(
            reference,
            dispatch,
            (carrier.model_copy(update={"canonical_record_bytes": b"{}"}),),
            (member,),
        )
        is None
    )
    altered_intent = original_intent(members=("different-child",))
    altered_dispatch = dispatch.model_copy(
        update={"snapshot": dispatch.snapshot.model_copy(update={"intent": altered_intent})}
    )
    assert (
        _selected_external_intent_preimage(reference, altered_dispatch, (carrier,), (member,))
        is None
    )
    assert not _external_intent_matches_dispatch_carrier(
        source.model_copy(update={"canonical_record_bytes": b"different intent"}), row
    )
    assert not _external_intent_matches_dispatch_carrier(
        source.model_copy(
            update={
                "subject": reference.model_copy(
                    update={
                        "revision": Present(
                            head=reference.revision.head,
                            fingerprint="b" * 64,
                        )
                    }
                )
            }
        ),
        row,
    )
    assert not _external_intent_matches_dispatch_carrier(
        source,
        ("another-inventory-row", *row[1:]),
    )
    substituted_carrier = carrier.model_copy(
        update={
            "physical_record": CallSubjectHead(
                subject_id="foreign-record",
                revision=Present(head="foreign-head", fingerprint="c" * 64),
            )
        }
    )
    assert (
        _selected_external_intent_preimage(reference, dispatch, (substituted_carrier,), (member,))
        is None
    )
    substituted_member = member.model_copy(
        update={
            "selected_decision": member.selected_decision.model_copy(
                update={"fingerprint": "e" * 64}
            )
        }
    )
    assert (
        _selected_external_intent_preimage(reference, dispatch, (carrier,), (substituted_member,))
        is None
    )


def test_r17_scope_children_are_exactly_bound_to_authenticated_trust_carrier() -> None:
    from chiplog.composition.r14_native_recovery_cut import _ref

    scope_bytes = b"authenticated scope"
    admission_bytes = b"admission"
    recipient_bytes = b"recipient"
    policy_bytes = b"policy"
    scope_id = "scope-1"
    scope_head = "scope-head"
    decision = _ref("trust-decision", b"decision", "decision-head")
    physical = _ref("trust-record", b"scope physical", "physical-head")
    admission_ref = SimpleNamespace(
        identity="selected-admission",
        head="admission-head",
        fingerprint=hashlib.sha256(admission_bytes).hexdigest(),
    )
    policy_revision = Present(
        head="policy-head", fingerprint=hashlib.sha256(policy_bytes).hexdigest()
    )
    policy_ref = CallSubjectHead(
        subject_id="native-one-call-context-policy", revision=policy_revision
    )
    recipient = SimpleNamespace(canonical_bytes=lambda: recipient_bytes)
    scope = SimpleNamespace(
        schema_id="chiplog.native-one-call-context-scope.v1",
        scope_id=scope_id,
        canonical_bytes=lambda: scope_bytes,
        selected_admission_bytes=admission_bytes,
        selected_admission_ref=admission_ref,
        recipient=recipient,
        disclosure_policy=SimpleNamespace(canonical_source_bytes=policy_bytes, ref=policy_ref),
    )
    scope_source = RecoverySourceRecord(
        owner="deployment_trust",
        subject=_ref(scope_id, scope_bytes, scope_head),
        schema_id=scope.schema_id,
        canonical_record_bytes=scope_bytes,
        selected_decision=decision,
        physical_record=physical,
    )
    children = (
        RecoverySourceRecord(
            owner="deployment_trust",
            subject=_ref("selected-admission", admission_bytes, admission_ref.head),
            schema_id="chiplog.native-recovery.selected-admission.v1",
            canonical_record_bytes=admission_bytes,
            selected_decision=decision,
            physical_record=physical,
        ),
        RecoverySourceRecord(
            owner="deployment_trust",
            subject=_ref(
                "native-recipient:" + hashlib.sha256(recipient_bytes).hexdigest(),
                recipient_bytes,
            ),
            schema_id="chiplog.native-recovery.recipient.v1",
            canonical_record_bytes=recipient_bytes,
            selected_decision=decision,
            physical_record=physical,
        ),
        RecoverySourceRecord(
            owner="deployment_trust",
            subject=_ref(
                "native-context-policy:" + hashlib.sha256(policy_bytes).hexdigest(),
                policy_bytes,
                policy_revision.head,
            ),
            schema_id="chiplog.native-one-call-context-policy.v1",
            canonical_record_bytes=policy_bytes,
            selected_decision=decision,
            physical_record=physical,
        ),
    )
    cut = SimpleNamespace(complete_sources=(scope_source, *children))

    assert _authenticated_scope_sources(cut, scope_source, SimpleNamespace(scope=scope))
    tampered = children[-1].model_copy(update={"canonical_record_bytes": b"altered"})
    tampered_cut = SimpleNamespace(complete_sources=(scope_source, *children[:-1], tampered))
    assert not _authenticated_scope_sources(
        tampered_cut, scope_source, SimpleNamespace(scope=scope)
    )


@pytest.mark.parametrize(
    "schema_id",
    (
        "chiplog.execution.accepted-open-accounting.v1",
        "chiplog.execution.accepted-open-recovery-opening.v1",
        "chiplog.loop.original-resolver-batch.v1",
        "chiplog.loop.semantic-reduction.v1",
        "chiplog.loop.semantic-reducer-policy.v1",
    ),
)
def test_direct_selector_holds_rival_r14_schema_under_foreign_operation(schema_id: str) -> None:
    raw = json.dumps({"kind": "DECIDED"}).encode()
    command = SimpleNamespace(
        operation_kind="custom.foreign-operation.v1",
        records=(SimpleNamespace(schema_id=schema_id),),
    )
    log = SimpleNamespace(entries=lambda: (("decision", "unused", raw),))
    runtime = SimpleNamespace(_loop_decisions=lambda: log, _publication=lambda entry: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert claimed
    assert selected is None


@pytest.mark.parametrize(
    ("operation", "schema_id"),
    (
        (
            "agent_loop.accepted-open-accounting.v1",
            "chiplog.execution.accepted-open-accounting.v1",
        ),
        (
            "agent_loop.accepted-open-recovery.v1",
            "chiplog.execution.accepted-open-recovery-opening.v1",
        ),
        (
            "agent_loop.original-call-resolution.v1",
            "chiplog.loop.original-resolver-resolution.v1",
        ),
        (
            "agent_loop.loop-semantic-reduction.v1",
            "chiplog.loop.semantic-reduction.v1",
        ),
        (
            "agent_loop.original-resolver-authority-policy.v1",
            "chiplog.loop.original-resolver-authority-policy.v1",
        ),
        (
            "agent_loop.original-resolver-invocation.v1",
            "chiplog.loop.original-resolver-invocation.v1",
        ),
    ),
)
def test_direct_selector_routes_selected_recovery_operations_to_recovery_path(
    operation: str, schema_id: str
) -> None:
    raw = json.dumps({"kind": "DECIDED"}).encode()
    command = SimpleNamespace(
        operation_kind=operation,
        records=(SimpleNamespace(schema_id=schema_id),),
    )
    log = SimpleNamespace(entries=lambda: (("decision", "unused", raw),))
    runtime = SimpleNamespace(_loop_decisions=lambda: log, _publication=lambda entry: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert not claimed
    assert selected is None


def test_direct_selector_holds_recovery_route_competing_with_direct_terminal_marker() -> None:
    recovery_raw = json.dumps({"kind": "DECIDED", "route": "recovery"}).encode()
    terminal_raw = json.dumps(
        {
            "kind": "DECIDED",
            "route": "terminal",
            "accepted_direct_terminal_evidence": "retained",
        }
    ).encode()
    commands = {
        "recovery": SimpleNamespace(
            operation_kind="agent_loop.original-resolver-authority-policy.v1",
            records=(
                SimpleNamespace(schema_id="chiplog.loop.original-resolver-authority-policy.v1"),
            ),
        ),
        "terminal": SimpleNamespace(
            operation_kind="custom.foreign-operation.v1",
            records=(),
        ),
    }
    entries = (
        ("recovery", "unused", recovery_raw),
        ("terminal", "unused", terminal_raw),
    )
    log = SimpleNamespace(entries=lambda: entries)
    runtime = SimpleNamespace(
        _loop_decisions=lambda: log,
        _publication=lambda entry: commands[entry["route"]],
    )

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert claimed
    assert selected is None


def test_direct_selector_holds_terminal_marker_embedded_in_recovery_operation() -> None:
    raw = json.dumps({"kind": "DECIDED", "accepted_direct_terminal_evidence": "retained"}).encode()
    command = SimpleNamespace(
        operation_kind="agent_loop.original-resolver-authority-policy.v1",
        records=(SimpleNamespace(schema_id="chiplog.loop.original-resolver-authority-policy.v1"),),
    )
    log = SimpleNamespace(entries=lambda: (("decision", "unused", raw),))
    runtime = SimpleNamespace(_loop_decisions=lambda: log, _publication=lambda entry: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert claimed
    assert selected is None


def test_direct_selector_holds_retained_rival_marker_under_foreign_operation() -> None:
    raw = json.dumps({"kind": "DECIDED", "original_call_resolution_evidence": "retained"}).encode()
    command = SimpleNamespace(operation_kind="custom.foreign-operation.v1", records=())
    log = SimpleNamespace(entries=lambda: (("decision", "unused", raw),))
    runtime = SimpleNamespace(_loop_decisions=lambda: log, _publication=lambda entry: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert claimed
    assert selected is None


def test_direct_selector_forwards_exact_authenticated_history_from_subset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.composition import r14_accepted_direct_terminal_records as records

    operation = records.ACCEPTED_DIRECT_TERMINAL_OPERATION
    command = SimpleNamespace(operation_kind=operation, records=())
    raw = json.dumps({"kind": "DECIDED"}).encode()
    log = SimpleNamespace(entries=lambda: (("terminal", "unused", raw),))
    history = (object(), object(), (), (), (), ())
    captured = []
    runtime = SimpleNamespace(
        _loop_decisions=lambda: log,
        _publication=lambda entry: command,
        read_selected_accepted_direct_terminal_frontier=lambda **kwargs: (
            captured.append(kwargs["authenticated_history"]) or None
        ),
    )
    subset = SimpleNamespace(
        accepted_closed_outcome=object(), authenticated_execution_history=history
    )
    evidence = object()
    monkeypatch.setattr(records, "retained_accepted_direct_terminal", lambda entry: evidence)
    monkeypatch.setattr(records, "accepted_direct_terminal_command", lambda retained: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, subset
    )

    assert (claimed, selected) == (True, None)
    assert len(captured) == 1
    assert captured[0] is history


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", (True, False))
async def test_selected_native_cut_reopens_and_legacy_prepare_holds(
    tmp_path: Path, bound: bool
) -> None:
    database = tmp_path / "native-cut.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    source = NativeRecoveryCutSource()
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        await publish_native_recovery_profile(runtime)
        await selected_native_cancelled(runtime, bound=bound)
        await runtime.publish_native_one_call_cancelled_accounting()
        if not bound:
            assert source.collect(runtime) is None
        # Call the internal method here so failure diagnostics cannot be hidden
        # behind the public HOLD projection while the positive path is mounted.
        with runtime._authority_gate().hold():
            cut = source._collect_held(runtime)
        if bound:
            assert isinstance(cut, ExecutionRecoveryCut)
            validate_native_recovery_cut(cut)
            cross_family_bindings = cut.current_bindings.model_copy(
                update={"semantic_bindings": (cut.current_bindings.policy,)}
            )
            cross_family_cut = cut.model_copy(update={"current_bindings": cross_family_bindings})
            cross_family_value = cross_family_cut.model_dump(mode="json")
            del cross_family_value["complete_inventory_fingerprint"]
            cross_family_cut = cross_family_cut.model_copy(
                update={
                    "complete_inventory_fingerprint": native_recovery_fingerprint(
                        "cut", cross_family_value
                    )
                }
            )
            with pytest.raises(
                ValueError,
                match="native frozen binding is outside its registered family: semantic_bindings",
            ):
                validate_native_recovery_cut(cross_family_cut)
            omitted_family_frontier = cut.frontier.model_copy(
                update={"ordered_members": cut.frontier.ordered_members[:-1]}
            )
            frontier_value = omitted_family_frontier.model_dump(mode="json")
            del frontier_value["fingerprint"]
            omitted_family_frontier = omitted_family_frontier.model_copy(
                update={"fingerprint": native_recovery_fingerprint("frontier", frontier_value)}
            )
            omitted_family_cut = cut.model_copy(update={"frontier": omitted_family_frontier})
            cut_value = omitted_family_cut.model_dump(mode="json")
            del cut_value["complete_inventory_fingerprint"]
            omitted_family_cut = omitted_family_cut.model_copy(
                update={
                    "complete_inventory_fingerprint": native_recovery_fingerprint("cut", cut_value)
                }
            )
            with pytest.raises(
                ValueError, match="native cut family cardinality or ordering differs"
            ):
                validate_native_recovery_cut(omitted_family_cut)
            profileless_v2_cut = cut.model_copy(
                update={
                    "complete_sources": tuple(
                        row
                        for row in cut.complete_sources
                        if row.schema_id
                        not in (
                            "chiplog.execution.native-one-call-recovery-profile.v1",
                            "chiplog.execution.native-one-call-recovery-profile.v2",
                        )
                    )
                }
            )
            rejected = prepare_execution_accounting(
                PrepareExecutionAccounting(command_id="profileless-v2", cut=profileless_v2_cut)
            )
            assert isinstance(rejected, CallPreparationRejected)
            assert rejected.code == "HOLD"
            assert rejected.reason == (
                "V2 registry requires exactly one selected V2/V3/V4/V5 profile"
            )
            with pytest.raises(ValueError, match="member lacks"):
                validate_native_recovery_cut(
                    cut.model_copy(
                        update={
                            "complete_sources": tuple(
                                row
                                for row in cut.complete_sources
                                if row.schema_id != "chiplog.native-recovery.turn.v1"
                            )
                        }
                    )
                )
            with runtime._authority_gate().hold():
                assert source.matches_current_held(runtime, cut)
                assert not source.matches_current_held(
                    runtime,
                    cut.model_copy(
                        update={
                            "complete_sources": cut.complete_sources[:-1],
                        }
                    ),
                )
        else:
            assert cut is None
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        assert source.collect(runtime) == cut
        if bound:
            assert isinstance(cut, ExecutionRecoveryCut)
            with runtime._authority_gate().hold():
                candidate, inert_entry = inert_recovery_candidate(runtime, cut)
                assert not source.authenticate_selected_held(runtime, candidate, inert_entry)
            evidence = await runtime.publish_execution_accounting_readiness()
            assert evidence.accounting_request.cut == cut
            with runtime._authority_gate().hold():
                import json

                from chiplog.composition.r14_recovery_publication import OPERATION

                entry = next(
                    json.loads(raw)
                    for _, _, raw in runtime._loop_decisions().entries()
                    if json.loads(raw).get("operation_kind") == OPERATION
                )
                assert source.authenticate_selected_held(runtime, evidence, entry)
            resulting_cut = source.collect(runtime)
            assert resulting_cut is not None
            assert resulting_cut.tenant_commit_sequence == cut.tenant_commit_sequence + 1
            assert len(resulting_cut.complete_sources) == len(cut.complete_sources) + 2
    if not bound:
        return
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
        recovery_cut_source=source,
    ) as runtime:
        assert source.collect(runtime) == resulting_cut
        assert await runtime.publish_execution_accounting_readiness() == evidence
        # A different selected and physical custody token is evidence even
        # when it has no row in evidence_inbox; it cannot inherit ABSENT.
        runtime.provision_retained("foreign-recovery-evidence", b"foreign evidence")
        await runtime.allocate_receipt("foreign-recovery-evidence")
        assert source.collect(runtime) is None
