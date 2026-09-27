"""R13 writer admission keeps H1 V2 workspace policies physically distinct."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticOutputPolicyV1,
    HermeticOutputScopeAnchorV1,
    SelectedHermeticResourceObservationRefV1,
)
from chiplog.composition.h1_workspace_policy_v2 import (
    H1PreissuanceRegistrationV1,
    H1WorkspacePolicyHeadsV1,
    H1WorkspacePolicyV2,
)
from chiplog.composition.r13_runtime import R13Runtime, open_r13_runtime
from chiplog.platform._sqlite import PhysicalPublicationCommand, PhysicalRecord


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _head(identity: str) -> ExactHead:
    return ExactHead(identity=identity, head=identity + "/head", fingerprint="a" * 64)


def _policy(tenant: str) -> H1WorkspacePolicyV2:
    selected = SelectedHermeticResourceObservationRefV1(
        signature_domain="dispatch-resources.v1",
        selected_initialization=_head("initialization"),
        signed_observation_fingerprint="b" * 64,
    )
    output = HermeticOutputPolicyV1(
        endpoint_ref=_head("endpoint"),
        selected_resource_observation_ref=selected,
        selection="ORIGIN_EXACT",
        ingress_class="AUTHENTICATED_R17_CLI",
        payload_class="NonAuthoritativeText",
        purpose="H1_LOCAL_COMMENTARY",
        external_delivery=False,
        attempt_ordinal=0,
        call_count=0,
    )
    output_raw = output.canonical_bytes()
    registration = H1PreissuanceRegistrationV1(
        deployment_id="deployment",
        database_id="database",
        database_genesis_digest="c" * 64,
        tenant_id=tenant,
        principal_id="principal",
        channel_id="origin-channel",
        registration_id="registration",
        generation=0,
        custody_entry_digest="d" * 64,
        origin_recipient_id="recipient",
        conversation_id="conversation",
        visible_channels=("origin-channel",),
        accepted_policy_selector="H1_OWNER_ISSUED_ORIGIN_EXACT_V1",
        accepted_policy=ExactHead(
            identity="h1-disclosure-policy",
            head="h1-disclosure-policy/" + _digest(output_raw),
            fingerprint=_digest(output_raw),
        ),
        accepted_policy_bytes_base64=base64.b64encode(output_raw).decode(),
        output_scope_anchor=HermeticOutputScopeAnchorV1(
            owner_id="deployment_trust",
            decision=_head("decision"),
            record_ordinal=0,
            record_type_id="chiplog.deployment_trust.hermetic_output_scope",
            schema_id="chiplog.deployment_trust.record.v1",
            record=_head("scope-record"),
            scope_revision=0,
            predecessor=None,
            selected_resource_observation_ref=selected,
        ),
        output_scope_ref=_head("scope"),
        selected_resource_observation_ref=selected,
        admitted_authentication_ref=_head("authentication"),
    )
    return H1WorkspacePolicyV2(
        tenant=tenant,
        principal="principal",
        channel="origin-channel",
        database="database",
        endpoint="endpoint",
        heads=H1WorkspacePolicyHeadsV1(
            policy="policy",
            credential="credential",
            session="session",
            contour="contour",
            deletion="deletion",
        ),
        sources=(),
        registration=registration,
    )


def _command(
    runtime: R13Runtime, *, operation: str = "workspace.policy.h1.v2"
) -> PhysicalPublicationCommand:
    policy = _policy(runtime._tenant_id)
    payload = policy.canonical_bytes()
    fingerprint = _digest(payload)
    policy_id = "workspace-policy:" + hashlib.sha256(
        json.dumps([policy.tenant, policy.principal, policy.channel]).encode()
    ).hexdigest()
    record_id = policy_id + ":" + fingerprint
    return PhysicalPublicationCommand(
        tenant_id=runtime._tenant_id,
        operation_kind=operation,
        idempotency_key=record_id,
        request_fingerprint=fingerprint,
        expected_head=0,
        fence_generation="r6",
        expected_fence_frontier=0,
        minimum_fence_frontier=0,
        records=(
            PhysicalRecord(
                record_id,
                "workspace_policy",
                "chiplog.workspace.policy.v2",
                payload,
                fingerprint,
            ),
        ),
    )


def test_r13_keeps_v1_primary_and_adds_the_exact_v2_writer_variant() -> None:
    assert R13Runtime._record_contracts["workspace_policy"] == "chiplog.workspace.policy.v1"
    assert ("workspace_policy", "chiplog.workspace.policy.v2") in R13Runtime._record_schema_variants


async def test_v2_writer_admission_persists_the_real_canonical_policy(tmp_path: Path) -> None:
    async with open_r13_runtime(tmp_path / "runtime.sqlite") as runtime:
        command = _command(runtime)
        result = await runtime._appender.submit(command)

        assert result.disposition == "COMMITTED"
        assert result.record_ids == (command.idempotency_key,)
        assert runtime._pending() == ()


@pytest.mark.parametrize("operation", ("workspace.policy", "conversation.accept"))
async def test_v2_record_under_an_old_operation_is_denied(tmp_path: Path, operation: str) -> None:
    async with open_r13_runtime(tmp_path / "runtime.sqlite") as runtime:
        with pytest.raises(LoopRejected, match="H1 V2 workspace policy"):
            runtime.decide_publication(_command(runtime, operation=operation), "resulting")


async def test_v2_operation_with_a_v1_record_is_denied(tmp_path: Path) -> None:
    async with open_r13_runtime(tmp_path / "runtime.sqlite") as runtime:
        command = _command(runtime)
        legacy = PhysicalRecord(
            command.idempotency_key,
            "workspace_policy",
            "chiplog.workspace.policy.v1",
            command.records[0].canonical_bytes,
            command.request_fingerprint,
        )
        with pytest.raises(LoopRejected, match="H1 V2 workspace policy"):
            runtime.decide_publication(replace(command, records=(legacy,)), "resulting")


async def test_v1_command_preserves_its_exact_operation_and_bytes(tmp_path: Path) -> None:
    payload = b'{"legacy":"bytes stay exact"}'
    fingerprint = _digest(payload)
    async with open_r13_runtime(tmp_path / "runtime.sqlite") as runtime:
        command = PhysicalPublicationCommand(
            runtime._tenant_id,
            "workspace.policy",
            "legacy-policy",
            fingerprint,
            0,
            "r6",
            0,
            0,
            (
                PhysicalRecord(
                    "legacy-policy",
                    "workspace_policy",
                    "chiplog.workspace.policy.v1",
                    payload,
                    fingerprint,
                ),
            ),
        )
        assert (await runtime._appender.submit(command)).disposition == "COMMITTED"
        entries = [json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()]
        decided = next(entry for entry in entries if entry["kind"] == "DECIDED")
        assert decided["operation_kind"] == "workspace.policy"
        assert base64.b64decode(decided["records"][0]["payload"], validate=True) == payload


@pytest.mark.parametrize("fault", ("before_commit", "after_commit"))
async def test_v2_decision_recovers_exactly_after_persisted_failure(
    tmp_path: Path, fault: Literal["before_commit", "after_commit"]
) -> None:
    database = tmp_path / "runtime.sqlite"
    async with open_r13_runtime(database) as runtime:
        command = _command(runtime)
        with pytest.raises(RuntimeError, match="injected"):
            await runtime._appender.submit(replace(command, fault=fault))
        (pending,) = runtime._pending()
        assert pending["operation_kind"] == "workspace.policy.h1.v2"

    async with open_r13_runtime(database) as reopened:
        assert reopened._pending() == ()
