"""Actual owner, writer, original provider evidence and separate resolver witnesses."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from chiplog.adapters.driven.effects_hermetic import HermeticResponseLost
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
from chiplog.capabilities.effects.dispatch_v2 import DispatchRecordV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime, open_dispatch_loop
from chiplog.platform._owner_publication_contracts import (
    JournalSelectedPublication,
    WorkerAuthentication,
)
from chiplog.platform.broker import PublicPortCall
from tests.support.dispatch import _response


def outcome(result: object) -> DispatchOutcomeRecordV2:
    assert isinstance(result, JournalSelectedPublication), result
    return DispatchOutcomeRecordV2.model_validate_json(result.complete_records[0].canonical_bytes)


async def prepare(runtime: R16DispatchRuntime) -> str:
    display = await runtime.preview_dispatch("r/turn/1/proposal/effect")
    accepted = await runtime.adopt_dispatch(
        "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id, "r"
    )
    assert isinstance(accepted, JournalSelectedPublication), accepted
    record = DispatchRecordV2.model_validate_json(accepted.complete_records[-1].canonical_bytes)
    intent_id = record.snapshot.intent.intent_id
    assert isinstance(
        await runtime.authorize_dispatch("hermetic-ingress", intent_id, "authorize", "r"),
        JournalSelectedPublication,
    )
    assert isinstance(
        await runtime.commit_first_send("hermetic-ingress", intent_id, "send", "r"),
        JournalSelectedPublication,
    )
    return intent_id


@pytest.mark.parametrize(
    "phase", ["boundary_provider", "boundary_resources", "resolution_provider"]
)
async def test_outcome_boundary_preserves_one_shot_and_custody_during_owner_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from chiplog.adapters.driven.effects_hermetic import HermeticEffectsProvider
    from chiplog.composition import r16_dispatch_outcomes
    from chiplog.composition.r16_dispatch_publication import owner_call

    resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    provider = resources.require_original_provider()
    replacement = HermeticEffectsProvider(receipt_key=b"replacement", scenarios=("CONFIRM",))
    other = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
    async with open_dispatch_loop(
        tmp_path / "race.db", resources=resources, responses=(_response(),)
    ) as loop:
        created = await loop.create("r", "Action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        intent_id = await prepare(runtime)
        if phase == "resolution_provider":
            await runtime.emit_committed("hermetic-ingress", intent_id)
        before = runtime._owner_decisions().snapshot()
        owner = owner_call
        reached: list[str] = []

        async def substitute(
            bound: R16DispatchRuntime, operation: str, schema: str, payload: bytes
        ) -> tuple[PublicPortCall, bytes]:
            result = await owner(bound, operation, schema, payload)
            if operation == "effects.prepare_dispatch_outcome_v2":
                reached.append(operation)
                if phase == "boundary_resources":
                    bound._dispatch_resources = other
                else:
                    resources._provider = replacement
            return result

        with monkeypatch.context() as fault:
            fault.setattr(r16_dispatch_outcomes, "owner_call", substitute)
            with pytest.raises(ValueError, match="identity differs from original custody"):
                if phase == "resolution_provider":
                    await runtime.resolve_dispatch_outcome("hermetic-ingress", intent_id, "resolve")
                else:
                    await runtime.emit_committed("hermetic-ingress", intent_id)
        assert len(reached) == 1
        assert replacement.transfers == () and other.require_original_provider().transfers == ()
        assert len(provider.transfers) == (1 if phase == "resolution_provider" else 0)
        after = runtime._owner_decisions().snapshot()
        if phase == "boundary_provider":
            new = after.decisions[len(before.decisions) :]
            assert tuple(row.prepared.request.operation for row in new) == (
                "effects.record_evidence",
                "dispatch.consume_effect_send",
            )
            row = DispatchOutcomeRecordV2.model_validate_json(
                new[0].prepared.request.complete_records[0].canonical_bytes
            )
            assert row.snapshot.state == "OUTCOME_UNKNOWN"
            assert row.snapshot.obligation.state == "OPEN"
            authentication = new[0].prepared.request.authentication
            assert isinstance(authentication, WorkerAuthentication)
            issuance = r16_dispatch_outcomes.OutcomeIssuance.model_validate_json(
                authentication.applicability_bytes
            )
            raw = r16_dispatch_outcomes._sealed(issuance.request, issuance.record)
            assert resources.verify_outcome(raw, issuance.signature)
            assert not resources.verify_outcome(raw + b"changed", issuance.signature)
            changed = issuance.request.model_copy(
                update={
                    "command": issuance.request.command.model_copy(
                        update={"operation": "RESOLVE_OBLIGATION", "evidence": None}
                    )
                }
            )
            with pytest.raises(ValueError, match="boundary seal"):
                resources.seal_boundary_outcome(changed, issuance.record)
        else:
            assert after == before
        resources._provider = provider
        runtime._dispatch_resources = resources
        if phase != "boundary_resources":
            assert await runtime.emit_committed("hermetic-ingress", intent_id) is None
            assert len(provider.transfers) == (1 if phase == "resolution_provider" else 0)


async def test_lost_reply_original_evidence_and_os_process_resolution(tmp_path: Path) -> None:
    database, custody = tmp_path / "dispatch.db", tmp_path / "custody.json"
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",), cap=1, custody_path=custody
    )
    async with open_dispatch_loop(database, resources=resources, responses=(_response(),)) as loop:
        created = await loop.create("r", "Prepare my action", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        intent_id = await prepare(runtime)
        with pytest.raises(HermeticResponseLost):
            await runtime.emit_committed("hermetic-ingress", intent_id)
        assert len(resources.require_original_provider().transfers) == 1
        assert await runtime.emit_committed("hermetic-ingress", intent_id) is None
        with pytest.raises(LoopRejected, match="outcome stream"):
            await runtime.commit_first_send("hermetic-ingress", intent_id, "blind-replacement", "r")
        assert len(resources.require_original_provider().transfers) == 1
        with runtime._authority_gate().hold():
            decisions = runtime._owner_decisions().snapshot().decisions
        opening = next(
            DispatchOutcomeRecordV2.model_validate_json(row.canonical_bytes)
            for decision in decisions
            for row in decision.prepared.request.complete_records
            if row.schema_id == "chiplog.effects.dispatch-outcome-record.v2"
        )
        assert opening.snapshot.state == "OUTCOME_UNKNOWN"
        assert opening.snapshot.obligation.state == "OPEN"
        runtime.restart_generation()  # old worker/session is dead; evidence does not use it
        resources.revoke()  # current SEND grant is revoked; historical receipt still authenticates
        receipt = resources.require_original_provider().transfers[0].signed_receipt
        with pytest.raises(ValueError):
            await runtime.observe_dispatch_outcome(intent_id, receipt[:-1] + b"x")
        evidence = outcome(await runtime.observe_dispatch_outcome(intent_id, receipt))
        assert evidence.snapshot.state == "CONFIRMED"
        assert evidence.snapshot.obligation.state == "OPEN"
        assert evidence.snapshot.obligation.obligation == opening.snapshot.obligation.obligation
        assert outcome(await runtime.observe_dispatch_outcome(intent_id, receipt)) == evidence
        # Crashing/reopening here leaves a valid evidence-present / obligation-open cut.
    script = tmp_path / "resume.py"
    output = tmp_path / "result.json"
    script.write_text("""import asyncio, json, sys
from pathlib import Path
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.composition.r16_dispatch_runtime import open_dispatch_runtime
from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2
async def main():
    database, custody, intent, output = sys.argv[1:]
    resources = HermeticDispatchResources(
        scenarios=("LOST_RESPONSE_AFTER_EFFECT",), cap=1, custody_path=Path(custody))
    async with open_dispatch_runtime(Path(database), resources=resources) as runtime:
        assert await runtime.emit_committed("hermetic-ingress", intent) is None
        observed = await runtime.observe_dispatch_outcome(intent)
        before = DispatchOutcomeRecordV2.model_validate_json(
            observed.complete_records[0].canonical_bytes)
        assert before.snapshot.obligation.state == "OPEN"
        result = await runtime.resolve_dispatch_outcome("hermetic-ingress", intent, "resolve")
        record = DispatchOutcomeRecordV2.model_validate_json(
            result.complete_records[0].canonical_bytes)
        replay = await runtime.resolve_dispatch_outcome("hermetic-ingress", intent, "resolve")
        assert replay.complete_records == result.complete_records
        Path(output).write_text(json.dumps({
            "state": record.snapshot.state, "obligation": record.snapshot.obligation.state,
            "transfers": len(resources.require_original_provider().transfers)}))
if __name__ == "__main__":
    asyncio.run(main())
""")
    completed = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script),
        str(database),
        str(custody),
        intent_id,
        str(output),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(completed.communicate(), timeout=120)
    assert completed.returncode == 0, stdout.decode() + stderr.decode()
    assert json.loads(output.read_text()) == {
        "state": "CONFIRMED",
        "obligation": "CLOSED",
        "transfers": 1,
    }


@pytest.mark.parametrize(
    "scenario,expected",
    (("PERMANENT_NO_EFFECT", "FAILED_NO_EFFECT"), ("MIXED", "PARTIAL_CONFIRMED")),
)
async def test_signed_provider_partition_terminalizes_only_separate_resolver(
    tmp_path: Path, scenario: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Literal, cast

    response = json.loads(_response())
    proposal = json.loads(response["tool_calls"][0]["text"])
    proposal["bundle_members"] = ["first", "second"]
    response["tool_calls"][0]["text"] = json.dumps(proposal, sort_keys=True, separators=(",", ":"))
    resources = HermeticDispatchResources(
        scenarios=(cast(Literal["PERMANENT_NO_EFFECT", "MIXED"], scenario),), cap=1
    )
    async with open_dispatch_loop(
        tmp_path / "effect.db", resources=resources, responses=(json.dumps(response).encode(),)
    ) as loop:
        created = await loop.create("r", "Prepare bundle", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        runtime = loop._planning
        assert isinstance(runtime, R16DispatchRuntime)
        intent_id = await prepare(runtime)
        import time
        from dataclasses import replace

        from chiplog.composition.r7_planning import ObservedTrustCall
        from chiplog.composition.r16_dispatch_outcomes import OutcomeAuthority
        from chiplog.platform._owner_publication_contracts import (
            ExactReplayQuery,
            PublicationRejected,
            RegisteredPublication,
            SingleOwnerBatch,
        )
        from chiplog.platform.owner_publications import PreparedOwnerPublication

        trust_checks: list[tuple[int, int, str | None]] = []
        original_trust_guard = runtime._trust_observation_guard

        def track_auth(
            value: ObservedTrustCall,
        ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
            checked_at = time.monotonic_ns()
            result = original_trust_guard(value)
            trust_checks.append((checked_at, value.request.budget.absolute_deadline_ns, result))
            return result

        monkeypatch.setattr(runtime, "_trust_observation_guard", track_auth)
        original_prepare = OutcomeAuthority.prepare
        reached: list[str] = []
        captured_preparations: list[tuple[OutcomeAuthority, PreparedOwnerPublication]] = []

        async def checked_prepare(
            authority: OutcomeAuthority, request: RegisteredPublication
        ) -> PreparedOwnerPublication | PublicationRejected:
            prepared = await original_prepare(authority, request)
            assert isinstance(prepared, PreparedOwnerPublication)
            assert authority.check_prepared(replace(prepared)) == "DENIED"
            assert isinstance(request, SingleOwnerBatch)
            assert authority.proof is not None
            query = ExactReplayQuery(
                identity=request.identity,
                operation=request.operation,
                current_invocation=authority.proof,
                original_commands=(request.command,),
            )
            for wrong in (
                query.model_copy(
                    update={
                        "identity": query.identity.model_copy(
                            update={"command_id": "unissued-command"}
                        )
                    }
                ),
                query.model_copy(update={"operation": "effects.prepare_dispatch_v2"}),
                query.model_copy(
                    update={
                        "original_commands": (
                            request.command.model_copy(
                                update={"canonical_bytes": b"unissued-command"}
                            ),
                        )
                    }
                ),
            ):
                assert isinstance(authority.authenticate_replay(wrong), PublicationRejected)
            if authority.observed is not None:
                from chiplog.composition.r16_dispatch_outcomes import same_resolver_authority

                actor = authority.observed
                assert same_resolver_authority(actor, actor)
                refreshed_deadline = actor.request.budget.model_copy(
                    update={"absolute_deadline_ns": actor.request.budget.absolute_deadline_ns + 1}
                )
                assert same_resolver_authority(
                    actor,
                    replace(
                        actor,
                        request=actor.request.model_copy(
                            update={"budget": refreshed_deadline, "request_id": "fresh-observation"}
                        ),
                    ),
                )
                for changed in (
                    replace(
                        actor,
                        observation=replace(
                            actor.observation,
                            snapshot_bytes=actor.observation.snapshot_bytes
                            + b"changed-policy-or-revocation",
                        ),
                    ),
                    replace(
                        actor,
                        request=actor.request.model_copy(
                            update={
                                "callee": actor.request.callee.model_copy(
                                    update={"session_id": "new-session"}
                                )
                            }
                        ),
                    ),
                    replace(
                        actor,
                        request=actor.request.model_copy(
                            update={
                                "budget": actor.request.budget.model_copy(
                                    update={"policy_version": 99}
                                )
                            }
                        ),
                    ),
                ):
                    assert not same_resolver_authority(actor, changed)
            original_resources = runtime._dispatch_resources
            runtime._dispatch_resources = HermeticDispatchResources(scenarios=("CONFIRM",), cap=1)
            try:
                assert authority.check_prepared(prepared) == "INDETERMINATE"
            finally:
                runtime._dispatch_resources = original_resources
            assert isinstance(
                await original_prepare(authority, request.model_copy()), PublicationRejected
            )
            reached.append(request.operation)
            captured_preparations.append((authority, prepared))
            return prepared

        monkeypatch.setattr(OutcomeAuthority, "prepare", checked_prepare)
        raw = await runtime.emit_committed("hermetic-ingress", intent_id)
        assert raw is not None
        observed = outcome(await runtime.observe_dispatch_outcome(intent_id, raw))
        assert observed.snapshot.state == expected
        assert observed.snapshot.obligation.state == "OPEN"
        from chiplog.composition import r16_dispatch_outcomes
        from chiplog.composition.r16_dispatch_publication import owner_call as original_owner_call

        async def restart_after_preparation(
            runtime: R16DispatchRuntime, operation: str, schema: str, payload: bytes
        ) -> tuple[PublicPortCall, bytes]:
            returned = await original_owner_call(runtime, operation, schema, payload)
            runtime.restart_generation()
            return returned

        with monkeypatch.context() as race:
            race.setattr(r16_dispatch_outcomes, "owner_call", restart_after_preparation)
            with pytest.raises(ValueError, match="authority changed"):
                await runtime.resolve_dispatch_outcome(
                    "hermetic-ingress", intent_id, "stale-session"
                )
        resolution = await runtime.resolve_dispatch_outcome(
            "hermetic-ingress", intent_id, "resolve"
        )
        assert isinstance(resolution, JournalSelectedPublication), (resolution, trust_checks[-5:])
        resolved = outcome(resolution)
        assert resolved.snapshot.state == expected
        assert resolved.snapshot.obligation.state == "CLOSED"
        assert "effects.record_evidence" in reached and "effects.reconcile" in reached
        original_authority, original_prepared = captured_preparations[0]
        assert original_authority.check_prepared(original_prepared) == "STALE"
        assert len(resources.require_original_provider().transfers) == 1
