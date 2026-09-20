"""Transcript boundary driver; all Run/Turn decisions execute the production service."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import cast

from chiplog.architecture.r7_runtime import (
    R13_EVALUATION_MANIFEST,
    R13_PRODUCTION_MANIFEST,
    verify_production_evaluation_equivalence,
)
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.composition.r13 import open_r13_loop
from chiplog.verification.identity import canonical_json_bytes, digest_file, sha256_bytes
from chiplog.verification.primitives import FaultPoint, StateObservation
from chiplog.verification.transcripts import compile_transcript


async def run_scenario(source: Path, database: Path, artifact: Path) -> dict[str, object]:
    compiled = compile_transcript(source)
    scenario = cast(dict[str, object], compiled.compiled["scenario"])
    arrange = cast(dict[str, str], scenario["arrange"])
    fixtures = cast(list[dict[str, str]], scenario["fixtures"])
    if compiled.compiled["schema_version"] == 2:
        _validate_local_steps(compiled.compiled)
    if scenario["allowed_outcomes"] != [
        "local committed receipt with provider HOLD"
    ] or compiled.compiled["forbid"] != [
        [
            "planning mutation before authenticated adoption",
            "provider or real recipient exposure",
            "provider success receipt",
        ]
    ]:
        raise LoopRejected("unregistered verdict policy; scenario HOLD")
    if (
        compiled.scenario_id != "local-planning-receipt"
        or scenario["version"] != (2 if compiled.compiled["schema_version"] == 2 else 1)
        or arrange
        != {
            "principal": "hermetic-principal",
            "channel": "hermetic-local",
            "proposal": "Help me plan weekly swimming",
            "adoption": "exact displayed planning proposal",
        }
        or any(fixture["boundary"] != "model" for fixture in fixtures)
        or len(fixtures) != 3
    ):
        raise LoopRejected("unregistered transcript boundary contract; scenario HOLD")
    if await asyncio.to_thread(database.exists) or await asyncio.to_thread(artifact.exists):
        raise LoopRejected("retained scenario database/artifact cannot be overwritten")
    verify_production_evaluation_equivalence(R13_PRODUCTION_MANIFEST, R13_EVALUATION_MANIFEST)
    observed = FaultPoint("proposal-before-authority")
    trace: list[str] = []
    async with open_r13_loop(
        database,
        responses=tuple(f["result"].encode() for f in fixtures),
        matches=tuple(f["match"] for f in fixtures),
    ) as loop:
        created = await loop.create(compiled.scenario_id, arrange["proposal"], BudgetPolicy())
        current = await loop.activate(created.run_id, created.head)
        before = loop.record(created.run_id).planning_receipts
        current = await loop.step(created.run_id, current.head)
        first = loop.record(created.run_id)
        if (
            first.state != "ACTIVE"
            or first.planning_receipts
            or first.turns[0].sealed_calls is None
        ):
            raise LoopRejected("proposal-before-authority invariant failed")
        proposals = [
            outcome.proposal_id
            for outcome in first.turns[0].sealed_calls
            if outcome.call.tool == "propose_planning"
        ]
        if len(proposals) != 1 or proposals[0] is None:
            raise LoopRejected("actual loop did not produce one planning proposal")
        observed.hit()
        trace.append("proposal before authority")
        display = await loop.display(proposals[0])
        if compiled.compiled["schema_version"] == 2:
            displayed = loop.record(created.run_id)
            request_state = {
                "planning_changed": StateObservation(before, displayed.planning_receipts).changed,
                "external_effects": sum(
                    delivery.state != "PENDING_LOCAL" for delivery in displayed.deliveries
                ),
                "run": displayed.state,
            }
            expectations = cast(list[dict[str, object]], compiled.compiled["expect"])
            if _eq_state(expectations[0]) != request_state:
                raise LoopRejected("observed request state does not satisfy authored contract")
        receipt = await loop.adopt(
            "hermetic-ingress", display.display_id, display.display_digest, display.adoption_act_id
        )
        trace.append("exact adoption before local receipt")
        changed = StateObservation(before, loop.record(created.run_id).planning_receipts)
        current = await loop.step(created.run_id, loop.status(created.run_id).head)
        second = loop.record(created.run_id)
        if second.turns[-1].sealed_calls is None or any(
            outcome.call.tool != "propose_intent" or outcome.evidence_id is not None
            for outcome in second.turns[-1].sealed_calls
        ):
            raise LoopRejected("intent fixture did not remain proposal-only")
        trace.append("intent is proposal only")
        current = await loop.step(created.run_id, current.head)
        final = loop.record(created.run_id)
        observed.require_reached()
        expectations = cast(list[dict[str, object]], compiled.compiled["expect"])
        if len(expectations) != (2 if compiled.compiled["schema_version"] == 2 else 1):
            raise LoopRejected("unsupported expectation policy")
        expected = expectations[-1]
        if compiled.compiled["schema_version"] == 2:
            expected = {**expected, "state": _eq_state(expected)}
        response = cast(dict[str, str], expected["response"])
        dispatched = tuple(
            delivery for delivery in final.deliveries if delivery.state != "PENDING_LOCAL"
        )
        state = {
            "planning_changed": changed.changed,
            "external_effects": len(dispatched),
            "run": final.state,
        }
        if (
            expected["trace"] != trace
            or expected["state"] != state
            or final.accepted_text != (response["exact"],)
            or final.state != "SUCCEEDED"
        ):
            raise LoopRejected("observed scenario does not satisfy authored contract")
        result: dict[str, object] = {
            "schema_version": 1,
            "status": "PASS",
            "deployment": "HOLD",
            "provider_execution": "HOLD",
            "scenario_id": compiled.scenario_id,
            "source_digest": compiled.source_digest,
            "compiled_bundle_digest": compiled.compiled_bundle_digest,
            "production_manifest": R13_PRODUCTION_MANIFEST.fingerprint(),
            "evaluation_manifest": R13_EVALUATION_MANIFEST.fingerprint(),
            "loop_id": R13_PRODUCTION_MANIFEST.application_loop_id,
            "implementation_catalog": digest_file(
                Path(__file__).parents[1] / "inert_shared/r8-implementation-v1.json"
            ),
            "fault_observer": {"name": observed.name, "reached": observed.reached},
            "trace": trace,
            "state": state,
            "observed_delivery_states": [delivery.state for delivery in final.deliveries],
            "display": display.model_dump(mode="json"),
            "receipt": receipt.model_dump(mode="json"),
            "records": [
                record.model_dump(mode="json") for record in loop._store.snapshot().records
            ],
        }
    payload = canonical_json_bytes(result)
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with artifact.open("xb") as stream:
        stream.write(payload)
    if sha256_bytes(await asyncio.to_thread(artifact.read_bytes)) != sha256_bytes(payload):
        raise LoopRejected("result artifact readback mismatch")
    return result


def _validate_local_steps(compiled: dict[str, object]) -> None:
    scenario = cast(dict[str, object], compiled["scenario"])
    if scenario.get("status") != "executable" or compiled["steps"] != [
        {
            "id": "request",
            "input": {"kind": "message", "message_ref": "request"},
            "end": "proposal.displayed",
        },
        {
            "id": "accept",
            "input": {
                "kind": "authenticated_adoption",
                "message_ref": "accept",
                "peer": "hermetic-ingress",
            },
            "end": "run.succeeded",
        },
    ]:
        raise LoopRejected("unsupported ordered step contract; scenario HOLD")
    messages = cast(list[dict[str, str]], compiled["messages"])
    inputs = {message["id"]: message for message in messages}
    for identity, content in (
        ("request", "Help me plan weekly swimming"),
        ("accept", "Confirm this entry."),
    ):
        if inputs.get(identity) != {
            "id": identity,
            "role": "Пользователь",
            "mode": "input",
            "text": content,
        }:
            raise LoopRejected("unsupported canonical input; scenario HOLD")
    expectations = cast(list[dict[str, object]], compiled["expect"])
    if (
        len(expectations) != 2
        or set(expectations[0]) != {"step", "state"}
        or expectations[0]["step"] != "request"
        or set(expectations[1]) != {"step", "response", "trace", "state"}
        or expectations[1]["step"] != "accept"
    ):
        raise LoopRejected("unsupported step expectation; scenario HOLD")
    for expectation in expectations:
        if set(_eq_state(expectation)) != {"planning_changed", "external_effects", "run"}:
            raise LoopRejected("unsupported step state fields; scenario HOLD")
    response = expectations[1]["response"]
    if not isinstance(response, dict) or set(response) != {"exact"}:
        raise LoopRejected("unsupported response expectation; scenario HOLD")
    if list(inputs) != ["request", "accept", "receipt"] or inputs["receipt"] != {
        "id": "receipt",
        "role": "Chiplog",
        "mode": "exact",
        "text": response["exact"],
    }:
        raise LoopRejected("unsupported canonical message binding; scenario HOLD")


def _eq_state(expectation: dict[str, object]) -> dict[str, object]:
    predicates = cast(dict[str, dict[str, object]], expectation["state"])
    if any(set(predicate) != {"eq"} for predicate in predicates.values()):
        raise LoopRejected("unsupported state operator; scenario HOLD")
    state = {key: predicate["eq"] for key, predicate in predicates.items()}
    expected_types = {"planning_changed": bool, "external_effects": int, "run": str}
    if any(key in state and type(state[key]) is not kind for key, kind in expected_types.items()):
        raise LoopRejected("unsupported state predicate type; scenario HOLD")
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run_scenario(args.source, args.database, args.artifact))
    print(json.dumps({key: result[key] for key in ("status", "deployment", "scenario_id")}))


if __name__ == "__main__":
    main()
