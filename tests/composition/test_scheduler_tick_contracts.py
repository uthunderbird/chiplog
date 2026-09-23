"""Public consumer transport only; no fixture claims runtime authority."""

import asyncio
import json
from pathlib import Path
from typing import NoReturn

import pytest
from pydantic import ValidationError

from chiplog.composition.r15_tick_contracts import (
    SchedulerTickPort,
    TickClockObservation,
    TickClockPort,
    TickClockReading,
    TickClockSource,
    TickPolicyAdoption,
    TickPolicyDraft,
    TickPolicyPreview,
)


def source() -> TickClockSource:
    return TickClockSource(
        source_id="shape-clock",
        contract_version="chiplog.scheduler.coordinate-clock.v1",
        coordinate_codec="chiplog.scheduler.unix-ns.v1",
        deployment_profile="shape-only",
        implementation_fingerprint="a" * 64,
    )


def preview(draft: TickPolicyDraft) -> TickPolicyPreview:
    return TickPolicyPreview.model_validate(
        {
            "schema_id": "chiplog.scheduler.tick-policy-adoption.v1",
            "adoption_act_id": draft.adoption_act_id,
            "delivery_id": draft.delivery_id,
            "schedule": {
                "schedule_id": draft.schedule_id,
                "schedule_revision": "revision",
                "head": "schedule-head",
                "fingerprint": "b" * 64,
            },
            "missed_policy": {
                "policy_id": "policy",
                "policy_revision": "revision",
                "head": "policy-head",
                "fingerprint": "c" * 64,
                "kind": "COALESCE",
            },
            "bound": {
                "head_id": "bound",
                "predecessor": {"kind": "ABSENT"},
                "generation": 0,
                "bound": {
                    "max_member_count": 2,
                    "max_manifest_bytes": 4096,
                    "max_serialized_batch_bytes": 65536,
                },
                "owner_id": "agent_loop",
                "authority_epoch": "epoch",
                "broker_generation": "broker",
                "runtime_graph_generation": "graph",
                "canonicalization_version": "chiplog.scheduler.canonical.v1",
            },
            "policy": {
                "schema_id": "chiplog.scheduler.hermetic-tick-policy.v1",
                "operation": "scheduler.decide_interval",
                "scope": "ONE_DELIVERY_PRE_ROOT_MATERIALIZATION_NO_EXECUTION_NO_SEND",
                "tenant_id": "tenant",
                "principal_id": "principal",
                "service_identity": "scheduler",
                "registered_ingress": "hermetic-ingress",
                "clock_source": source().model_dump(),
            },
        }
    )


class RecordingPort:
    def __init__(self) -> None:
        self.received: list[TickPolicyAdoption] = []

    async def preview_scheduler_tick_policy(
        self, peer: str, draft: TickPolicyDraft
    ) -> TickPolicyPreview:
        assert peer == "hermetic-ingress"
        return preview(draft)

    async def adopt_scheduler_tick(self, peer: str, adoption: TickPolicyAdoption) -> NoReturn:
        assert peer == "hermetic-ingress"
        self.received.append(adoption)
        # Recorder proves argument transport only, not a runtime result.
        raise RuntimeError("shape-only consumer has no publication authority")


async def consume(port: SchedulerTickPort, path: Path) -> None:
    draft = TickPolicyDraft(adoption_act_id="act", schedule_id="schedule", delivery_id="delivery")
    shown = await port.preview_scheduler_tick_policy("hermetic-ingress", draft)
    adoption = TickPolicyAdoption(
        adoption_act_id=draft.adoption_act_id, policy_bytes=shown.canonical_bytes()
    )
    await asyncio.to_thread(path.write_bytes, adoption.canonical_bytes())
    restored = TickPolicyAdoption.model_validate_json(await asyncio.to_thread(path.read_bytes))
    await port.adopt_scheduler_tick("hermetic-ingress", restored)


def test_public_consumer_retains_exact_policy_across_restart(tmp_path: Path) -> None:
    recorder = RecordingPort()
    with pytest.raises(RuntimeError, match="no publication authority"):
        asyncio.run(consume(recorder, tmp_path / "adoption.json"))
    restored = TickPolicyPreview.model_validate_json(recorder.received[0].policy_bytes)
    assert restored.delivery_id == "delivery"
    assert restored.policy.clock_source == source()
    assert restored.canonical_bytes() == recorder.received[0].policy_bytes
    assert "clock" not in TickPolicyPreview.model_fields


@pytest.mark.parametrize("raw", [b"\xff\x00arbitrary", b"{}"])
def test_adoption_preserves_untrusted_bytes_for_runtime_validation(raw: bytes) -> None:
    value = TickPolicyAdoption(adoption_act_id="act", policy_bytes=raw)
    assert TickPolicyAdoption.model_validate_json(value.canonical_bytes()) == value


@pytest.mark.parametrize("field", ["cutoff", "authority", "clock", "prepared"])
def test_public_draft_rejects_authority_and_time_injection(field: str) -> None:
    with pytest.raises(ValidationError):
        TickPolicyDraft.model_validate(
            {
                "adoption_act_id": "act",
                "schedule_id": "schedule",
                "delivery_id": "delivery",
                field: "asserted-by-caller",
            }
        )


def test_clock_observation_preserves_original_source_and_broker_binding() -> None:
    class Clock:
        source = source()

        def observe(self) -> TickClockReading:
            return TickClockReading(unix_ns=123, monotonic_ns=7)

    clock: TickClockPort = Clock()
    observation = TickClockObservation(
        source=clock.source,
        observation_id="observation",
        broker_epoch="epoch",
        broker_session="session",
        reading=clock.observe(),
        valid_until_monotonic_ns=8,
    )
    assert TickClockObservation.model_validate_json(observation.canonical_bytes()) == observation
    encoded = json.loads(observation.canonical_bytes())
    encoded["reading"]["unix_ns"] = -1
    with pytest.raises(ValidationError):
        TickClockObservation.model_validate(encoded)
