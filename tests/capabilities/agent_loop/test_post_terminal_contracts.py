"""Public consumers of work ownership; no fixture is authenticated runtime state."""

import json

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.execution_fan_out import fixture
from tests.support.fan_out_shapes import head

from chiplog.capabilities.agent_loop import post_terminal_contracts as work
from chiplog.capabilities.agent_loop import recovery_contracts as recovery
from chiplog.capabilities.agent_loop.scheduler_leases import IssuedLeaseObservation
from chiplog.capabilities.agent_loop.scheduler_rollover import IssuedRolloverObservation


def present(name: str) -> recovery.Present:
    return recovery.Present(head=name, fingerprint="a" * 64)


def view() -> work.PostTerminalWorkView:
    obligation = recovery.OriginalObligationBinding(
        original_run_id="run",
        original_call_id="call",
        obligation_id="obligation",
        obligation_stream_id="original-stream",
        obligation_head="obligation-head",
        closure_predicate_id="all-original-children",
        closure_predicate_version="1",
        resolver_id="original-resolver",
        resolver_version="1",
        reducer_id="original-reducer",
        reducer_version="1",
        evidence_stream_id="original-evidence",
        evidence_head=recovery.Absent(),
    )
    return work.PostTerminalWorkView(
        subject=recovery.WorkSubjectBinding(
            work_id="work",
            work_subject_head="subject-head",
            work_subject_fingerprint="b" * 64,
            terminal_manifest_head="terminal",
            terminal_manifest_member_fingerprint="c" * 64,
            original_obligation=obligation,
        ),
        work_epoch=recovery.WorkEpochBinding(
            selector_id="selector",
            selector_head="selector-head",
            selector_version=0,
            current_epoch_id="epoch",
            current_epoch_head="epoch-head",
        ),
        work_state_head=present("state"),
        lease=work.UnleasedWork(lease_head="genesis"),
        predecessor_rollover=recovery.Absent(),
    )


def lease(generation: int = 1) -> recovery.LeaseBinding:
    return recovery.LeaseBinding(
        lease_head="lease-head",
        holder_id="holder",
        holder_session_id="session",
        lease_id="lease",
        generation=generation,
        trusted_expiry=100,
        clock_contract_version="1",
    )


def proof() -> recovery.TrustedClockProofRef:
    return recovery.TrustedClockProofRef(
        proof_id="proof",
        proof_fingerprint="a" * 64,
        proof_version="1",
        clock_contract_version="1",
        fence_fingerprint="b" * 64,
        command_id="command",
        command_payload_fingerprint="c" * 64,
        submission_id="submission",
    )


async def test_work_requests_preserve_original_streams_and_binary_exchange() -> None:
    current = view()
    identity = work.WorkCommandIdentity(tenant_id="tenant", command_id="command")
    authority = recovery.RolloverAuthorityRef(
        proof_id="rollover",
        proof_fingerprint="d" * 64,
        authority_head="authority",
        command_id="command",
        command_payload_fingerprint="e" * 64,
        predecessor_rollover=recovery.Absent(),
    )
    original = await fixture()
    requests = (
        work.PrepareTerminalWork(
            identity=identity,
            terminal_run=original.captured_run,
            original_terminalization_request=b"\xff\x00original-owner-request",
            terminal_manifest=head("terminal"),
            ordered_open_obligations=(current.subject.original_obligation,),
        ),
        *(
            work.PrepareWorkLease(
                identity=identity,
                operation=operation,
                expected=current,
                current_original_obligation=present("obligation-head"),
                current_original_evidence=recovery.Absent(),
                proposed_holder_id="holder",
                proposed_holder_session_id="session",
                proposed_lease_id="new-lease",
                proposed_generation=1,
                proposed_expiry=100,
                clock_proof=proof(),
                issued=IssuedLeaseObservation(
                    proof=proof(),
                    now=10,
                    max_lease_duration=100,
                    holder_id="holder",
                    holder_session_id="session",
                    authority_epoch="authority",
                    used_lease_ids=(),
                ),
                submission_id="submission",
            )
            for operation in ("CLAIM", "RENEW", "TAKEOVER")
        ),
        work.PrepareWorkRollover(
            identity=identity,
            expected=current,
            fence=recovery.WorkEpochRolloverFence(
                subject=current.subject,
                work_epoch=current.work_epoch,
                exhaustion=recovery.ExhaustionBinding(
                    hold_head="hold",
                    exhausted_command_id="takeover",
                    lease=lease(2**64 - 1),
                    authority_epoch="authority",
                ),
                authority=authority,
            ),
            current_original_obligation=present("obligation-head"),
            current_original_evidence=present("evidence-head"),
            issued=IssuedRolloverObservation(
                authority=authority,
                snapshot_fingerprint="f" * 64,
                submission_id="submission",
                authority_epoch="authority",
                used_epoch_ids=("epoch",),
                used_lease_heads=("genesis",),
            ),
        ),
        work.PrepareWorkClose(
            identity=identity,
            expected=current,
            exact_obligation_terminal_head=present("closed"),
            original_resolver_batch=present("resolver-batch"),
        ),
    )
    adapter: TypeAdapter[work.PostTerminalWorkRequest] = TypeAdapter(work.PostTerminalWorkRequest)
    for request in requests:
        restored = adapter.validate_json(request.canonical_bytes())
        assert restored == request
        assert restored.canonical_bytes() == request.canonical_bytes()
        wire = json.loads(request.canonical_bytes())
        wire["publication_authorized"] = True
        with pytest.raises(ValidationError):
            adapter.validate_json(json.dumps(wire))
    # The synthetic RENEW/rollover from genesis is representable but not authorized.
    # Actual owner state predicates and broker admission are deliberately not asserted here.
    assert isinstance(requests[0], work.PrepareTerminalWork)
    assert requests[0].original_terminalization_request == b"\xff\x00original-owner-request"


@pytest.mark.parametrize(
    "state",
    [
        work.UnleasedWork(lease_head="genesis"),
        work.ClaimedWork(binding=lease()),
        work.ExhaustedWork(
            lease_head="hold",
            exhausted_command_id="command",
            trusted_expiry=100,
            authority_epoch="authority",
            preceding_claimed_lease=lease(2**64 - 1),
        ),
        work.ClosedWork(exact_obligation_terminal_head=present("closed")),
    ],
)
def test_closed_work_states_roundtrip_and_reject_hybrids(state: recovery.RecoveryDTO) -> None:
    adapter: TypeAdapter[work.PostTerminalWorkLeaseState] = TypeAdapter(
        work.PostTerminalWorkLeaseState
    )
    assert adapter.validate_json(state.canonical_bytes()) == state
    wire = json.loads(state.canonical_bytes())
    wire["kind"] = "TRANSFERRED"
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(wire))
    wire = json.loads(state.canonical_bytes())
    wire["arbitrary_lease"] = "head"
    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(wire))


@pytest.mark.parametrize("generation", [-1, 1, 2**64, False, "0"])
def test_genesis_does_not_coerce_or_accept_nonzero_generation(generation: object) -> None:
    with pytest.raises(ValidationError):
        work.UnleasedWork.model_validate({"lease_head": "genesis", "generation": generation})


@pytest.mark.parametrize("generation", [-1, 0, 2**64, True, "18446744073709551615"])
def test_exhaustion_is_exact_uint64_max(generation: object) -> None:
    with pytest.raises(ValidationError):
        work.ExhaustedWork.model_validate(
            {
                "lease_head": "hold",
                "generation": generation,
                "exhausted_command_id": "command",
                "trusted_expiry": 100,
                "authority_epoch": "authority",
                "preceding_claimed_lease": lease(2**64 - 1),
            }
        )


def test_work_result_preserves_full_order_and_excludes_fake_success() -> None:
    member = work.WorkCanonicalMember(
        record_kind="LEASE",
        record_id="lease",
        schema_id="work.lease.v1",
        canonical_record_bytes=b"\xff\x00untrusted-owner-bytes",
        fingerprint="a" * 64,
    )
    result = work.PreparedPostTerminalWork(
        source_request_fingerprint="b" * 64,
        ordered_work=(view(),),
        complete_records=(member,),
        complete_commitment="c" * 64,
    )
    adapter: TypeAdapter[work.PostTerminalWorkResult] = TypeAdapter(work.PostTerminalWorkResult)
    assert adapter.validate_json(result.canonical_bytes()) == result
    rejected = work.WorkPreparationRejected(code="STALE", reason="selector changed")
    assert adapter.validate_json(rejected.canonical_bytes()) == rejected
    with pytest.raises(ValidationError):
        adapter.validate_json('{"kind":"COMMITTED","command_id":"command"}')
