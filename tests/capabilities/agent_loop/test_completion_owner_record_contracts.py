"""Closed physical member contracts for Phase-C completion records."""

from __future__ import annotations

import hashlib
from base64 import b64encode
from typing import cast

import pytest
from tests.support.delivery_completion import _captured as captured_delivery
from tests.support.execution_fan_out import fixture as captured_execution
from tests.support.execution_versions import execution_run_v3

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    COMPLETION_REJECTION_SCHEMA,
    DELIVERY_ACCEPTANCE_SCHEMA,
    CompletionCanonicalMember,
    CompletionRecordIntegrityError,
    CompletionRejectionRecordV1,
    completion_request_fingerprint,
    decode_completion_canonical_member,
    make_completion_rejection_member,
    make_delivery_acceptance_member,
    make_prepared_delivery_acceptance_member,
    make_terminal_manifest_member,
    make_terminal_run_member,
    validate_completion_request_source,
)
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryAcceptanceProposal,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.delivery_preparation import (
    prepare_completion as prepare_delivery_completion,
)
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
    PreparedExecutionCompletionReject,
    PrepareExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ExecutionModelAttempt,
    ExecutionTurn,
)
from chiplog.capabilities.agent_loop.execution_history_contracts import (
    ExecutionModelAttemptV3,
    ExecutionTurnV3,
)
from chiplog.capabilities.agent_loop.execution_recovery_observations import (
    ExecutionRecoveryCut,
    ExecutionTerminalManifest,
    RecoverySourceRecord,
)
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import (
    NonSchedulerFence,
    NotApplicable,
    Present,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrontierMember,
    FrozenRunBindings,
    RecoveryFrontier,
    RecoveryFrontierRegistry,
    RecoveryRegistryRow,
)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _head(identity: str) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=identity,
        revision=Present(head=f"physical:{identity}", fingerprint=_digest(identity)),
    )


def _terminal(run: ExecutionRun, state: str, event: str) -> ExecutionRun:
    pending = run.model_copy(update={"state": state, "event": event, "head": "pending"})
    return pending.model_copy(update={"head": "loop:" + pending.digest()})


async def _run(version: str) -> ExecutionRun:
    if version == "v3":
        return execution_run_v3()
    return (await captured_execution(complete=True)).captured_run


def _cut(run: ExecutionRun) -> ExecutionRecoveryCut:
    return ExecutionRecoveryCut(
        tenant_id="tenant",
        database_id="database",
        tenant_commit_sequence=4,
        materialization_commitment="d" * 64,
        current_run=_run_ref(run),
        complete_ordered_run_lineage=(run,),
        complete_ordered_responses=(),
        frontier=RecoveryFrontier(
            tenant_id="tenant",
            run_id="run",
            tenant_commit_sequence=4,
            registry=RecoveryFrontierRegistry(
                registry_id="registry",
                version="1",
                fingerprint="a" * 64,
                ordered_rows=(
                    RecoveryRegistryRow(
                        family="RUN",
                        ordinal=0,
                        subject_extractor_id="run",
                        cardinality_rule="exact-one",
                        terminal_conflict_rule="reject-rival",
                        serialization_rule="canonical",
                        canonicalization_version="1",
                    ),
                ),
            ),
            ordered_members=(
                FrontierMember(
                    family="RUN",
                    subject="run",
                    branch="ACTIVE",
                    ordered_heads=(_run_ref(run).revision,),
                    fingerprint="b" * 64,
                ),
            ),
            ordered_calls=(),
            canonicalization_version="chiplog.recovery.frontier.v1",
            fingerprint="c" * 64,
        ),
        current_bindings=FrozenRunBindings(
            objective="Plan",
            requested_work="Plan",
            prompt_artifact=Present(head="prompt", fingerprint="1" * 64),
            ordered_tool_specs=(Present(head="tool", fingerprint="2" * 64),),
            generated_schema=Present(head="schema", fingerprint="3" * 64),
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=Present(head="scope", fingerprint="4" * 64),
            authority_mandate_heads=(),
            policy=Present(head="policy", fingerprint="5" * 64),
            no_retry_boundaries=(),
        ),
        original_closures=(),
        current_reductions=(),
        complete_sources=(
            RecoverySourceRecord(
                owner="agent_loop",
                subject=_head("source"),
                schema_id="source.v1",
                canonical_record_bytes=b"source",
                selected_decision=_head("decision"),
                physical_record=_head("physical"),
            ),
        ),
        complete_causal_changes=(),
        complete_inventory_fingerprint="e" * 64,
    )


def _run_ref(run: ExecutionRun) -> CallSubjectHead:
    raw = run.canonical_bytes()
    return CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=hashlib.sha256(raw).hexdigest()),
    )


def _selected(
    run: ExecutionRun,
) -> tuple[ExecutionTurn | ExecutionTurnV3, ExecutionModelAttempt | ExecutionModelAttemptV3]:
    selected: list[
        tuple[ExecutionTurn | ExecutionTurnV3, ExecutionModelAttempt | ExecutionModelAttemptV3]
    ] = [
        (turn, turn.attempts[turn.selector])
        for turn in run.turns
        if turn.selector < len(turn.attempts)
    ]
    assert len(selected) == 1
    return selected[0]


def _captured_delivery_run(
    run: ExecutionRun, raw: bytes | None = None
) -> tuple[ExecutionRun, bytes]:
    turn, attempt = _selected(run)
    if raw is None:
        raw = DeliveryCompletion(
            tenant=run.tenant,
            run_id=run.run_id,
            turn_id=turn.turn_id,
            deliveries=(ProposedDelivery(payload=(Commentary(text="completed"),)),),
        ).canonical_bytes()
    replaced_attempt = attempt.model_copy(update={"response_base64": b64encode(raw).decode()})
    replaced_turn = turn.model_copy(
        update={
            "attempts": tuple(
                replaced_attempt if item is attempt else item for item in turn.attempts
            )
        }
    )
    replaced_run = run.model_copy(
        update={"turns": tuple(replaced_turn if item is turn else item for item in run.turns)}
    )
    pending = replaced_run.model_copy(update={"head": "pending"})
    return pending.model_copy(update={"head": "loop:" + pending.digest()}), raw


async def _request(run: ExecutionRun, raw: bytes | None = None) -> PrepareExecutionCompletion:
    run, raw = _captured_delivery_run(run, raw)
    turn, attempt = _selected(run)
    _, observation = await captured_delivery()
    run_raw = run.canonical_bytes()
    selected_attempt = CallSubjectHead(
        subject_id=attempt.attempt_id,
        revision=Present(head=attempt.head, fingerprint=attempt.digest()),
    )
    return PrepareExecutionCompletion(
        command_id="completion",
        run=run,
        selected_attempt=selected_attempt,
        selector_generation=attempt.generation,
        visibility_manifest=_head("visibility"),
        exact_captured_response=raw,
        cut=_cut(run),
        complete_earlier_continuations=(),
        delivery=observation.model_copy(
            update={
                "tenant": run.tenant,
                "run": ExactHead(
                    identity=run.run_id,
                    head=run.head,
                    fingerprint=hashlib.sha256(run_raw).hexdigest(),
                ),
                "turn_id": turn.turn_id,
                "captured_response": raw,
            }
        ),
        fence=NonSchedulerFence(
            lineage=NotApplicable(),
            physical_root=NotApplicable(),
            lease=NotApplicable(),
            clock_proof=NotApplicable(),
            run_id=run.run_id,
            run_head=run.head,
            worker_session_id="worker",
            runtime_generation="generation",
        ),
    )


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_native_terminal_run_keeps_schema_head_and_full_bytes(version: str) -> None:
    run = _terminal(await _run(version), "SUCCEEDED", "ExecutionCompleted")

    member = make_terminal_run_member(run)

    assert member.record_kind == "Run"
    assert member.schema_id == run.schema_id
    assert member.record_id == run.head
    assert member.fingerprint == hashlib.sha256(run.canonical_bytes()).hexdigest()
    assert decode_completion_canonical_member(member).record == run

    bad_head = member.model_copy(update={"record_id": "loop:wrong"})
    with pytest.raises(CompletionRecordIntegrityError):
        decode_completion_canonical_member(bad_head)

    changed_body = run.model_copy(update={"event": "TamperedTerminalEvent"})
    changed_raw = changed_body.canonical_bytes()
    with pytest.raises(CompletionRecordIntegrityError):
        decode_completion_canonical_member(
            member.model_copy(
                update={
                    "canonical_record_bytes": changed_raw,
                    "fingerprint": hashlib.sha256(changed_raw).hexdigest(),
                }
            )
        )


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_completion_source_binds_the_native_run_selected_turn_and_raw_bytes(
    version: str,
) -> None:
    request = await _request(await _run(version))

    selected = validate_completion_request_source(request)

    assert selected.turn_id == request.delivery.turn_id
    assert selected.attempts[selected.selector].response_base64 is not None


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_prepared_acceptance_uses_a_coherent_native_completion_source(version: str) -> None:
    request = await _request(await _run(version))
    completion = DeliveryCompletion.model_validate_json(request.exact_captured_response)
    proposal = prepare_delivery_completion(completion, request.delivery)
    terminal = _terminal(request.run, "SUCCEEDED", "ExecutionCompleted")
    manifest = ExecutionTerminalManifest(
        manifest_id=f"accepted-{version}",
        command_id=request.command_id,
        prior_run=_run_ref(request.run),
        target="SUCCEEDED",
        source_cut_fingerprint=request.cut.digest(),
        complete_accounting=(),
        complete_open_original_obligations=(),
    )
    prepared = PreparedExecutionCompletion(
        source_request_fingerprint=completion_request_fingerprint(request),
        complete_earlier_continuations=(),
        delivery=proposal,
        terminal_manifest=manifest,
        run=terminal,
        complete_owner_commitment="c" * 64,
    )

    member = make_prepared_delivery_acceptance_member(request, prepared)

    assert member.record_kind == "DELIVERY_ACCEPTANCE"


@pytest.mark.parametrize(
    "mutation", ("tenant", "run_head", "native_head", "state", "turn", "attempt", "raw")
)
async def test_completion_source_rejects_each_native_binding_mutation(mutation: str) -> None:
    request = await _request(await _run("v3"))
    if mutation == "tenant":
        changed = request.model_copy(
            update={"delivery": request.delivery.model_copy(update={"tenant": "foreign"})}
        )
    elif mutation == "run_head":
        changed = request.model_copy(
            update={
                "delivery": request.delivery.model_copy(
                    update={"run": request.delivery.run.model_copy(update={"head": "foreign-head"})}
                )
            }
        )
    elif mutation == "native_head":
        bogus_run = request.run.model_copy(update={"head": "loop:" + "0" * 64})
        bogus_raw = bogus_run.canonical_bytes()
        bogus_ref = CallSubjectHead(
            subject_id=bogus_run.run_id,
            revision=Present(
                head=bogus_run.head,
                fingerprint=hashlib.sha256(bogus_raw).hexdigest(),
            ),
        )
        changed = request.model_copy(
            update={
                "run": bogus_run,
                "delivery": request.delivery.model_copy(
                    update={
                        "run": ExactHead(
                            identity=bogus_run.run_id,
                            head=bogus_run.head,
                            fingerprint=hashlib.sha256(bogus_raw).hexdigest(),
                        )
                    }
                ),
                "cut": request.cut.model_copy(update={"current_run": bogus_ref}),
            }
        )
    elif mutation == "state":
        changed = request.model_copy(update={"run": _terminal(request.run, "SUCCEEDED", "done")})
    elif mutation == "turn":
        changed = request.model_copy(
            update={"delivery": request.delivery.model_copy(update={"turn_id": "foreign-turn"})}
        )
    elif mutation == "attempt":
        changed = request.model_copy(update={"selected_attempt": _head("foreign-attempt")})
    else:
        changed = request.model_copy(
            update={
                "exact_captured_response": b"different",
                "delivery": request.delivery.model_copy(update={"captured_response": b"different"}),
            }
        )

    with pytest.raises(CompletionRecordIntegrityError):
        validate_completion_request_source(changed)


async def test_malformed_captured_raw_remains_a_valid_rejection_source_but_not_acceptance() -> None:
    request = await _request(await _run("v3"), raw=b"not a delivery response")
    aborted = _terminal(request.run, "ABORTED", "ExecutionRejected")
    rejected = PreparedExecutionCompletionReject(
        source_request_fingerprint=completion_request_fingerprint(request),
        original_captured_attempt=request.selected_attempt,
        preserved_trace=_head("retained-captured-source"),
        visibility_manifest=request.visibility_manifest,
        reasons=("SCHEMA",),
        run=aborted,
        complete_owner_commitment="f" * 64,
    )

    assert make_completion_rejection_member(request, rejected).record_kind == "COMPLETION_REJECTION"
    with pytest.raises(CompletionRecordIntegrityError, match="invalid delivery bytes"):
        make_prepared_delivery_acceptance_member(request, cast(PreparedExecutionCompletion, None))


async def test_foreign_valid_complete_is_not_an_accepted_native_completion() -> None:
    raw = DeliveryCompletion(
        tenant="foreign-tenant",
        run_id="foreign-run",
        turn_id="foreign-turn",
        deliveries=(ProposedDelivery(payload=(Commentary(text="foreign"),)),),
    ).canonical_bytes()
    request = await _request(await _run("v2"), raw=raw)

    validate_completion_request_source(request)
    with pytest.raises(CompletionRecordIntegrityError, match="accepted delivery tenant"):
        make_prepared_delivery_acceptance_member(request, cast(PreparedExecutionCompletion, None))


async def test_delivery_acceptance_uses_external_physical_id_not_semantic_digests() -> None:
    _, observation = await captured_delivery()
    completion = DeliveryCompletion.model_validate_json(observation.captured_response)

    member = make_delivery_acceptance_member(completion, observation)
    proposal = decode_completion_canonical_member(member).record
    assert isinstance(proposal, DeliveryAcceptanceProposal)

    assert member.schema_id == DELIVERY_ACCEPTANCE_SCHEMA
    assert member.record_id == DELIVERY_ACCEPTANCE_SCHEMA + ":" + member.fingerprint
    assert member.fingerprint != proposal.proposal_digest
    assert member.record_id != proposal.acceptance.identity
    assert (
        proposal.proposal_digest == hashlib.sha256(proposal.manifest.canonical_bytes()).hexdigest()
    )

    with pytest.raises(CompletionRecordIntegrityError):
        decode_completion_canonical_member(member.model_copy(update={"schema_id": "rival.v1"}))


async def test_terminal_manifest_is_adapted_through_the_existing_recovery_decoder() -> None:
    run = _terminal(await _run("v3"), "SUCCEEDED", "ExecutionCompleted")
    manifest = ExecutionTerminalManifest(
        manifest_id="terminal-manifest",
        command_id="completion",
        prior_run=_head("prior-run"),
        target="SUCCEEDED",
        source_cut_fingerprint="a" * 64,
        complete_accounting=(),
        complete_open_original_obligations=(),
    )

    member = make_terminal_manifest_member(manifest)

    assert member.record_kind == "TERMINAL_MANIFEST"
    assert member.record_id == manifest.manifest_id
    assert decode_completion_canonical_member(member).record == manifest
    assert run.schema_id == "chiplog.agent-loop.execution-record.v3"


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_rejection_is_commitment_free_and_references_native_aborted_run(version: str) -> None:
    request = await _request(await _run(version))
    aborted = _terminal(request.run, "ABORTED", "ExecutionRejected")
    rejected = PreparedExecutionCompletionReject(
        source_request_fingerprint=completion_request_fingerprint(request),
        original_captured_attempt=request.selected_attempt,
        preserved_trace=_head("retained-original-capture"),
        visibility_manifest=request.visibility_manifest,
        reasons=("SCHEMA",),
        run=aborted,
        complete_owner_commitment="f" * 64,
    )

    member = make_completion_rejection_member(request, rejected)
    record = decode_completion_canonical_member(member).record
    assert isinstance(record, CompletionRejectionRecordV1)

    assert member.schema_id == COMPLETION_REJECTION_SCHEMA
    assert member.record_id == COMPLETION_REJECTION_SCHEMA + ":" + member.fingerprint
    assert b"complete_owner_commitment" not in member.canonical_record_bytes
    assert b"TRACE" not in member.canonical_record_bytes
    assert record.preserved_trace == rejected.preserved_trace
    assert record.terminal_run.revision.head == aborted.head
    assert (
        record.terminal_run.revision.fingerprint
        == hashlib.sha256(aborted.canonical_bytes()).hexdigest()
    )
    with pytest.raises(CompletionRecordIntegrityError):
        decode_completion_canonical_member(member.model_copy(update={"record_id": "rival"}))

    with pytest.raises(CompletionRecordIntegrityError):
        make_completion_rejection_member(
            request, rejected.model_copy(update={"source_request_fingerprint": "0" * 64})
        )


def test_closed_decoder_rejects_body_hash_identity_and_unregistered_kind_mutations() -> None:
    raw = b'{"kind":"unknown"}'
    valid_hash = hashlib.sha256(raw).hexdigest()
    member = CompletionCanonicalMember(
        record_kind="UNKNOWN",
        schema_id="unknown.v1",
        record_id="unknown:" + valid_hash,
        canonical_record_bytes=raw,
        fingerprint=valid_hash,
    )
    with pytest.raises(CompletionRecordIntegrityError):
        decode_completion_canonical_member(member)
    with pytest.raises(CompletionRecordIntegrityError):
        decode_completion_canonical_member(member.model_copy(update={"fingerprint": "0" * 64}))
