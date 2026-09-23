"""Pure owner proposals over consistent supplied observations, never publication proof."""

import base64
import hashlib
import json
from typing import Literal

import pytest

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.call_acceptance_preparation import (
    call_record_reference,
    call_subject_id,
    prepare_consequential_acceptance,
    prepare_pre_accept_cancellation,
)
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    ExecutionLineageBinding,
    IndividualSubject,
    LeaseBinding,
    NonSchedulerFence,
    NotApplicable,
    PhysicalRootBinding,
    Present,
    RecoveryDTO,
    SchedulerExecutionFence,
    TrustedClockProofRef,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    ConsequentialAcceptedCall,
    InitializedCall,
    ReadOnlyRetryLineage,
)


def _wire(value: object) -> bytes:
    def ordered(item: object) -> object:
        if isinstance(item, dict):
            return {key: ordered(item[key]) for key in sorted(item, key=lambda k: (k != "kind", k))}
        if isinstance(item, list):
            return [ordered(child) for child in item]
        return item

    return json.dumps(ordered(value), ensure_ascii=False, separators=(",", ":")).encode()


def _digest(value: RecoveryDTO) -> str:
    return hashlib.sha256(_wire(value.model_dump(mode="json"))).hexdigest()


def _reference(subject: str, record: RecoveryDTO) -> call.CallSubjectHead:
    digest = _digest(record)
    return call.CallSubjectHead(
        subject_id=subject, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def _source(subject: str) -> call.CallSubjectHead:
    fingerprint = hashlib.sha256(subject.encode()).hexdigest()
    return call.CallSubjectHead(
        subject_id=subject, revision=Present(head="record:" + fingerprint, fingerprint=fingerprint)
    )


def _initialized(
    ordinal: int, classification: Literal["CONSEQUENTIAL", "READ_ONLY"]
) -> call.InitializedCallRecord:
    original = call.OriginalCallKey(
        tenant_id="tenant",
        original_run_id="original-run",
        original_turn_id="turn",
        captured_response=_source("capture"),
        ordinal=ordinal,
        model_call_label=f"tool-{ordinal}",
    )
    identity = "call:" + _digest(original)
    lineage = call.InitializedReadOnlyLineage(
        lineage=ReadOnlyRetryLineage(
            lineage_id="retry:" + identity,
            original_call_id=identity,
            max_attempts=3,
            budget_version="budget-v1",
            reducer_id="readonly",
            reducer_version="v1",
        )
    )
    return call.InitializedCallRecord(
        original_call_id=identity,
        call=call.SealedCallInput(
            original=original,
            classification=classification,
            tool_schema=_source("schema"),
            tool_policy=_source("policy"),
            canonical_call_base64=base64.b64encode(b'{"tool":"example"}').decode(),
            retry_lineage=lineage if classification == "READ_ONLY" else NotApplicable(),
        ),
        predecessor=Absent(),
    )


def _observation(record: call.InitializedCallRecord) -> call.CallLifecycleObservation:
    reference = _reference(record.original_call_id, record)
    return call.CallLifecycleObservation(
        original_call_id=record.original_call_id,
        initialized=reference,
        initialized_record=record,
        acceptance=InitializedCall(initialized=reference.revision),
        terminal=Absent(),
    )


def _inventory_cut(
    cut: call.CallPreparationCut, rows: tuple[call.CallLifecycleObservation, ...]
) -> call.CallPreparationCut:
    inventory = call.CallInventorySnapshot(
        tenant_id=cut.tenant_id,
        tenant_commit_sequence=cut.tenant_commit_sequence,
        ordered_calls=tuple(sorted(rows, key=lambda row: row.original_call_id)),
    )
    return cut.model_copy(
        update={
            "predecessor_inventory": inventory,
            "complete_call_inventory": _reference("call-inventory:" + cut.tenant_id, inventory),
        }
    )


def _requests() -> tuple[call.AcceptConsequentialCallRequest, call.CancelBeforeAcceptRequest]:
    target, sibling = _initialized(0, "CONSEQUENTIAL"), _initialized(1, "READ_ONLY")
    inventory = call.CallInventorySnapshot(
        tenant_id="tenant",
        tenant_commit_sequence=8,
        ordered_calls=tuple(
            sorted((_observation(target), _observation(sibling)), key=lambda r: r.original_call_id)
        ),
    )
    current = _source("successor-run")
    cut = call.CallPreparationCut(
        tenant_id="tenant",
        current_run=current,
        run_state="ACTIVE",
        tenant_commit_sequence=8,
        materialization_commitment="a" * 64,
        predecessor_inventory=inventory,
        complete_call_inventory=_reference("call-inventory:tenant", inventory),
        authority_registry=_source("registry"),
        sources=(
            call.CallAuthorityObservation(
                source_id="actor",
                family="ACTOR",
                source=_source("actor"),
                generation="generation",
                frontier="frontier",
                canonical_value_base64=base64.b64encode(b"actor").decode(),
                observed_at_ns=10,
                valid_until_ns=20,
            ),
        ),
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=current.subject_id,
            run_head=current.revision.head,
            worker_session_id="worker",
            runtime_generation="generation",
        ),
    )
    reference = _reference(target.original_call_id, target)
    binding = call.ConsequentialAcceptanceBinding(
        original_call_id=target.original_call_id,
        original=target.call.original,
        initialized=reference,
        tool_schema=target.call.tool_schema,
        tool_policy=target.call.tool_policy,
        external_intent=_source("external-intent"),
        cut=cut,
        dispatch_semantics=call.CallDispatchSemantics(
            normative_manifest=_source("normative"),
            reducer=_source("reducer"),
            transition_registry=_source("transitions"),
            canonicalization=_source("codec"),
            adapter_contract=_source("adapter"),
        ),
    )
    return (
        call.AcceptConsequentialCallRequest(
            command_id="accept", binding=binding, initialized_record=target
        ),
        call.CancelBeforeAcceptRequest(
            command_id="cancel",
            original_call_id=target.original_call_id,
            original=target.call.original,
            initialized=reference,
            initialized_record=target,
            cancellation_act=_source("cancel-act"),
            cut=cut,
        ),
    )


def _assert_proposal_digest(proposal: RecoveryDTO) -> None:
    body = proposal.model_dump(mode="json")
    fingerprint = body.pop("proposal_fingerprint")
    assert fingerprint == hashlib.sha256(_wire(body)).hexdigest()


def _both_reject(cut: call.CallPreparationCut, code: str) -> None:
    accept, cancel = _requests()
    accepted = prepare_consequential_acceptance(
        accept.model_copy(
            update={
                "command_id": "fresh-accept",
                "binding": accept.binding.model_copy(update={"cut": cut}),
            }
        )
    )
    cancelled = prepare_pre_accept_cancellation(
        cancel.model_copy(update={"command_id": "fresh-cancel", "cut": cut})
    )
    for result in (accepted, cancelled):
        assert isinstance(result, call.CallPreparationRejected), result
        assert result.code == code


def test_acceptance_is_deterministic_and_has_exact_acyclic_three_member_manifest() -> None:
    request, _ = _requests()
    original_bytes = request.canonical_bytes()
    result = prepare_consequential_acceptance(request)
    assert isinstance(result, call.PreparedConsequentialAcceptance), result
    assert prepare_consequential_acceptance(request) == result
    assert request.canonical_bytes() == original_bytes
    digest = _digest(request)
    assert result.source_request_fingerprint == digest
    assert result.accepted.accepted_id == "accepted:" + digest
    assert result.execution_intent.execution_intent_id == "execution:" + digest
    assert result.accepted.binding == request.binding
    assert result.execution_intent.original_call_id == request.binding.original_call_id
    assert result.execution_intent.initialized == request.binding.initialized
    assert result.execution_intent.accepted == _reference(
        result.accepted.accepted_id, result.accepted
    )
    assert result.complete_acceptance_manifest == (
        _reference(result.accepted.accepted_id, result.accepted),
        _reference(result.execution_intent.execution_intent_id, result.execution_intent),
        request.binding.external_intent,
    )
    assert request.binding.original.original_run_id == "original-run"
    assert request.binding.cut.current_run.subject_id == "successor-run"
    assert call_subject_id(request.binding.original) == "call:" + _digest(request.binding.original)
    assert (
        call_record_reference(request.binding.original_call_id, request.initialized_record)
        == request.binding.initialized
    )
    _assert_proposal_digest(result)


def test_cancellation_is_deterministic_not_executed_with_exact_two_member_manifest() -> None:
    _, request = _requests()
    result = prepare_pre_accept_cancellation(request)
    assert isinstance(result, call.PreparedPreAcceptCancellation), result
    assert prepare_pre_accept_cancellation(request) == result
    digest = _digest(request)
    assert result.source_request_fingerprint == digest
    assert result.terminal.terminal_id == "cancelled:" + digest
    assert result.result.result_id == "not-executed:" + digest
    assert result.result.outcome == "NOT_EXECUTED"
    assert result.terminal.initialized == result.result.initialized == request.initialized
    assert result.result.terminal == _reference(result.terminal.terminal_id, result.terminal)
    assert result.complete_ordered_record_manifest == (
        _reference(result.terminal.terminal_id, result.terminal),
        _reference(result.result.result_id, result.result),
    )
    _assert_proposal_digest(result)


@pytest.mark.parametrize("branch", ("accepted", "cancelled"))
def test_fresh_requests_cannot_accept_or_cancel_consumed_original_predecessor(branch: str) -> None:
    request, cancellation = _requests()
    cut = request.binding.cut
    rows = []
    for row in cut.predecessor_inventory.ordered_calls:
        if row.original_call_id == request.binding.original_call_id:
            if branch == "accepted":
                prepared = prepare_consequential_acceptance(request)
                assert isinstance(prepared, call.PreparedConsequentialAcceptance)
                refs = prepared.complete_acceptance_manifest
                row = row.model_copy(
                    update={
                        "acceptance": ConsequentialAcceptedCall(
                            initialized=row.initialized.revision,
                            accepted=refs[0].revision,
                            execution_intent=refs[1].revision,
                            external_effect_intent=refs[2].revision,
                            complete_acceptance_manifest=tuple(ref.revision for ref in refs),
                        )
                    }
                )
            else:
                cancelled = prepare_pre_accept_cancellation(cancellation)
                assert isinstance(cancelled, call.PreparedPreAcceptCancellation)
                row = row.model_copy(
                    update={
                        "terminal": _reference(
                            cancelled.terminal.terminal_id, cancelled.terminal
                        ).revision
                    }
                )
        rows.append(row)
    _both_reject(_inventory_cut(cut, tuple(rows)), "CONFLICT")


@pytest.mark.parametrize(
    "mutation", ("tenant", "source_duplicate", "invalid_base64", "source_interval", "run_head")
)
def test_bad_cut_observations_reject_both_proposals(mutation: str) -> None:
    accept, _ = _requests()
    cut = accept.binding.cut
    source = cut.sources[0]
    if mutation == "tenant":
        cut = cut.model_copy(update={"tenant_id": "foreign"})
    elif mutation == "source_duplicate":
        cut = cut.model_copy(update={"sources": (source, source)})
    elif mutation == "invalid_base64":
        cut = cut.model_copy(
            update={"sources": (source.model_copy(update={"canonical_value_base64": "!!!"}),)}
        )
    elif mutation == "source_interval":
        cut = cut.model_copy(
            update={
                "sources": (source.model_copy(update={"valid_until_ns": source.observed_at_ns}),)
            }
        )
    else:
        cut = cut.model_copy(update={"current_run": _source("other-run")})
    _both_reject(cut, "STALE" if mutation in {"source_interval", "run_head"} else "INTEGRITY_FAULT")


@pytest.mark.parametrize("mutation", ("original_id", "record_head"))
def test_changed_requested_original_or_record_reference_rejects_both(mutation: str) -> None:
    accept, cancel = _requests()
    update = (
        {"original_call_id": "other-call"}
        if mutation == "original_id"
        else {"initialized": _source("other-record")}
    )
    result = prepare_consequential_acceptance(
        accept.model_copy(
            update={
                "binding": accept.binding.model_copy(update=update),
            }
        )
    )
    cancelled = prepare_pre_accept_cancellation(cancel.model_copy(update=update))
    for value in (result, cancelled):
        assert isinstance(value, call.CallPreparationRejected)
        assert value.code == "INTEGRITY_FAULT"


@pytest.mark.parametrize("mutation", ("foreign_tenant", "readonly_lineage", "call_bytes"))
def test_rehashed_nontarget_sibling_is_fully_validated(mutation: str) -> None:
    accept, _ = _requests()
    cut = accept.binding.cut
    rows = []
    for row in cut.predecessor_inventory.ordered_calls:
        if row.original_call_id != accept.binding.original_call_id:
            record = row.initialized_record
            sealed = record.call
            if mutation == "foreign_tenant":
                sealed = sealed.model_copy(
                    update={"original": sealed.original.model_copy(update={"tenant_id": "foreign"})}
                )
                record = record.model_copy(
                    update={"original_call_id": "call:" + _digest(sealed.original)}
                )
            elif mutation == "readonly_lineage":
                lineage = sealed.retry_lineage
                assert isinstance(lineage, call.InitializedReadOnlyLineage)
                sealed = sealed.model_copy(
                    update={
                        "retry_lineage": lineage.model_copy(
                            update={
                                "lineage": lineage.lineage.model_copy(
                                    update={"original_call_id": "foreign-call"}
                                ),
                            }
                        )
                    }
                )
            else:
                sealed = sealed.model_copy(update={"canonical_call_base64": "not-base64!"})
            record = record.model_copy(update={"call": sealed})
            row = _observation(record)
        rows.append(row)
    # Hashes and initialized references are repaired, so the non-target body's
    # invalid meaning must be noticed even though the requested target is valid.
    _both_reject(_inventory_cut(cut, tuple(rows)), "INTEGRITY_FAULT")


def test_acceptance_cannot_replace_initialized_tool_policy() -> None:
    accept, _ = _requests()
    changed = accept.model_copy(
        update={"binding": accept.binding.model_copy(update={"tool_policy": _source("new-policy")})}
    )
    result = prepare_consequential_acceptance(changed)
    assert isinstance(result, call.CallPreparationRejected)
    assert result.code == "STALE"


@pytest.mark.parametrize("field,value", (("run_state", "TERMINAL"), ("tenant_commit_sequence", -1)))
def test_unchecked_nested_model_copy_is_revalidated(field: str, value: object) -> None:
    accept, _ = _requests()
    _both_reject(accept.binding.cut.model_copy(update={field: value}), "INTEGRITY_FAULT")


def test_scheduler_execution_fence_is_explicitly_unsupported() -> None:
    accept, _ = _requests()
    fence = SchedulerExecutionFence(
        run_head=accept.binding.cut.current_run.revision.head,
        lineage=ExecutionLineageBinding(
            root_id="root",
            subject=IndividualSubject(occurrence_id="occurrence"),
            root_fingerprint="a" * 64,
            lineage_head="lineage",
            initial_run_id="original-run",
            current_run_id="successor-run",
            schedule_id="schedule",
            schedule_revision="schedule-v1",
            policy_revision="policy-v1",
        ),
        physical_root=PhysicalRootBinding(
            selector_id="selector",
            selector_head="selector-head",
            selector_version=1,
            current_epoch_id="epoch",
            current_epoch_head="epoch-head",
        ),
        lease=LeaseBinding(
            lease_head="lease-head",
            holder_id="holder",
            holder_session_id="holder-session",
            lease_id="lease",
            generation=1,
            trusted_expiry=20,
            clock_contract_version="clock-v1",
        ),
        clock_proof=TrustedClockProofRef(
            proof_id="proof",
            proof_fingerprint="a" * 64,
            proof_version="v1",
            clock_contract_version="clock-v1",
            fence_fingerprint="b" * 64,
            command_id="command",
            command_payload_fingerprint="c" * 64,
            submission_id="submission",
        ),
    )
    _both_reject(accept.binding.cut.model_copy(update={"fence": fence}), "UNSUPPORTED")


@pytest.mark.parametrize("external_present", (False, True))
def test_rehashed_proposal_only_sibling_cannot_have_consequential_acceptance(
    external_present: bool,
) -> None:
    accept, _ = _requests()
    cut = accept.binding.cut
    rows = []
    for row in cut.predecessor_inventory.ordered_calls:
        if row.original_call_id != accept.binding.original_call_id:
            record = row.initialized_record
            record = record.model_copy(
                update={
                    "call": record.call.model_copy(
                        update={
                            "classification": "PROPOSAL_ONLY",
                            "retry_lineage": NotApplicable(),
                        }
                    )
                }
            )
            row = _observation(record)
            accepted = _source("sibling-accepted").revision
            execution = _source("sibling-execution").revision
            external = _source("sibling-external").revision if external_present else NotApplicable()
            manifest: tuple[Present, ...] = (accepted, execution)
            if isinstance(external, Present):
                manifest += (external,)
            row = row.model_copy(
                update={
                    "acceptance": ConsequentialAcceptedCall(
                        initialized=row.initialized.revision,
                        accepted=accepted,
                        execution_intent=execution,
                        external_effect_intent=external,
                        complete_acceptance_manifest=manifest,
                    )
                }
            )
        rows.append(row)
    # Both the changed original record reference and complete inventory hash are
    # valid, and the acceptance manifest matches its own two/three references.
    # Only the supplied sibling's incompatible lifecycle classification is wrong.
    _both_reject(_inventory_cut(cut, tuple(rows)), "INTEGRITY_FAULT")
