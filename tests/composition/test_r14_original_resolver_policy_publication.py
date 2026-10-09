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
        reducer_policy, reducer_source = (
            runtime.read_selected_original_effect_evidence_reducer_policy_source()
        )
        assert reducer_policy.reducer_id == "R14_ORIGINAL_EFFECT_EVIDENCE_REDUCER"
        assert reducer_policy.reducer_version == "r14-effect-evidence.v1"
        assert reducer_source.schema_id == (
            "chiplog.loop.original-effect-evidence-reducer-policy.v1"
        )
        assert reducer_source.canonical_record_bytes == reducer_policy.canonical_bytes()
        assert reducer_source.selected_decision == source.selected_decision
        assert reducer_source.physical_record.subject_id == reducer_policy.policy_id
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
        assert (
            runtime.read_selected_original_effect_evidence_reducer_policy_source()[0]
            == reducer_policy
        )
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


@pytest.mark.asyncio
async def test_selected_policy_catalog_rejects_extra_reducer_record(tmp_path: Path) -> None:
    import sqlite3
    from contextlib import closing

    from chiplog.composition.r14_original_resolver_policy_publication import (
        _selected_entries,
        _validate_physical_catalog,
    )

    database = tmp_path / "resolver-policy-extra.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_ORIGINAL_RESOLVER_POLICY_V31_PRODUCTION_MANIFEST,
    ) as runtime:
        await runtime.publish_original_resolver_authority_policy()
        batch = _selected_entries(runtime)[0][3]
        reducer = batch.records[1]
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?)",
                (
                    runtime._tenant_id,
                    "unselected-extra-reducer-policy",
                    reducer.owner,
                    reducer.schema_id,
                    reducer.canonical_bytes,
                    batch.expected_head + 1,
                ),
            )
        with pytest.raises(ValueError, match="physical catalog differs from selection"):
            _validate_physical_catalog(runtime, batch)


@pytest.mark.asyncio
async def test_invocation_publication_replays_exact_lost_ack_without_new_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import hashlib
    from unittest.mock import patch

    from chiplog.architecture.r7_runtime import (
        R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST,
    )
    from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
    from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
        AcceptedOpenRecoveryOpeningV1,
        AcceptedOpenTerminalDispositionV1,
    )
    from chiplog.capabilities.agent_loop.execution_recovery_observations import RecoverySourceRecord
    from chiplog.capabilities.agent_loop.original_recovery_contracts import (
        original_effect_evidence_reducer_policy_v1,
        original_resolver_authority_policy_v1,
    )
    from chiplog.capabilities.agent_loop.recovery_contracts import (
        Absent,
        OriginalObligationBinding,
        Present,
    )
    from chiplog.composition import r14_original_resolver_invocation_publication as publication
    from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
    from chiplog.platform._sqlite import LostCommitAcknowledgement

    tenant = "hermetic-tenant"
    binding = OriginalObligationBinding(
        original_run_id="original-run",
        original_call_id="original-call",
        obligation_id="original-obligation",
        obligation_stream_id="original-obligation-stream",
        obligation_head="open-head",
        closure_predicate_id="R14_ORIGINAL_EFFECT_CHILDREN_TERMINAL",
        closure_predicate_version="1",
        resolver_id="R14_AUTHENTICATED_ORIGINAL_PRINCIPAL_RESOLVER",
        resolver_version="r14-original-resolver.v1",
        reducer_id="R14_ORIGINAL_EFFECT_EVIDENCE_REDUCER",
        reducer_version="r14-effect-evidence.v1",
        evidence_stream_id="original-evidence-stream",
        evidence_head=Absent(),
    )
    opening = AcceptedOpenRecoveryOpeningV1(
        opening_id="accepted-open-opening:fixture",
        source_cut_fingerprint="1" * 64,
        original=binding,
        evidence_sought=(),
        safe_actions=("RESOLVE_ORIGINAL_OBLIGATION",),
        denied_actions=("SEND", "RETRY", "APPEND_EVIDENCE"),
    )
    opening_digest = hashlib.sha256(opening.canonical_bytes()).hexdigest()
    accepted_decision = CallSubjectHead(
        subject_id="accepted-open-decision",
        revision=Present(head="accepted-open-decision", fingerprint="2" * 64),
    )
    opening_ref = CallSubjectHead(
        subject_id=opening.opening_id,
        revision=Present(head="record:" + opening_digest, fingerprint=opening_digest),
    )
    opening_source = RecoverySourceRecord(
        owner="agent_loop",
        subject=CallSubjectHead(
            subject_id=binding.obligation_id,
            revision=Present(head=binding.obligation_head, fingerprint=opening_digest),
        ),
        schema_id=opening.schema_id,
        canonical_record_bytes=opening.canonical_bytes(),
        selected_decision=accepted_decision,
        physical_record=opening_ref,
    )
    terminal = AcceptedOpenTerminalDispositionV1(
        terminal_id="accepted-open-terminal:fixture",
        source_cut_fingerprint="1" * 64,
        original_call_id=binding.original_call_id,
        response_id="response",
        accepted=CallSubjectHead(
            subject_id="acceptance", revision=Present(head="acceptance", fingerprint="3" * 64)
        ),
        external_effect_intent=CallSubjectHead(
            subject_id="intent", revision=Present(head="intent", fingerprint="4" * 64)
        ),
        obligation_opening=opening_ref,
    )
    terminal_digest = hashlib.sha256(terminal.canonical_bytes()).hexdigest()
    terminal_source = RecoverySourceRecord(
        owner="agent_loop",
        subject=CallSubjectHead(
            subject_id=binding.original_call_id,
            revision=Present(head="record:" + terminal_digest, fingerprint=terminal_digest),
        ),
        schema_id=terminal.schema_id,
        canonical_record_bytes=terminal.canonical_bytes(),
        selected_decision=accepted_decision,
        physical_record=CallSubjectHead(
            subject_id=terminal.terminal_id,
            revision=Present(head="record:" + terminal_digest, fingerprint=terminal_digest),
        ),
    )
    policy_decision = CallSubjectHead(
        subject_id="policy-decision",
        revision=Present(head="policy-decision", fingerprint="5" * 64),
    )

    def policy_source(policy, schema: str) -> RecoverySourceRecord:
        digest = hashlib.sha256(policy.canonical_bytes()).hexdigest()
        record_ref = CallSubjectHead(
            subject_id=policy.policy_id,
            revision=Present(head="record:" + digest, fingerprint=digest),
        )
        return RecoverySourceRecord(
            owner="agent_loop",
            subject=record_ref,
            schema_id=schema,
            canonical_record_bytes=policy.canonical_bytes(),
            selected_decision=policy_decision,
            physical_record=record_ref,
        )

    resolver_source = policy_source(
        original_resolver_authority_policy_v1(tenant),
        "chiplog.loop.original-resolver-authority-policy.v1",
    )
    reducer_source = policy_source(
        original_effect_evidence_reducer_policy_v1(tenant),
        "chiplog.loop.original-effect-evidence-reducer-policy.v1",
    )
    sources = (opening_source, terminal_source, resolver_source, reducer_source)
    monkeypatch.setattr(publication, "_current_sources", lambda _runtime: sources)

    database = tmp_path / "resolver-invocation.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST,
    ) as runtime:
        submit = runtime._appender.submit

        async def commit_then_lose_ack(batch):
            await submit(batch)
            raise LostCommitAcknowledgement("invocation acknowledgement lost")

        with (
            patch.object(runtime._appender, "submit", side_effect=commit_then_lose_ack),
            pytest.raises(LostCommitAcknowledgement),
        ):
            await runtime.publish_original_resolver_invocation()

    async with open_common_cli_execution_runtime(
        database,
        resources=resources,
        manifest=R14_EXECUTION_ORIGINAL_RESOLVER_INVOCATION_V32_PRODUCTION_MANIFEST,
    ) as runtime:
        selected, source = runtime.read_selected_original_resolver_invocation_source()
        assert selected.original == binding
        assert source.canonical_record_bytes == selected.canonical_bytes()
        with patch.object(
            publication,
            "issue_execution_recovery_actor_v1",
            side_effect=AssertionError("selected replay must not authenticate a new invocation"),
        ):
            assert await runtime.publish_original_resolver_invocation() == selected
