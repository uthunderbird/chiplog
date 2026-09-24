"""Consumer checks for closed successor-record wire bodies and identities."""

import hashlib
import json
from typing import Literal

import pytest
from tests.support.execution_fan_out import fixture
from tests.support.execution_versions import execution_run_v3
from tests.support.successor_records import (
    RepeatedSchedulerSuccessorFixture,
    content_member,
    non_scheduler_successor,
    repeated_scheduler_successors,
    scheduler_successor,
)

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_recovery_contracts import PreparedExecutionSuccessor
from chiplog.capabilities.agent_loop.execution_run_record_contracts import (
    ExecutionRunCanonicalMember,
    decode_execution_run_member,
)
from chiplog.capabilities.agent_loop.execution_run_versions import ExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    ExecutionLineageBinding,
    IndividualSubject,
    LeaseBinding,
    NotApplicable,
    OriginalObligationBinding,
    PhysicalRootBinding,
    Present,
    RecoveryDTO,
    RolloverPredecessor,
)
from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
    FrozenRunBindings,
    PendingCallFrontier,
    ReadOnlyAttemptMember,
    ReadOnlyRetryLineage,
    ReferenceExternalObligation,
)
from chiplog.capabilities.agent_loop.recovery_record_contracts import (
    AGENT_LOOP_OWNER,
    RecoveryRecordIntegrityError,
    RecoveryRecordMember,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import GenesisLease
from chiplog.capabilities.agent_loop.successor_ancestry_contracts import (
    successor_ancestry_request_fingerprint,
)
from chiplog.capabilities.agent_loop.successor_record_contracts import (
    SCHEDULER_EPOCH_OBSERVATION_SCHEMA,
    SCHEDULER_LINEAGE_ADVANCE_SCHEMA,
    SUCCESSOR_INITIALIZATION_SCHEMA,
    SUCCESSOR_RECORD_ROWS,
    EpochCreationLineageWitness,
    ExecutionSuccessorInitializationRecord,
    PriorSuccessorObservationLineageWitness,
    RetainedSuccessorInput,
    SchedulerSuccessorEpochObservationRecord,
    SchedulerSuccessorInputs,
    SchedulerSuccessorLineageAdvanceRecord,
    decode_retained_successor_input,
    decode_successor_record_member,
    validate_execution_successor_records,
)


def _present(name: str) -> Present:
    return Present(head="record:" + name, fingerprint=hashlib.sha256(name.encode()).hexdigest())


def _head(name: str) -> CallSubjectHead:
    return CallSubjectHead(subject_id=name, revision=_present(name))


def _member(record: ExecutionSuccessorInitializationRecord) -> RecoveryRecordMember:
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind="SUCCESSOR_INITIALIZATION",
        schema_id=SUCCESSOR_INITIALIZATION_SCHEMA,
        record_id=SUCCESSOR_INITIALIZATION_SCHEMA + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def _content_member(kind: str, schema_id: str, record: RecoveryDTO) -> RecoveryRecordMember:
    raw = record.canonical_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    return RecoveryRecordMember(
        owner=AGENT_LOOP_OWNER,
        record_kind=kind,
        schema_id=schema_id,
        record_id=schema_id + ":" + fingerprint,
        canonical_record_bytes=raw,
        fingerprint=fingerprint,
    )


def _retained_native(
    subject_id: str, head_id: str, schema_id: str, body: bytes
) -> RetainedSuccessorInput:
    return RetainedSuccessorInput(
        reference=CallSubjectHead(
            subject_id=subject_id,
            revision=Present(head=head_id, fingerprint=hashlib.sha256(body).hexdigest()),
        ),
        schema_id=schema_id,
        canonical_record_bytes=body,
    )


def _rollover_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


async def _initialization() -> ExecutionSuccessorInitializationRecord:
    captured = await fixture()
    original = OriginalObligationBinding(
        original_run_id="run",
        original_call_id="call",
        obligation_id="obligation",
        obligation_stream_id="stream",
        obligation_head="obligation-head",
        closure_predicate_id="predicate",
        closure_predicate_version="1",
        resolver_id="resolver",
        resolver_version="1",
        reducer_id="reducer",
        reducer_version="1",
        evidence_stream_id="evidence",
        evidence_head=_present("evidence"),
    )
    pending = PendingCallFrontier(
        original_call_id="call",
        response_id="response",
        pending=_present("pending"),
        terminal=Absent(),
        call_outcome=Absent(),
        initialized=_present("initialized"),
        lineage=ReadOnlyRetryLineage(
            lineage_id="lineage",
            original_call_id="call",
            max_attempts=2,
            budget_version="1",
            reducer_id="reducer",
            reducer_version="1",
        ),
        ordered_attempts=(
            ReadOnlyAttemptMember(
                ordinal=0,
                attempt_id="attempt",
                initialized=_present("initialized"),
                accepted=_present("accepted"),
                outcome=Absent(),
                result_or_obligation=Absent(),
                predecessor=Absent(),
            ),
        ),
        last_retryable_failure=_present("failure"),
        counter=_present("counter"),
        attempts_consumed=1,
        next_ordinal=1,
        readonly_proof=_present("readonly"),
        snapshot=_present("snapshot"),
        execution_binding=NotApplicable(),
        crossed_binding_heads=(),
        closure_registry_id="registry",
        closure_registry_version="1",
    )
    return ExecutionSuccessorInitializationRecord(
        command_id="successor-command",
        predecessor_run=_head("predecessor"),
        successor_run=_head("successor"),
        source_cut_fingerprint="a" * 64,
        disposition_version="1",
        observation_frontier=1,
        current_bindings=FrozenRunBindings(
            objective="objective",
            requested_work="work",
            prompt_artifact=_present("prompt"),
            ordered_tool_specs=(_present("tool"),),
            generated_schema=_present("schema"),
            semantic_bindings=(),
            recipient_effect_bindings=(),
            authority_scope=_present("scope"),
            authority_mandate_heads=(),
            policy=_present("policy"),
            no_retry_boundaries=(),
        ),
        execution_fence=captured.request.cut.fence,
        original_obligations=(
            ReferenceExternalObligation(original=original, observation_frontier=1),
        ),
        inherited_no_retry_boundaries=(_head("boundary"),),
        inherited_pending_branches=(pending,),
    )


async def test_successor_initialization_decodes_real_nonempty_inheritance() -> None:
    record = await _initialization()
    decoded = decode_successor_record_member(_member(record))
    assert decoded.record == record
    assert (
        decoded.member.record_id
        == SUCCESSOR_INITIALIZATION_SCHEMA
        + ":"
        + hashlib.sha256(record.canonical_bytes()).hexdigest()
    )
    assert record.inherited_pending_branches[0].ordered_attempts[0].attempt_id == "attempt"


@pytest.mark.parametrize(
    "field", ["record_id", "fingerprint", "schema_id", "canonical_record_bytes"]
)
async def test_successor_decoder_rejects_one_mutated_member_dimension(field: str) -> None:
    supplied = _member(await _initialization())
    value: str | bytes = b"{}" if field == "canonical_record_bytes" else "rival"
    with pytest.raises(RecoveryRecordIntegrityError):
        decode_successor_record_member(supplied.model_copy(update={field: value}))


def test_successor_row_table_is_literal_and_closed() -> None:
    assert [(row.record_kind, row.schema_id) for row in SUCCESSOR_RECORD_ROWS] == [
        ("SUCCESSOR_INITIALIZATION", SUCCESSOR_INITIALIZATION_SCHEMA),
        ("SCHEDULER_LINEAGE_ADVANCE", "chiplog.scheduler.successor-lineage-advance.v1"),
        ("SCHEDULER_EPOCH_LINEAGE_OBSERVATION", "chiplog.scheduler.successor-epoch-observation.v1"),
    ]


def test_scheduler_companions_have_distinct_content_heads_from_selected_epoch_input() -> None:
    root = PhysicalRootBinding(
        selector_id="selector",
        selector_head="selector-head",
        selector_version=3,
        current_epoch_id="epoch",
        current_epoch_head="epoch-head",
    )
    lease = LeaseBinding(
        lease_head="lease-head",
        holder_id="holder",
        holder_session_id="session",
        lease_id="lease",
        generation=4,
        trusted_expiry=5,
        clock_contract_version="clock.v1",
    )
    lineage = SchedulerSuccessorLineageAdvanceRecord(
        command_id="command",
        predecessor_run=_head("predecessor"),
        successor_run=_head("successor"),
        previous_lineage=ExecutionLineageBinding(
            root_id="root",
            subject=IndividualSubject(occurrence_id="occurrence"),
            root_fingerprint="a" * 64,
            lineage_head="old-lineage-head",
            initial_run_id="initial",
            current_run_id="predecessor",
            schedule_id="schedule",
            schedule_revision="schedule-1",
            policy_revision="policy-1",
        ),
        next_current_run_id="successor",
        physical_root=root,
        lease=lease,
        source_cut_fingerprint="b" * 64,
    )
    lineage_member = _content_member(
        "SCHEDULER_LINEAGE_ADVANCE", SCHEDULER_LINEAGE_ADVANCE_SCHEMA, lineage
    )
    epoch = SchedulerSuccessorEpochObservationRecord(
        command_id="command",
        predecessor_run=_head("predecessor"),
        successor_run=_head("successor"),
        physical_root=root,
        lease=lease,
        previous_lineage=_head("old-lineage"),
        advanced_lineage=CallSubjectHead(
            subject_id="lineage",
            revision=Present(
                head=lineage_member.record_id,
                fingerprint=lineage_member.fingerprint,
            ),
        ),
        source_cut_fingerprint="b" * 64,
    )
    decoded = decode_successor_record_member(
        _content_member(
            "SCHEDULER_EPOCH_LINEAGE_OBSERVATION", SCHEDULER_EPOCH_OBSERVATION_SCHEMA, epoch
        )
    )
    assert decoded.record == epoch
    assert decoded.member.record_id != root.current_epoch_head


async def test_retained_scheduler_decoder_accepts_native_genesis_rollover_and_prior_advance() -> (
    None
):
    value = await scheduler_successor("v2")
    inputs = value.scheduler_inputs
    lineage = value.request.fence.lineage
    root = value.request.fence.physical_root

    genesis_lineage = decode_retained_successor_input(inputs.original_lineage, "original_lineage")
    genesis_selector = decode_retained_successor_input(inputs.current_selector, "current_selector")
    genesis_epoch = decode_retained_successor_input(inputs.selected_epoch, "selected_epoch")
    assert genesis_lineage.lineage == lineage
    assert genesis_selector.physical_root == root
    assert genesis_epoch.observed_current_run_id == lineage.current_run_id

    prior = _retained_native(
        lineage.root_id,
        value.scheduler_members[0].record_id,
        SCHEDULER_LINEAGE_ADVANCE_SCHEMA,
        value.scheduler_members[0].canonical_record_bytes,
    )
    prior_source = decode_retained_successor_input(prior, "original_lineage")
    assert prior_source.lineage is not None
    assert prior_source.lineage.current_run_id == value.result.successor_run.run_id

    decision = _present("rollover-decision")
    epoch_id = "rollover-epoch"
    epoch_body = {
        "lineage": lineage.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "epoch_id": epoch_id,
        "predecessor_epoch_id": root.current_epoch_id,
        "genesis": GenesisLease(lease_head="rollover-genesis").model_dump(mode="json"),
        "edge_id": "rollover-edge",
    }
    epoch = _retained_native(
        epoch_id,
        epoch_id,
        "chiplog.scheduler.rollover-physical-root.v1",
        _rollover_bytes(epoch_body),
    )
    selector_id = "rollover-selector"
    selector_body = {
        "lineage": lineage.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
        "selector_id": selector_id,
        "previous": root.model_dump(mode="json"),
        "selector_version": root.selector_version + 1,
        "selected_epoch": epoch.reference.revision.model_dump(mode="json"),
        "rollover": RolloverPredecessor(
            decision=decision, edge=_present("rollover-edge")
        ).model_dump(mode="json"),
    }
    selector = _retained_native(
        selector_id,
        decision.head + "/selector",
        "chiplog.scheduler.rollover-physical-selector.v1",
        _rollover_bytes(selector_body),
    )
    rollover_epoch = decode_retained_successor_input(epoch, "selected_epoch")
    rollover_selector = decode_retained_successor_input(selector, "current_selector")
    assert rollover_epoch.lineage == lineage
    assert rollover_selector.lineage == lineage
    assert rollover_selector.selected_epoch == epoch.reference.revision


@pytest.mark.parametrize("mutation", ("schema", "bytes", "physical_id"))
async def test_successor_validator_rejects_mutated_native_selected_source(mutation: str) -> None:
    value = await scheduler_successor("v3")
    original = value.scheduler_inputs.current_selector
    if mutation == "schema":
        changed = original.model_copy(update={"schema_id": "chiplog.scheduler.physical-root.v1"})
    elif mutation == "bytes":
        changed = _retained_native(
            original.reference.subject_id,
            original.reference.revision.head,
            original.schema_id,
            b"{}",
        )
    else:
        changed = _retained_native(
            original.reference.subject_id,
            "rival-physical-selector",
            original.schema_id,
            original.canonical_record_bytes,
        )
    inputs = value.scheduler_inputs.model_copy(update={"current_selector": changed})
    result = value.result.model_copy(
        update={
            "lineage_advance": value.result.lineage_advance.model_copy(
                update={"current_selector": changed.reference}
            )
        }
    )
    with pytest.raises(
        RecoveryRecordIntegrityError, match=r"retained scheduler|unsupported retained"
    ):
        validate_execution_successor_records(
            value.request,
            result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (value.initialization_member,),
            value.scheduler_members,
            inputs,
        )


async def test_non_scheduler_successor_validates_real_request_result_and_members() -> None:
    value = await non_scheduler_successor()
    validate_execution_successor_records(
        value.request,
        value.result,
        value.edge_member,
        value.baseline_member,
        value.pair_member,
        (value.initialization_member,),
        (),
        None,
    )


@pytest.mark.parametrize("initializations", [(), ("one", "two")])
async def test_successor_join_rejects_missing_or_multiple_initialization_members(
    initializations: tuple[str, ...],
) -> None:
    value = await non_scheduler_successor()
    members = tuple(value.initialization_member for _ in initializations)
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_execution_successor_records(
            value.request,
            value.result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            members,
            (),
            None,
        )


async def test_successor_join_rejects_fence_mutation() -> None:
    value = await non_scheduler_successor()
    initialization = decode_successor_record_member(value.initialization_member).record
    assert isinstance(initialization, ExecutionSuccessorInitializationRecord)
    changed = content_member(
        "SUCCESSOR_INITIALIZATION",
        SUCCESSOR_INITIALIZATION_SCHEMA,
        initialization.model_copy(
            update={
                "execution_fence": initialization.execution_fence.model_copy(
                    update={"run_head": "rival-head"}
                )
            }
        ),
    )
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_execution_successor_records(
            value.request,
            value.result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (changed,),
            (),
            None,
        )


@pytest.mark.parametrize("field", ("source_request_fingerprint", "edge"))
async def test_successor_join_rejects_result_binding_mutation(field: str) -> None:
    value = await non_scheduler_successor()
    changed: str | object
    if field == "source_request_fingerprint":
        changed = "0" * 64
    else:
        changed = value.result.edge.model_copy(update={"command_id": "rival-command"})
    result = value.result.model_copy(update={field: changed})
    with pytest.raises(RecoveryRecordIntegrityError, match=r"result successor edge|source request"):
        validate_execution_successor_records(
            value.request,
            result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (value.initialization_member,),
            (),
            None,
        )


@pytest.mark.parametrize("version", ("v2", "v3"))
async def test_scheduler_successor_validates_native_versioned_runs_and_companions(
    version: Literal["v2", "v3"],
) -> None:
    value = await scheduler_successor(version)
    assert (
        value.scheduler_inputs.original_lineage.schema_id == "chiplog.scheduler.stable-lineage.v1"
    )
    assert (
        value.scheduler_inputs.current_selector.schema_id
        == "chiplog.scheduler.physical-selector.v1"
    )
    assert value.scheduler_inputs.selected_epoch.schema_id == "chiplog.scheduler.physical-root.v1"
    validate_execution_successor_records(
        value.request,
        value.result,
        value.edge_member,
        value.baseline_member,
        value.pair_member,
        (value.initialization_member,),
        value.scheduler_members,
        value.scheduler_inputs,
    )


async def test_scheduler_successor_rejects_changed_selected_input_reference() -> None:
    value = await scheduler_successor("v2")
    altered_inputs = value.scheduler_inputs.model_copy(
        update={
            "current_selector": value.scheduler_inputs.current_selector.model_copy(
                update={"reference": _head("rival-selector")}
            )
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="selector input"):
        validate_execution_successor_records(
            value.request,
            value.result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (value.initialization_member,),
            value.scheduler_members,
            altered_inputs,
        )


async def test_scheduler_successor_rejects_rehashed_lineage_companion() -> None:
    value = await scheduler_successor("v3")
    lineage = decode_successor_record_member(value.scheduler_members[0]).record
    assert isinstance(lineage, SchedulerSuccessorLineageAdvanceRecord)
    altered_lineage = content_member(
        "SCHEDULER_LINEAGE_ADVANCE",
        SCHEDULER_LINEAGE_ADVANCE_SCHEMA,
        lineage.model_copy(update={"next_current_run_id": "rival-successor"}),
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="lineage bytes"):
        validate_execution_successor_records(
            value.request,
            value.result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (value.initialization_member,),
            (altered_lineage, value.scheduler_members[1]),
            value.scheduler_inputs,
        )


async def test_scheduler_successor_rejects_lineage_for_a_different_predecessor() -> None:
    value = await scheduler_successor("v2")
    lineage = decode_successor_record_member(value.scheduler_members[0]).record
    assert isinstance(lineage, SchedulerSuccessorLineageAdvanceRecord)
    altered_lineage = content_member(
        "SCHEDULER_LINEAGE_ADVANCE",
        SCHEDULER_LINEAGE_ADVANCE_SCHEMA,
        lineage.model_copy(
            update={
                "previous_lineage": lineage.previous_lineage.model_copy(
                    update={"current_run_id": "rival-predecessor"}
                )
            }
        ),
    )
    result = value.result.model_copy(
        update={
            "lineage_advance": value.result.lineage_advance.model_copy(
                update={"canonical_lineage_bytes": altered_lineage.canonical_record_bytes}
            )
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="lineage predecessor source"):
        validate_execution_successor_records(
            value.request,
            result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (value.initialization_member,),
            (altered_lineage, value.scheduler_members[1]),
            value.scheduler_inputs,
        )


async def test_scheduler_successor_rejects_epoch_companion_with_other_lineage_subject() -> None:
    value = await scheduler_successor("v2")
    epoch = decode_successor_record_member(value.scheduler_members[1]).record
    assert isinstance(epoch, SchedulerSuccessorEpochObservationRecord)
    altered_epoch = content_member(
        "SCHEDULER_EPOCH_LINEAGE_OBSERVATION",
        SCHEDULER_EPOCH_OBSERVATION_SCHEMA,
        epoch.model_copy(
            update={
                "advanced_lineage": epoch.advanced_lineage.model_copy(
                    update={"subject_id": "rival"}
                )
            }
        ),
    )
    result = value.result.model_copy(
        update={
            "lineage_advance": value.result.lineage_advance.model_copy(
                update={"canonical_epoch_observation_bytes": altered_epoch.canonical_record_bytes}
            )
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="advanced lineage subject"):
        validate_execution_successor_records(
            value.request,
            result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (value.initialization_member,),
            (value.scheduler_members[0], altered_epoch),
            value.scheduler_inputs,
        )


async def test_scheduler_successor_rejects_actual_other_run_version() -> None:
    value = await scheduler_successor("v3")
    v2 = await scheduler_successor("v2")
    altered_result = value.result.model_copy(update={"successor_run": v2.result.successor_run})
    with pytest.raises(RecoveryRecordIntegrityError, match="successor Run predecessor"):
        validate_execution_successor_records(
            value.request,
            altered_result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (value.initialization_member,),
            value.scheduler_members,
            value.scheduler_inputs,
        )


@pytest.mark.parametrize("version", ("v2", "v3"))
@pytest.mark.parametrize("source", ("genesis", "rollover"))
async def test_repeated_scheduler_successors_keep_native_epoch_and_selector_bytes(
    version: Literal["v2", "v3"],
    source: Literal["genesis", "rollover"],
) -> None:
    chain = await repeated_scheduler_successors(version, source)
    for value in (chain.first, chain.second):
        validate_execution_successor_records(
            value.request,
            value.result,
            value.edge_member,
            value.baseline_member,
            value.pair_member,
            (value.initialization_member,),
            value.scheduler_members,
            value.scheduler_inputs,
        )
    assert (
        chain.first.scheduler_inputs.current_selector.canonical_record_bytes
        == chain.second.scheduler_inputs.current_selector.canonical_record_bytes
    )
    assert (
        chain.first.scheduler_inputs.selected_epoch.canonical_record_bytes
        == chain.second.scheduler_inputs.selected_epoch.canonical_record_bytes
    )
    assert isinstance(chain.first.scheduler_inputs.continuity, EpochCreationLineageWitness)
    assert isinstance(
        chain.second.scheduler_inputs.continuity, PriorSuccessorObservationLineageWitness
    )
    assert chain.second.scheduler_inputs.continuity.observation == chain.first.scheduler_members[1]
    ancestry = chain.second.scheduler_inputs.continuity.ancestry.ordered_runs
    runs = tuple(decode_execution_run_member(source.member).run for source in ancestry)
    assert tuple(run.state for run in runs) == ("CREATED", "ACTIVE", "ACTIVE", "SUSPENDED")
    assert runs[2].event == "ModelResponseCaptured"
    assert tuple(run.predecessor for run in runs[1:]) == tuple(run.head for run in runs[:-1])
    assert ancestry[-1].member.record_id == chain.second.request.run.head
    baseline = chain.second.request.original_suspension.baseline
    pair = chain.second.request.original_suspension.pair
    assert baseline.source_cut_fingerprint == pair.source_cut_fingerprint
    assert baseline.source_cut_fingerprint != chain.second.request.cut.digest()
    assert baseline.predecessor_run != chain.second.request.cut.current_run
    assert (
        baseline.frontier.tenant_commit_sequence
        < chain.second.request.cut.frontier.tenant_commit_sequence
    )


def _validate_repeated_second(
    chain: RepeatedSchedulerSuccessorFixture,
    inputs: SchedulerSuccessorInputs,
    result: PreparedExecutionSuccessor | None = None,
) -> None:
    fixture = chain.second
    validate_execution_successor_records(
        fixture.request,
        fixture.result if result is None else result,
        fixture.edge_member,
        fixture.baseline_member,
        fixture.pair_member,
        (fixture.initialization_member,),
        fixture.scheduler_members,
        inputs,
    )


def _continuity_with_observation(
    chain: RepeatedSchedulerSuccessorFixture, member: RecoveryRecordMember
) -> PriorSuccessorObservationLineageWitness:
    continuity = chain.second.scheduler_inputs.continuity
    assert isinstance(continuity, PriorSuccessorObservationLineageWitness)
    reference = CallSubjectHead(
        subject_id=member.record_id,
        revision=Present(head=member.record_id, fingerprint=member.fingerprint),
    )
    request = continuity.ancestry_request.model_copy(update={"prior_epoch_observation": reference})
    ancestry = continuity.ancestry.model_copy(
        update={
            "prior_epoch_observation": reference,
            "source_request_fingerprint": successor_ancestry_request_fingerprint(request),
        }
    )
    return continuity.model_copy(
        update={"observation": member, "ancestry_request": request, "ancestry": ancestry}
    )


def _continuity_with_ancestry(
    chain: RepeatedSchedulerSuccessorFixture, **changes: object
) -> PriorSuccessorObservationLineageWitness:
    continuity = chain.second.scheduler_inputs.continuity
    assert isinstance(continuity, PriorSuccessorObservationLineageWitness)
    return continuity.model_copy(
        update={"ancestry": continuity.ancestry.model_copy(update=changes)}
    )


def _reissued_run_member(run: ExecutionRun, **changes: object) -> ExecutionRunCanonicalMember:
    values = run.model_dump()
    values.update(changes, head="pending")
    pending = type(run).model_validate(values)
    native = pending.model_copy(update={"head": "loop:" + pending.digest()})
    raw = native.canonical_bytes()
    return ExecutionRunCanonicalMember(
        record_id=native.head,
        schema_id=native.schema_id,
        canonical_record_bytes=raw,
        fingerprint=hashlib.sha256(raw).hexdigest(),
    )


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("advanced", "prior observation advanced lineage"),
        ("predecessor", "prior observation predecessor"),
        ("root", "prior observation physical root"),
        ("previous_lineage", "prior observation previous lineage head"),
    ),
)
async def test_repeated_scheduler_successor_rejects_mutated_prior_observation_join(
    mutation: str, reason: str
) -> None:
    chain = await repeated_scheduler_successors("v2")
    observation = decode_successor_record_member(chain.first.scheduler_members[1]).record
    assert isinstance(observation, SchedulerSuccessorEpochObservationRecord)
    if mutation == "advanced":
        changed = observation.model_copy(update={"advanced_lineage": _head("rival-advance")})
    elif mutation == "predecessor":
        changed = observation.model_copy(update={"predecessor_run": _head("rival-predecessor")})
    elif mutation == "root":
        changed = observation.model_copy(
            update={
                "physical_root": observation.physical_root.model_copy(
                    update={"selector_id": "rival"}
                )
            }
        )
    else:
        changed = observation.model_copy(
            update={"previous_lineage": _head("rival-previous-lineage")}
        )
    member = content_member(
        "SCHEDULER_EPOCH_LINEAGE_OBSERVATION", SCHEDULER_EPOCH_OBSERVATION_SCHEMA, changed
    )
    inputs = chain.second.scheduler_inputs.model_copy(
        update={"continuity": _continuity_with_observation(chain, member)}
    )
    with pytest.raises(RecoveryRecordIntegrityError, match=reason):
        _validate_repeated_second(chain, inputs)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    (
        ("command", "prior observation command"),
        ("cut", "prior observation source cut"),
        ("current_run", "prior advance current run"),
    ),
)
async def test_repeated_scheduler_successor_rejects_mutated_prior_advance_join(
    mutation: str, reason: str
) -> None:
    chain = await repeated_scheduler_successors("v3")
    advance = decode_successor_record_member(chain.first.scheduler_members[0]).record
    observation = decode_successor_record_member(chain.first.scheduler_members[1]).record
    assert isinstance(advance, SchedulerSuccessorLineageAdvanceRecord)
    assert isinstance(observation, SchedulerSuccessorEpochObservationRecord)
    if mutation == "command":
        changed_advance = advance.model_copy(update={"command_id": "rival-command"})
    elif mutation == "cut":
        changed_advance = advance.model_copy(update={"source_cut_fingerprint": "0" * 64})
    else:
        changed_advance = advance.model_copy(update={"next_current_run_id": "rival-current-run"})
    advance_member = content_member(
        "SCHEDULER_LINEAGE_ADVANCE", SCHEDULER_LINEAGE_ADVANCE_SCHEMA, changed_advance
    )
    first_lineage = chain.first.request.fence.lineage
    assert isinstance(first_lineage, ExecutionLineageBinding)
    original = _retained_native(
        first_lineage.root_id,
        advance_member.record_id,
        advance_member.schema_id,
        advance_member.canonical_record_bytes,
    )
    changed_observation = content_member(
        "SCHEDULER_EPOCH_LINEAGE_OBSERVATION",
        SCHEDULER_EPOCH_OBSERVATION_SCHEMA,
        observation.model_copy(
            update={
                "advanced_lineage": CallSubjectHead(
                    subject_id=observation.advanced_lineage.subject_id,
                    revision=Present(
                        head=advance_member.record_id, fingerprint=advance_member.fingerprint
                    ),
                )
            }
        ),
    )
    continuity = _continuity_with_observation(chain, changed_observation)
    ancestry_request = continuity.ancestry_request.model_copy(
        update={"prior_lineage_advance": original.reference}
    )
    continuity = continuity.model_copy(
        update={
            "ancestry_request": ancestry_request,
            "ancestry": continuity.ancestry.model_copy(
                update={
                    "prior_lineage_advance": original.reference,
                    "source_request_fingerprint": successor_ancestry_request_fingerprint(
                        ancestry_request
                    ),
                }
            ),
        }
    )
    inputs = chain.second.scheduler_inputs.model_copy(
        update={"original_lineage": original, "continuity": continuity}
    )
    result = chain.second.result.model_copy(
        update={
            "lineage_advance": chain.second.result.lineage_advance.model_copy(
                update={"original_lineage": original.reference}
            )
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError, match=reason):
        _validate_repeated_second(chain, inputs, result)


@pytest.mark.parametrize("mutation", ("omit", "reorder", "duplicate", "fingerprint", "sequence"))
async def test_repeated_scheduler_successor_rejects_incomplete_or_unselected_ancestry(
    mutation: str,
) -> None:
    chain = await repeated_scheduler_successors("v2")
    continuity = chain.second.scheduler_inputs.continuity
    assert isinstance(continuity, PriorSuccessorObservationLineageWitness)
    ordered = continuity.ancestry.ordered_runs
    if mutation == "omit":
        changes: dict[str, object] = {"ordered_runs": (ordered[0], ordered[1], ordered[3])}
    elif mutation == "reorder":
        changes = {"ordered_runs": (ordered[0], ordered[2], ordered[1], ordered[3])}
    elif mutation == "duplicate":
        changes = {"ordered_runs": (ordered[0], ordered[1], ordered[1], ordered[3])}
    elif mutation == "fingerprint":
        changes = {"source_request_fingerprint": "0" * 64}
    else:
        changes = {
            "ordered_runs": (
                ordered[0],
                ordered[1].model_copy(update={"selected_commit_sequence": 1}),
                ordered[2],
                ordered[3],
            )
        }
    inputs = chain.second.scheduler_inputs.model_copy(
        update={"continuity": _continuity_with_ancestry(chain, **changes)}
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="invalid retained successor ancestry"):
        _validate_repeated_second(chain, inputs)


async def test_repeated_scheduler_successor_rejects_real_rollover_selector_epoch_splice() -> None:
    left = await repeated_scheduler_successors("v3", "rollover")
    right = await repeated_scheduler_successors("v3", "rollover-alt")
    inputs = left.second.scheduler_inputs.model_copy(
        update={"current_selector": right.second.scheduler_inputs.current_selector}
    )
    result = left.second.result.model_copy(
        update={
            "lineage_advance": left.second.result.lineage_advance.model_copy(
                update={"current_selector": inputs.current_selector.reference}
            )
        }
    )
    with pytest.raises(
        RecoveryRecordIntegrityError, match="selected physical root differs from fence"
    ):
        _validate_repeated_second(left, inputs, result)


@pytest.mark.parametrize("mutation", ("foreign", "mixed_version"))
async def test_repeated_scheduler_successor_rejects_native_foreign_or_mixed_ancestry(
    mutation: str,
) -> None:
    chain = await repeated_scheduler_successors("v2")
    continuity = chain.second.scheduler_inputs.continuity
    assert isinstance(continuity, PriorSuccessorObservationLineageWitness)
    ordered = continuity.ancestry.ordered_runs
    original = decode_execution_run_member(ordered[1].member).run
    if mutation == "foreign":
        member = _reissued_run_member(original, run_id="foreign-same-root-run")
    else:
        template = execution_run_v3()
        member = _reissued_run_member(
            template,
            tenant=original.tenant,
            principal=original.principal,
            run_id=original.run_id,
            root_binding=original.root_binding,
            predecessor=ordered[0].member.record_id,
            state="ACTIVE",
            event="ModelResponseCaptured",
        )
    changed = ordered[1].model_copy(update={"member": member})
    inputs = chain.second.scheduler_inputs.model_copy(
        update={
            "continuity": _continuity_with_ancestry(
                chain, ordered_runs=(ordered[0], changed, ordered[2], ordered[3])
            )
        }
    )
    with pytest.raises(RecoveryRecordIntegrityError, match="invalid retained successor ancestry"):
        _validate_repeated_second(chain, inputs)


@pytest.mark.parametrize("mutation", ("current_cut", "foreign_pair", "historical_hash"))
async def test_repeated_scheduler_successor_rejects_cross_cut_substitution(mutation: str) -> None:
    chain = await repeated_scheduler_successors("v3")
    fixture = chain.second
    continuity = fixture.scheduler_inputs.continuity
    assert isinstance(continuity, PriorSuccessorObservationLineageWitness)
    if mutation == "current_cut":
        captured = decode_execution_run_member(continuity.ancestry.ordered_runs[-2].member).run
        request = fixture.request.model_copy(
            update={
                "cut": fixture.request.cut.model_copy(update={"current_run": _head(captured.head)})
            }
        )
        pair_member = fixture.pair_member
        baseline_member = fixture.baseline_member
    elif mutation == "foreign_pair":
        foreign_pair = fixture.request.original_suspension.pair.model_copy(
            update={"suspended_run": _head("foreign-suspended")}
        )
        pair_member = content_member(
            "SUSPENSION_PAIR", "chiplog.execution.suspension-pair.v2", foreign_pair
        )
        selected = fixture.request.original_suspension.model_copy(
            update={
                "pair": foreign_pair,
                "canonical_pair_bytes": foreign_pair.canonical_bytes(),
                "selected_pair": CallSubjectHead(
                    subject_id=fixture.request.original_suspension.selected_pair.subject_id,
                    revision=Present(
                        head=pair_member.record_id, fingerprint=pair_member.fingerprint
                    ),
                ),
            }
        )
        request = fixture.request.model_copy(update={"original_suspension": selected})
        baseline_member = fixture.baseline_member
    else:
        baseline = fixture.request.original_suspension.baseline.model_copy(
            update={"source_cut_fingerprint": "0" * 64}
        )
        baseline_member = RecoveryRecordMember(
            owner="agent_loop",
            record_kind="SUSPENSION_BASELINE",
            schema_id="chiplog.execution.suspension-baseline.v2",
            record_id=baseline.baseline_id,
            canonical_record_bytes=baseline.canonical_bytes(),
            fingerprint=hashlib.sha256(baseline.canonical_bytes()).hexdigest(),
        )
        request = fixture.request.model_copy(
            update={
                "original_suspension": fixture.request.original_suspension.model_copy(
                    update={"baseline": baseline}
                )
            }
        )
        pair_member = fixture.pair_member
    with pytest.raises(RecoveryRecordIntegrityError):
        validate_execution_successor_records(
            request,
            fixture.result,
            fixture.edge_member,
            baseline_member,
            pair_member,
            (fixture.initialization_member,),
            fixture.scheduler_members,
            fixture.scheduler_inputs,
        )
