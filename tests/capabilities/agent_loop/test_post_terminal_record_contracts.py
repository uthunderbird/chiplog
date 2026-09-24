"""Shape evidence for durable post-terminal companion records."""

import hashlib
import json

import pytest
from tests.support.execution_fan_out import fixture as execution_fixture
from tests.support.fan_out_shapes import head

from chiplog.capabilities.agent_loop import post_terminal_contracts as work
from chiplog.capabilities.agent_loop import post_terminal_record_contracts as records
from chiplog.capabilities.agent_loop import recovery_contracts as recovery
from chiplog.capabilities.agent_loop.scheduler_rollover import IssuedRolloverObservation


def ref(head: str) -> recovery.Present:
    return recovery.Present(head=head, fingerprint=hashlib.sha256(head.encode()).hexdigest())


def member(value: records.WorkDurableRecord) -> work.WorkCanonicalMember:
    raw = value.canonical_bytes()
    return work.WorkCanonicalMember(
        record_kind=value.record_kind,
        record_id=value.record_id,
        schema_id=value.schema_id,
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


def lease_record() -> records.PostTerminalLeaseRecord:
    return records.PostTerminalLeaseRecord(
        tenant_id="tenant",
        command_id="command",
        record_id="lease-head",
        work_id="work",
        epoch=ref("epoch-head"),
        selector=ref("selector-head"),
        predecessor_lease=recovery.Absent(),
        lease=work.UnleasedWork(lease_head="lease-head"),
        original_obligation=ref("obligation-head"),
        original_evidence=recovery.Absent(),
        resolver_batch=recovery.Absent(),
    )


def test_canonical_lease_member_decodes_and_retains_supplied_bytes() -> None:
    supplied = member(lease_record())
    decoded = records.decode_work_canonical_member(supplied)
    assert decoded.member is supplied
    assert decoded.record == lease_record()
    assert decoded.record.lease.kind == "UNLEASED"


@pytest.mark.parametrize("mutation", ["kind", "schema", "record_id", "fingerprint", "extra"])
def test_decoder_fails_closed_on_bad_envelope_or_noncanonical_payload(mutation: str) -> None:
    supplied = member(lease_record())
    if mutation == "kind":
        supplied = supplied.model_copy(update={"record_kind": "EDGE"})
    elif mutation == "schema":
        supplied = supplied.model_copy(update={"schema_id": "chiplog.post-terminal.lease.v2"})
    elif mutation == "record_id":
        supplied = supplied.model_copy(update={"record_id": "rival"})
    elif mutation == "fingerprint":
        supplied = supplied.model_copy(update={"fingerprint": "0" * 64})
    else:
        payload = json.loads(supplied.canonical_record_bytes)
        payload["unexpected"] = True
        raw = json.dumps(payload, separators=(",", ":")).encode()
        supplied = supplied.model_copy(
            update={"canonical_record_bytes": raw, "fingerprint": hashlib.sha256(raw).hexdigest()}
        )
    with pytest.raises(records.PostTerminalRecordIntegrityError):
        records.decode_work_canonical_member(supplied)


def test_decoder_rejects_self_fingerprint_reference() -> None:
    value = lease_record()
    value = value.model_copy(update={"epoch": ref(value.record_id)})
    supplied = member(value)
    with pytest.raises(records.PostTerminalRecordIntegrityError):
        records.decode_work_canonical_member(supplied)


def obligation(
    evidence: recovery.Absent | recovery.Present | None = None,
) -> recovery.OriginalObligationBinding:
    return recovery.OriginalObligationBinding(
        original_run_id="run",
        original_call_id="call",
        obligation_id="obligation",
        obligation_stream_id="obligation-stream",
        obligation_head="obligation-head",
        closure_predicate_id="predicate",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id="reducer",
        reducer_version="1",
        evidence_stream_id="evidence-stream",
        evidence_head=recovery.Absent() if evidence is None else evidence,
    )


def genesis_graph(
    request: work.PrepareTerminalWork, source: recovery.OriginalObligationBinding
) -> work.PreparedPostTerminalWork:
    subject_record = records.PostTerminalSubjectRecord(
        tenant_id="tenant",
        command_id="command",
        record_id="subject",
        work_id="work",
        terminal_run_head=request.terminal_run.head,
        terminal_manifest_head=request.terminal_manifest.revision.head,
        terminal_manifest_member_fingerprint=request.terminal_manifest.revision.fingerprint,
        original_obligation=source,
    )
    subject_member = member(subject_record)
    epoch_record = records.PostTerminalEpochRecord(
        tenant_id="tenant",
        command_id="command",
        record_id="epoch-head",
        work_id="work",
        epoch_id="epoch",
        subject=ref_from(subject_member),
        predecessor_epoch=recovery.Absent(),
    )
    epoch_member = member(epoch_record)
    selector_record = records.PostTerminalSelectorRecord(
        tenant_id="tenant",
        command_id="command",
        record_id="selector-head",
        work_id="work",
        selector_id="selector",
        selector_version=0,
        selected_epoch_id="epoch",
        selected_epoch=ref_from(epoch_member),
        predecessor_selector=recovery.Absent(),
    )
    selector_member = member(selector_record)
    lease_record = records.PostTerminalLeaseRecord(
        tenant_id="tenant",
        command_id="command",
        record_id="lease-head",
        work_id="work",
        epoch=ref_from(epoch_member),
        selector=ref_from(selector_member),
        predecessor_lease=recovery.Absent(),
        lease=work.UnleasedWork(lease_head="lease-head"),
        original_obligation=recovery.Present(head=source.obligation_head, fingerprint="b" * 64),
        original_evidence=source.evidence_head,
        resolver_batch=recovery.Absent(),
    )
    lease_member = member(lease_record)
    view = work.PostTerminalWorkView(
        subject=recovery.WorkSubjectBinding(
            work_id="work",
            work_subject_head="subject",
            work_subject_fingerprint=subject_member.fingerprint,
            terminal_manifest_head=request.terminal_manifest.revision.head,
            terminal_manifest_member_fingerprint=request.terminal_manifest.revision.fingerprint,
            original_obligation=source,
        ),
        work_epoch=recovery.WorkEpochBinding(
            selector_id="selector",
            selector_head="selector-head",
            selector_version=0,
            current_epoch_id="epoch",
            current_epoch_head="epoch-head",
        ),
        work_state_head=ref_from(lease_member),
        lease=lease_record.lease,
        predecessor_rollover=recovery.Absent(),
    )
    return work.PreparedPostTerminalWork(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        ordered_work=(view,),
        complete_records=(subject_member, epoch_member, selector_member, lease_member),
        complete_commitment="c" * 64,
    )


def ref_from(value: work.WorkCanonicalMember) -> recovery.Present:
    return recovery.Present(head=value.record_id, fingerprint=value.fingerprint)


async def test_nonempty_terminal_genesis_joins_exact_original_stream_and_records() -> None:
    captured = (await execution_fixture()).captured_run
    run = captured.model_copy(
        update={"state": "SUCCEEDED", "event": "Terminalized", "head": "terminal-run-head"}
    )
    source = obligation()
    request = work.PrepareTerminalWork(
        identity=work.WorkCommandIdentity(tenant_id="tenant", command_id="command"),
        terminal_run=run,
        original_terminalization_request=b"\xfforiginal-owner-request",
        terminal_manifest=head("terminal"),
        ordered_open_obligations=(source,),
    )
    result = genesis_graph(request, source)
    records.validate_prepared_post_terminal_work(request, result)
    restored = records.decode_work_canonical_member(result.complete_records[0]).record
    assert isinstance(restored, records.PostTerminalSubjectRecord)
    assert restored.original_obligation == source
    assert restored.terminal_run_head == run.head
    assert result.complete_records[0].canonical_record_bytes == restored.canonical_bytes()


@pytest.mark.asyncio
async def test_genesis_graph_rejects_missing_duplicate_reordered_and_obligation_substitution() -> (
    None
):
    captured = (await execution_fixture()).captured_run
    run = captured.model_copy(
        update={"state": "SUCCEEDED", "event": "Terminalized", "head": "terminal-run-head"}
    )
    source = obligation()
    request = work.PrepareTerminalWork(
        identity=work.WorkCommandIdentity(tenant_id="tenant", command_id="command"),
        terminal_run=run,
        original_terminalization_request=b"raw",
        terminal_manifest=head("terminal"),
        ordered_open_obligations=(source,),
    )
    result = genesis_graph(request, source)
    subject_record = records.decode_work_canonical_member(result.complete_records[0]).record
    assert isinstance(subject_record, records.PostTerminalSubjectRecord)
    substituted = member(
        subject_record.model_copy(
            update={"original_obligation": source.model_copy(update={"obligation_id": "rival"})}
        )
    )
    invalid = (
        result.model_copy(update={"complete_records": result.complete_records[:-1]}),
        result.model_copy(
            update={"complete_records": result.complete_records + result.complete_records[:1]}
        ),
        result.model_copy(update={"complete_records": tuple(reversed(result.complete_records))}),
        result.model_copy(update={"complete_records": (substituted, *result.complete_records[1:])}),
    )
    for mutated in invalid:
        with pytest.raises(records.PostTerminalRecordIntegrityError):
            records.validate_prepared_post_terminal_work(request, mutated)


def test_rollover_and_close_graph_consumer_shapes() -> None:
    evidence = ref("evidence")
    original = obligation(evidence)
    subject = recovery.WorkSubjectBinding(
        work_id="work",
        work_subject_head="subject",
        work_subject_fingerprint="a" * 64,
        terminal_manifest_head="terminal",
        terminal_manifest_member_fingerprint="b" * 64,
        original_obligation=original,
    )
    epoch = recovery.WorkEpochBinding(
        selector_id="selector",
        selector_head="old-selector",
        selector_version=1,
        current_epoch_id="old-epoch",
        current_epoch_head="old-epoch-head",
    )
    binding = recovery.LeaseBinding(
        lease_head="claimed",
        holder_id="holder",
        holder_session_id="session",
        lease_id="lease",
        generation=2**64 - 1,
        trusted_expiry=9,
        clock_contract_version="clock",
    )
    expected = work.PostTerminalWorkView(
        subject=subject,
        work_epoch=epoch,
        work_state_head=ref("old-lease"),
        lease=work.ExhaustedWork(
            lease_head="old-lease",
            exhausted_command_id="exhausted",
            trusted_expiry=9,
            authority_epoch="authority",
            preceding_claimed_lease=binding,
        ),
        predecessor_rollover=recovery.Absent(),
    )
    authority = recovery.RolloverAuthorityRef(
        proof_id="proof",
        proof_fingerprint="c" * 64,
        authority_head="authority",
        command_id="command",
        command_payload_fingerprint="d" * 64,
        predecessor_rollover=recovery.Absent(),
    )
    fence = recovery.WorkEpochRolloverFence(
        subject=subject,
        work_epoch=epoch,
        exhaustion=recovery.ExhaustionBinding(
            hold_head="old-lease",
            exhausted_command_id="exhausted",
            lease=binding,
            authority_epoch="authority",
        ),
        authority=authority,
    )
    issued = IssuedRolloverObservation(
        authority=authority,
        snapshot_fingerprint="e" * 64,
        submission_id="submission",
        authority_epoch="authority",
        used_epoch_ids=("old-epoch",),
        used_lease_heads=("old-lease",),
    )
    request = work.PrepareWorkRollover(
        identity=work.WorkCommandIdentity(tenant_id="tenant", command_id="command"),
        expected=expected,
        fence=fence,
        current_original_obligation=ref("obligation-head"),
        current_original_evidence=evidence,
        issued=issued,
    )
    new_epoch = member(
        records.PostTerminalEpochRecord(
            tenant_id="tenant",
            command_id="command",
            record_id="new-epoch-head",
            work_id="work",
            epoch_id="new-epoch",
            subject=ref("subject"),
            predecessor_epoch=ref("old-epoch-head"),
        )
    )
    selector = member(
        records.PostTerminalSelectorRecord(
            tenant_id="tenant",
            command_id="command",
            record_id="new-selector-head",
            work_id="work",
            selector_id="selector",
            selector_version=2,
            selected_epoch_id="new-epoch",
            selected_epoch=ref_from(new_epoch),
            predecessor_selector=ref("old-selector"),
        )
    )
    new_lease = member(
        records.PostTerminalLeaseRecord(
            tenant_id="tenant",
            command_id="command",
            record_id="new-lease",
            work_id="work",
            epoch=ref_from(new_epoch),
            selector=ref_from(selector),
            predecessor_lease=expected.work_state_head,
            lease=work.UnleasedWork(lease_head="new-lease"),
            original_obligation=request.current_original_obligation,
            original_evidence=evidence,
            resolver_batch=recovery.Absent(),
        )
    )
    decision = member(
        records.PostTerminalRolloverRecord(
            tenant_id="tenant",
            command_id="command",
            record_id="decision",
            subject=ref("subject"),
            old_epoch=ref("old-epoch-head"),
            new_epoch=ref_from(new_epoch),
            old_selector=ref("old-selector"),
            new_selector=ref_from(selector),
            old_lease=expected.work_state_head,
            new_lease=ref_from(new_lease),
            fence=fence,
            issued=issued,
            current_original_obligation=request.current_original_obligation,
            current_original_evidence=evidence,
        )
    )
    edge = member(
        records.PostTerminalEdgeRecord(
            tenant_id="tenant",
            command_id="command",
            record_id="edge",
            rollover_decision=ref_from(decision),
            old_epoch=ref("old-epoch-head"),
            new_epoch=ref_from(new_epoch),
            old_selector=ref("old-selector"),
            new_selector=ref_from(selector),
            old_lease=expected.work_state_head,
            new_lease=ref_from(new_lease),
            predecessor_rollover=recovery.Absent(),
        )
    )
    rolled = expected.model_copy(
        update={
            "work_epoch": recovery.WorkEpochBinding(
                selector_id="selector",
                selector_head="new-selector-head",
                selector_version=2,
                current_epoch_id="new-epoch",
                current_epoch_head="new-epoch-head",
            ),
            "work_state_head": ref_from(new_lease),
            "lease": work.UnleasedWork(lease_head="new-lease"),
            "predecessor_rollover": recovery.RolloverPredecessor(
                decision=ref_from(decision), edge=ref_from(edge)
            ),
        }
    )
    result = work.PreparedPostTerminalWork(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        ordered_work=(rolled,),
        complete_records=(new_epoch, selector, new_lease, decision, edge),
        complete_commitment="f" * 64,
    )
    records.validate_prepared_post_terminal_work(request, result)
    lease_record = records.decode_work_canonical_member(result.complete_records[2]).record
    assert isinstance(lease_record, records.PostTerminalLeaseRecord)
    stale_lease = member(lease_record.model_copy(update={"predecessor_lease": ref("stale")}))
    with pytest.raises(records.PostTerminalRecordIntegrityError):
        records.validate_prepared_post_terminal_work(
            request,
            result.model_copy(
                update={
                    "complete_records": (
                        *result.complete_records[:2],
                        stale_lease,
                        *result.complete_records[3:],
                    )
                }
            ),
        )
    close = work.PrepareWorkClose(
        identity=request.identity,
        expected=rolled,
        exact_obligation_terminal_head=ref("terminal-obligation"),
        original_resolver_batch=ref("resolver-batch"),
    )
    closed = member(
        records.PostTerminalLeaseRecord(
            tenant_id="tenant",
            command_id="command",
            record_id="close-head",
            work_id="work",
            epoch=ref("new-epoch-head"),
            selector=ref("new-selector-head"),
            predecessor_lease=rolled.work_state_head,
            lease=work.ClosedWork(
                exact_obligation_terminal_head=close.exact_obligation_terminal_head
            ),
            original_obligation=ref("obligation-head"),
            original_evidence=evidence,
            resolver_batch=close.original_resolver_batch,
        )
    )
    closed_view = rolled.model_copy(
        update={
            "work_state_head": ref_from(closed),
            "lease": closed.record_id
            and work.ClosedWork(
                exact_obligation_terminal_head=close.exact_obligation_terminal_head
            ),
        }
    )
    close_result = work.PreparedPostTerminalWork(
        source_request_fingerprint=hashlib.sha256(close.canonical_bytes()).hexdigest(),
        ordered_work=(closed_view,),
        complete_records=(closed,),
        complete_commitment="f" * 64,
    )
    records.validate_prepared_post_terminal_work(close, close_result)
    stale = new_lease.model_copy(
        update={"canonical_record_bytes": new_lease.canonical_record_bytes}
    )
    assert stale.record_id == "new-lease"
