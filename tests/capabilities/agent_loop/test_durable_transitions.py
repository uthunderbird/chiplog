from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop import domain
from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    Complete,
    DisclosureLabel,
    EndpointSelection,
    LoopRejected,
    NoExposureProof,
    RunRecord,
)
from chiplog.composition.r13 import open_r13_loop


@pytest.mark.parametrize(
    "source", ["CREATED", "ACTIVE", "SUCCEEDED", "SUSPENDED", "SUPERSEDED", "ABORTED", "CANCELLED"]
)
@pytest.mark.parametrize(
    "target", ["CREATED", "ACTIVE", "SUCCEEDED", "SUSPENDED", "SUPERSEDED", "ABORTED", "CANCELLED"]
)
def test_run_transition_matrix_requires_owner_completion_and_recovery(
    source: str, target: str
) -> None:
    run = RunRecord.model_validate(
        {
            "tenant": "t",
            "principal": "p",
            "run_id": "r",
            "state": source,
            "head": "head",
            "predecessor": None,
            "prompt": "prompt",
            "policy": BudgetPolicy(),
            "origin": EndpointSelection(
                kind="ORIGIN_EXACT",
                ingress_binding_head="i",
                endpoint_head="e",
                endpoint_id="local",
                provider="hermetic-local",
                recipient="p",
                canonical_address="local://p",
                credential_binding_head="c",
            ),
            "contour_head": "contour",
            "policy_head": "policy",
            "worker_session": "worker",
            "event": "fixture",
        }
    )
    legal = {
        ("CREATED", "ACTIVE"),
        ("CREATED", "ABORTED"),
        ("CREATED", "CANCELLED"),
        ("ACTIVE", "SUSPENDED"),
        ("ACTIVE", "ABORTED"),
        ("ACTIVE", "CANCELLED"),
        ("SUSPENDED", "ABORTED"),
        ("SUSPENDED", "CANCELLED"),
    }
    if (source, target) in legal:
        changed = domain.transition(run, target)  # type: ignore[arg-type]
        assert changed.predecessor == run.head and changed.state == target
    else:
        with pytest.raises(LoopRejected):
            domain.transition(run, target)  # type: ignore[arg-type]


async def test_prepared_retry_preserves_predecessor_and_fences_old_generation(
    tmp_path: Path,
) -> None:
    # Stop at the public model boundary, then inspect its already durable history.
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=()) as loop:
        created = await loop.create("r", "Plan", BudgetPolicy(max_model_retries=1))
        active = await loop.activate("r", created.head)
        with pytest.raises(LoopRejected, match="exhausted"):
            await loop.step("r", active.head)
        records = loop._store.snapshot().records
        prepared = next(record for record in records if record.event == "ModelAttemptPrepared")
        attempt = prepared.turns[-1].attempts[0]
        proof = NoExposureProof(
            attempt_id=attempt.attempt_id,
            attempt_head=attempt.head,
            run_head=prepared.head,
            worker_session=attempt.worker_session,
        )
        replacement = domain.replace_unemitted(prepared, proof)
        assert replacement.turns[-1].attempts[0] == attempt
        assert replacement.turns[-1].selector == 1
        assert replacement.turns[-1].attempts[1].manifest.generation == 1
        with pytest.raises(LoopRejected):
            domain.replace_unemitted(replacement, proof)
        with pytest.raises(LoopRejected):
            domain.replace_unemitted(loop.record("r"), proof)


async def test_visibility_closure_and_historical_disclosure_mutants_fail(tmp_path: Path) -> None:
    raw = b'{"kind":"Complete","deliveries":[{"kind":"NonAuthoritativeText","text":"Ready"}]}'
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=(raw,)) as loop:
        created = await loop.create("r", "Private context", BudgetPolicy())
        active = await loop.activate("r", created.head)
        await loop.step("r", active.head)
        records = loop._store.snapshot().records
        accumulated = next(
            record for record in records if record.event == "TurnVisibilityAccumulated"
        )
        prepared = next(record for record in records if record.event == "ModelAttemptPrepared")
        manifest = prepared.turns[-1].attempts[0].manifest
        for changed in (
            manifest.model_copy(update={"members": manifest.members[:-1]}),
            manifest.model_copy(
                update={
                    "artifact": manifest.artifact.model_copy(
                        update={"rendered": "uncovered secret"}
                    )
                }
            ),
            manifest.model_copy(
                update={"joined_label": DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=())}
            ),
            manifest.model_copy(update={"tenant": "foreign"}),
            manifest.model_copy(update={"generation": 1}),
        ):
            with pytest.raises(LoopRejected):
                domain.prepare(accumulated, changed)
        captured = next(record for record in records if record.event == "ModelResponseCaptured")
        attempt = captured.turns[-1].attempts[0]
        denied_member = manifest.members[0].model_copy(
            update={"label": DisclosureLabel(value="DENY_ALL", allowed_endpoints=())}
        )
        denied_manifest = manifest.model_copy(
            update={
                "members": (denied_member, *manifest.members[1:]),
                "joined_label": DisclosureLabel(value="DENY_ALL", allowed_endpoints=()),
            }
        )
        denied = captured.model_copy(
            update={
                "turns": (
                    captured.turns[-1].model_copy(
                        update={
                            "attempts": (attempt.model_copy(update={"manifest": denied_manifest}),)
                        }
                    ),
                )
            }
        )
        with pytest.raises(LoopRejected, match="disclosure"):
            domain.complete(denied, Complete.model_validate_json(raw))
        with pytest.raises(LoopRejected):
            domain.emit(captured)
        with pytest.raises(LoopRejected):
            domain.capture(captured, raw, "duplicate")


@pytest.mark.parametrize(
    "state", ["CREATED", "SUCCEEDED", "SUSPENDED", "SUPERSEDED", "ABORTED", "CANCELLED"]
)
async def test_nonactive_run_cannot_start_turn(tmp_path: Path, state: str) -> None:
    async with open_r13_loop(tmp_path / "loop.sqlite", responses=()) as loop:
        await loop.create("r", "Plan", BudgetPolicy())
        with pytest.raises(LoopRejected):
            domain.start_turn(loop.record("r").model_copy(update={"state": state}))
