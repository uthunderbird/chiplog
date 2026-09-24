"""Inert first-path joins reject substitution; selection still belongs to the writer."""

import base64
import hashlib
import json

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.delivery_completion import _captured
from tests.support.execution_fan_out import bind_run, fixture

from chiplog.capabilities.agent_loop import _execution_completion_process as process
from chiplog.capabilities.agent_loop import execution_first_path_completion_contracts as contracts
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    ExecutionCompletionResult,
    PreparedExecutionCompletion,
    PrepareExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutProposal,
)
from chiplog.capabilities.agent_loop.execution_fan_out_preparation import (
    prepare_execution_captured_fan_out,
)
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    SelectedAdmittedRunInput,
)
from chiplog.capabilities.agent_loop.execution_lifecycle import create_ingress_execution_run
from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrontierMember,
    RecoveryFrontier,
)
from chiplog.capabilities.agent_loop.recovery_frontier_registry_contracts import (
    RECOVERY_FRONTIER_REGISTRY_SCHEMA,
    execution_zero_call_frontier_registry,
    frontier_registry_reference,
)


def ref(subject: str, raw: bytes, head: str | None = None) -> CallSubjectHead:
    digest = hashlib.sha256(raw).hexdigest()
    return CallSubjectHead(
        subject_id=subject, revision=Present(head=head or "record:" + digest, fingerprint=digest)
    )


def source(schema: str, reference: CallSubjectHead, raw: bytes) -> RecoverySourceRecord:
    return RecoverySourceRecord(
        owner="agent_loop",
        subject=reference,
        schema_id=schema,
        canonical_record_bytes=raw,
        selected_decision=ref("decision", b"decision"),
        physical_record=ref("physical:" + reference.revision.fingerprint, raw),
    )


async def request(
    *, canonical_response: bool = False
) -> contracts.PrepareExecutionCompletionFirstPathV2:
    """Owner-produced seal and exact native source bodies, not an authenticated history."""
    fanout = await fixture(complete=True, canonical_response=canonical_response)
    captured = fanout.captured_run
    turn = captured.turns[0]
    attempt = turn.attempts[0].model_copy(update={"head": "pending"})
    attempt = attempt.model_copy(update={"head": "execution-attempt:" + attempt.digest()})
    turn = turn.model_copy(update={"head": "pending", "attempts": (attempt,)})
    turn = turn.model_copy(update={"head": "execution-turn:" + turn.digest()})
    captured = captured.model_copy(update={"turns": (turn,)})
    root = create_ingress_execution_run(
        tenant=captured.tenant,
        principal=captured.principal,
        run_id=captured.run_id,
        prompt=captured.prompt,
        policy=captured.policy,
        origin=captured.origin,
        contour_head=captured.contour_head,
        policy_head=captured.policy_head,
        worker_session=captured.worker_session,
    )
    # These are consistency-only sources. Owner transition replay is a separate check.
    fanout = bind_run(fanout, captured.model_copy(update={"predecessor": root.head}))
    captured = fanout.captured_run
    proposal = prepare_execution_captured_fan_out(fanout)
    assert isinstance(proposal, ExecutionCapturedFanOutProposal)
    run, seal = proposal.sealed_run, proposal.fan_out.response_seal
    lineage = (root, captured, run)
    current = ref(run.run_id, run.canonical_bytes(), run.head)
    capture = ref(captured.run_id, captured.canonical_bytes(), captured.head)
    selected_seal = ref(seal.response_seal_id, seal.canonical_bytes())
    registry = execution_zero_call_frontier_registry()
    sources = (
        *(
            source(
                row.schema_id,
                ref(row.run_id, row.canonical_bytes(), row.head),
                row.canonical_bytes(),
            )
            for row in lineage
        ),
        source("chiplog.call.response-seal.v1", selected_seal, seal.canonical_bytes()),
        source(
            RECOVERY_FRONTIER_REGISTRY_SCHEMA,
            frontier_registry_reference(registry),
            registry.canonical_bytes(),
        ),
    )
    members = []
    for row in registry.ordered_rows:
        selected = {
            "RUN": current,
            "TURN": ref(run.turns[0].turn_id, run.turns[0].canonical_bytes(), run.turns[0].head),
            "SEALED_RESPONSE": selected_seal,
        }.get(row.family)
        member = FrontierMember(
            family=row.family,
            subject=selected.subject_id if selected else row.family,
            branch="PRESENT" if selected else "ABSENT",
            ordered_heads=(selected.revision,)
            if selected
            else (NotApplicable() if row.family == "EXECUTION_LINEAGE" else Absent(),),
            fingerprint="0" * 64,
        )
        members.append(
            member.model_copy(
                update={"fingerprint": contracts.first_path_frontier_member_fingerprint(member)}
            )
        )
    frontier = RecoveryFrontier(
        tenant_id=run.tenant,
        run_id=run.run_id,
        tenant_commit_sequence=10,
        registry=registry,
        ordered_members=tuple(members),
        ordered_calls=(),
        canonicalization_version="chiplog.recovery.frontier.v1",
        fingerprint="0" * 64,
    )
    frontier = frontier.model_copy(
        update={"fingerprint": contracts.first_path_frontier_fingerprint(frontier)}
    )
    head = ref("input", b"input")
    admitted = SelectedAdmittedRunInput(
        tenant_id=run.tenant,
        database_id="database",
        source_class="CLI",
        source_contract=head,
        token=head,
        custody=head,
        inbox=head,
        selected_decision=head,
        physical_record=head,
        commit_sequence=1,
        raw_input_bytes=b"input",
        custody_schema="custody.v1",
        canonical_custody_record=b"input",
        inbox_schema="inbox.v1",
        canonical_inbox_record=b"input",
        source_authentication=head,
        authentication_schema="authentication.v1",
        canonical_authentication=b"input",
        normalization=head,
        normalization_schema="normalization.v1",
        canonical_normalization_record=b"input",
        normalized_prompt=run.prompt,
        principal_id=run.principal,
        contour_head=run.contour_head,
        origin=run.origin,
    )
    # model_construct is only used to compute the self-excluding fingerprint.
    cut = contracts.FirstPathCompletionCutV2.model_construct(
        tenant_id=run.tenant,
        database_id="database",
        tenant_commit_sequence=10,
        materialization_commitment="a" * 64,
        selected_admitted_input=admitted,
        complete_ordered_run_lineage=lineage,
        current_run=current,
        selected_capture=capture,
        selected_response_seal=selected_seal,
        seal=seal,
        frontier=frontier,
        complete_sources=sources,
        complete_inventory_fingerprint="0" * 64,
    )
    cut = cut.model_copy(
        update={"complete_inventory_fingerprint": contracts.first_path_inventory_fingerprint(cut)}
    )
    cut = contracts.FirstPathCompletionCutV2.model_validate_json(cut.canonical_bytes())
    attempt = run.turns[0].attempts[0]
    assert attempt.response_base64 is not None
    raw = base64.b64decode(attempt.response_base64)
    _, observation = await _captured()
    observation = observation.model_copy(
        update={
            "tenant": run.tenant,
            "run": ExactHead(identity=run.run_id, head=run.head, fingerprint=run.digest()),
            "turn_id": run.turns[0].turn_id,
            "captured_response": raw,
            "origin": run.origin,
            "recipients": (run.origin.recipient,),
        }
    )
    fence = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id=run.run_id,
        run_head=run.head,
        worker_session_id=run.worker_session,
        runtime_generation="generation",
    )
    return contracts.PrepareExecutionCompletionFirstPathV2(
        command_id="complete",
        run=run,
        selected_attempt=ref(attempt.attempt_id, attempt.canonical_bytes(), attempt.head),
        selector_generation=0,
        visibility_manifest=ref("visibility", attempt.manifest.canonical_bytes()),
        exact_captured_response=raw,
        source=cut,
        delivery=observation,
        fence=fence,
    )


async def test_roundtrip_discriminant_fingerprints_and_frozen_wire() -> None:
    value = await request()
    raw = value.canonical_bytes()
    assert contracts.decode_first_path_completion_request(raw) == value
    assert contracts.decode_completion_request(raw) == value
    assert (
        contracts.first_path_completion_request_fingerprint(value)
        == hashlib.sha256(raw).hexdigest()
    )
    assert value.exact_captured_response.endswith(b"\n")
    with pytest.raises(ValidationError):
        value.command_id = "changed"
    with pytest.raises(ValueError, match="noncanonical"):
        contracts.decode_first_path_completion_request(
            json.dumps(json.loads(raw), indent=2).encode()
        )
    wire = json.loads(raw)
    wire["kind"] = "PREPARE_EXECUTION_COMPLETION_V1"
    with pytest.raises(ValidationError):
        contracts.decode_completion_request(json.dumps(wire).encode())
    wire = json.loads(raw)
    wire["selector_generation"] = "0"
    with pytest.raises(ValidationError):
        contracts.PrepareExecutionCompletionFirstPathV2.model_validate_json(json.dumps(wire))


@pytest.mark.parametrize(
    "mutation",
    [
        "capture",
        "seal",
        "source",
        "registry",
        "frontier",
        "frontier_run",
        "inventory",
        "admitted",
        "lineage",
    ],
)
async def test_cut_rejects_substituted_sources_even_with_rehashed_inventory(mutation: str) -> None:
    value = await request()
    cut = value.source
    if mutation == "capture":
        cut = cut.model_copy(update={"selected_capture": cut.current_run})
    elif mutation == "seal":
        cut = cut.model_copy(
            update={"seal": cut.seal.model_copy(update={"original_run_id": "foreign"})}
        )
    elif mutation == "source":
        changed = cut.complete_sources[0].model_copy(update={"canonical_record_bytes": b"fake"})
        cut = cut.model_copy(update={"complete_sources": (changed, *cut.complete_sources[1:])})
    elif mutation == "registry":
        frontier = cut.frontier.model_copy(
            update={"registry": cut.frontier.registry.model_copy(update={"fingerprint": "0" * 64})}
        )
        cut = cut.model_copy(update={"frontier": frontier})
    elif mutation == "frontier":
        cut = cut.model_copy(
            update={"frontier": cut.frontier.model_copy(update={"fingerprint": "0" * 64})}
        )
    elif mutation == "frontier_run":
        cut = cut.model_copy(
            update={"frontier": cut.frontier.model_copy(update={"run_id": "foreign"})}
        )
    elif mutation == "admitted":
        cut = cut.model_copy(
            update={
                "selected_admitted_input": cut.selected_admitted_input.model_copy(
                    update={"source_class": "TELEGRAM_PUSH"}
                )
            }
        )
    elif mutation == "lineage":
        cut = cut.model_copy(
            update={
                "complete_ordered_run_lineage": tuple(reversed(cut.complete_ordered_run_lineage))
            }
        )
    cut = cut.model_copy(
        update={
            "complete_inventory_fingerprint": "0" * 64
            if mutation == "inventory"
            else contracts.first_path_inventory_fingerprint(cut)
        }
    )
    with pytest.raises(ValueError):
        contracts.FirstPathCompletionCutV2.model_validate_json(cut.canonical_bytes())


@pytest.mark.parametrize(
    "mutation", ["response", "attempt", "generation", "visibility", "delivery", "fence"]
)
async def test_request_rejects_cross_source_joins_and_unvalidated_copies(mutation: str) -> None:
    value = await request()
    changes: dict[str, dict[str, object]] = {
        "response": {"exact_captured_response": b"fake"},
        "attempt": {"selected_attempt": value.source.current_run},
        "generation": {"selector_generation": 1},
        "visibility": {"visibility_manifest": value.source.current_run},
        "delivery": {"delivery": value.delivery.model_copy(update={"turn_id": "foreign"})},
        "fence": {"fence": value.fence.model_copy(update={"run_head": "stale"})},
    }
    changed = value.model_copy(update=changes[mutation])
    with pytest.raises(ValueError):
        contracts.first_path_completion_request_fingerprint(changed)


@pytest.mark.parametrize("family", ["TURN", "CALL", "OBLIGATION", "EXECUTION_LINEAGE"])
async def test_rehashed_frontier_cannot_hide_required_or_forbidden_heads(family: str) -> None:
    value = await request()
    members = []
    for member in value.source.frontier.ordered_members:
        if member.family == family:
            member = member.model_copy(
                update={
                    "ordered_heads": (Absent(),)
                    if family == "TURN"
                    else (value.source.current_run.revision,),
                }
            )
            member = member.model_copy(
                update={
                    "fingerprint": contracts.first_path_frontier_member_fingerprint(member),
                }
            )
        members.append(member)
    frontier = value.source.frontier.model_copy(update={"ordered_members": tuple(members)})
    frontier = frontier.model_copy(
        update={
            "fingerprint": contracts.first_path_frontier_fingerprint(frontier),
        }
    )
    cut = value.source.model_copy(update={"frontier": frontier})
    cut = cut.model_copy(
        update={
            "complete_inventory_fingerprint": contracts.first_path_inventory_fingerprint(cut),
        }
    )
    with pytest.raises(ValueError):
        contracts.FirstPathCompletionCutV2.model_validate_json(cut.canonical_bytes())


async def test_v1_request_bytes_remain_exact_through_versioned_decoder() -> None:
    from tests.support.completion_assembly import accepted_completion_fixture

    original = (
        await accepted_completion_fixture("v3", "empty")
    ).assembly.original_completion_request
    raw = original.canonical_bytes()
    assert contracts.decode_completion_request(raw).canonical_bytes() == raw
    with pytest.raises(ValueError):
        contracts.decode_first_path_completion_request(raw)


async def test_first_path_request_reaches_its_mounted_owner_and_prepares_acceptance() -> None:
    value = await request(canonical_response=True)

    assert (
        process.FIRST_PATH_OPERATION,
        "broker",
        "agent_loop",
        contracts.FIRST_PATH_COMPLETION_SCHEMA,
        process.RESULT_SCHEMA,
    ) in process.ROUTES
    reply = process.dispatch(process.FIRST_PATH_OPERATION, value.canonical_bytes())

    assert reply["schema_id"] == process.RESULT_SCHEMA
    payload = reply["payload"]
    assert isinstance(payload, str)
    raw = base64.b64decode(payload)
    decoded: ExecutionCompletionResult = TypeAdapter(ExecutionCompletionResult).validate_json(
        raw
    )
    assert isinstance(decoded, PreparedExecutionCompletion)
    assert decoded.canonical_bytes() == raw
    assert decoded.source_request_fingerprint == hashlib.sha256(value.canonical_bytes()).hexdigest()
    assert decoded.complete_earlier_continuations == ()
    assert decoded.run.state == "SUCCEEDED"


async def test_legacy_completion_owner_route_keeps_its_existing_result_bytes() -> None:
    from tests.support.completion_assembly import accepted_completion_fixture

    from chiplog.capabilities.agent_loop.execution_completion_preparation import (
        prepare_execution_completion,
    )

    original = (
        await accepted_completion_fixture("v3", "empty")
    ).assembly.original_completion_request
    assert isinstance(original, PrepareExecutionCompletion)
    expected = prepare_execution_completion(original)

    reply = process.dispatch(process.OPERATION, original.canonical_bytes())

    assert reply == {
        "payload": base64.b64encode(expected.canonical_bytes()).decode(),
        "schema_id": process.RESULT_SCHEMA,
    }


@pytest.mark.parametrize("mutation", ["noncanonical", "rehash_splice"])
async def test_first_path_owner_rejects_noncanonical_and_rehashed_splice(mutation: str) -> None:
    value = await request()
    if mutation == "noncanonical":
        payload = json.dumps(json.loads(value.canonical_bytes()), indent=2).encode()
    else:
        cut = value.source.model_copy(update={"selected_capture": value.source.current_run})
        cut = cut.model_copy(
            update={
                "complete_inventory_fingerprint": contracts.first_path_inventory_fingerprint(cut)
            }
        )
        payload = value.model_copy(update={"source": cut}).canonical_bytes()

    reply = process.dispatch(process.FIRST_PATH_OPERATION, payload)

    assert reply["failure"] == "PROTOCOL_REJECTED"
