"""Owner wire preparation tests; canonical process wiring and broker authority separate."""

import base64
import hashlib
import json

import pytest
from tests.capabilities.agent_loop.support import DIGEST, issue, view

from chiplog.capabilities.agent_loop._scheduler_process import ROUTES, dispatch
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    FirstPublication,
    PreRootDecisionFence,
    Present,
)
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    FixedIntervalDefinition,
    policy_head,
    schedule_head,
    stream_definition,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    DecideIntervalCommand,
    DueCoordinate,
    SchedulerCommandIdentity,
    SchedulerContextRef,
    SchedulerEligibilityBoundary,
    SchedulerEligibilityManifest,
    SchedulerIntervalBound,
    SchedulerOverflowHold,
)
from chiplog.capabilities.agent_loop.scheduler_domain import CANONICAL_VERSION, COORDINATE_VERSION
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerRunInputs
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BATCH_SCHEMA,
    PREPARED_SCHEMA,
    BatchPreparationCut,
    ConfigurationGenesisCommand,
    ConfigurationPolicyCommand,
    ConfigurationPreparationRequest,
    ConfigurationPreparationSnapshot,
    ConfigurationScheduleCommand,
    IntervalPreparationRequest,
    IntervalPreparationSnapshot,
    LeasePreparationRequest,
    LeasePreparationSnapshot,
    PreparedSchedulerBatch,
    PreparedSchedulerRecord,
    SchedulerLeaseTransitionRecord,
    prepare_configuration,
    prepare_lease,
)


def request() -> LeasePreparationRequest:
    current = view()
    command, issued = issue(current, "ACQUIRE")
    return LeasePreparationRequest(
        operation="scheduler.acquire",
        context=SchedulerContextRef(
            tenant_id="tenant",
            service_identity="worker",
            session_id="session",
            mandate_head="mandate",
            issuance_id="context-issuance",
            issuance_fingerprint=DIGEST,
        ),
        snapshot=LeasePreparationSnapshot(
            tenant_id="tenant",
            tenant_frontier=7,
            materialization_commitment=DIGEST,
            registry_head="registry",
            registry_fingerprint=DIGEST,
            current=current,
            issued=issued,
            submission_id="submission",
        ),
        command_bytes=command.canonical_bytes(),
    )


def test_registered_wire_route_returns_only_complete_canonical_owner_record() -> None:
    command = request()
    response = dispatch("scheduler.prepare_lease", command.canonical_bytes())
    assert response["schema_id"] == PREPARED_SCHEMA
    assert isinstance(response["payload"], str)
    result = PreparedSchedulerRecord.model_validate_json(base64.b64decode(response["payload"]))
    assert result == prepare_lease(command)
    assert hashlib.sha256(result.canonical_record_bytes).hexdigest() == result.fingerprint
    record = SchedulerLeaseTransitionRecord.model_validate_json(result.canonical_record_bytes)
    assert record.canonical_bytes() == result.canonical_record_bytes
    assert record.expected_tenant_frontier == 7
    assert record.previous.lease.kind == "UNLEASED"
    assert record.candidate.state.kind == "HELD"
    assert result.record_id == record.candidate.transition_id
    assert ROUTES[0] == (
        "scheduler.prepare_lease",
        "broker",
        "agent_loop",
        "chiplog.scheduler.lease-preparation.v1",
        PREPARED_SCHEMA,
    )


@pytest.mark.parametrize("mutation", ["tenant", "session", "operation", "proof", "cut"])
def test_wire_mutations_fail_before_any_candidate_response(mutation: str) -> None:
    original = request()
    changed = original
    if mutation == "tenant":
        changed = original.model_copy(
            update={
                "context": original.context.model_copy(
                    update={"tenant_id": "other"},
                )
            }
        )
    elif mutation == "session":
        changed = original.model_copy(
            update={
                "context": original.context.model_copy(
                    update={"session_id": "other"},
                )
            }
        )
    elif mutation == "operation":
        changed = original.model_copy(update={"operation": "scheduler.renew"})
    elif mutation == "proof":
        changed = original.model_copy(
            update={
                "snapshot": original.snapshot.model_copy(
                    update={"issued": None},
                )
            }
        )
    else:
        changed = original.model_copy(
            update={
                "snapshot": original.snapshot.model_copy(
                    update={"submission_id": "other"},
                )
            }
        )
    response = dispatch("scheduler.prepare_lease", changed.canonical_bytes())
    assert response["failure"] == "PROTOCOL_REJECTED"
    assert "payload" not in response


def test_noncanonical_envelope_or_command_and_unknown_route_reject() -> None:
    original = request()
    assert dispatch("unregistered", original.canonical_bytes())["failure"] == "PROTOCOL_REJECTED"
    spaced = json.dumps(json.loads(original.canonical_bytes())).encode()
    assert dispatch("scheduler.prepare_lease", spaced)["failure"] == "PROTOCOL_REJECTED"
    changed = original.model_copy(
        update={
            "command_bytes": json.dumps(json.loads(original.command_bytes)).encode(),
        }
    )
    assert dispatch("scheduler.prepare_lease", changed.canonical_bytes())["failure"] == (
        "PROTOCOL_REJECTED"
    )


def configuration_request() -> ConfigurationPreparationRequest:
    context = request().context
    command = ConfigurationGenesisCommand(
        identity=SchedulerCommandIdentity(
            command_id="genesis", schema_version="1", canonicalization_version=CANONICAL_VERSION
        ),
        schedule_id="schedule",
        policy="MATERIALIZE_EACH",
        definition=FixedIntervalDefinition(
            start_ns=10,
            period_ns=10,
            end_exclusive_ns="NO_END",
            run_inputs=SchedulerRunInputs(
                tenant="tenant",
                principal="principal",
                prompt="scheduled",
                policy=BudgetPolicy(),
                origin=EndpointSelection(
                    kind="ORIGIN_EXACT",
                    ingress_binding_head="ingress",
                    endpoint_head="endpoint",
                    endpoint_id="endpoint",
                    provider="hermetic-local",
                    recipient="principal",
                    canonical_address="local://principal",
                    credential_binding_head="credential",
                ),
                contour_head="contour",
                policy_head="policy",
                worker_session="worker",
                authority_epoch="epoch",
            ),
        ),
        bound=SchedulerIntervalBound(
            max_member_count=3, max_manifest_bytes=100000, max_serialized_batch_bytes=1000000
        ),
    )
    cut = BatchPreparationCut(
        tenant_id="tenant",
        tenant_frontier=7,
        materialization_commitment=DIGEST,
        registry_head="registry",
        registry_fingerprint=DIGEST,
        submission_id="submission",
        authorized_context=context,
        authorized_command_fingerprint=hashlib.sha256(command.canonical_bytes()).hexdigest(),
        authority_proof=Present(head="authorized", fingerprint=DIGEST),
    )
    return ConfigurationPreparationRequest(
        operation="scheduler.genesis",
        context=context,
        command_bytes=command.canonical_bytes(),
        snapshot=ConfigurationPreparationSnapshot(
            cut=cut,
            current=ConfigurationSnapshot(
                schedule=None, policy=None, bound=None, active_hold=Absent()
            ),
            authority_epoch="epoch",
            broker_generation="broker",
            runtime_graph_generation="graph",
            scheduler_authority_head="authority",
        ),
    )


def interval_request(cutoff: str = "30") -> IntervalPreparationRequest:
    config_request = configuration_request()
    prepared = prepare_configuration(config_request)
    from chiplog.capabilities.agent_loop.scheduler_configuration import ConfigurationProposal

    assert isinstance(prepared.result, ConfigurationProposal)
    current = prepared.result.proposed
    assert current.schedule and current.policy and current.bound
    boundary = SchedulerEligibilityBoundary(
        schedule_definition_head=schedule_head(current.schedule),
        missed_occurrence_policy_head=policy_head(current.policy),
        previous_due_boundary=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate="10"
        ),
        cutoff_due_coordinate=DueCoordinate(
            coordinate_policy_version=COORDINATE_VERSION, canonical_coordinate=cutoff
        ),
        enumeration_frontier="frontier",
        predecessor_interval=Absent(),
        canonicalization_version=CANONICAL_VERSION,
    )
    measured = stream_definition(
        current, boundary, (), Present(head="enumeration", fingerprint=DIGEST)
    )
    manifest = (
        measured.evidence.manifest
        if measured.evidence.kind == "FULL_MANIFEST"
        else measured.evidence
    )
    fence = PreRootDecisionFence(
        command_id="interval",
        disposition=FirstPublication(
            decision=Absent(), expected_canonical_absence_manifest="absence"
        ),
        scheduler_authority_head="authority",
        broker_generation="broker",
        runtime_generation="graph",
    )
    command = DecideIntervalCommand(
        identity=SchedulerCommandIdentity(
            command_id="interval", schema_version="1", canonicalization_version=CANONICAL_VERSION
        ),
        boundary=boundary,
        bound_head=current.bound,
        manifest=manifest,
        publication_fence=fence,
    )
    return IntervalPreparationRequest(
        operation="scheduler.decide_interval",
        context=config_request.context,
        command_bytes=command.canonical_bytes(),
        snapshot=IntervalPreparationSnapshot(
            cut=config_request.snapshot.cut.model_copy(
                update={
                    "authorized_command_fingerprint": hashlib.sha256(
                        command.canonical_bytes()
                    ).hexdigest()
                }
            ),
            configuration=current,
            disposed=(),
            previous_due_boundary=boundary.previous_due_boundary,
            predecessor_interval=Absent(),
            enumeration_frontier="frontier",
            enumeration_proof=Present(head="enumeration", fingerprint=DIGEST),
            active_hold=None,
            publication_fence=fence,
            operator_proof=None,
        ),
    )


def test_configuration_wire_genesis_and_each_revision_output_exact_records() -> None:
    original = configuration_request()
    response = dispatch("scheduler.prepare_configuration", original.canonical_bytes())
    assert response["schema_id"] == BATCH_SCHEMA and isinstance(response["payload"], str)
    result = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    assert len(result.records) == 3
    assert (
        result.source_request_fingerprint == hashlib.sha256(original.canonical_bytes()).hexdigest()
    )
    from chiplog.capabilities.agent_loop.scheduler_configuration import ConfigurationProposal

    assert isinstance(result.result, ConfigurationProposal)
    current = result.result.proposed
    assert current.schedule and current.policy
    for command in (
        ConfigurationPolicyCommand(
            identity=SchedulerCommandIdentity(
                command_id="policy", schema_version="1", canonicalization_version=CANONICAL_VERSION
            ),
            observed_schedule=schedule_head(current.schedule),
            observed_policy=policy_head(current.policy),
            policy="SKIP",
        ),
        ConfigurationScheduleCommand(
            identity=SchedulerCommandIdentity(
                command_id="schedule",
                schema_version="1",
                canonicalization_version=CANONICAL_VERSION,
            ),
            observed_schedule=schedule_head(current.schedule),
            observed_policy=policy_head(current.policy),
            definition=current.schedule.definition,
        ),
    ):
        updated = original.model_copy(
            update={
                "operation": "scheduler." + command.kind.lower(),
                "command_bytes": command.canonical_bytes(),
                "snapshot": original.snapshot.model_copy(
                    update={
                        "current": current,
                        "cut": original.snapshot.cut.model_copy(
                            update={
                                "authorized_command_fingerprint": hashlib.sha256(
                                    command.canonical_bytes()
                                ).hexdigest()
                            }
                        ),
                    }
                ),
            }
        )
        response = dispatch("scheduler.prepare_configuration", updated.canonical_bytes())
        assert isinstance(response["payload"], str)
        amended = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
        assert len(amended.records) == 1
        for record in amended.records:
            assert (
                hashlib.sha256(base64.b64decode(record.canonical_base64)).hexdigest()
                == record.fingerprint
            )
        held = updated.model_copy(
            update={
                "snapshot": updated.snapshot.model_copy(
                    update={
                        "current": current.model_copy(
                            update={"active_hold": Present(head="hold", fingerprint=DIGEST)}
                        )
                    }
                )
            }
        )
        assert (
            dispatch("scheduler.prepare_configuration", held.canonical_bytes())["failure"]
            == "PROTOCOL_REJECTED"
        )


def test_interval_wire_derives_runs_from_definition_and_overflow_cannot_be_success() -> None:
    original = interval_request()
    response = dispatch("scheduler.prepare_interval", original.canonical_bytes())
    assert isinstance(response["payload"], str)
    result = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    from chiplog.capabilities.agent_loop.scheduler_materialization import IntervalCandidate

    assert isinstance(result.result, IntervalCandidate)
    assert len(result.result.occurrences) == 2
    assert all(row.run.tenant == "tenant" for row in result.result.occurrences)
    oversized = interval_request("50")
    response = dispatch("scheduler.prepare_interval", oversized.canonical_bytes())
    assert isinstance(response["payload"], str)
    held = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    assert isinstance(held.result, IntervalCandidate)
    assert isinstance(held.result.result, SchedulerOverflowHold)
    assert (
        held.result.result.actual_value == 4 and held.result.result.evidence.kind == "FULL_MANIFEST"
    )
    assert len(held.records) == 1 and held.result.occurrences == ()


@pytest.mark.parametrize(
    "mutation", ["context", "command", "boundary", "fence", "membership", "schema"]
)
def test_interval_wire_cannot_substitute_authority_or_enumeration(mutation: str) -> None:
    original = interval_request()
    command = DecideIntervalCommand.model_validate_json(original.command_bytes)
    if mutation == "context":
        changed = original.model_copy(
            update={"context": original.context.model_copy(update={"session_id": "other"})}
        )
    elif mutation == "schema":
        response = dispatch("scheduler.prepare_rollover", original.canonical_bytes())
        assert response["failure"] == "PROTOCOL_REJECTED"
        return
    else:
        if mutation == "boundary":
            command = command.model_copy(
                update={
                    "boundary": command.boundary.model_copy(
                        update={"enumeration_frontier": "other"}
                    )
                }
            )
        elif mutation == "fence":
            command = command.model_copy(
                update={
                    "publication_fence": command.publication_fence.model_copy(
                        update={"broker_generation": "other"}
                    )
                }
            )
        elif mutation == "membership":
            command = command.model_copy(
                update={"manifest": command.manifest.model_copy(update={"members": ()})}
            )
        else:
            command = command.model_copy(
                update={"identity": command.identity.model_copy(update={"command_id": "other"})}
            )
        snapshot = original.snapshot
        if mutation != "command":
            snapshot = snapshot.model_copy(
                update={
                    "cut": snapshot.cut.model_copy(
                        update={
                            "authorized_command_fingerprint": hashlib.sha256(
                                command.canonical_bytes()
                            ).hexdigest()
                        }
                    )
                }
            )
        changed = original.model_copy(
            update={"command_bytes": command.canonical_bytes(), "snapshot": snapshot}
        )
    assert (
        dispatch("scheduler.prepare_interval", changed.canonical_bytes())["failure"]
        == "PROTOCOL_REJECTED"
    )


def test_resolution_wire_requires_exact_held_snapshot_and_operator_proof() -> None:
    from chiplog.capabilities.agent_loop.scheduler_configuration import replace_bound
    from chiplog.capabilities.agent_loop.scheduler_contracts import (
        ResolveIntervalCommand,
        SchedulerOverflowHold,
    )
    from chiplog.capabilities.agent_loop.scheduler_materialization import IntervalCandidate

    original = interval_request()
    config = original.snapshot.configuration
    assert config.bound
    tight = replace_bound(
        config,
        observed=config.bound,
        proposed_bound=config.bound.bound.model_copy(update={"max_manifest_bytes": 1}),
        context=original.context,
        command_id="tight",
        authority_epoch="epoch",
        broker_generation="broker",
        runtime_graph_generation="graph",
    ).proposed
    command = DecideIntervalCommand.model_validate_json(original.command_bytes)
    assert tight.bound
    assert isinstance(command.manifest, SchedulerEligibilityManifest)
    full_manifest = command.manifest
    measured = stream_definition(tight, command.boundary, (), original.snapshot.enumeration_proof)
    assert measured.evidence.kind == "STREAMING_MANIFEST"
    command = command.model_copy(update={"bound_head": tight.bound, "manifest": measured.evidence})
    tight_request = original.model_copy(
        update={
            "command_bytes": command.canonical_bytes(),
            "snapshot": original.snapshot.model_copy(
                update={
                    "configuration": tight,
                    "cut": original.snapshot.cut.model_copy(
                        update={
                            "authorized_command_fingerprint": hashlib.sha256(
                                command.canonical_bytes()
                            ).hexdigest()
                        }
                    ),
                }
            ),
        }
    )
    response = dispatch("scheduler.prepare_interval", tight_request.canonical_bytes())
    assert isinstance(response["payload"], str)
    result = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    assert isinstance(result.result, IntervalCandidate)
    hold = result.result.result
    assert isinstance(hold, SchedulerOverflowHold)
    held = tight.model_copy(update={"active_hold": hold.hold})
    successor = replace_bound(
        held,
        observed=tight.bound,
        proposed_bound=config.bound.bound,
        context=original.context,
        command_id="sufficient",
        authority_epoch="epoch",
        broker_generation="broker",
        runtime_graph_generation="graph",
    ).proposed
    assert successor.bound
    operator = Present(head="operator-proof", fingerprint=DIGEST)
    resolution = ResolveIntervalCommand(
        identity=command.identity.model_copy(update={"command_id": "resolve"}),
        active_hold=hold,
        successor_bound=successor.bound,
        boundary=command.boundary,
        complete_manifest=full_manifest,
        operator_proof=operator,
        publication_fence=command.publication_fence.model_copy(update={"command_id": "resolve"}),
    )
    resolve_request = original.model_copy(
        update={
            "operation": "scheduler.resolve_interval",
            "command_bytes": resolution.canonical_bytes(),
            "snapshot": original.snapshot.model_copy(
                update={
                    "configuration": successor,
                    "active_hold": hold,
                    "operator_proof": operator,
                    "publication_fence": resolution.publication_fence,
                    "cut": original.snapshot.cut.model_copy(
                        update={
                            "authorized_command_fingerprint": hashlib.sha256(
                                resolution.canonical_bytes()
                            ).hexdigest()
                        }
                    ),
                }
            ),
        }
    )
    response = dispatch("scheduler.prepare_interval", resolve_request.canonical_bytes())
    assert isinstance(response["payload"], str)
    resolved = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    assert isinstance(resolved.result, IntervalCandidate) and len(resolved.result.occurrences) == 2
    for updates in ({"active_hold": None}, {"operator_proof": None}):
        changed = resolve_request.model_copy(
            update={"snapshot": resolve_request.snapshot.model_copy(update=updates)}
        )
        assert (
            dispatch("scheduler.prepare_interval", changed.canonical_bytes())["failure"]
            == "PROTOCOL_REJECTED"
        )


def test_rollover_wire_retains_complete_source_and_all_six_records() -> None:
    from chiplog.capabilities.agent_loop.recovery_contracts import (
        ExhaustionBinding,
        PhysicalRootRolloverFence,
        RolloverAuthorityRef,
    )
    from chiplog.capabilities.agent_loop.scheduler_contracts import (
        ExhaustedLease,
        PhysicalRootRolloverCommand,
    )
    from chiplog.capabilities.agent_loop.scheduler_preparation import (
        RolloverPreparationRequest,
        RolloverPreparationSnapshot,
    )
    from chiplog.capabilities.agent_loop.scheduler_rollover import (
        IssuedRolloverObservation,
        RolloverCandidate,
        rollover_payload_fingerprint,
        rollover_snapshot_fingerprint,
    )

    current = view(2**64 - 1)
    assert current.lease.kind == "HELD"
    held = current.lease.binding
    current = current.model_copy(
        update={
            "lease": ExhaustedLease(
                lease_head="exhausted",
                exhausted_command_id="takeover",
                trusted_expiry=held.trusted_expiry,
                authority_epoch="epoch",
                preceding_held_lease=held,
            )
        }
    )
    authority = RolloverAuthorityRef(
        proof_id="proof",
        proof_fingerprint=DIGEST,
        authority_head="operator",
        command_id="rollover",
        command_payload_fingerprint=DIGEST,
        predecessor_rollover=Absent(),
    )
    command = PhysicalRootRolloverCommand(
        identity=SchedulerCommandIdentity(
            command_id="rollover", schema_version="1", canonicalization_version=CANONICAL_VERSION
        ),
        fence=PhysicalRootRolloverFence(
            lineage=current.lineage,
            physical_root=current.physical_root,
            exhaustion=ExhaustionBinding(
                hold_head="exhausted",
                exhausted_command_id="takeover",
                lease=held,
                authority_epoch="epoch",
            ),
            authority=authority,
        ),
    )
    authority = authority.model_copy(
        update={"command_payload_fingerprint": rollover_payload_fingerprint(command)}
    )
    command = command.model_copy(
        update={"fence": command.fence.model_copy(update={"authority": authority})}
    )
    base = configuration_request()
    cut = base.snapshot.cut.model_copy(
        update={
            "authorized_command_fingerprint": hashlib.sha256(command.canonical_bytes()).hexdigest()
        }
    )
    original = RolloverPreparationRequest(
        operation="scheduler.rollover",
        context=base.context,
        command_bytes=command.canonical_bytes(),
        snapshot=RolloverPreparationSnapshot(
            cut=cut,
            current=current,
            issued=IssuedRolloverObservation(
                authority=authority,
                snapshot_fingerprint=rollover_snapshot_fingerprint(current),
                submission_id=cut.submission_id,
                authority_epoch="epoch",
                used_epoch_ids=("epoch",),
                used_lease_heads=("held-head", "exhausted"),
            ),
        ),
    )
    response = dispatch("scheduler.prepare_rollover", original.canonical_bytes())
    assert isinstance(response["payload"], str)
    result = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    assert isinstance(result.result, RolloverCandidate) and len(result.records) == 6
    assert result.result.view.lineage == current.lineage
    changed = original.model_copy(
        update={
            "snapshot": original.snapshot.model_copy(
                update={"cut": cut.model_copy(update={"submission_id": "later"})}
            )
        }
    )
    assert (
        dispatch("scheduler.prepare_rollover", changed.canonical_bytes())["failure"]
        == "PROTOCOL_REJECTED"
    )


def test_bound_replacement_wire_preserves_exact_predecessor_and_rejects_authority_change() -> None:
    from chiplog.capabilities.agent_loop.scheduler_configuration import ConfigurationProposal
    from chiplog.capabilities.agent_loop.scheduler_contracts import ReplaceIntervalBoundCommand
    from chiplog.capabilities.agent_loop.scheduler_preparation import ConfigurationBoundCommand

    original = configuration_request()
    initial = prepare_configuration(original).result
    assert isinstance(initial, ConfigurationProposal) and initial.proposed.bound
    previous = initial.proposed.bound
    command = ConfigurationBoundCommand(
        command=ReplaceIntervalBoundCommand(
            identity=SchedulerCommandIdentity(
                command_id="replace", schema_version="1", canonicalization_version=CANONICAL_VERSION
            ),
            observed_head=previous,
            proposed_bound=previous.bound.model_copy(update={"max_member_count": 4}),
            scheduler_authority_head="authority",
            authority_epoch="epoch",
            broker_generation="broker",
            runtime_graph_generation="graph",
        )
    )
    prepared_request = original.model_copy(
        update={
            "operation": "scheduler.replace_bound",
            "command_bytes": command.canonical_bytes(),
            "snapshot": original.snapshot.model_copy(
                update={
                    "current": initial.proposed,
                    "cut": original.snapshot.cut.model_copy(
                        update={
                            "authorized_command_fingerprint": hashlib.sha256(
                                command.canonical_bytes()
                            ).hexdigest()
                        }
                    ),
                }
            ),
        }
    )
    response = dispatch("scheduler.prepare_configuration", prepared_request.canonical_bytes())
    assert isinstance(response["payload"], str)
    result = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    assert (
        len(result.records) == 1
        and result.records[0].schema_id == "chiplog.scheduler.interval-bound.v1"
    )
    assert isinstance(result.result, ConfigurationProposal) and result.result.proposed.bound
    assert result.result.proposed.bound.predecessor == Present(
        head=previous.head_id, fingerprint=hashlib.sha256(previous.canonical_bytes()).hexdigest()
    )
    changed = prepared_request.model_copy(
        update={
            "snapshot": prepared_request.snapshot.model_copy(
                update={"authority_epoch": "different"}
            )
        }
    )
    assert (
        dispatch("scheduler.prepare_configuration", changed.canonical_bytes())["failure"]
        == "PROTOCOL_REJECTED"
    )


@pytest.mark.parametrize("policy", ["SKIP", "COALESCE", "MATERIALIZE_EACH"])
def test_streamed_overflow_all_policies_compare_entire_witness_and_never_materialize(
    policy: str,
) -> None:
    original = interval_request("110")
    config = original.snapshot.configuration
    assert config.policy and config.bound
    config = config.model_copy(
        update={
            "policy": config.policy.model_copy(update={"policy": policy}),
            "bound": config.bound.model_copy(
                update={"bound": config.bound.bound.model_copy(update={"max_manifest_bytes": 1})}
            ),
        }
    )
    command = DecideIntervalCommand.model_validate_json(original.command_bytes)
    assert config.policy
    boundary = command.boundary.model_copy(
        update={"missed_occurrence_policy_head": policy_head(config.policy)}
    )
    measured = stream_definition(config, boundary, (), original.snapshot.enumeration_proof)
    assert measured.evidence.kind == "STREAMING_MANIFEST"
    command = command.model_copy(
        update={"boundary": boundary, "bound_head": config.bound, "manifest": measured.evidence}
    )

    def wrapped(value: DecideIntervalCommand) -> IntervalPreparationRequest:
        return original.model_copy(
            update={
                "command_bytes": value.canonical_bytes(),
                "snapshot": original.snapshot.model_copy(
                    update={
                        "configuration": config,
                        "cut": original.snapshot.cut.model_copy(
                            update={
                                "authorized_command_fingerprint": hashlib.sha256(
                                    value.canonical_bytes()
                                ).hexdigest()
                            }
                        ),
                    }
                ),
            }
        )

    response = dispatch("scheduler.prepare_interval", wrapped(command).canonical_bytes())
    assert isinstance(response["payload"], str)
    result = PreparedSchedulerBatch.model_validate_json(base64.b64decode(response["payload"]))
    from chiplog.capabilities.agent_loop.scheduler_materialization import IntervalCandidate

    assert isinstance(result.result, IntervalCandidate) and result.result.occurrences == ()
    assert isinstance(result.result.result, SchedulerOverflowHold)
    assert result.result.result.evidence == measured.evidence
    assert [row.schema_id for row in result.records] == ["chiplog.scheduler.overflow-hold.v1"]
    for field, value in (
        ("manifest_digest", "f" * 64),
        ("member_count", measured.member_count - 1),
        ("first_member", Absent()),
        ("last_member", Absent()),
        ("order_contract_version", "other"),
        ("enumeration_completeness_proof", Present(head="other", fingerprint=DIGEST)),
    ):
        mutant = command.model_copy(
            update={"manifest": measured.evidence.model_copy(update={field: value})}
        )
        assert (
            dispatch("scheduler.prepare_interval", wrapped(mutant).canonical_bytes())["failure"]
            == "PROTOCOL_REJECTED"
        )


def test_streaming_witness_cannot_replace_required_full_ordinary_manifest() -> None:
    from chiplog.capabilities.agent_loop.scheduler_contracts import StreamingEligibilityEvidence

    original = interval_request()
    command = DecideIntervalCommand.model_validate_json(original.command_bytes)
    assert isinstance(command.manifest, SchedulerEligibilityManifest)
    members = command.manifest.members
    witness = StreamingEligibilityEvidence(
        manifest_digest=command.manifest.fingerprint,
        member_count=len(members),
        first_member=Present(
            head=members[0].undisposed_head, fingerprint=members[0].undisposed_fingerprint
        ),
        last_member=Present(
            head=members[-1].undisposed_head, fingerprint=members[-1].undisposed_fingerprint
        ),
        order_contract_version="chiplog.scheduler.numeric-due.v1",
        enumeration_completeness_proof=original.snapshot.enumeration_proof,
    )
    command = command.model_copy(update={"manifest": witness})
    changed = original.model_copy(
        update={
            "command_bytes": command.canonical_bytes(),
            "snapshot": original.snapshot.model_copy(
                update={
                    "cut": original.snapshot.cut.model_copy(
                        update={
                            "authorized_command_fingerprint": hashlib.sha256(
                                command.canonical_bytes()
                            ).hexdigest()
                        }
                    )
                }
            ),
        }
    )
    assert (
        dispatch("scheduler.prepare_interval", changed.canonical_bytes())["failure"]
        == "PROTOCOL_REJECTED"
    )
