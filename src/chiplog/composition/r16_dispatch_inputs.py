"""Independent current dispatch source acquisition and immutable source descriptions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.dispatch_authority_contracts import (
    CapturedSource,
    DispatchAuthorityObservation,
    DispatchObservationCut,
    DispatchSourceInventory,
)
from chiplog.capabilities.effects.dispatch_v2 import canonical, digest, history_inventory, reference
from chiplog.capabilities.effects.dispatch_v2_contracts import (
    CurrentDispatchInputsV2,
    DispatchMandateV2,
    ExternalActionIntentV2,
)
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r16_denial_inputs import require_invocation
from chiplog.composition.r16_dispatch_history import (
    VerifiedDispatchHistory,
    normative_generation,
    verify_dispatch_history,
)
from chiplog.composition.r16_dispatch_registry import (
    CLOCK,
    SEMANTICS,
    HermeticDispatchResources,
    ResourceObservation,
    policy_bytes,
    policy_reference,
)
from chiplog.composition.r16_effects import (
    MaterializedEffectsCut,
    _read_materialized_effects_with_history,
)
from chiplog.platform.broker import BrokerSession

if TYPE_CHECKING:
    from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime


@dataclass(frozen=True)
class DispatchCapture:
    cut: MaterializedEffectsCut
    history: VerifiedDispatchHistory
    principal: bytes
    sessions: tuple[BrokerSession, ...]
    read_state: bytes
    loop_head: str | None
    resources: ResourceObservation
    planning_records: tuple[tuple[str, bytes], ...]
    own_planning_records: tuple[tuple[str, tuple[str, ...]], ...]
    observed_time_ns: int = field(compare=False)


def capture_dispatch(
    runtime: R16DispatchRuntime,
    resources: HermeticDispatchResources,
    observed: ObservedTrustCall,
    worker_run_id: str,
) -> DispatchCapture:
    if resources is not runtime._require_dispatch_resources():
        raise LoopRejected("captured dispatch resources differ from original custody")
    runtime._require_no_pending()
    runtime._check_database_identity()
    principal = require_invocation(runtime, observed)
    cut, selected = _read_materialized_effects_with_history(
        runtime, runtime._owner_decisions(), run_id=worker_run_id
    )
    if any(row.record.schema_id == "chiplog.effects.record.v1" for row in cut.rows):
        raise LoopRejected("mixed legacy/v2 effects history is unsupported")
    history = verify_dispatch_history(cut, selected)
    if cut.worker is None or cut.worker.run.principal != "hermetic-principal":
        raise LoopRejected("dispatch needs an authenticated current hermetic worker")
    sessions = tuple(
        runtime._supervisor.runtime().session(owner)
        for owner in ("effects", "planning", "agent_loop", "deployment_trust")
    )
    if cut.worker.owner_session != sessions[2]:
        raise LoopRejected("dispatch worker generation differs")
    resource_observation = resources.observe()
    if not resources.verify_current(resource_observation):
        raise LoopRejected("offline dispatch resource grant is unavailable or revoked")
    epoch, now = resources.clock()
    if epoch != resource_observation.clock_epoch:
        raise LoopRejected("offline resource clock continuity changed")
    entries = runtime._loop_decisions().entries()
    planning_records = tuple(
        (record.record_id.value, record.canonical_bytes)
        for publication in runtime._verified_publications("planning")
        for record in publication.records
    )
    own_planning_records = tuple(
        (
            record.snapshot.intent.intent_id,
            tuple(
                item.record_id
                for item in decision.prepared.request.complete_records
                if item.owner == "planning"
            ),
        )
        for record in history.v2_records
        if record.kind == "PLAN_EFFECT_PUBLISHED"
        for decision in selected.decisions
        if any(
            item.record_id == record.record.head
            for item in decision.prepared.request.complete_records
        )
    )
    return DispatchCapture(
        cut,
        history,
        principal,
        sessions,
        runtime._read_ledger.current_state(runtime._tenant_id).canonical_bytes(),
        entries[-1][0] if entries else None,
        resource_observation,
        planning_records,
        own_planning_records,
        now,
    )


def planning_scope(captured: DispatchCapture, own: ExternalActionIntentV2 | None) -> bytes:
    exempt = set()
    if own is not None:
        exempt = {
            record_id
            for intent_id, ids in captured.own_planning_records
            if intent_id == own.intent_id
            for record_id in ids
        }
        if own.mandate.planning_revision.head not in {
            name for name, _ in captured.planning_records
        }:
            raise LoopRejected(
                "adopted planning revision is absent from authenticated current inventory"
            )
    return canonical(
        [[name, raw.hex()] for name, raw in captured.planning_records if name not in exempt]
    )


def require_dispatch_scope(
    captured: DispatchCapture,
    resources: HermeticDispatchResources,
    mandate: DispatchMandateV2,
    own: ExternalActionIntentV2 | None,
    *,
    first_send: bool,
) -> None:
    if (
        mandate.tenant_id != captured.cut.tenant_id
        or mandate.principal_id != "hermetic-principal"
        or mandate.actor_id != "hermetic-principal"
        or mandate.operation_profile != policy_reference()
        or mandate.semantics != SEMANTICS
        or mandate.recipient != resources.recipient(captured.resources)
        or mandate.horizon.clock_contract != CLOCK
        or mandate.horizon.clock_epoch != captured.resources.clock_epoch
        or not mandate.horizon.not_before_ns
        <= captured.observed_time_ns
        < mandate.horizon.expires_at_ns
        or mandate.horizon.expires_at_ns - mandate.horizon.not_before_ns > 60_000_000_000
        or len(mandate.payload) > 65536
        or mandate.dependencies
        or mandate.factual_assertion_evidence
        or normative_generation(captured.history, own) != mandate.normative_conflict_generation
        or reference("dispatch.planning-scope", planning_scope(captured, own))
        not in mandate.authority_sources
    ):
        raise LoopRejected("current dispatch differs from immutable registered self-only mandate")
    # Fresh adoption cannot absorb an unresolved selected SEND into its baseline.
    # The closed self-only policy uses the same affected-party/resource scope as
    # its v1 predecessor; only latest attempts decide whether work is unresolved.
    from chiplog.capabilities.effects.contracts import EffectRecord

    latest_states: dict[str, str] = {}
    v2_by_record = {row.record: row for row in captured.history.v2_records}
    for member in captured.history.members:
        retained = v2_by_record.get(member.record)
        if retained is not None:
            latest_states[member.intent.subject_id] = retained.snapshot.state
        else:
            legacy = EffectRecord.model_validate_json(member.record_bytes)
            latest_states[member.intent.subject_id] = legacy.snapshot.state
    if any(
        state in {"SEND_COMMITTED", "SENT", "OUTCOME_UNKNOWN", "PARTIAL", "PARTIAL_CONFIRMED"}
        for state in latest_states.values()
    ):
        raise LoopRejected("registered self-only scope has unresolved selected effect work")
    if first_send:
        # The independently granted stable identity owns the cap. Every historical
        # SEND counts, including subsequently resolved work; no latest projection.
        uses = 0
        for member in captured.history.members:
            if member.kind != "SEND_COMMITTED":
                continue
            if member.record not in {row.record for row in captured.history.v2_records}:
                # Unknown grant interpretation cannot be guessed as another grant.
                raise LoopRejected("legacy SEND grant accounting has no registered interpreter")
            retained = next(
                row for row in captured.history.v2_records if row.record == member.record
            )
            source = retained.snapshot.intent.acquisition.original_sources.deployment_entitlement
            if not isinstance(source, CapturedSource):
                raise LoopRejected("historical SEND has no captured deployment source")
            grant = json.loads(source.canonical_value)
            if grant["grant_id"] == resources.grant_identity:
                uses += 1
        if uses >= resources.cap:
            raise LoopRejected("offline SEND entitlement cap exhausted")


def source_inventory(
    captured: DispatchCapture,
    mandate: DispatchMandateV2,
    original_adoption_bytes: bytes,
    own: ExternalActionIntentV2 | None,
) -> DispatchSourceInventory:
    worker = captured.cut.worker
    if worker is None:
        raise LoopRejected("current dispatch worker absent")
    deadline = min(mandate.horizon.expires_at_ns, captured.observed_time_ns + 5_000_000_000)
    values: dict[str, bytes] = {
        "trust": captured.principal,
        "planning": canonical([[name, raw.hex()] for name, raw in captured.planning_records]),
        "semantic_registry": policy_bytes(),
        "original_adoption": original_adoption_bytes,
        "runtime_and_fence": canonical(
            {
                "run": worker.run.model_dump(mode="json"),
                "fence": worker.fence.model_dump(mode="json"),
                "sessions": [session.__dict__ for session in captured.sessions],
                "loop_head": captured.loop_head,
            }
        ),
        "endpoint": captured.resources.endpoint_bytes,
        "credential_lifecycle": captured.resources.credential_bytes,
        "deployment_entitlement": captured.resources.grant_bytes,
        "clock": canonical(
            {
                "contract": CLOCK,
                "epoch": captured.resources.clock_epoch,
                "observed_time_ns": captured.observed_time_ns,
            }
        ),
        "effects_history": history_inventory(captured.history.members),
        "normative_conflict_generation": normative_generation(
            captured.history, own
        ).canonical_bytes(),
    }
    return DispatchSourceInventory.model_validate(
        {
            role: CapturedSource(
                source_id="dispatch." + role,
                source_version="2",
                owner_id="broker" if role != "semantic_registry" else "effects",
                reader_id="chiplog.composition.r16_dispatch_inputs.capture_dispatch",
                invalidation_manifest=policy_reference(),
                head=reference("dispatch." + role, raw),
                canonical_value=raw,
                clock_contract=CLOCK,
                clock_epoch=captured.resources.clock_epoch,
                valid_until_ns=deadline,
            )
            for role, raw in values.items()
        }
    )


def current_inputs(
    captured: DispatchCapture,
    command_bytes: bytes,
    intent: ExternalActionIntentV2,
    *,
    original_adoption_bytes: bytes,
) -> CurrentDispatchInputsV2:
    mandate = intent.mandate
    sources = source_inventory(captured, mandate, original_adoption_bytes, intent)
    deadline = min(mandate.horizon.expires_at_ns, captured.observed_time_ns + 5_000_000_000)
    cut = captured.cut
    observation = DispatchAuthorityObservation(
        schema_id="chiplog.effects.dispatch-observation.v2",
        query_fingerprint=digest(command_bytes),
        cut=DispatchObservationCut(
            tenant_id=cut.tenant_id,
            database_identity=reference(
                "database",
                canonical(
                    [
                        cut.physical_path,
                        cut.physical_device,
                        cut.physical_inode,
                    ]
                ),
            ),
            selected_journal_head=reference("owner-journal", canonical(cut.owner_journal_head)),
            materialization_commitment=cut.materialization_commitment,
            tenant_frontier=cut.tenant_frontier,
            source_role_registry=policy_reference(),
            capture_id=digest(canonical([cut.owner_journal_head, captured.observed_time_ns])),
        ),
        sources=sources,
        complete_effect_history=captured.history.members,
        complete_history_fingerprint=digest(
            canonical([member.model_dump(mode="json") for member in captured.history.members])
        ),
    )
    return CurrentDispatchInputsV2(
        schema_id="chiplog.effects.current-dispatch-inputs.v2",
        command_fingerprint=digest(command_bytes),
        immutable_mandate=reference(mandate.mandate_id, mandate.canonical_bytes()),
        observation=observation,
        supported_semantics=SEMANTICS,
        clock_contract=CLOCK,
        clock_epoch=captured.resources.clock_epoch,
        observed_time_ns=captured.observed_time_ns,
        lease_expires_at_ns=deadline,
    )
