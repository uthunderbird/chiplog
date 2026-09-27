"""Characterize replay purity of the four mounted H1 preparation routes."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.effects.h1_local_preparation import _intent_id
from chiplog.capabilities.effects.h1_local_preparation_contracts import (
    H1LocalCommentaryOwnerCallV1,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_candidate
from chiplog.composition.r14_execution_complete_seal_records import RetainedExecutionCompleteSealV3
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_leaves import ProductionClock, ProductionPlanningStore
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime
from tests.support.h1_cli_execution import _admit
from tests.support.h1_installed_launch import TENANT, installed_slot, prepare_installed_slot

_ROUTES = {
    "completion": (
        "agent_loop.prepare_first_path_completion",
        "agent_loop",
        "chiplog.execution.first-path-completion.v2",
    ),
    "conversation": (
        "projections.prepare_conversation_completion",
        "projections",
        "chiplog.conversation.prepare-completion.v1",
    ),
    "effects": (
        "effects.prepare_h1_local_commentary",
        "effects",
        "chiplog.effects.h1-local-commentary-owner-call.v1",
    ),
    "terminal": (
        "agent_loop.prepare_terminal_work",
        "agent_loop",
        "chiplog.agent-loop.prepare-terminal-work.v1",
    ),
}


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


async def _mounted_semantic_requests(tmp_path: Path) -> dict[str, bytes]:
    """Capture accepted semantic requests from one installed H1 exchange."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            request = await _admit(runtime)
            initial = await runtime.drive_input(request)
            runtime._execution_model._responses = (
                DeliveryCompletion(
                    tenant=TENANT,
                    run_id=initial.stable_run_lineage_id,
                    turn_id=initial.stable_run_lineage_id + "/turn/1",
                    deliveries=(ProposedDelivery(payload=(Commentary(text="pure replay"),)),),
                ).canonical_bytes(),
            )
            started = await runtime.begin_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, initial.selected_run_head.head
            )
            captured = await runtime.capture_execution(
                "hermetic-ingress", initial.stable_run_lineage_id, started.head
            )
            selected = select_h1_v3_prepare_for_candidate(
                runtime, captured, expected_head=captured.head
            )
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            original = await port.verify_selected_original(selected)
            sealed = await runtime.seal_execution_complete(
                "hermetic-ingress", initial.stable_run_lineage_id, captured.head, profile="H1_V3"
            )
            decision = next(
                json.loads(raw)
                for _, _, raw in runtime._loop_decisions().entries()
                if json.loads(raw).get("operation_id") == sealed.head
            )
            retained = RetainedExecutionCompleteSealV3.model_validate_json(
                decision["execution_complete_seal"]
            )
            response_seal = retained.exchange.proposal.fan_out.response_seal
            seal = CallSubjectHead(
                subject_id=response_seal.response_seal_id,
                revision=Present(
                    head="record:" + response_seal.digest(), fingerprint=response_seal.digest()
                ),
            )
            first_path = runtime._h1_first_path_sources.capture_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )
            native = runtime._h1_native_member_sources.capture_current(first_path)
            scope = await port._capture_completion_scope(first_path, native, original)
            port._replay_completion_scope(scope, native)
            worker_owner = runtime._h1_installed_worker_evidence_owner
            worker_issuer = runtime._h1_pre_request_worker_evidence
            evidence_journal = runtime._h1_delivery_evidence_journal
            assert worker_owner is not None
            assert worker_issuer is not None
            assert evidence_journal is not None
            worker_capability = worker_owner._capture_for_pre_request(native)
            worker_receipt = worker_issuer._issue_worker(native, worker_capability)
            worker_locator, worker_record, _ = worker_issuer._replay_worker(worker_receipt)
            assert evidence_journal.read_exact(worker_locator).canonical_bytes() == (
                worker_record.canonical_bytes()
            )
            enrollment = runtime._h1_live_completion_enrollment
            assert enrollment is not None
            session = enrollment._open_session()
            session.capture_first_path(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
            )
            completion = await session.prepare_first_path_completion()
            session.capture_current(
                original_identity=request.identity,
                original_fingerprint=request.original_driver_command_fingerprint(),
                selected_seal=seal,
                completion_exchange=completion,
            )
            await session.prepare_conversation_completion()
            await session.prepare_local_commentary()
            await session.prepare_terminal_work()
            exchanges = {
                "completion": session._completion_exchange,
                "conversation": session._conversation_exchange,
                "effects": session._effects_exchange,
                "terminal": session._terminal_work_exchange,
            }
            assert all(exchange is not None for exchange in exchanges.values())
            return {name: exchange.sent.canonical_payload for name, exchange in exchanges.items()}


def _leaves() -> dict[str, object]:
    # The mounted processes receive no leaf object.  This effect leaf has no
    # registered scenario, so any accidental SEND would fail rather than hide.
    return {
        "clock": ProductionClock(),
        "planning_store": ProductionPlanningStore(),
        "model": HermeticModel(),
        "effects_transport": HermeticEffectsProvider(receipt_key=b"poison", scenarios=()),
    }


def _assert_exact_replay_edges() -> None:
    """Fail before the costly installed capture if replay wires drift from H1."""
    expected = {
        (operation, "broker", owner_id, schema_id)
        for operation, owner_id, schema_id in _ROUTES.values()
    }
    declared = {
        (route.operation_id, route.caller_owner_id, route.callee_owner_id, route.request_schema_id)
        for route in R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST.routes
    }
    assert expected <= declared

    with AuthorityBrokerRuntime(
        TENANT,
        1,
        "pure-replay-topology",
        R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST,
        b"pure-replay-topology-secret",
        realized_leaves=_leaves(),
    ) as runtime:
        realized = {
            (operation, caller, callee, schema)
            for operation, caller, callee, schema, _ in runtime.graph_generation().routes
        }
        assert expected <= realized


def _call(
    runtime: AuthorityBrokerRuntime,
    *,
    route: tuple[str, str, str],
    payload: bytes,
    request_id: str,
) -> bytes:
    operation, owner_id, schema_id = route
    callee = runtime.session(owner_id)
    sent = PublicPortCall(
        operation_id=operation,
        request_id=request_id,
        caller=BrokerSession(
            tenant_id=callee.tenant_id,
            broker_epoch=callee.broker_epoch,
            generation_id=callee.generation_id,
            owner_id="broker",
            session_id="broker:" + callee.generation_id,
        ),
        callee=callee,
        schema_id=schema_id,
        canonical_payload=payload,
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
        ),
    )
    result = runtime.call_sync(sent)
    assert isinstance(result, PublicPortSuccess), result
    assert result.request_id == sent.request_id
    assert result.responder == sent.callee
    return result.canonical_payload


def _replay_in_fresh_generation(
    payloads: Mapping[str, bytes], generation: str
) -> tuple[dict[str, bytes], HermeticEffectsProvider]:
    leaves = _leaves()
    provider = leaves["effects_transport"]
    assert isinstance(provider, HermeticEffectsProvider)
    with AuthorityBrokerRuntime(
        TENANT,
        1,
        generation,
        R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST,
        b"pure-replay-secret",
        realized_leaves=leaves,
    ) as runtime:
        results = {
            name: _call(
                runtime,
                route=_ROUTES[name],
                payload=payload,
                request_id=f"transport:{generation}:{name}",
            )
            for name, payload in payloads.items()
        }
        # The owner process attestation is exercised here as well: preparation
        # remains in the isolated effects process, while leaves stay broker-local.
        assert next(item for item in runtime.attest() if item.identity.owner_id == "effects")
    return results, provider


@pytest.mark.asyncio
async def test_mounted_h1_preparations_replay_exactly_across_fresh_transport_generations(
    tmp_path: Path,
) -> None:
    _assert_exact_replay_edges()
    payloads = await _mounted_semantic_requests(tmp_path)

    first, first_provider = _replay_in_fresh_generation(payloads, "purity-generation-a")
    second, second_provider = _replay_in_fresh_generation(payloads, "purity-generation-b")

    assert first == second
    # A preparation call cannot reach either the storage leaf or the provider
    # SEND boundary; an attempted provider SEND has no registered scenario.
    assert first_provider.transfers == ()
    assert second_provider.transfers == ()

    original = H1LocalCommentaryOwnerCallV1.model_validate_json(payloads["effects"])
    delivery_id = original.request.prepared_completion.delivery.manifest.ordered_deliveries[
        0
    ].delivery_id

    outer_only = original.model_copy(
        update={
            "route": original.route.model_copy(
                update={
                    "runtime_generation": "fresh-outer-generation",
                    "broker_session_id": "fresh-broker-session",
                    "owner_session_id": "fresh-owner-session",
                }
            )
        }
    )
    changed_id = "h1-effects:replay-inner-identity"
    changed_identity = original.request.identity.model_copy(
        update={
            "command_id": changed_id,
            "fingerprint": hashlib.sha256(changed_id.encode()).hexdigest(),
        }
    )
    changed_route = outer_only.route.model_copy(update={"request_id": changed_id})
    provisional = original.request.model_copy(
        update={"identity": changed_identity, "intent_id": "pending"}
    )
    provisional_call = H1LocalCommentaryOwnerCallV1(
        route=changed_route,
        request=provisional,
        request_digest=hashlib.sha256(provisional.canonical_bytes()).hexdigest(),
    )
    changed_request = provisional.model_copy(
        update={"intent_id": _intent_id(provisional_call, delivery_id)}
    )
    changed = H1LocalCommentaryOwnerCallV1(
        route=changed_route,
        request=changed_request,
        request_digest=hashlib.sha256(changed_request.canonical_bytes()).hexdigest(),
    )

    original_results, _ = _replay_in_fresh_generation(
        {"effects": original.canonical_bytes()}, "effects-identity-a"
    )
    outer_results, _ = _replay_in_fresh_generation(
        {"effects": outer_only.canonical_bytes()}, "effects-identity-b"
    )
    changed_results, _ = _replay_in_fresh_generation(
        {"effects": changed.canonical_bytes()}, "effects-identity-c"
    )

    assert outer_results["effects"] == original_results["effects"]
    assert changed_results["effects"] != original_results["effects"]
    assert changed.request.identity.command_id != original.request.identity.command_id
    assert changed.request.intent_id != original.request.intent_id
