"""Shared scheduler test builders; simulated issuer values are not authority."""

import hashlib
import json
import socket
import time
from pathlib import Path
from typing import Literal

import pytest

from chiplog.architecture.r7_runtime import R14_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.recovery_contracts import (
    Absent,
    ExecutionLineageBinding,
    IndividualSubject,
    LeaseBinding,
    PhysicalRootBinding,
    TrustedClockProofRef,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    GenesisLease,
    HeldLease,
    LeaseTransitionCommand,
    SchedulerCommandIdentity,
    SchedulerContextRef,
    SchedulerLineageView,
)
from chiplog.capabilities.agent_loop.scheduler_leases import (
    IssuedLeaseObservation,
    lease_fence_fingerprint,
    lease_payload_fingerprint,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    LeasePreparationRequest,
    LeasePreparationSnapshot,
    PreparedSchedulerRecord,
    SchedulerLeaseTransitionRecord,
)
from chiplog.capabilities.effects.contracts import (
    AuthorityBinding,
    AuthorityRead,
    CommandIdentity,
    CurrentEffectInputs,
    DirectAuthorityAct,
    DispatchSemanticBinding,
    EffectPreparationRequest,
    EffectStoreSnapshot,
    ExactHead,
    ExternalActionIntent,
    OrdinaryPurpose,
    PreparedEffectPublication,
    ProviderRecipient,
    PublishPlanEffectCommand,
    TransmissionAttempt,
)
from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable
from chiplog.platform import r7_runtime as runtime_module
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


async def test_generic_owner_uses_captured_parser_for_completion_and_continuation() -> None:
    from chiplog.capabilities.agent_loop import domain
    from chiplog.capabilities.agent_loop.contracts import Continue, ToolCall, TransitionRequest
    from chiplog.capabilities.agent_loop.delivery_preparation import DELIVERY_TOOLS
    from tests.support.delivery_completion import _captured

    with AuthorityBrokerRuntime(
        "t", 1, "generation", R14_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:

        def invoke(payload: bytes, identity: str) -> PublicPortSuccess | PublicPortRejected:
            return runtime.call_sync(
                PublicPortCall(
                    operation_id="agent_loop.validate_transition",
                    request_id=identity,
                    caller=BrokerSession(
                        tenant_id="t",
                        broker_epoch=1,
                        generation_id="generation",
                        owner_id="broker",
                        session_id="broker",
                    ),
                    callee=runtime.session("agent_loop"),
                    schema_id="chiplog.agent-loop.transition.v1",
                    canonical_payload=payload,
                    budget=CallBudget(
                        remaining_calls=1,
                        remaining_depth=1,
                        absolute_deadline_ns=time.monotonic_ns() + 10_000_000_000,
                        policy_version=1,
                    ),
                )
            )

        fixture = Path(__file__).parents[1] / (
            "capabilities/agent_loop/fixtures/delivery_generator_legacy_completion.json"
        )
        assert isinstance(invoke(fixture.read_bytes(), "legacy-completion"), PublicPortRejected)
        subsets = (DELIVERY_TOOLS, (DELIVERY_TOOLS[0],), (DELIVERY_TOOLS[1],))
        for index, tools in enumerate(subsets):
            captured, _ = await _captured(tools=tools)
            turn, attempt = captured.turns[-1], captured.turns[-1].attempts[-1]
            emitted = captured.model_copy(
                update={
                    "turns": (
                        turn.model_copy(
                            update={
                                "attempts": (
                                    attempt.model_copy(
                                        update={
                                            "state": "EMITTED_OUTCOME_UNKNOWN",
                                            "response_base64": None,
                                            "receipt": None,
                                        }
                                    ),
                                ),
                            }
                        ),
                    )
                }
            )
            response = Continue(
                kind="Continue",
                tool_calls=(ToolCall(call_id="call", tool=tools[0].name, text="proposal"),),
            )
            previous = domain.capture(emitted, response.canonical_bytes(), "captured")
            proposed = domain.accept_tools(previous, response)
            frame = TransitionRequest(previous=previous, proposed=proposed)
            assert isinstance(invoke(frame.canonical_bytes(), f"valid-{index}"), PublicPortSuccess)
            for field, value, reason in (
                ("generator_version", "unknown-generator", "generator_version"),
                ("generator_version", "chiplog.turn-schema.v1", "schema bytes differ"),
                ("response_schema_json", "{}", "wrong captured delivery response schema"),
            ):
                old_turn = previous.turns[-1]
                old_attempt = old_turn.attempts[-1]
                artifact = old_attempt.manifest.artifact.model_copy(update={field: value})
                manifest = old_attempt.manifest.model_copy(update={"artifact": artifact})
                changed_attempt = old_attempt.model_copy(update={"manifest": manifest})
                changed = previous.model_copy(
                    update={
                        "turns": (old_turn.model_copy(update={"attempts": (changed_attempt,)}),)
                    }
                )
                rejected = invoke(
                    TransitionRequest(previous=changed, proposed=proposed).canonical_bytes(),
                    f"invalid-{index}-{field}-{value}",
                )
                assert isinstance(rejected, PublicPortRejected)
                assert reason in rejected.failure.reason


@pytest.mark.parametrize(
    "foreign",
    [
        "chiplog.capabilities.effects._process",
        "chiplog.capabilities.agent_loop._unregistered_process",
    ],
)
def test_attestation_rejects_additional_loaded_policy_module(
    monkeypatch: pytest.MonkeyPatch,
    foreign: str,
) -> None:
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        receive = runtime_module._receive_frame

        def mutated(connection: socket.socket, secret: bytes) -> bytes:
            value = json.loads(receive(connection, secret))
            if value.get("owner_id") == "agent_loop":
                value["loaded_policy_modules"].append(foreign)
            return json.dumps(value).encode()

        monkeypatch.setattr(runtime_module, "_receive_frame", mutated)
        with pytest.raises(runtime_module.OwnerProcessFailure, match="attestation"):
            runtime.attest()


DIGEST = "a" * 64


def view(generation: int | None = None) -> SchedulerLineageView:
    return SchedulerLineageView(
        lineage=ExecutionLineageBinding(
            root_id="root",
            subject=IndividualSubject(occurrence_id="occurrence"),
            root_fingerprint=DIGEST,
            lineage_head="lineage",
            initial_run_id="run",
            current_run_id="run",
            schedule_id="schedule",
            schedule_revision="revision",
            policy_revision="policy",
        ),
        physical_root=PhysicalRootBinding(
            selector_id="selector",
            selector_head="selector-head",
            selector_version=0,
            current_epoch_id="epoch",
            current_epoch_head="epoch-head",
        ),
        lease=GenesisLease(lease_head="genesis")
        if generation is None
        else HeldLease(
            binding=LeaseBinding(
                lease_head="held-head",
                holder_id="worker",
                holder_session_id="session",
                lease_id="lease",
                generation=generation,
                trusted_expiry=100,
                clock_contract_version="clock-v1",
            ),
        ),
        predecessor_rollover=Absent(),
    )


def issue(
    current: SchedulerLineageView,
    kind: Literal["ACQUIRE", "RENEW", "TAKEOVER"],
    *,
    generation: int = 1,
    now: int = 50,
    expiry: int = 120,
    lease_id: str = "fresh-lease",
    holder: str = "worker",
    session: str = "session",
    command_id: str = "command",
) -> tuple[LeaseTransitionCommand, IssuedLeaseObservation]:
    proof = TrustedClockProofRef(
        proof_id="clock-proof",
        proof_fingerprint=DIGEST,
        proof_version="1",
        clock_contract_version="clock-v1",
        fence_fingerprint=lease_fence_fingerprint(current),
        command_id=command_id,
        command_payload_fingerprint=DIGEST,
        submission_id="submission",
    )
    command = LeaseTransitionCommand(
        identity=SchedulerCommandIdentity(
            command_id=command_id,
            schema_version="1",
            canonicalization_version="1",
        ),
        kind=kind,
        lineage=current.lineage,
        physical_root=current.physical_root,
        observed_lease=current.lease,
        proposed_holder_id=holder,
        proposed_holder_session_id=session,
        proposed_lease_id=lease_id,
        proposed_expiry=expiry,
        proposed_generation=generation,
        clock_proof=proof,
    )
    proof = proof.model_copy(
        update={
            "command_payload_fingerprint": lease_payload_fingerprint(command),
        }
    )
    command = command.model_copy(update={"clock_proof": proof})
    observation = IssuedLeaseObservation(
        proof=proof,
        now=now,
        max_lease_duration=100,
        holder_id=holder,
        holder_session_id=session,
        authority_epoch="authority",
        used_lease_ids=() if current.lease.kind == "UNLEASED" else ("lease",),
    )
    return command, observation


def test_scheduler_preparation_crosses_real_isolated_process_and_keeps_exact_closure() -> None:
    current = view()
    command, issued = issue(current, "ACQUIRE")
    preparation = LeasePreparationRequest(
        operation="scheduler.acquire",
        context=SchedulerContextRef(
            tenant_id="tenant",
            service_identity="worker",
            session_id="session",
            mandate_head="mandate",
            issuance_id="issuer",
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
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        call = PublicPortCall(
            operation_id="scheduler.prepare_lease",
            request_id="request",
            caller=BrokerSession(
                tenant_id="tenant",
                broker_epoch=1,
                generation_id="generation",
                owner_id="broker",
                session_id="broker",
            ),
            callee=runtime.session("agent_loop"),
            schema_id="chiplog.scheduler.lease-preparation.v1",
            canonical_payload=preparation.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=4,
                remaining_depth=2,
                absolute_deadline_ns=time.monotonic_ns() + 10_000_000_000,
                policy_version=1,
            ),
        )
        result = runtime.call_sync(call)
        assert isinstance(result, PublicPortSuccess)
        assert result.responder == runtime.session("agent_loop")
        assert result.schema_id == "chiplog.scheduler.prepared-lease.v1"
        prepared = PreparedSchedulerRecord.model_validate_json(result.canonical_payload)
        assert prepared.canonical_bytes() == result.canonical_payload
        assert hashlib.sha256(prepared.canonical_record_bytes).hexdigest() == prepared.fingerprint
        record = SchedulerLeaseTransitionRecord.model_validate_json(prepared.canonical_record_bytes)
        assert record.expected_tenant_frontier == 7
        assert record.previous == current
        assert record.candidate.state.kind == "HELD"
        assert record.candidate.state.binding.holder_session_id == "session"
        for mutation in (
            {"schema_id": "unregistered"},
            {"operation_id": "scheduler.unknown"},
            {"callee": call.callee.model_copy(update={"session_id": "stale"})},
        ):
            assert isinstance(
                runtime.call_sync(call.model_copy(update=mutation)), PublicPortRejected
            )
        owners = {item.identity.owner_id: item for item in runtime.attest()}
        assert owners["agent_loop"].loaded_policy_modules == (
            "chiplog.capabilities.agent_loop._delivery_process",
            "chiplog.capabilities.agent_loop._r13_process",
            "chiplog.capabilities.agent_loop._r14_process",
            "chiplog.capabilities.agent_loop._scheduler_process",
        )


def head(value: str) -> ExactHead:
    return ExactHead(subject_id=value, head=value + "-head", fingerprint=value + "-digest")


def semantics() -> DispatchSemanticBinding:
    return DispatchSemanticBinding(
        normative_manifest="VISION-dispatch",
        reducer_version="1",
        transition_registry_version="1",
        canonicalization_fingerprint_version="1",
        adapter_contract_version="fake-1",
    )


def child(ordinal: int) -> TransmissionAttempt:
    return TransmissionAttempt(
        transmission=head(f"child-{ordinal}"),
        intent=head("intent"),
        ordinal=ordinal,
        semantics=semantics(),
        dispatch_time_ns=1,
        payload_fingerprint="payload",
        recipient=ProviderRecipient(
            provider="fake",
            account="account",
            recipient="principal",
            endpoint=head("endpoint"),
            canonical_address=b"local",
            credential_binding=head("credential"),
        ),
        idempotency_fence_key="key",
        send_commit=head(f"send-{ordinal}"),
        coverage_proof=None,
    )


def intent() -> ExternalActionIntent:
    reference = head("authority")
    authority = AuthorityBinding(
        tenant_id="tenant",
        principal_id="principal",
        actor_id="principal",
        authenticated_session=reference,
        act=DirectAuthorityAct(kind="DIRECT_AUTHENTICATED_ACT", act=reference, ingress=reference),
        planning_revision=reference,
        authorization_evidence=reference,
        authority_sources=(reference,),
        affected_party_constraints=(reference,),
        hold_conflict_order=reference,
        dependencies=(reference,),
        factual_assertion_evidence=(reference,),
        verification_contradiction=(reference,),
        authority_applicability=(reference,),
        consequence_scope=reference,
        communication_mandate=reference,
        disclosure_projection=reference,
        channel_class="hermetic",
        interaction_context=reference,
        recipient=child(0).recipient,
        reads=(
            AuthorityRead(
                source_id="authority",
                source_version="1",
                head=reference,
                generation="gen",
                frontier="frontier",
                valid_until_ns=100,
                canonical_value=b"authority",
            ),
        ),
        registry_inputs=(("registry", "1"),),
        valid_until_ns=100,
    )
    return ExternalActionIntent(
        intent_id="intent",
        fingerprint="intent-digest",
        authority=authority,
        semantics=semantics(),
        payload=b"payload",
        effect_fingerprint=hashlib.sha256(b"payload").hexdigest(),
        idempotency_fence_key="key",
        inseparable_bundle_members=(head("member"),),
        purpose=OrdinaryPurpose(kind="ORDINARY_EFFECT"),
    )


def test_effect_preparation_returns_atomic_companions_through_real_owner_process() -> None:
    original = intent()
    values = original.model_dump(mode="json")
    del values["fingerprint"]
    original = original.model_copy(
        update={
            "fingerprint": hashlib.sha256(
                json.dumps(
                    values, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()
        }
    )
    intent_ref = ExactHead(
        subject_id=original.intent_id,
        head=original.intent_id + "/" + original.fingerprint,
        fingerprint=original.fingerprint,
    )
    fence = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id="run",
        run_head="run-head",
        worker_session_id="session",
        runtime_generation="generation",
    )
    store = EffectStoreSnapshot(tenant_id="tenant", tenant_head=0, records=())
    command = PublishPlanEffectCommand(
        identity=CommandIdentity(
            command_id="create",
            fingerprint=hashlib.sha256(b"create").hexdigest(),
            expected_tenant_head=0,
        ),
        intent=original,
        planning_publication=head("plan"),
        planning_owner_bytes=b"planning bytes",
        complete_publication_manifest=(head("plan"), intent_ref),
        fence=fence,
    )
    current = CurrentEffectInputs(
        command_id="create",
        command_fingerprint=command.identity.fingerprint,
        store_frontier=0,
        observed_time_ns=10,
        authority=original.authority,
        supported_semantics=original.semantics,
        fence=fence,
        authority_decision=head("decision"),
        blocking_effect_heads=(),
        current_original_ambiguity_heads=(),
        initialized_call=None,
        active_run_head="run-head",
        independently_verified_safe_proof=None,
        authenticated_evidence=None,
        original_reducer_semantics=original.semantics,
        authorized_reconciler=None,
    )
    preparation = EffectPreparationRequest(
        operation="effects.publish_plan_effect",
        command_bytes=command.canonical_bytes(),
        expected=store,
        current=current,
    )
    with AuthorityBrokerRuntime(
        "tenant", 1, "generation", R14_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        call = PublicPortCall(
            operation_id="effects.prepare_transition",
            request_id="effect",
            caller=BrokerSession(
                tenant_id="tenant",
                broker_epoch=1,
                generation_id="generation",
                owner_id="broker",
                session_id="broker",
            ),
            callee=runtime.session("effects"),
            schema_id="chiplog.effects.prepare.v1",
            canonical_payload=preparation.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=4,
                remaining_depth=2,
                absolute_deadline_ns=time.monotonic_ns() + 10_000_000_000,
                policy_version=1,
            ),
        )
        result = runtime.call_sync(call)
        assert isinstance(result, PublicPortSuccess)
        assert result.responder == runtime.session("effects")
        assert result.schema_id == "chiplog.effects.prepared-publication.v1"
        prepared = PreparedEffectPublication.model_validate_json(result.canonical_payload)
        assert prepared.canonical_bytes() == result.canonical_payload
        assert prepared.expected_store == store
        assert prepared.exact_companion_manifest == (head("plan"), intent_ref)
        assert prepared.record.source_command == command.canonical_bytes()
        assert prepared.record.snapshot.intent == original
        assert isinstance(
            runtime.call_sync(
                call.model_copy(update={"canonical_payload": b" " + call.canonical_payload})
            ),
            PublicPortRejected,
        )
        owners = {item.identity.owner_id: item for item in runtime.attest()}
        assert owners["effects"].loaded_policy_modules == ("chiplog.capabilities.effects._process",)


@pytest.mark.parametrize("tool_subset", [0, 1, 2])
async def test_delivery_routes_cross_isolated_owner_and_reject_protocol_and_history(
    tool_subset: int,
) -> None:
    from chiplog.capabilities.agent_loop.contracts import RunRecord
    from chiplog.capabilities.agent_loop.delivery_preparation import (
        DELIVERY_TOOLS,
        DeliveryPrepareRequest,
        DeliveryValidateRequest,
    )
    from tests.support.delivery_completion import _captured

    tools = DELIVERY_TOOLS if tool_subset == 2 else (DELIVERY_TOOLS[tool_subset],)
    captured, observation = await _captured(tools=tools)
    request = DeliveryPrepareRequest(previous=captured, observation=observation)
    with AuthorityBrokerRuntime(
        "t", 1, "generation", R14_PRODUCTION_MANIFEST, b"secret"
    ) as runtime:
        call = PublicPortCall(
            operation_id="agent_loop.prepare_delivery_completion",
            request_id="delivery",
            caller=BrokerSession(
                tenant_id="t",
                broker_epoch=1,
                generation_id="generation",
                owner_id="broker",
                session_id="broker",
            ),
            callee=runtime.session("agent_loop"),
            schema_id="chiplog.delivery.prepare-completion.v1",
            canonical_payload=request.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=30,
                remaining_depth=2,
                absolute_deadline_ns=time.monotonic_ns() + 10_000_000_000,
                policy_version=1,
            ),
        )
        result = runtime.call_sync(call)
        assert isinstance(result, PublicPortSuccess)
        accepted = RunRecord.model_validate_json(result.canonical_payload)
        assert accepted.canonical_bytes() == result.canonical_payload
        assert accepted.state == "SUCCEEDED"
        assert accepted.predecessor == captured.head
        validation = DeliveryValidateRequest(
            previous=captured, proposed=accepted, observation=observation
        )
        validate_call = call.model_copy(
            update={
                "operation_id": "agent_loop.validate_delivery_completion",
                "schema_id": "chiplog.delivery.validate-completion.v1",
                "canonical_payload": validation.canonical_bytes(),
            }
        )
        validated = runtime.call_sync(validate_call)
        assert isinstance(validated, PublicPortSuccess)
        assert validated.canonical_payload == result.canonical_payload
        for base in (call, validate_call):
            for mutation in (
                {"schema_id": "unregistered"},
                {"operation_id": "agent_loop.unknown"},
                {"callee": base.callee.model_copy(update={"session_id": "stale"})},
                {"canonical_payload": base.canonical_payload + b" "},
            ):
                assert isinstance(
                    runtime.call_sync(base.model_copy(update=mutation)), PublicPortRejected
                )
        bad_observation = observation.model_copy(update={"history": ()})
        bad_prepare = request.model_copy(update={"observation": bad_observation})
        bad_validate = validation.model_copy(update={"observation": bad_observation})
        for base, payload in ((call, bad_prepare), (validate_call, bad_validate)):
            assert isinstance(
                runtime.call_sync(
                    base.model_copy(
                        update={
                            "canonical_payload": payload.canonical_bytes(),
                        }
                    )
                ),
                PublicPortRejected,
            )
        legacy, legacy_observation = await _captured(legacy=True)
        prior = captured.turns[0].model_copy(update={"turn_id": "previous"})
        unfinished = captured.model_copy(update={"turns": (prior, captured.turns[0])})
        for base, payload in (
            (call, request.model_copy(update={"previous": unfinished})),
            (validate_call, validation.model_copy(update={"previous": unfinished})),
        ):
            rejected = runtime.call_sync(
                base.model_copy(
                    update={
                        "canonical_payload": payload.canonical_bytes(),
                    }
                )
            )
            assert isinstance(rejected, PublicPortRejected)
        legacy_request = DeliveryPrepareRequest(previous=legacy, observation=legacy_observation)
        assert isinstance(
            runtime.call_sync(
                call.model_copy(
                    update={
                        "canonical_payload": legacy_request.canonical_bytes(),
                    }
                )
            ),
            PublicPortRejected,
        )
        assert (
            "chiplog.capabilities.agent_loop._delivery_process"
            in {item.identity.owner_id: item for item in runtime.attest()}[
                "agent_loop"
            ].loaded_policy_modules
        )
