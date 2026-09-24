"""Actual isolated precursor and independently selected original preview custody."""

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust._journal import IndependentTenantDecisionJournal
from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    SelfEffectArguments,
)
from chiplog.composition import r14_call_preview as previews
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_acceptance_preview import decode_call_acceptance_preview
from chiplog.composition.r14_call_dispatch_policy import MAX_HORIZON_NS
from chiplog.composition.r14_call_issuance import (
    CallAcceptanceIssuance,
    call_batch,
    call_identity,
    call_receipt,
    validate_call_issuance,
)
from chiplog.composition.r14_call_preparation import prepare_call_exchange
from chiplog.composition.r14_call_preview import read_call_previews
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r16_dispatch_authority import DispatchPublicationAuthority
from chiplog.composition.r16_dispatch_publication import owner_call
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import (
    ExecutionDispatchRuntime,
    R16DispatchRuntime,
    open_execution_dispatch_runtime,
)
from chiplog.platform.broker import PublicPortCall, PublicPortResult
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


@pytest.fixture
async def initialized(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources]
]:
    response = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(
                    payload=b"\xff\x00preview", bundle_members=("first", "second")
                ),
            ),
        ),
    )
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody.json"
    )
    async with open_execution_dispatch_runtime(
        tmp_path / "preview.sqlite",
        resources=resources,
        responses=(json.dumps(response.model_dump(mode="json")).encode(),),
    ) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        captured = await runtime.capture_execution("hermetic-ingress", "run", started.head)
        sealed = await runtime.seal_execution("hermetic-ingress", "run", captured.head)
        _, inventory, _ = read_execution_call_history(runtime)
        row = inventory.ordered_calls[0]
        yield (
            runtime,
            CallAcceptanceTarget(
                original_call_id=row.original_call_id,
                initialized=row.initialized,
                current_run=previews.execution_run_head(sealed),
            ),
            resources,
        )


async def test_preview_selects_original_owner_result_without_changing_its_source_cut(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    runtime, target, resources = initialized
    before = (
        read_execution_call_history(runtime),
        runtime._owner_decisions().snapshot(),
        runtime._loop_decisions().entries(),
        runtime._commitment_journal.load(runtime._tenant_id),
    )
    first = await runtime.preview_call_acceptance("hermetic-ingress", target)
    second = await runtime.preview_call_acceptance("hermetic-ingress", target)
    retained = read_call_previews(runtime)
    assert tuple(item.preview for item in retained) == (first, second)
    assert first.preview_id != second.preview_id
    assert (
        read_execution_call_history(runtime),
        runtime._owner_decisions().snapshot(),
        runtime._loop_decisions().entries(),
        runtime._commitment_journal.load(runtime._tenant_id),
    ) == before
    _, mandate = decode_call_acceptance_preview(first.canonical_bytes())
    assert mandate.payload == b"\xff\x00preview"
    assert retained[0].request.mandate == mandate
    assert retained[0].result.mandate_digest == retained[0].request.request.mandate_digest
    assert resources.require_original_provider().transfers == ()

    reopened_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=runtime._database.parent / "custody.json"
    )
    async with open_execution_dispatch_runtime(
        runtime._database, resources=reopened_resources
    ) as reopened:
        assert read_call_previews(reopened) == retained
        assert read_call_previews(runtime) == retained
        assert reopened_resources.require_original_provider().transfers == ()


@pytest.mark.parametrize(
    "operation", ("agent_loop.prepare_consequential_acceptance", "effects.prepare_dispatch_v2")
)
async def test_call_preparation_rejects_revocation_during_either_actual_owner_exchange(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    runtime, target, resources = initialized
    preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
    selected = runtime._owner_decisions().snapshot()
    original = AuthorityBrokerRuntime.call
    reached: list[str] = []

    async def changed(self: AuthorityBrokerRuntime, request: PublicPortCall) -> PublicPortResult:
        response = await original(self, request)
        if request.operation_id == operation:
            reached.append(operation)
            resources.revoke()
        return response

    monkeypatch.setattr(AuthorityBrokerRuntime, "call", changed)
    with pytest.raises(LoopRejected):
        await prepare_call_exchange(
            runtime,
            "hermetic-ingress",
            CallAcceptanceAdoption(act_id="race", preview_bytes=preview.canonical_bytes()),
        )
    assert reached == [operation]
    assert runtime._owner_decisions().snapshot() == selected
    assert read_execution_call_history(runtime)[1].ordered_calls[0].acceptance.kind == "INITIALIZED"
    assert resources.require_original_provider().transfers == ()


async def test_preview_rejects_foreign_actor_and_changed_initialized_head(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    runtime, target, _ = initialized
    with pytest.raises(LoopRejected):
        await runtime.preview_call_acceptance("foreign", target)
    changed = target.model_copy(
        update={
            "initialized": target.initialized.model_copy(
                update={
                    "revision": target.initialized.revision.model_copy(
                        update={"fingerprint": "0" * 64}
                    )
                }
            )
        }
    )
    with pytest.raises(LoopRejected):
        await runtime.preview_call_acceptance("hermetic-ingress", changed)
    assert read_call_previews(runtime) == ()


async def test_preview_reader_rejects_journal_corruption_and_substitution(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, target, _ = initialized
    await runtime.preview_call_acceptance("hermetic-ingress", target)
    with monkeypatch.context() as patch:
        patch.setattr(runtime, "_call_preview_journal", object())
        with pytest.raises(EffectsIntegrityError, match=r"call-preview\.read"):
            read_call_previews(runtime)
    journal = runtime._require_call_preview_journal()
    original = journal._path.read_bytes()
    journal._path.write_bytes(original.replace(b'"authentication":"', b'"authentication":"bad', 1))
    with pytest.raises(EffectsIntegrityError, match=r"call-preview\.read"):
        read_call_previews(runtime)


@pytest.mark.parametrize("change", ("expiry", "revocation"))
async def test_changed_sources_or_elapsed_horizon_during_owner_exchange_select_nothing(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    runtime, target, resources = initialized
    original = owner_call
    clock = resources.clock

    async def changed(
        runtime: R16DispatchRuntime, operation: str, schema: str, payload: bytes
    ) -> tuple[PublicPortCall, bytes]:
        result = await original(runtime, operation, schema, payload)
        if change == "expiry":
            epoch, now = clock()
            monkeypatch.setattr(resources, "clock", lambda: (epoch, now + MAX_HORIZON_NS))
        else:
            resources.revoke()
        return result

    monkeypatch.setattr(previews, "owner_call", changed)
    with pytest.raises(LoopRejected):
        await runtime.preview_call_acceptance("hermetic-ingress", target)
    assert read_call_previews(runtime) == ()
    assert resources.require_original_provider().transfers == ()


async def test_lost_response_after_completed_journal_append_retains_exact_original_preview(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, target, resources = initialized
    original = IndependentTenantDecisionJournal.append
    selected: list[bytes] = []

    def lost(
        self: IndependentTenantDecisionJournal, decision: bytes, predecessor: str | None
    ) -> str:
        result = original(self, decision, predecessor)
        if self._path == runtime._database.with_suffix(".call-previews"):
            selected.append(decision)
            raise OSError("injected loss after completed preview selection")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(IndependentTenantDecisionJournal, "append", lost)
        with pytest.raises(EffectsIntegrityError, match=r"call-preview\.select"):
            await runtime.preview_call_acceptance("hermetic-ingress", target)
    retained = read_call_previews(runtime)
    assert len(retained) == 1
    assert [retained[0].canonical_bytes()] == selected
    assert read_call_previews(runtime) == retained
    assert resources.require_original_provider().transfers == ()


async def test_authentic_preview_journal_cannot_be_transplanted_to_another_database(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    runtime, target, _ = initialized
    await runtime.preview_call_acceptance("hermetic-ingress", target)
    original = runtime._require_call_preview_journal()
    database = runtime._database.with_name("other.sqlite")
    body = database.with_suffix(".call-previews")
    for source, destination in (
        (original._path, body),
        (original._key_path, body.with_suffix(body.suffix + ".key")),
        (original._head_path, body.with_suffix(body.suffix + ".head")),
    ):
        assert not destination.exists()
        destination.write_bytes(source.read_bytes())
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=runtime._database.parent / "custody.json"
    )
    with pytest.raises(EffectsIntegrityError, match=r"call-preview\.read") as caught:
        async with open_execution_dispatch_runtime(database, resources=resources):
            raise AssertionError("transplanted preview journal was admitted")
    assert caught.value.__cause__ is not None
    assert "another physical authority database" in str(caught.value.__cause__)
    assert resources.require_original_provider().transfers == ()


async def test_nonempty_preview_journal_missing_key_is_typed_integrity_failure_on_reopen(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    runtime, target, _ = initialized
    await runtime.preview_call_acceptance("hermetic-ingress", target)
    runtime._require_call_preview_journal()._key_path.unlink()
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=runtime._database.parent / "custody.json"
    )
    with pytest.raises(EffectsIntegrityError, match=r"call-preview\.open") as caught:
        async with open_execution_dispatch_runtime(runtime._database, resources=resources):
            raise AssertionError("missing preview key was silently recreated")
    assert caught.value.__cause__ is not None
    assert "no authentication key" in str(caught.value.__cause__)


async def test_adopted_preview_prepares_both_real_owners_without_publication_or_send(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    runtime, target, resources = initialized
    preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
    history = read_execution_call_history(runtime)
    selected = runtime._owner_decisions().snapshot()
    adopted = CallAcceptanceAdoption(act_id="accept", preview_bytes=preview.canonical_bytes())
    prepared = await prepare_call_exchange(runtime, "hermetic-ingress", adopted)
    assert type(prepared).model_validate_json(prepared.canonical_bytes()) == prepared
    assert prepared.captured.cut.worker is not None
    assert target.current_run.revision.head == prepared.captured.cut.worker.run.head
    retained = prepared.retained
    assert retained.loop_proposal.accepted.binding.initialized == target.initialized
    assert retained.effects_proposal.snapshot.intent.mandate == prepared.preview.request.mandate
    from chiplog.capabilities.effects.dispatch_v2_contracts import PublishDispatchIntentV2

    assert isinstance(retained.effects_request.command, PublishDispatchIntentV2)
    assert retained.effects_request.command.complete_publication_manifest == tuple(
        previews._effect_head(ref) for ref in retained.loop_proposal.complete_acceptance_manifest
    )
    assert prepared.loop_sent.callee.owner_id == "agent_loop"
    assert prepared.effects_sent.callee.owner_id == "effects"
    value = CallAcceptanceIssuance.model_validate_json(prepared.canonical_bytes())
    # Issuing an invocation and constructing a batch do not publish or grant SEND.
    authority = DispatchPublicationAuthority(runtime, prepared.observed)
    invocation = authority.invocation(call_identity(value))
    batch = call_batch(value, invocation)
    assert validate_call_issuance(batch, runtime) == value
    receipt = call_receipt(batch, value)
    assert receipt.original_call_id == target.original_call_id
    assert receipt.publication_id == retained.loop_request.command_id
    altered_values = (
        value.model_copy(update={"adoption": adopted.model_copy(update={"act_id": "other"})}),
        value.model_copy(
            update={
                "loop_sent": value.loop_sent.__class__(
                    **{**value.loop_sent.__dict__, "schema_id": "other"}
                )
            }
        ),
        value.model_copy(
            update={
                "retained": retained.model_copy(
                    update={
                        "loop_request": retained.loop_request.model_copy(
                            update={
                                "binding": retained.loop_request.binding.model_copy(
                                    update={
                                        "cut": retained.loop_request.binding.cut.model_copy(
                                            update={"sources": ()}
                                        )
                                    }
                                )
                            }
                        )
                    }
                )
            }
        ),
    )
    for altered in altered_values:
        with pytest.raises(ValueError):
            validate_call_issuance(call_batch(altered, invocation), runtime)
    assert {source.family for source in retained.loop_request.binding.cut.sources} == {
        "ACTOR",
        "MANDATE",
        "TOOL_SCHEMA",
        "POLICY",
        "APPLICABILITY",
        "RECIPIENT",
        "CONSEQUENCE_SCOPE",
        "PLANNING",
        "DEPENDENCY",
        "EVIDENCE",
        "CONFLICT_ORDER",
    }
    assert read_execution_call_history(runtime) == history
    assert runtime._owner_decisions().snapshot() == selected
    assert resources.require_original_provider().transfers == ()
    resources.revoke()
    assert validate_call_issuance(batch, runtime) == value


async def test_call_writer_authority_requires_exact_issued_objects_and_current_scope(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    from dataclasses import replace

    from chiplog.composition.r14_call_authority import CallPublicationAuthority
    from chiplog.composition.r16_dispatch_publication import authenticate
    from chiplog.platform._owner_publication_contracts import PublicationRejected
    from chiplog.platform.owner_publications import PreparedOwnerPublication

    runtime, target, resources = initialized
    preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
    observed = await authenticate(runtime, "hermetic-ingress")
    authority = CallPublicationAuthority(runtime, observed)
    batch = await authority.prepare_fresh(
        "hermetic-ingress",
        CallAcceptanceAdoption(act_id="writer", preview_bytes=preview.canonical_bytes()),
    )
    prepared = await authority.prepare(batch)
    assert isinstance(prepared, PreparedOwnerPublication)
    assert authority.check_prepared(prepared) is None
    assert authority.check_prepared(replace(prepared)) == "DENIED"
    copied = await authority.prepare(batch.model_copy())
    assert isinstance(copied, PublicationRejected) and copied.kind == "DENIED"
    before = runtime._owner_decisions().snapshot()
    resources.revoke()
    assert authority.check_prepared(prepared) == "STALE"
    assert runtime._owner_decisions().snapshot() == before
    assert resources.require_original_provider().transfers == ()


async def test_actual_atomic_call_acceptance_joins_both_owner_records_and_reopens(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    from chiplog.composition.r14_call_authority import CallPublicationAuthority
    from chiplog.composition.r16_dispatch_publication import authenticate
    from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
    from chiplog.platform.owner_publications import BrokerPublicationCoordinator

    runtime, target, resources = initialized
    preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
    before = read_execution_call_history(runtime)
    authority = CallPublicationAuthority(runtime, await authenticate(runtime, "hermetic-ingress"))
    batch = await authority.prepare_fresh(
        "hermetic-ingress",
        CallAcceptanceAdoption(act_id="atomic", preview_bytes=preview.canonical_bytes()),
    )
    coordinator = BrokerPublicationCoordinator(
        runtime._appender, authority, runtime._owner_decisions()
    )
    result = await coordinator.commit(batch)
    assert isinstance(result, JournalSelectedPublication), result
    assert result.kind == "COMMITTED"
    after = read_execution_call_history(runtime)
    assert after[0].records == before[0].records
    assert after[0].tenant_head == before[0].tenant_head + 1
    accepted = after[1].ordered_calls[0]
    assert accepted.acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
    assert accepted.initialized == target.initialized
    assert accepted.terminal.kind == "ABSENT"
    selected = runtime._owner_decisions().lookup(runtime._tenant_id, batch.identity.command_id)
    assert selected is not None
    assert authority.materialization_state(selected) == "COMPLETE"
    assert resources.require_original_provider().transfers == ()
    restarted_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=runtime._database.parent / "custody.json"
    )
    async with open_execution_dispatch_runtime(
        runtime._database, resources=restarted_resources
    ) as reopened:
        assert read_execution_call_history(reopened) == after
        assert reopened._owner_decisions().snapshot() == runtime._owner_decisions().snapshot()
        assert restarted_resources.require_original_provider().transfers == ()


@pytest.mark.parametrize(("crash", "reopen_pending"), ((False, False), (True, False), (True, True)))
async def test_public_acceptance_exact_replay_recovers_original_batch_without_owner_reissue(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
    monkeypatch: pytest.MonkeyPatch,
    crash: bool,
    reopen_pending: bool,
) -> None:
    from dataclasses import replace

    from chiplog.platform._sqlite import PhysicalPublicationCommand, PublicationResult
    from chiplog.platform.owner_publications import OwnerPublicationUncertain

    runtime, target, resources = initialized
    preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
    adoption = CallAcceptanceAdoption(act_id="public", preview_bytes=preview.canonical_bytes())
    before = read_execution_call_history(runtime)
    submit = runtime._appender.submit

    async def fail(command: PhysicalPublicationCommand) -> PublicationResult:
        if command.operation_kind == "effects.accept_call":
            command = replace(command, fault="before_commit")
        return await submit(command)

    receipt = None
    if crash:
        with monkeypatch.context() as patch:
            patch.setattr(runtime._appender, "submit", fail)
            with pytest.raises(OwnerPublicationUncertain):
                await runtime.accept_call("hermetic-ingress", adoption)
        assert len(runtime._pending_owners()) == 1
    else:
        receipt = await runtime.accept_call("hermetic-ingress", adoption)
    resources.revoke()
    epoch, now = resources.clock()
    monkeypatch.setattr(resources, "clock", lambda: (epoch, now + MAX_HORIZON_NS))
    original_call = AuthorityBrokerRuntime.call

    async def prohibit_reissue(
        self: AuthorityBrokerRuntime, request: PublicPortCall
    ) -> PublicPortResult:
        assert request.operation_id not in {
            "agent_loop.prepare_consequential_acceptance",
            "effects.prepare_dispatch_v2",
            "effects.evaluate_dispatch_mandate_v2",
        }
        return await original_call(self, request)

    monkeypatch.setattr(AuthorityBrokerRuntime, "call", prohibit_reissue)
    if reopen_pending:
        recovery_resources = HermeticDispatchResources(
            scenarios=("CONFIRM",), cap=1, custody_path=runtime._database.parent / "custody.json"
        )
        async with open_execution_dispatch_runtime(
            runtime._database, resources=recovery_resources
        ) as recovery:
            assert recovery._pending_owners() == ()
            receipt = await recovery.accept_call("hermetic-ingress", adoption)
            recovery_resources.revoke()
            assert await recovery.accept_call("hermetic-ingress", adoption) == receipt
            recovered = read_execution_call_history(recovery)
            assert recovered[0].records == before[0].records
            assert recovered[0].tenant_head == before[0].tenant_head + 1
            assert recovered[1].ordered_calls[0].acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
            with pytest.raises(LoopRejected, match="different exact adoption"):
                await recovery.accept_call(
                    "hermetic-ingress",
                    adoption.model_copy(update={"preview_bytes": adoption.preview_bytes + b" "}),
                )
            assert recovery_resources.require_original_provider().transfers == ()
        # Restart admitted a new epoch; the old live object must not authenticate.
        with pytest.raises(EffectsIntegrityError) as stale:
            await runtime.accept_call("hermetic-ingress", adoption)
        assert stale.value.__cause__ is not None
        assert "epoch is stale" in str(stale.value.__cause__)
        assert resources.require_original_provider().transfers == ()
        return
    replay = await runtime.accept_call("hermetic-ingress", adoption)
    assert receipt is None or replay == receipt
    assert await runtime.accept_call("hermetic-ingress", adoption) == replay
    after = read_execution_call_history(runtime)
    assert after[0].records == before[0].records
    assert after[0].tenant_head == before[0].tenant_head + 1
    assert after[1].ordered_calls[0].acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
    assert runtime._pending_owners() == ()
    with pytest.raises(LoopRejected, match="different exact adoption"):
        await runtime.accept_call(
            "hermetic-ingress",
            adoption.model_copy(update={"preview_bytes": adoption.preview_bytes + b" "}),
        )
    restarted_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=runtime._database.parent / "custody.json"
    )
    async with open_execution_dispatch_runtime(
        runtime._database, resources=restarted_resources
    ) as reopened:
        assert await reopened.accept_call("hermetic-ingress", adoption) == replay
        assert read_execution_call_history(reopened) == after
        assert restarted_resources.require_original_provider().transfers == ()
    assert resources.require_original_provider().transfers == ()


async def test_corrupt_accepted_companion_is_typed_integrity_failure_on_public_replay(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    import sqlite3

    runtime, target, resources = initialized
    preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
    adoption = CallAcceptanceAdoption(act_id="corrupt", preview_bytes=preview.canonical_bytes())
    receipt = await runtime.accept_call("hermetic-ingress", adoption)
    selected = runtime._owner_decisions().lookup(runtime._tenant_id, receipt.publication_id)
    assert selected is not None
    with sqlite3.connect(runtime._database) as connection:
        changed = connection.execute(
            "DELETE FROM records WHERE tenant_id=? AND record_id=?",
            (runtime._tenant_id, selected.prepared.request.complete_records[0].record_id),
        )
        assert changed.rowcount == 1
    with pytest.raises(EffectsIntegrityError, match=r"call\.accept tenant=.*record=") as caught:
        await runtime.accept_call("hermetic-ingress", adoption)
    assert caught.value.__cause__ is not None
    assert "materialization is corrupt" in str(caught.value.__cause__)
    assert resources.require_original_provider().transfers == ()


async def test_accepted_call_authorizes_and_emits_only_its_original_payload_once(
    initialized: tuple[ExecutionDispatchRuntime, CallAcceptanceTarget, HermeticDispatchResources],
) -> None:
    from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2
    from chiplog.platform._owner_publication_contracts import JournalSelectedPublication

    runtime, target, resources = initialized
    preview = await runtime.preview_call_acceptance("hermetic-ingress", target)
    receipt = await runtime.accept_call(
        "hermetic-ingress",
        CallAcceptanceAdoption(act_id="dispatch-call", preview_bytes=preview.canonical_bytes()),
    )
    intent_id = receipt.external_intent.subject_id
    with pytest.raises(ValueError):
        await runtime.emit_committed("hermetic-ingress", intent_id)
    assert resources.require_original_provider().transfers == ()
    authorized = await runtime.authorize_dispatch(
        "hermetic-ingress", intent_id, "authorize-call", "run"
    )
    assert isinstance(authorized, JournalSelectedPublication), authorized
    send = await runtime.commit_first_send("hermetic-ingress", intent_id, "send-call", "run")
    assert isinstance(send, JournalSelectedPublication), send
    record = DispatchRecordV2.model_validate_json(send.complete_records[0].canonical_bytes)
    assert record.kind == "SEND_COMMITTED"
    assert record.snapshot.intent.mandate.canonical_bytes() == preview.mandate_bytes
    raw = await runtime.emit_committed("hermetic-ingress", intent_id)
    assert raw is not None
    assert len(resources.require_original_provider().transfers) == 1
    assert await runtime.emit_committed("hermetic-ingress", intent_id) is None
    assert len(resources.require_original_provider().transfers) == 1
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2

    evidence = await runtime.observe_dispatch_outcome(intent_id, raw)
    assert isinstance(evidence, JournalSelectedPublication), evidence
    observed = DispatchOutcomeRecordV2.model_validate_json(
        evidence.complete_records[0].canonical_bytes
    )
    assert observed.snapshot.state == "CONFIRMED"
    assert observed.snapshot.obligation.state == "OPEN"
    resolved = await runtime.resolve_dispatch_outcome("hermetic-ingress", intent_id, "resolve-call")
    assert isinstance(resolved, JournalSelectedPublication), resolved
    result = DispatchOutcomeRecordV2.model_validate_json(
        resolved.complete_records[0].canonical_bytes
    )
    assert result.snapshot.state == "CONFIRMED"
    assert result.snapshot.obligation.state == "CLOSED"
    assert len(resources.require_original_provider().transfers) == 1
    after = read_execution_call_history(runtime)
    restarted_resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=runtime._database.parent / "custody.json"
    )
    async with open_execution_dispatch_runtime(
        runtime._database, resources=restarted_resources
    ) as reopened:
        assert read_execution_call_history(reopened) == after
        replay = await reopened.resolve_dispatch_outcome(
            "hermetic-ingress", intent_id, "resolve-call"
        )
        assert isinstance(replay, JournalSelectedPublication)
        assert replay.complete_records == resolved.complete_records
        assert await reopened.emit_committed("hermetic-ingress", intent_id) is None
        assert (
            restarted_resources.require_original_provider().transfers
            == resources.require_original_provider().transfers
        )
        assert len(restarted_resources.require_original_provider().transfers) == 1
