"""Independent retained-output verification and physical publication byte accounting."""

import base64
import hashlib
import json

import pytest

from chiplog.adapters.driven.r9_fence import CONVERSATION_OWNER, CONVERSATION_SCHEMA
from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop import contracts as loop
from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.fan_out_contracts import CapturedFanOutProposal
from chiplog.capabilities.agent_loop.fan_out_preparation import prepare_captured_fan_out
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present, RecoveryDTO
from chiplog.capabilities.agent_loop.response_parsing import parse_captured_response
from chiplog.composition.r14_fanout_contracts import (
    RetainedFanOutPreparation,
)
from chiplog.composition.r14_fanout_records import (
    build_envelope,
    inventory_from_history,
    physical_command,
    reference,
)
from chiplog.platform.broker import BrokerSession
from tests.support.captured_fan_out import bind_run as _bind_run
from tests.support.captured_fan_out import fixture as _fixture


def _hash(value: RecoveryDTO, field: str) -> str:
    wire = json.loads(value.canonical_bytes())
    del wire[field]
    return hashlib.sha256(
        json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def _signed(value: CapturedFanOutProposal) -> CapturedFanOutProposal:
    inner = value.fan_out.model_copy(
        update={"proposal_fingerprint": _hash(value.fan_out, "proposal_fingerprint")}
    )
    value = value.model_copy(update={"fan_out": inner})
    return value.model_copy(update={"proposal_fingerprint": _hash(value, "proposal_fingerprint")})


async def _evidence(complete: bool = False, delivery: bool = False) -> RetainedFanOutPreparation:
    request = await _fixture(delivery=delivery, complete=complete)
    turn = request.captured_run.turns[-1]
    attempt = turn.attempts[-1]
    worker = "1:0:owner-session"
    member = loop.VisibilityMember(
        record_id="context",
        revision_head="context-head",
        content="Help",
        provenance_head="provenance",
        label_head="label",
        label=attempt.manifest.joined_label,
        producer="fixture",
        surface="context",
    )
    manifest = attempt.manifest.model_copy(update={"worker_session": worker, "members": (member,)})
    attempt = attempt.model_copy(update={"worker_session": worker, "manifest": manifest})
    run = request.captured_run.model_copy(
        update={
            "worker_session": worker,
            "turns": (turn.model_copy(update={"attempts": (attempt,)}),),
        }
    )
    cut = request.request.cut.model_copy(
        update={
            "fence": request.request.cut.fence.model_copy(update={"worker_session_id": worker}),
            "authority_registry": request.tool_registry_head,
            "sources": (
                call.CallAuthorityObservation(
                    source_id=request.tool_registry.registry_id,
                    family="TOOL_SCHEMA",
                    source=reference(request.tool_registry.registry_id, request.tool_registry),
                    generation="0",
                    frontier="1",
                    canonical_value_base64=base64.b64encode(
                        request.tool_registry.canonical_bytes()
                    ).decode(),
                    observed_at_ns=1,
                    valid_until_ns=10,
                ),
            ),
        }
    )
    request = request.model_copy(
        update={"request": request.request.model_copy(update={"cut": cut})}
    )
    request = _bind_run(request, run)
    proposal = prepare_captured_fan_out(request)
    assert isinstance(proposal, CapturedFanOutProposal), proposal
    captured = request.captured_run
    response = parse_captured_response(
        base64.b64decode(request.request.canonical_response_base64), manifest.artifact
    )
    companions: tuple[loop.DurableCompanion, ...]
    if isinstance(response, loop.Continue):
        accepted = domain.accept_tools(captured, response)
        companions = ()
    else:
        assert isinstance(response, loop.Complete)
        accepted = domain.complete(captured, response)
        companions = (
            loop.DurableCompanion(
                record_id="run/accepted",
                owner=CONVERSATION_OWNER,
                schema_id=CONVERSATION_SCHEMA,
                payload_base64=base64.b64encode(b'{"retained":"original"}').decode(),
            ),
        )
    snapshot = loop.LoopSnapshot(tenant_head=1, records=(captured,))
    return RetainedFanOutPreparation(
        request=request,
        proposal=proposal,
        accepted_run=accepted,
        companions=companions,
        expected_snapshot_fingerprint=snapshot.digest(),
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="0",
            owner_id="broker",
            session_id="broker",
        ),
        callee=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="0",
            owner_id="agent_loop",
            session_id="owner-session",
        ),
        request_id="request",
        deadline_ns=10,
    )


def _bound(
    evidence: RetainedFanOutPreparation, field: str, maximum: int
) -> RetainedFanOutPreparation:
    request = evidence.request
    inner = request.request.model_copy(
        update={"bound": request.request.bound.model_copy(update={field: maximum})}
    )
    request = request.model_copy(update={"request": inner})
    proposal = prepare_captured_fan_out(request)
    assert isinstance(proposal, CapturedFanOutProposal), proposal
    return evidence.model_copy(update={"request": request, "proposal": proposal})


@pytest.mark.parametrize("complete,delivery", ((False, False), (False, True), (True, False)))
async def test_complete_physical_order_hashes_and_exact_payloads(
    complete: bool, delivery: bool
) -> None:
    evidence = await _evidence(complete, delivery)
    envelope = build_envelope(evidence)
    assert envelope == build_envelope(evidence)
    assert envelope.request_fingerprint == _hash(envelope, "request_fingerprint")
    assert envelope.idempotency_key == evidence.accepted_run.head
    prepared = evidence.proposal.fan_out
    expected = [
        evidence.accepted_run.canonical_bytes(),
        prepared.response_seal.canonical_bytes(),
        *(item.canonical_bytes() for item in prepared.initialized_records),
        *(base64.b64decode(item.payload_base64) for item in evidence.companions),
    ]
    assert [
        base64.b64decode(item.canonical_payload_base64) for item in envelope.records
    ] == expected
    assert envelope.records[1].record_id == "record:" + prepared.response_seal.digest()
    assert tuple(
        item.record_id for item in envelope.records[2 : 2 + len(prepared.initialized_records)]
    ) == tuple("record:" + item.digest() for item in prepared.initialized_records)
    command = physical_command(envelope)
    assert [item.canonical_bytes for item in command.records] == expected
    assert command.request_fingerprint == envelope.request_fingerprint
    assert command.expected_head == evidence.request.request.cut.tenant_commit_sequence
    assert command.admission_guard is None and command.decision_guard is None


@pytest.mark.parametrize(
    "defect", ("order", "classification", "call_text", "identity", "seal", "manifest")
)
async def test_rehashed_malicious_owner_reply_is_rejected(defect: str) -> None:
    evidence = await _evidence()
    prepared = evidence.proposal.fan_out
    initialized = prepared.initialized_records
    if defect == "order":
        prepared = prepared.model_copy(update={"initialized_records": tuple(reversed(initialized))})
    elif defect == "classification":
        first = initialized[0].model_copy(
            update={
                "call": initialized[0].call.model_copy(update={"classification": "CONSEQUENTIAL"})
            }
        )
        prepared = prepared.model_copy(update={"initialized_records": (first, initialized[1])})
    elif defect == "call_text":
        first = initialized[0].model_copy(
            update={
                "call": initialized[0].call.model_copy(update={"canonical_call_base64": "e30="})
            }
        )
        prepared = prepared.model_copy(update={"initialized_records": (first, initialized[1])})
    elif defect == "identity":
        prepared = prepared.model_copy(
            update={
                "initialized_records": (
                    initialized[0].model_copy(update={"original_call_id": "foreign"}),
                    initialized[1],
                )
            }
        )
    elif defect == "seal":
        prepared = prepared.model_copy(
            update={
                "response_seal": prepared.response_seal.model_copy(
                    update={"original_turn_id": "foreign"}
                )
            }
        )
    else:
        prepared = prepared.model_copy(
            update={
                "complete_ordered_record_manifest": tuple(
                    reversed(prepared.complete_ordered_record_manifest)
                )
            }
        )
    proposal = _signed(evidence.proposal.model_copy(update={"fan_out": prepared}))
    with pytest.raises(ValueError, match="exact captured semantics"):
        build_envelope(evidence.model_copy(update={"proposal": proposal}))


@pytest.mark.parametrize(
    "defect",
    ("accepted", "caller", "callee", "generation", "deadline", "companions", "fingerprint"),
)
async def test_retained_input_and_exchange_substitution_rejected(defect: str) -> None:
    evidence = await _evidence()
    if defect == "accepted":
        accepted = evidence.accepted_run.model_copy(
            update={"prompt": "different", "head": "pending"}
        )
        accepted = accepted.model_copy(update={"head": "loop:" + accepted.digest()})
        evidence = evidence.model_copy(update={"accepted_run": accepted})
    elif defect == "caller":
        evidence = evidence.model_copy(
            update={"caller": evidence.caller.model_copy(update={"tenant_id": "foreign"})}
        )
    elif defect == "callee":
        evidence = evidence.model_copy(
            update={"callee": evidence.callee.model_copy(update={"session_id": "other"})}
        )
    elif defect == "generation":
        evidence = evidence.model_copy(
            update={"callee": evidence.callee.model_copy(update={"generation_id": "other"})}
        )
    elif defect == "deadline":
        evidence = evidence.model_copy(update={"deadline_ns": 0})
    elif defect == "companions":
        evidence = evidence.model_copy(
            update={
                "companions": (
                    loop.DurableCompanion(
                        record_id="extra",
                        owner="conversation",
                        schema_id="extra",
                        payload_base64="e30=",
                    ),
                )
            }
        )
    else:
        evidence = evidence.model_copy(
            update={
                "proposal": evidence.proposal.model_copy(update={"proposal_fingerprint": "0" * 64})
            }
        )
    with pytest.raises(ValueError):
        build_envelope(evidence)


@pytest.mark.parametrize(
    "field,value",
    (
        ("record_id", "foreign"),
        ("owner", "foreign"),
        ("schema_id", "foreign"),
        ("payload_base64", "not-base64"),
    ),
)
async def test_complete_companion_identity_and_encoding_are_closed(field: str, value: str) -> None:
    evidence = await _evidence(complete=True)
    companion = evidence.companions[0].model_copy(update={field: value})
    with pytest.raises(ValueError):
        build_envelope(evidence.model_copy(update={"companions": (companion,)}))


@pytest.mark.parametrize("complete", (False, True))
async def test_physical_bound_counts_whole_envelope_at_exact_limit(complete: bool) -> None:
    evidence = _bound(await _evidence(complete), "max_serialized_batch_bytes", 99999)
    size = len(build_envelope(evidence).canonical_bytes())
    assert 10000 <= size < 99999  # Same decimal width keeps bound payload length stable.
    exact = _bound(evidence, "max_serialized_batch_bytes", size)
    assert len(build_envelope(exact).canonical_bytes()) == size
    short = _bound(evidence, "max_serialized_batch_bytes", size - 1)
    with pytest.raises(ValueError, match="complete physical envelope"):
        build_envelope(short)


async def test_semantic_bound_cannot_be_hidden_by_rehashed_proposal() -> None:
    evidence = await _evidence()
    manifest = evidence.proposal.fan_out.complete_ordered_record_manifest
    size = len(b"[" + b",".join(item.canonical_bytes() for item in manifest) + b"]")
    exact = _bound(evidence, "max_manifest_bytes", size)
    build_envelope(exact)
    inner = exact.request.request.model_copy(
        update={
            "bound": exact.request.request.bound.model_copy(update={"max_manifest_bytes": size - 1})
        }
    )
    request = exact.request.model_copy(update={"request": inner})
    with pytest.raises(ValueError, match="complete semantic manifest"):
        build_envelope(exact.model_copy(update={"request": request}))


@pytest.mark.parametrize("defect", ("payload", "duplicate", "fingerprint", "base64"))
async def test_physical_conversion_refuses_corrupt_envelope(defect: str) -> None:
    envelope = build_envelope(await _evidence())
    records = envelope.records
    if defect == "fingerprint":
        envelope = envelope.model_copy(update={"request_fingerprint": "0" * 64})
    else:
        first = records[0]
        if defect == "payload":
            first = first.model_copy(update={"canonical_payload_base64": "e30="})
        elif defect == "base64":
            first = first.model_copy(update={"canonical_payload_base64": "%%%"})
        else:
            first = first.model_copy(update={"record_id": records[1].record_id})
        envelope = envelope.model_copy(update={"records": (first, *records[1:])})
        envelope = envelope.model_copy(
            update={"request_fingerprint": _hash(envelope, "request_fingerprint")}
        )
    with pytest.raises(ValueError):
        physical_command(envelope)


async def test_model_copy_bypasses_are_revalidated() -> None:
    evidence = await _evidence()
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"), pytest.raises(ValueError):
        build_envelope(evidence.model_copy(update={"deadline_ns": "soon"}))


async def test_inventory_uses_earliest_exact_toolterminal_run_and_preserves_pending() -> None:
    evidence = await _evidence()
    accepted = evidence.accepted_run
    first = domain.terminal_tool(accepted, "model:0", "first result")
    second = domain.terminal_tool(first, "model:1", "second result")
    snapshot = loop.LoopSnapshot(
        tenant_head=4, records=(evidence.request.captured_run, accepted, first, second)
    )
    inventory = inventory_from_history("tenant", snapshot, (evidence,))
    by_ordinal = {
        row.initialized_record.call.original.ordinal: row for row in inventory.ordered_calls
    }
    assert by_ordinal[0].terminal == Present(head=first.head, fingerprint=first.digest())
    assert by_ordinal[1].terminal == Present(head=second.head, fingerprint=second.digest())
    assert tuple(row.original_call_id for row in inventory.ordered_calls) == tuple(
        sorted(row.original_call_id for row in inventory.ordered_calls)
    )
    for row in inventory.ordered_calls:
        assert row.initialized == reference(row.original_call_id, row.initialized_record)
        assert row.acceptance.kind == "INITIALIZED"
    partial = inventory_from_history(
        "tenant", snapshot.model_copy(update={"records": snapshot.records[:-1]}), (evidence,)
    )
    pending = next(
        row for row in partial.ordered_calls if row.initialized_record.call.original.ordinal == 1
    )
    assert pending.terminal == Absent()


async def test_inventory_rejects_missing_acceptance_duplicate_and_changed_terminal_history() -> (
    None
):
    evidence = await _evidence()
    captured, accepted = evidence.request.captured_run, evidence.accepted_run
    snapshot = loop.LoopSnapshot(tenant_head=2, records=(captured, accepted))
    with pytest.raises(ValueError, match="duplicate selected capture"):
        inventory_from_history("tenant", snapshot, (evidence, evidence))
    with pytest.raises(ValueError, match="absent from history"):
        inventory_from_history(
            "tenant", snapshot.model_copy(update={"records": (captured,)}), (evidence,)
        )
    terminal = domain.terminal_tool(accepted, "model:0", "done")
    malformed = terminal.model_copy(update={"prompt": "foreign", "head": "pending"})
    malformed = malformed.model_copy(update={"head": "loop:" + malformed.digest()})
    with pytest.raises(ValueError):
        inventory_from_history(
            "tenant",
            snapshot.model_copy(update={"records": (captured, accepted, malformed)}),
            (evidence,),
        )


async def test_inventory_rejects_duplicate_zero_call_seals() -> None:
    evidence = await _evidence(complete=True)
    snapshot = loop.LoopSnapshot(
        tenant_head=2, records=(evidence.request.captured_run, evidence.accepted_run)
    )
    assert inventory_from_history("tenant", snapshot, (evidence,)).ordered_calls == ()
    with pytest.raises(ValueError, match="duplicate selected capture"):
        inventory_from_history("tenant", snapshot, (evidence, evidence))


@pytest.mark.parametrize(
    "defect",
    (
        "registry",
        "source_bytes",
        "source_subject",
        "source_head",
        "generation",
        "frontier",
        "expired_deadline",
        "unobserved_deadline",
    ),
)
async def test_retained_cut_source_reference_and_deadline_must_bind_exact_exchange(
    defect: str,
) -> None:
    evidence = await _evidence()
    request = evidence.request
    cut = request.request.cut
    source = cut.sources[0]
    if defect == "registry":
        cut = cut.model_copy(
            update={"authority_registry": reference("other", request.tool_registry)}
        )
    elif defect == "expired_deadline":
        evidence = evidence.model_copy(update={"deadline_ns": 11})
    elif defect == "unobserved_deadline":
        evidence = evidence.model_copy(update={"deadline_ns": 1})
    else:
        if defect == "source_bytes":
            source = source.model_copy(update={"canonical_value_base64": "e30="})
        elif defect == "source_subject":
            source = source.model_copy(
                update={"source": source.source.model_copy(update={"subject_id": "foreign"})}
            )
        elif defect == "source_head":
            source = source.model_copy(
                update={
                    "source": source.source.model_copy(
                        update={
                            "revision": source.source.revision.model_copy(
                                update={"head": "foreign"}
                            )
                        }
                    )
                }
            )
        else:
            source = source.model_copy(update={defect: "foreign"})
        cut = cut.model_copy(update={"sources": (source,)})
    request = request.model_copy(
        update={"request": request.request.model_copy(update={"cut": cut})}
    )
    with pytest.raises(ValueError, match=r"registry|source reference"):
        build_envelope(evidence.model_copy(update={"request": request}))


async def test_warm_envelope_rejects_changed_exchange_and_returns_fresh_objects() -> None:
    evidence = await _evidence()
    first = build_envelope(evidence)
    original = first.canonical_bytes()
    object.__setattr__(first, "tenant_id", "poisoned")
    second = build_envelope(evidence)
    assert second is not first
    assert second.canonical_bytes() == original
    with pytest.raises(ValueError):
        build_envelope(evidence.model_copy(update={"deadline_ns": 0}))
    assert build_envelope(evidence).canonical_bytes() == original
