"""The R14 resolver policy is actor-authorized, selected, and restart-readable."""

import hashlib
import json
from pathlib import Path

import pytest

from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.original_recovery_contracts import (
    OriginalResolverAuthorityPolicyV1,
)
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_original_resolver_policy_publication import (
    EVIDENCE_FIELD,
    RetainedOriginalResolverPolicyPublication,
    command,
    validate_selected,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources


@pytest.mark.asyncio
async def test_selected_policy_replays_and_reopens_with_physical_readback(tmp_path: Path) -> None:
    database = tmp_path / "resolver-policy.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
    ) as runtime:
        selected = await runtime.publish_original_resolver_authority_policy()
        assert isinstance(selected, OriginalResolverAuthorityPolicyV1)
        assert selected.tenant_id == runtime._tenant_id
        assert selected.principal_id == "hermetic-principal"
        assert runtime.read_selected_original_resolver_authority_policy() == selected
        source_policy, source = runtime.read_selected_original_resolver_authority_policy_source()
        assert source_policy == selected
        assert source.schema_id == "chiplog.loop.original-resolver-authority-policy.v1"
        assert source.canonical_record_bytes == selected.canonical_bytes()
        assert source.subject.subject_id == selected.policy_id
        assert source.physical_record.subject_id == selected.policy_id
        decision_id, decision_bytes = next(
            (decision_id, raw)
            for decision_id, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("operation_kind")
            == "agent_loop.original-resolver-authority-policy.v1"
        )
        assert source.selected_decision.subject_id == decision_id
        assert source.selected_decision.revision.head == decision_id
        assert (
            source.selected_decision.revision.fingerprint
            == hashlib.sha256(
                decision_bytes.encode() if isinstance(decision_bytes, str) else decision_bytes
            ).hexdigest()
        )
        assert await runtime.publish_original_resolver_authority_policy() == selected

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
    ) as runtime:
        assert runtime.read_selected_original_resolver_authority_policy() == selected
        assert runtime.read_selected_original_resolver_authority_policy_source()[0] == selected
        assert await runtime.publish_original_resolver_authority_policy() == selected


@pytest.mark.asyncio
async def test_policy_requires_registered_fresh_ingress_and_rejects_tampered_history(
    tmp_path: Path,
) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "resolver-policy.sqlite",
        resources=resources,
        manifest=R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
    ) as runtime:
        with pytest.raises(LoopRejected, match="unregistered execution recovery ingress"):
            await runtime.publish_original_resolver_authority_policy("unregistered")
        assert runtime.read_selected_original_resolver_authority_policy() is None

        selected = await runtime.publish_original_resolver_authority_policy()
        entry = next(
            json.loads(raw)
            for _, _, raw in runtime._loop_decisions().entries()
            if json.loads(raw).get("operation_kind")
            == "agent_loop.original-resolver-authority-policy.v1"
        )
        evidence = RetainedOriginalResolverPolicyPublication.model_validate_json(
            entry[EVIDENCE_FIELD]
        )
        forged = evidence.model_copy(
            update={
                "actor_budget_v1": evidence.actor_budget_v1.model_copy(
                    update={"trust_journal_head": "missing-trust-head"}
                )
            }
        )
        entry[EVIDENCE_FIELD] = forged.canonical_bytes().decode()
        with pytest.raises(ValueError, match="trust head absent or ambiguous"):
            validate_selected(runtime, entry, command(evidence))
        assert runtime.read_selected_original_resolver_authority_policy() == selected


@pytest.mark.asyncio
async def test_lost_acknowledgement_recovers_exact_pending_policy_selection(
    tmp_path: Path,
) -> None:
    from unittest.mock import patch

    from chiplog.platform._sqlite import LostCommitAcknowledgement

    database = tmp_path / "resolver-policy-lost-ack.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
    ) as runtime:
        original = runtime._appender.submit

        async def lose_ack(batch):
            await original(batch)
            raise LostCommitAcknowledgement("resolver policy ack lost")

        with (
            patch.object(runtime._appender, "submit", side_effect=lose_ack),
            pytest.raises(LostCommitAcknowledgement),
        ):
            await runtime.publish_original_resolver_authority_policy()

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
    ) as runtime:
        selected = runtime.read_selected_original_resolver_authority_policy()
        assert isinstance(selected, OriginalResolverAuthorityPolicyV1)
        assert await runtime.publish_original_resolver_authority_policy() == selected
