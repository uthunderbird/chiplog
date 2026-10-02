"""Exact offline configuration adoption boundary; these values grant no authority.

The broker fills a preview from actual authenticated runtime sources. A consumer
persists the complete preview bytes and explicitly adopts those same bytes. A
selected historical retry must not replace them with current worker generations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Annotated, Final, Literal, Protocol

from pydantic import ConfigDict, Field, TypeAdapter

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection, LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Identity, RecoveryDTO
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    FixedIntervalDefinition,
    policy_head,
    schedule_head,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    ReplaceIntervalBoundCommand,
    SchedulerCommandIdentity,
    SchedulerIntervalBound,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerRunInputs
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    ConfigurationBoundCommand,
    ConfigurationGenesisCommand,
    ConfigurationPolicyCommand,
    ConfigurationScheduleCommand,
)
from chiplog.platform._owner_publication_contracts import (
    BrokerPublicationResult,
    JournalSelectedPublication,
    PublicationRejected,
)
from chiplog.platform.broker import (
    BrokerSession,
    PublicPortCall,
    PublicPortResult,
    PublicPortSuccess,
)
from chiplog.platform.r7_trust_durability import FrozenTrustObservation

if TYPE_CHECKING:
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r14_runtime import R14PlanningRuntime

GENESIS_OPERATION: Final = "scheduler.genesis"
GENESIS_COMMAND_SCHEMA: Final = "chiplog.scheduler.configuration-genesis.v1"
GENESIS_POLICY_SCHEMA: Final = "chiplog.scheduler.hermetic-genesis-policy.v1"
GENESIS_SERVICE_IDENTITY: Final = "r15-hermetic-scheduler-configuration-v1"
AMEND_SCHEDULE_OPERATION: Final = "scheduler.amend_schedule"
AMEND_POLICY_OPERATION: Final = "scheduler.amend_policy"
REPLACE_BOUND_OPERATION: Final = "scheduler.replace_bound"
AMEND_SCHEDULE_COMMAND_SCHEMA: Final = "chiplog.scheduler.configuration-schedule.v1"
AMEND_POLICY_COMMAND_SCHEMA: Final = "chiplog.scheduler.configuration-policy.v1"
REPLACE_BOUND_COMMAND_SCHEMA: Final = "chiplog.scheduler.configuration-bound.v1"


class ScheduleAmendmentDraft(RecoveryDTO):
    kind: Literal["AMEND_SCHEDULE"] = "AMEND_SCHEDULE"
    adoption_act_id: Identity
    schedule_id: Identity
    start_ns: int = Field(ge=0)
    period_ns: int = Field(gt=0)
    end_exclusive_ns: int | Literal["NO_END"]
    prompt: Identity
    policy: BudgetPolicy


class PolicyAmendmentDraft(RecoveryDTO):
    kind: Literal["AMEND_POLICY"] = "AMEND_POLICY"
    adoption_act_id: Identity
    schedule_id: Identity
    missed_policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"]


class BoundReplacementDraft(RecoveryDTO):
    kind: Literal["REPLACE_BOUND"] = "REPLACE_BOUND"
    adoption_act_id: Identity
    schedule_id: Identity
    interval_bound: SchedulerIntervalBound


SchedulerConfigurationDraft = Annotated[
    ScheduleAmendmentDraft | PolicyAmendmentDraft | BoundReplacementDraft,
    Field(discriminator="kind"),
]
ConfigurationTransitionCommand = Annotated[
    ConfigurationScheduleCommand | ConfigurationPolicyCommand | ConfigurationBoundCommand,
    Field(discriminator="kind"),
]


class SchedulerConfigurationAdoption(RecoveryDTO):
    """Untrusted exact bytes; construction confers no publication authority."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )
    adoption_act_id: Identity
    command_bytes: bytes = Field(min_length=1)


class SchedulerConfigurationPort(Protocol):
    async def preview_scheduler_configuration(
        self, peer: str, draft: SchedulerConfigurationDraft
    ) -> ConfigurationTransitionCommand: ...

    async def adopt_scheduler_configuration(
        self, peer: str, adoption: SchedulerConfigurationAdoption
    ) -> BrokerPublicationResult: ...


class SchedulerGenesisDraft(RecoveryDTO):
    """User-chosen configuration; actor, origin and runtime bindings are broker-owned.

    BudgetPolicy defaults are expanded in the preview. The preview is descriptive;
    neither this draft nor successful preview creation publishes configuration.
    """

    adoption_act_id: Identity
    schedule_id: Identity
    start_ns: int = Field(ge=0)
    period_ns: int = Field(gt=0)
    end_exclusive_ns: int | Literal["NO_END"]
    prompt: Identity
    policy: BudgetPolicy
    missed_policy: Literal["SKIP", "COALESCE", "MATERIALIZE_EACH"]
    interval_bound: SchedulerIntervalBound


class SchedulerGenesisAdoption(RecoveryDTO):
    """Untrusted exact-byte submission, including after restart.

    Construction checks only transport shape. Runtime admission must reject
    malformed/noncanonical commands and commands outside the registered scope.
    The act ID remains stable even when submitted bytes or schedule ID change.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        ser_json_bytes="base64",
        val_json_bytes="base64",
    )

    adoption_act_id: Identity
    command_bytes: bytes = Field(min_length=1)


class SchedulerGenesisPort(Protocol):
    """Consumer-facing configuration port, not a raw writer or issuer.

    Unsupported ingress/configuration raises LoopRejected before publication;
    durable integrity failures retain their typed cause. A publication rejection
    is a BrokerPublicationResult and never denotes successful materialization.
    """

    async def preview_scheduler_genesis(
        self, peer: str, draft: SchedulerGenesisDraft
    ) -> ConfigurationGenesisCommand: ...

    async def adopt_scheduler_genesis(
        self, peer: str, adoption: SchedulerGenesisAdoption
    ) -> BrokerPublicationResult: ...


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reference(namespace: str, value: object) -> str:
    return namespace + ":" + _digest(_canonical({"namespace": namespace, "value": value}))


def _origin() -> EndpointSelection:
    return EndpointSelection(
        kind="ORIGIN_EXACT",
        ingress_binding_head="hermetic-ingress-v1",
        endpoint_head="hermetic-endpoint-v1",
        endpoint_id="hermetic-local",
        provider="hermetic-local",
        recipient="hermetic-principal",
        canonical_address="local://hermetic-principal",
        credential_binding_head="hermetic-v1",
    )


def _policy_bytes() -> bytes:
    return _canonical(
        {
            "schema": GENESIS_POLICY_SCHEMA,
            "operation": GENESIS_OPERATION,
            "command_schema": GENESIS_COMMAND_SCHEMA,
            "canonicalization": "chiplog.scheduler.canonical.v1",
            "coordinates": "chiplog.scheduler.unix-ns.v1",
            "scope": "HERMETIC_CONFIGURATION_ONLY",
            "service_identity": GENESIS_SERVICE_IDENTITY,
            "tenant": "hermetic-tenant",
            "principal": "hermetic-principal",
            "peer": "hermetic-ingress",
            "contour": "CLI",
            "credential_id": "hermetic-credential",
            "session_id": "hermetic-session",
            "source_head": "local",
            "origin": _origin().model_dump(mode="json"),
            "contour_head": "hermetic-contour-v1",
            "policy_head": "hermetic-policy-v1",
            "budget_kinds": ["MAX_TURNS", "NO_POLICY"],
            "missed_policies": ["SKIP", "COALESCE", "MATERIALIZE_EACH"],
            "initial_scheduler_history": "EMPTY",
        }
    )


def _command_id(adoption_act_id: str) -> str:
    if not isinstance(adoption_act_id, str) or not adoption_act_id:
        raise LoopRejected("scheduler adoption act must be a nonempty identity")
    return _reference(
        "r15-genesis-adoption-v1",
        ("hermetic-tenant", "hermetic-principal", GENESIS_OPERATION, adoption_act_id),
    )


def _frame(value: PublicPortCall | PublicPortResult) -> dict[str, object]:
    result: dict[str, object] = value.model_dump(exclude={"canonical_payload"})
    if isinstance(value, PublicPortCall | PublicPortSuccess):
        result["canonical_payload_base64"] = base64.b64encode(value.canonical_payload).decode()
    return result


def _trust_observation(value: FrozenTrustObservation) -> dict[str, object]:
    return {
        "snapshot_base64": base64.b64encode(value.snapshot_bytes).decode(),
        "journal_head": value.journal_head,
        "bundle_path": value.bundle_path,
        "sources": value.sources,
    }


@dataclass(frozen=True)
class SchedulerGenerationSources:
    """Verified source description, not an issuance handle or publication right."""

    worker_session: str
    authority_epoch: str
    broker_generation: str
    runtime_graph_generation: str
    agent_session: BrokerSession
    evidence_bytes: bytes


def configuration_policy_bytes() -> bytes:
    policy = json.loads(_policy_bytes())
    policy.update(
        schema="chiplog.scheduler.hermetic-configuration-policy.v1",
        operations={
            AMEND_SCHEDULE_OPERATION: AMEND_SCHEDULE_COMMAND_SCHEMA,
            AMEND_POLICY_OPERATION: AMEND_POLICY_COMMAND_SCHEMA,
            REPLACE_BOUND_OPERATION: REPLACE_BOUND_COMMAND_SCHEMA,
        },
        initial_scheduler_history="ONE_REGISTERED_SCHEDULE",
    )
    del policy["operation"], policy["command_schema"]
    return _canonical(policy)


def configuration_authority_preimage(sources: SchedulerGenerationSources) -> bytes:
    """Describe registered configuration authority without a command dependency.

    This is not an issuance operation: constructed sources authenticate nothing.
    The private publisher must capture and revalidate its own actual observation.
    Per-invocation frames and adoption bytes belong to the separate mandate.
    """
    evidence = json.loads(sources.evidence_bytes)
    return _canonical(
        {
            "policy": json.loads(configuration_policy_bytes()),
            "authenticated_reference": evidence["trust_reference"],
            "authority_epoch": sources.authority_epoch,
            "broker_generation": sources.broker_generation,
            "runtime_graph_generation": sources.runtime_graph_generation,
            "worker_session": sources.worker_session,
        }
    )


def configuration_authority_head(sources: SchedulerGenerationSources) -> str:
    """Stable description across unchanged AUTHENTICATE calls; never a proof."""
    return _reference(
        "r15-configuration-authority-v1", json.loads(configuration_authority_preimage(sources))
    )


def capture_generation_sources(
    runtime: R14PlanningRuntime, observed: ObservedTrustCall
) -> SchedulerGenerationSources:
    """Recheck one actual invocation's sources; the private issuer owns provenance.

    A caller constructing an equal observation or this result gains no authority.
    Only the publication path retaining its own actual invocation can issue it.
    """
    with runtime._authority_gate().hold():
        runtime._check_database_identity()
        if runtime._tenant_id != "hermetic-tenant":
            raise LoopRejected("scheduler runtime tenant is outside registered policy")
        if runtime._trust_observation_guard(observed) is not None:
            raise LoopRejected("scheduler authentication is denied, stale or indeterminate")
        raw_reference = observed.result.reference_bytes
        if raw_reference is None:
            raise LoopRejected("scheduler authenticated reference is absent")
        reference = json.loads(raw_reference)
        if not isinstance(reference, dict) or set(reference) != {
            "contour",
            "credential_head",
            "freshness_sequence",
            "materialization_head",
            "peer_credential",
            "principal_id",
            "session_head",
            "source_head",
            "tenant_id",
            "trust_head",
        }:
            raise LoopRejected("scheduler trust reference schema is not registered")
        if (
            reference["tenant_id"] != "hermetic-tenant"
            or reference["principal_id"] != "hermetic-principal"
            or reference["contour"] != "CLI"
            or reference["source_head"] != "local"
            or reference["peer_credential"] != f"uid:{os.getuid()}"
        ):
            raise LoopRejected("scheduler authenticated subject is outside registered policy")
        trust = runtime._trust.verify()
        if (
            trust is None
            or trust.phase != "ACTIVE"
            or trust.tenant_id != reference["tenant_id"]
            or trust.trust_head != reference["trust_head"]
            or trust.materialization_head != reference["materialization_head"]
        ):
            raise LoopRejected("scheduler reference differs from verified trust state")
        broker = runtime._supervisor.runtime()
        agent, trust_session = broker.session("agent_loop"), broker.session("deployment_trust")
        ledger = runtime._read_ledger.current_state(runtime._tenant_id)
        graph = runtime._supervisor.admitted_graph
        admission = runtime._supervisor.admission_evidence
        if (
            graph is None
            or admission is None
            or not admission.accepted
            or admission.observation != observed.observation
            or admission.request is None
            or admission.response is None
            or admission.request.callee != trust_session
            or admission.response.responder != trust_session
            or admission.response.request_id != admission.request.request_id
            or (
                admission.tenant_id,
                admission.database_instance_id,
                admission.genesis_head,
                admission.trust_head,
                admission.materialization_head,
            )
            != (
                trust.tenant_id,
                trust.database_instance_id,
                trust.genesis_head,
                trust.trust_head,
                trust.materialization_head,
            )
            or ledger.owner_draining
            or ledger.tenant_id != runtime._tenant_id
            or (ledger.broker_epoch, ledger.owner_generation)
            != (agent.broker_epoch, agent.generation_id)
            or (graph.tenant_id, graph.broker_epoch, graph.generation_id)
            != (agent.tenant_id, agent.broker_epoch, agent.generation_id)
            or (trust_session.tenant_id, trust_session.broker_epoch, trust_session.generation_id)
            != (agent.tenant_id, agent.broker_epoch, agent.generation_id)
            or observed.request.callee != trust_session
        ):
            raise LoopRejected("scheduler runtime admission or generation sources differ")
        for session in (agent, trust_session):
            owners = tuple(owner for owner in graph.owners if owner.owner_id == session.owner_id)
            if len(owners) != 1 or (owners[0].generation_id, owners[0].session_id) != (
                session.generation_id,
                session.session_id,
            ):
                raise LoopRejected("scheduler owner session differs from admitted graph")
        epoch_preimage = {
            "tenant_id": trust.tenant_id,
            "database_instance_id": trust.database_instance_id,
            "genesis_head": trust.genesis_head,
            "trust_head": trust.trust_head,
        }
        broker_preimage = {
            "agent_session": agent.model_dump(),
            "read_generation": {
                "tenant_id": ledger.tenant_id,
                "broker_epoch": ledger.broker_epoch,
                "owner_generation": ledger.owner_generation,
            },
        }
        graph_preimage = asdict(graph)
        evidence = {
            "policy": json.loads(_policy_bytes()),
            "trust_reference": reference,
            "verified_trust": asdict(trust),
            "authentication": {
                "request": _frame(observed.request),
                "response": _frame(observed.response),
                "result_base64": base64.b64encode(observed.result.canonical_bytes()).decode(),
                "observation": _trust_observation(observed.observation),
            },
            "read_state": ledger.model_dump(),
            "authority_epoch_preimage": epoch_preimage,
            "broker_generation_preimage": broker_preimage,
            "runtime_graph_preimage": graph_preimage,
            "runtime_admission": {
                "request": _frame(admission.request),
                "response": _frame(admission.response),
                "observation": _trust_observation(observed.observation),
            },
        }
        worker = f"{agent.broker_epoch}:{agent.generation_id}:{agent.session_id}"
        if worker != runtime.current_worker():
            raise LoopRejected("scheduler worker changed during source capture")
        return SchedulerGenerationSources(
            worker,
            _reference("r15-trust-authority-v1", epoch_preimage),
            _reference("r15-broker-generation-v1", broker_preimage),
            _reference("r15-runtime-graph-v1", graph_preimage),
            agent,
            _canonical(evidence),
        )


def command_from_draft(
    draft: SchedulerGenesisDraft, sources: SchedulerGenerationSources
) -> ConfigurationGenesisCommand:
    draft = SchedulerGenesisDraft.model_validate_json(draft.canonical_bytes())
    if isinstance(draft.end_exclusive_ns, int) and draft.end_exclusive_ns <= draft.start_ns:
        raise LoopRejected("scheduler definition end must follow start")
    return ConfigurationGenesisCommand(
        identity=SchedulerCommandIdentity(
            command_id=_command_id(draft.adoption_act_id),
            schema_version=GENESIS_COMMAND_SCHEMA,
            canonicalization_version="chiplog.scheduler.canonical.v1",
        ),
        schedule_id=draft.schedule_id,
        definition=FixedIntervalDefinition(
            start_ns=draft.start_ns,
            period_ns=draft.period_ns,
            end_exclusive_ns=draft.end_exclusive_ns,
            run_inputs=SchedulerRunInputs(
                tenant="hermetic-tenant",
                principal="hermetic-principal",
                prompt=draft.prompt,
                policy=draft.policy,
                origin=_origin(),
                contour_head="hermetic-contour-v1",
                policy_head="hermetic-policy-v1",
                worker_session=sources.worker_session,
                authority_epoch=sources.authority_epoch,
            ),
        ),
        policy=draft.missed_policy,
        bound=draft.interval_bound,
    )


def configuration_command_id(adoption_act_id: str) -> str:
    """Act identity deliberately excludes operation, payload and mutable sources."""
    if not isinstance(adoption_act_id, str) or not adoption_act_id:
        raise LoopRejected("scheduler adoption act must be a nonempty identity")
    return _reference(
        "r15-configuration-adoption-v1",
        ("hermetic-tenant", "hermetic-principal", adoption_act_id),
    )


def configuration_command_from_draft(
    draft: SchedulerConfigurationDraft,
    current: ConfigurationSnapshot,
    sources: SchedulerGenerationSources,
) -> ConfigurationTransitionCommand:
    """Describe current bindings, without admitting history or bypassing compiler checks."""
    draft = TypeAdapter(SchedulerConfigurationDraft).validate_json(draft.canonical_bytes())
    current = ConfigurationSnapshot.model_validate_json(current.canonical_bytes())
    if current.schedule is None or current.policy is None or current.bound is None:
        raise LoopRejected("scheduler configuration requires a complete existing schedule")
    if draft.schedule_id != current.schedule.schedule_id:
        raise LoopRejected("scheduler configuration targets a different schedule")
    schema = {
        "AMEND_SCHEDULE": AMEND_SCHEDULE_COMMAND_SCHEMA,
        "AMEND_POLICY": AMEND_POLICY_COMMAND_SCHEMA,
        "REPLACE_BOUND": REPLACE_BOUND_COMMAND_SCHEMA,
    }[draft.kind]
    identity = SchedulerCommandIdentity(
        command_id=configuration_command_id(draft.adoption_act_id),
        schema_version=schema,
        canonicalization_version="chiplog.scheduler.canonical.v1",
    )
    if isinstance(draft, BoundReplacementDraft):
        return ConfigurationBoundCommand(
            command=ReplaceIntervalBoundCommand(
                identity=identity,
                observed_head=current.bound,
                proposed_bound=draft.interval_bound,
                scheduler_authority_head=configuration_authority_head(sources),
                authority_epoch=sources.authority_epoch,
                broker_generation=sources.broker_generation,
                runtime_graph_generation=sources.runtime_graph_generation,
            )
        )
    observed_schedule = schedule_head(current.schedule)
    observed_policy = policy_head(current.policy)
    if isinstance(draft, PolicyAmendmentDraft):
        return ConfigurationPolicyCommand(
            identity=identity,
            observed_schedule=observed_schedule,
            observed_policy=observed_policy,
            policy=draft.missed_policy,
        )
    # Reuse the GENESIS binding constructor without changing its historical bytes.
    definition = command_from_draft(
        SchedulerGenesisDraft(
            adoption_act_id=draft.adoption_act_id,
            schedule_id=draft.schedule_id,
            start_ns=draft.start_ns,
            period_ns=draft.period_ns,
            end_exclusive_ns=draft.end_exclusive_ns,
            prompt=draft.prompt,
            policy=draft.policy,
            missed_policy=current.policy.policy,
            interval_bound=current.bound.bound,
        ),
        sources,
    ).definition
    return ConfigurationScheduleCommand(
        identity=identity,
        observed_schedule=observed_schedule,
        observed_policy=observed_policy,
        definition=definition,
    )


def validate_configuration_adoption(
    adoption: SchedulerConfigurationAdoption,
    current: ConfigurationSnapshot,
    sources: SchedulerGenerationSources,
) -> ConfigurationTransitionCommand:
    adoption = SchedulerConfigurationAdoption.model_validate_json(adoption.canonical_bytes())
    command: ConfigurationTransitionCommand = TypeAdapter(
        ConfigurationTransitionCommand
    ).validate_json(adoption.command_bytes)
    if command.canonical_bytes() != adoption.command_bytes:
        raise LoopRejected("scheduler adoption command is not canonical")
    draft: SchedulerConfigurationDraft
    if isinstance(command, ConfigurationBoundCommand):
        if current.schedule is None:
            raise LoopRejected("scheduler configuration requires an existing schedule")
        draft = BoundReplacementDraft(
            adoption_act_id=adoption.adoption_act_id,
            schedule_id=current.schedule.schedule_id,
            interval_bound=command.command.proposed_bound,
        )
    elif isinstance(command, ConfigurationPolicyCommand):
        draft = PolicyAmendmentDraft(
            adoption_act_id=adoption.adoption_act_id,
            schedule_id=command.observed_schedule.schedule_id,
            missed_policy=command.policy,
        )
    else:
        definition = command.definition
        draft = ScheduleAmendmentDraft(
            adoption_act_id=adoption.adoption_act_id,
            schedule_id=command.observed_schedule.schedule_id,
            start_ns=definition.start_ns,
            period_ns=definition.period_ns,
            end_exclusive_ns=definition.end_exclusive_ns,
            prompt=definition.run_inputs.prompt,
            policy=definition.run_inputs.policy,
        )
    if configuration_command_from_draft(draft, current, sources).canonical_bytes() != (
        adoption.command_bytes
    ):
        raise LoopRejected("scheduler adoption differs from registered current command bindings")
    return command


def validate_fresh_adoption(
    adoption: SchedulerGenesisAdoption, sources: SchedulerGenerationSources
) -> ConfigurationGenesisCommand:
    command = ConfigurationGenesisCommand.model_validate_json(adoption.command_bytes)
    if command.canonical_bytes() != adoption.command_bytes:
        raise LoopRejected("scheduler adoption command is not canonical")
    definition = command.definition
    draft = SchedulerGenesisDraft(
        adoption_act_id=adoption.adoption_act_id,
        schedule_id=command.schedule_id,
        start_ns=definition.start_ns,
        period_ns=definition.period_ns,
        end_exclusive_ns=definition.end_exclusive_ns,
        prompt=definition.run_inputs.prompt,
        policy=definition.run_inputs.policy,
        missed_policy=command.policy,
        interval_bound=command.bound,
    )
    if command_from_draft(draft, sources).canonical_bytes() != adoption.command_bytes:
        raise LoopRejected("scheduler adoption differs from registered current command bindings")
    return command


__all__ = [
    "AMEND_POLICY_COMMAND_SCHEMA",
    "AMEND_POLICY_OPERATION",
    "AMEND_SCHEDULE_COMMAND_SCHEMA",
    "AMEND_SCHEDULE_OPERATION",
    "GENESIS_COMMAND_SCHEMA",
    "GENESIS_OPERATION",
    "GENESIS_POLICY_SCHEMA",
    "GENESIS_SERVICE_IDENTITY",
    "REPLACE_BOUND_COMMAND_SCHEMA",
    "REPLACE_BOUND_OPERATION",
    "BoundReplacementDraft",
    "BrokerPublicationResult",
    "ConfigurationGenesisCommand",
    "ConfigurationTransitionCommand",
    "JournalSelectedPublication",
    "PolicyAmendmentDraft",
    "PublicationRejected",
    "ScheduleAmendmentDraft",
    "SchedulerConfigurationAdoption",
    "SchedulerConfigurationDraft",
    "SchedulerConfigurationPort",
    "SchedulerGenesisAdoption",
    "SchedulerGenesisDraft",
    "SchedulerGenesisPort",
    "configuration_command_from_draft",
    "configuration_command_id",
    "validate_configuration_adoption",
]
