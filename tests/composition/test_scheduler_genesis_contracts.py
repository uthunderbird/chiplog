"""Public consumer shape only; the recorder below has no publication authority."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Absent
from chiplog.capabilities.agent_loop.scheduler_configuration import ConfigurationSnapshot, genesis
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    SchedulerContextRef,
    SchedulerIntervalBound,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import ConfigurationBoundCommand
from chiplog.composition.r15_scheduler_registry import (
    GENESIS_COMMAND_SCHEMA,
    BoundReplacementDraft,
    BrokerPublicationResult,
    ConfigurationGenesisCommand,
    PolicyAmendmentDraft,
    PublicationRejected,
    ScheduleAmendmentDraft,
    SchedulerConfigurationAdoption,
    SchedulerConfigurationDraft,
    SchedulerGenerationSources,
    SchedulerGenesisAdoption,
    SchedulerGenesisDraft,
    SchedulerGenesisPort,
    command_from_draft,
    configuration_authority_head,
    configuration_command_from_draft,
    configuration_command_id,
    validate_configuration_adoption,
)
from chiplog.platform.broker import BrokerSession


def draft() -> SchedulerGenesisDraft:
    return SchedulerGenesisDraft(
        adoption_act_id="act-1",
        schedule_id="schedule-1",
        start_ns=0,
        period_ns=1_000_000_000,
        end_exclusive_ns="NO_END",
        prompt="Проверить план",
        policy=BudgetPolicy(),
        missed_policy="COALESCE",
        interval_bound=SchedulerIntervalBound(
            max_member_count=16,
            max_manifest_bytes=65536,
            max_serialized_batch_bytes=1_048_576,
        ),
    )


def preview(value: SchedulerGenesisDraft) -> ConfigurationGenesisCommand:
    # Inert broker-shaped output for the consumer contract, not a live issued command.
    return ConfigurationGenesisCommand.model_validate(
        {
            "identity": {
                "command_id": "shape-only-command",
                "schema_version": GENESIS_COMMAND_SCHEMA,
                "canonicalization_version": "chiplog.scheduler.canonical.v1",
            },
            "schedule_id": value.schedule_id,
            "definition": {
                "start_ns": value.start_ns,
                "period_ns": value.period_ns,
                "end_exclusive_ns": value.end_exclusive_ns,
                "run_inputs": {
                    "tenant": "shape-tenant",
                    "principal": "shape-principal",
                    "prompt": value.prompt,
                    "policy": value.policy,
                    "origin": {
                        "kind": "ORIGIN_EXACT",
                        "ingress_binding_head": "shape-ingress",
                        "endpoint_head": "shape-endpoint-head",
                        "endpoint_id": "shape-endpoint",
                        "provider": "hermetic-local",
                        "recipient": "shape-principal",
                        "canonical_address": "local://shape-principal",
                        "credential_binding_head": "shape-credential",
                    },
                    "contour_head": "shape-contour",
                    "policy_head": "shape-policy",
                    "worker_session": "shape-original-worker",
                    "authority_epoch": "shape-original-epoch",
                },
            },
            "policy": value.missed_policy,
            "bound": value.interval_bound,
        }
    )


class RecordingPort:
    def __init__(self) -> None:
        self.adoptions: list[tuple[str, SchedulerGenesisAdoption]] = []

    async def preview_scheduler_genesis(
        self, peer: str, draft: SchedulerGenesisDraft
    ) -> ConfigurationGenesisCommand:
        assert peer == "hermetic-ingress"
        return preview(draft)

    async def adopt_scheduler_genesis(
        self, peer: str, adoption: SchedulerGenesisAdoption
    ) -> BrokerPublicationResult:
        self.adoptions.append((peer, adoption))
        return PublicationRejected(
            kind="HOLD",
            tenant_id="shape-tenant",
            command_id="shape-only-command",
            reason="contract fixture has no runtime authority",
        )


async def consume(port: SchedulerGenesisPort, path: Path) -> BrokerPublicationResult:
    value = draft()
    command = await port.preview_scheduler_genesis("hermetic-ingress", value)
    saved = SchedulerGenesisAdoption(
        adoption_act_id=value.adoption_act_id, command_bytes=command.canonical_bytes()
    )
    await asyncio.to_thread(path.write_bytes, saved.canonical_bytes())
    restored = SchedulerGenesisAdoption.model_validate_json(
        await asyncio.to_thread(path.read_bytes)
    )
    return await port.adopt_scheduler_genesis("hermetic-ingress", restored)


async def test_consumer_can_persist_and_submit_complete_original_command(tmp_path: Path) -> None:
    recorder = RecordingPort()
    result = await consume(recorder, tmp_path / "adoption.json")
    assert result.kind == "HOLD"
    assert recorder.adoptions == [
        (
            "hermetic-ingress",
            SchedulerGenesisAdoption(
                adoption_act_id="act-1", command_bytes=preview(draft()).canonical_bytes()
            ),
        )
    ]
    restored = ConfigurationGenesisCommand.model_validate_json(
        recorder.adoptions[0][1].command_bytes
    )
    assert restored.definition.run_inputs.worker_session == "shape-original-worker"
    assert restored.definition.run_inputs.authority_epoch == "shape-original-epoch"
    assert restored.definition.run_inputs.policy == BudgetPolicy()


@pytest.mark.parametrize("raw", [b"\x00\xff\x80", b'{ "not": "a command" }', b"x\n"])
def test_adoption_transport_preserves_untrusted_bytes_without_claiming_admission(
    raw: bytes,
) -> None:
    adoption = SchedulerGenesisAdoption(adoption_act_id="stable-act", command_bytes=raw)
    assert SchedulerGenesisAdoption.model_validate_json(adoption.canonical_bytes()) == adoption
    assert adoption.command_bytes == raw


@pytest.mark.parametrize("field", ["context", "tenant", "principal", "origin", "worker_session"])
def test_draft_rejects_caller_supplied_broker_owned_fields(field: str) -> None:
    value = draft().model_dump()
    value[field] = "forged"
    with pytest.raises(ValidationError):
        SchedulerGenesisDraft.model_validate(value)


@pytest.mark.parametrize("field", list(SchedulerGenesisDraft.model_fields))
def test_draft_requires_every_configuration_choice(field: str) -> None:
    value = draft().model_dump()
    del value[field]
    with pytest.raises(ValidationError):
        SchedulerGenesisDraft.model_validate(value)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("start_ns", -1),
        ("period_ns", 0),
        ("period_ns", True),
        ("period_ns", "100"),
        ("end_exclusive_ns", "FOREVER"),
        ("missed_policy", "DROP"),
        ("schedule_id", ""),
        ("adoption_act_id", ""),
    ],
)
def test_draft_rejects_unknown_or_coerced_values(field: str, replacement: object) -> None:
    value = draft().model_dump()
    value[field] = replacement
    with pytest.raises(ValidationError):
        SchedulerGenesisDraft.model_validate(value)


@pytest.mark.parametrize("field", list(SchedulerIntervalBound.model_fields))
@pytest.mark.parametrize("replacement", [0, True, "1"])
def test_interval_bound_cannot_lose_its_positive_integer_contract(
    field: str, replacement: object
) -> None:
    value = json.loads(draft().canonical_bytes())
    value["interval_bound"][field] = replacement
    with pytest.raises(ValidationError):
        SchedulerGenesisDraft.model_validate(value)


def test_budget_defaults_are_visible_in_canonical_preview_and_no_policy_is_expressible() -> None:
    value = draft().model_dump()
    value["policy"] = {"kind": "NO_POLICY"}
    parsed = SchedulerGenesisDraft.model_validate(value)
    command = preview(parsed)
    assert command.definition.run_inputs.policy == BudgetPolicy(kind="NO_POLICY")
    expanded = json.loads(command.canonical_bytes())["definition"]["run_inputs"]["policy"]
    assert command.definition.run_inputs.policy.live_model is None
    assert set(expanded) == set(BudgetPolicy.model_fields) - {"live_model"}
    assert expanded["max_tool_calls"] == BudgetPolicy().max_tool_calls


def test_draft_and_adoption_are_frozen_and_empty_bytes_reject() -> None:
    value = draft()
    with pytest.raises(ValidationError, match="frozen"):
        value.schedule_id = "changed"
    adoption = SchedulerGenesisAdoption(adoption_act_id="act", command_bytes=b"untrusted")
    with pytest.raises(ValidationError, match="frozen"):
        adoption.command_bytes = b"changed"
    with pytest.raises(ValidationError):
        SchedulerGenesisAdoption(adoption_act_id="act", command_bytes=b"")


def configuration_fixture() -> tuple[ConfigurationSnapshot, SchedulerGenerationSources]:
    # Constructed descriptions deliberately carry no authenticated issuance authority.
    sources = SchedulerGenerationSources(
        worker_session="worker",
        authority_epoch="epoch",
        broker_generation="broker",
        runtime_graph_generation="graph",
        agent_session=BrokerSession(
            tenant_id="hermetic-tenant",
            broker_epoch=1,
            generation_id="generation",
            owner_id="agent_loop",
            session_id="session",
        ),
        evidence_bytes=b'{"trust_reference":{"trust_head":"shape-trust"}}',
    )
    command = command_from_draft(draft(), sources)
    snapshot = genesis(
        ConfigurationSnapshot(schedule=None, policy=None, bound=None, active_hold=Absent()),
        schedule_id=command.schedule_id,
        definition=command.definition,
        policy=command.policy,
        bound=command.bound,
        context=SchedulerContextRef(
            tenant_id="hermetic-tenant",
            service_identity="shape-service",
            session_id="shape-session",
            mandate_head="shape-mandate",
            issuance_id="shape-issue",
            issuance_fingerprint="a" * 64,
        ),
        command_id=command.identity.command_id,
        authority_epoch=sources.authority_epoch,
        broker_generation=sources.broker_generation,
        runtime_graph_generation=sources.runtime_graph_generation,
    ).proposed
    return snapshot, sources


def configuration_drafts() -> tuple[
    ScheduleAmendmentDraft, PolicyAmendmentDraft, BoundReplacementDraft
]:
    value = draft()
    return (
        ScheduleAmendmentDraft(
            adoption_act_id="change-1",
            schedule_id=value.schedule_id,
            start_ns=1,
            period_ns=2,
            end_exclusive_ns="NO_END",
            prompt="Changed",
            policy=value.policy,
        ),
        PolicyAmendmentDraft(
            adoption_act_id="change-1",
            schedule_id=value.schedule_id,
            missed_policy="SKIP",
        ),
        BoundReplacementDraft(
            adoption_act_id="change-1",
            schedule_id=value.schedule_id,
            interval_bound=value.interval_bound,
        ),
    )


@pytest.mark.parametrize("value", configuration_drafts())
def test_configuration_exact_bytes_roundtrip_and_act_identity(
    value: SchedulerConfigurationDraft,
) -> None:
    snapshot, sources = configuration_fixture()
    command = configuration_command_from_draft(value, snapshot, sources)
    adoption = SchedulerConfigurationAdoption(
        adoption_act_id=value.adoption_act_id,
        command_bytes=command.canonical_bytes(),
    )
    restored = SchedulerConfigurationAdoption.model_validate_json(adoption.canonical_bytes())
    assert validate_configuration_adoption(restored, snapshot, sources) == command
    identity = (
        command.command.identity
        if isinstance(command, ConfigurationBoundCommand)
        else (command.identity)
    )
    assert identity.command_id == configuration_command_id("change-1")
    for altered in (
        adoption.model_copy(update={"command_bytes": b" " + adoption.command_bytes}),
        adoption.model_copy(update={"adoption_act_id": "other-act"}),
    ):
        with pytest.raises(LoopRejected):
            validate_configuration_adoption(altered, snapshot, sources)


@pytest.mark.parametrize("value", configuration_drafts())
def test_configuration_drafts_reject_injected_authority_and_unknown_kinds(
    value: SchedulerConfigurationDraft,
) -> None:
    adapter: TypeAdapter[SchedulerConfigurationDraft] = TypeAdapter(SchedulerConfigurationDraft)
    assert adapter.validate_json(value.canonical_bytes()) == value
    for field in ("worker_session", "observed_schedule", "scheduler_authority_head", "tenant"):
        with pytest.raises(ValidationError):
            adapter.validate_python(value.model_dump() | {field: "forged"})
    with pytest.raises(ValidationError):
        adapter.validate_python(value.model_dump() | {"kind": "GENESIS"})
    snapshot, sources = configuration_fixture()
    with pytest.raises(LoopRejected, match="different schedule"):
        configuration_command_from_draft(
            value.model_copy(update={"schedule_id": "different"}),
            snapshot,
            sources,
        )


def test_bound_adoption_binds_independent_authority_and_actual_generations() -> None:
    snapshot, sources = configuration_fixture()
    value = configuration_drafts()[2]
    command = configuration_command_from_draft(value, snapshot, sources)
    assert isinstance(command, ConfigurationBoundCommand)
    assert command.command.scheduler_authority_head == configuration_authority_head(sources)
    adoption = SchedulerConfigurationAdoption(
        adoption_act_id=value.adoption_act_id,
        command_bytes=command.canonical_bytes(),
    )
    for changed in (
        replace(sources, worker_session="new"),
        replace(sources, authority_epoch="new"),
        replace(sources, broker_generation="new"),
        replace(sources, runtime_graph_generation="new"),
    ):
        with pytest.raises(LoopRejected):
            validate_configuration_adoption(adoption, snapshot, changed)
    with pytest.raises(LoopRejected):
        validate_configuration_adoption(
            adoption,
            snapshot,
            replace(sources, evidence_bytes=b'{"trust_reference":{}}'),
        )
    changed_frame = replace(
        sources,
        evidence_bytes=(
            b'{"trust_reference":{"trust_head":"shape-trust"},"authentication":"fresh call"}'
        ),
    )
    assert validate_configuration_adoption(adoption, snapshot, changed_frame) == command


@pytest.mark.parametrize("value", configuration_drafts()[:2])
def test_amendment_adoption_rejects_changed_observed_schedule_or_policy(
    value: SchedulerConfigurationDraft,
) -> None:
    snapshot, sources = configuration_fixture()
    command = configuration_command_from_draft(value, snapshot, sources)
    adoption = SchedulerConfigurationAdoption(
        adoption_act_id=value.adoption_act_id,
        command_bytes=command.canonical_bytes(),
    )
    assert snapshot.schedule is not None and snapshot.policy is not None
    for field, revision in (("schedule", snapshot.schedule), ("policy", snapshot.policy)):
        changed = snapshot.model_copy(update={field: revision.model_copy(update={"revision": 1})})
        with pytest.raises(LoopRejected):
            validate_configuration_adoption(adoption, changed, sources)


def test_configuration_adoption_transport_preserves_arbitrary_untrusted_bytes() -> None:
    value = SchedulerConfigurationAdoption(adoption_act_id="act", command_bytes=b"\x00\xff")
    assert SchedulerConfigurationAdoption.model_validate_json(value.canonical_bytes()) == value
    with pytest.raises(ValidationError, match="frozen"):
        value.command_bytes = b"replacement"
    with pytest.raises(ValidationError):
        SchedulerConfigurationAdoption(adoption_act_id="act", command_bytes=b"")
