"""Mounted selected native full-cut boundary; inert DTOs never grant authority."""

import hashlib
import json
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
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
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
    RecoveryRequiredResult,
    SuccessResult,
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
    _selected_reduction_sources,
)
from chiplog.composition.r14_native_recovery_historical import (
    _authenticated_scope_sources,
    _external_intent_matches_dispatch_carrier,
    _selected_decision_matches_source,
    _selected_source_carrier_id,
    _v3_accepted_cut_route,
)
from chiplog.composition.r14_recovery_profile_publication import publish_native_recovery_profile
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.capabilities.effects.support.dispatch_outcomes import original_intent, original_send
from tests.support.native_cancelled import native_response, selected_native_cancelled
from tests.support.recovery_candidate import inert_recovery_candidate


def test_selected_reduction_source_projection_keeps_the_complete_chain(monkeypatch):
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


def test_closed_accepted_recovery_retains_only_exact_selected_open_preimages(monkeypatch):
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


def _v3_route_cut(acceptance_type, disposition_type, *, closures=(), reductions=()):
    call = SimpleNamespace(
        acceptance=acceptance_type.model_construct(),
        disposition=disposition_type.model_construct(),
    )
    return SimpleNamespace(
        frontier=SimpleNamespace(ordered_calls=(call,)),
        original_closures=closures,
        current_reductions=reductions,
    )


def test_v3_readback_routes_only_complete_direct_or_accepted_open_cuts():
    proof = (object(),)
    assert (
        _v3_accepted_cut_route(
            _v3_route_cut(ConsequentialAcceptedCall, SuccessResult)
        )
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
        _v3_accepted_cut_route(
            _v3_route_cut(InitializedCall, CancelledBeforeAcceptResult)
        )
        == "legacy-cancellation"
    )
    assert (
        _v3_accepted_cut_route(
            _v3_route_cut(ConsequentialAcceptedCall, RecoveryRequiredResult)
        )
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
    assert (
        _v3_accepted_cut_route(
            _v3_route_cut(ConsequentialAcceptedCall, UnknownResult)
        )
        is None
    )


def test_r16_effect_source_joins_selected_decision_by_exact_head():
    journal_decision = CallSubjectHead(
        subject_id="dispatch-command",
        revision=Present(head="owner-decision", fingerprint="a" * 64),
    )
    semantic_decision = CallSubjectHead(
        subject_id="owner-decision",
        revision=Present(head="owner-decision", fingerprint="a" * 64),
    )
    source = SimpleNamespace(
        schema_id="chiplog.effects.dispatch-record.v2",
        selected_decision=semantic_decision,
    )

    assert _selected_decision_matches_source(source, journal_decision)
    assert _selected_decision_matches_source(
        SimpleNamespace(
            schema_id="chiplog.effects.external-action-intent.v2",
            selected_decision=semantic_decision,
        ),
        journal_decision,
    )
    assert not _selected_decision_matches_source(
        SimpleNamespace(
            schema_id="chiplog.effects.dispatch-record.v2",
            selected_decision=semantic_decision.model_copy(
                update={"revision": Present(head="other", fingerprint="a" * 64)}
            ),
        ),
        journal_decision,
    )
    assert not _selected_decision_matches_source(
        SimpleNamespace(
            schema_id="chiplog.other.record.v1",
            selected_decision=semantic_decision,
        ),
        journal_decision,
    )


def test_external_intent_preimage_binds_nested_bytes_to_selected_dispatch_carrier():
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
    assert _selected_external_intent_preimage(
        wrong_reference, dispatch, (carrier,), (member,)
    ) is None
    assert _selected_external_intent_preimage(
        reference,
        dispatch,
        (carrier.model_copy(update={"canonical_record_bytes": b"{}"}),),
        (member,),
    ) is None
    altered_intent = original_intent(members=("different-child",))
    altered_dispatch = dispatch.model_copy(
        update={
            "snapshot": dispatch.snapshot.model_copy(update={"intent": altered_intent})
        }
    )
    assert _selected_external_intent_preimage(
        reference, altered_dispatch, (carrier,), (member,)
    ) is None
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
    assert _selected_external_intent_preimage(
        reference, dispatch, (substituted_carrier,), (member,)
    ) is None
    substituted_member = member.model_copy(
        update={
            "selected_decision": member.selected_decision.model_copy(
                update={"fingerprint": "e" * 64}
            )
        }
    )
    assert _selected_external_intent_preimage(
        reference, dispatch, (carrier,), (substituted_member,)
    ) is None


def test_r17_scope_children_are_exactly_bound_to_authenticated_trust_carrier():
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
        disclosure_policy=SimpleNamespace(
            canonical_source_bytes=policy_bytes, ref=policy_ref
        ),
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
                "native-recipient:"
                + hashlib.sha256(recipient_bytes).hexdigest(),
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
                "native-context-policy:"
                + hashlib.sha256(policy_bytes).hexdigest(),
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
def test_direct_selector_holds_rival_r14_schema_under_foreign_operation(schema_id: str):
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
):
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


def test_direct_selector_holds_recovery_route_competing_with_direct_terminal_marker():
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
                SimpleNamespace(
                    schema_id="chiplog.loop.original-resolver-authority-policy.v1"
                ),
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


def test_direct_selector_holds_terminal_marker_embedded_in_recovery_operation():
    raw = json.dumps(
        {"kind": "DECIDED", "accepted_direct_terminal_evidence": "retained"}
    ).encode()
    command = SimpleNamespace(
        operation_kind="agent_loop.original-resolver-authority-policy.v1",
        records=(
            SimpleNamespace(schema_id="chiplog.loop.original-resolver-authority-policy.v1"),
        ),
    )
    log = SimpleNamespace(entries=lambda: (("decision", "unused", raw),))
    runtime = SimpleNamespace(_loop_decisions=lambda: log, _publication=lambda entry: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert claimed
    assert selected is None


def test_direct_selector_holds_retained_rival_marker_under_foreign_operation():
    raw = json.dumps({"kind": "DECIDED", "original_call_resolution_evidence": "retained"}).encode()
    command = SimpleNamespace(operation_kind="custom.foreign-operation.v1", records=())
    log = SimpleNamespace(entries=lambda: (("decision", "unused", raw),))
    runtime = SimpleNamespace(_loop_decisions=lambda: log, _publication=lambda entry: command)

    claimed, selected = NativeRecoveryCutSource._selected_accepted_direct_terminal_inputs(
        runtime, None
    )

    assert claimed
    assert selected is None


def test_direct_selector_forwards_exact_authenticated_history_from_subset(monkeypatch):
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
async def test_selected_native_cut_reopens_and_legacy_prepare_holds(tmp_path: Path, bound: bool):
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
            assert cut is not None
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
                update={
                    "fingerprint": native_recovery_fingerprint("frontier", frontier_value)
                }
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
