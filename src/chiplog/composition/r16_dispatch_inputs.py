"""Independent current dispatch source acquisition and immutable source descriptions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.effects.contracts import DispatchSemanticBinding
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
    InitializedCallOrigin,
)
from chiplog.composition import r14_call_dispatch_policy as call_policy
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
    *,
    original_call_id: str | None = None,
    intent_id: str | None = None,
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
    selected_intent: ExternalActionIntentV2 | None = None
    if intent_id is not None:
        intents = tuple(
            row.snapshot.intent
            for row in history.v2_records
            if row.snapshot.intent.intent_id == intent_id
        )
        if not intents:
            raise LoopRejected("dispatch intent is absent from authenticated selected history")
        selected_intent = intents[-1]
        origin = selected_intent.mandate.origin
        selected_call = (
            origin.original_call_id if isinstance(origin, InitializedCallOrigin) else None
        )
        if original_call_id is not None and original_call_id != selected_call:
            raise LoopRejected("dispatch caller cannot replace the selected origin policy")
        original_call_id = selected_call
    if cut.worker is None or cut.worker.run.principal != "hermetic-principal":
        raise LoopRejected("dispatch needs an authenticated current hermetic worker")
    sessions = tuple(
        runtime._supervisor.runtime().session(owner)
        for owner in ("effects", "planning", "agent_loop", "deployment_trust")
    )
    if cut.worker.owner_session != sessions[2]:
        raise LoopRejected("dispatch worker generation differs")
    if original_call_id is not None:
        from chiplog.composition.r14_loop_history import read_execution_call_history
        from chiplog.composition.r14_runtime import R14PlanningRuntime

        if not isinstance(runtime, R14PlanningRuntime) or not isinstance(
            cut.worker.run, ExecutionRunRecord
        ):
            raise LoopRejected("initialized-call dispatch requires a registered executable Run")
        snapshot, inventory, _ = read_execution_call_history(runtime)
        originals = tuple(
            row.initialized_record
            for row in inventory.ordered_calls
            if row.original_call_id == original_call_id
        )
        if (
            snapshot.tenant_head != cut.tenant_frontier
            or len(originals) != 1
            or originals[0].call.classification != "CONSEQUENTIAL"
            or originals[0].call.original.original_run_id != worker_run_id
        ):
            raise LoopRejected("call grant requires exact selected consequential initialization")
        if selected_intent is not None:
            from chiplog.capabilities.agent_loop.recovery_contracts import Present
            from chiplog.capabilities.agent_loop.recovery_frontier_contracts import (
                ConsequentialAcceptedCall,
            )

            row = next(
                row for row in inventory.ordered_calls if row.original_call_id == original_call_id
            )
            accepted = row.acceptance
            expected_intent = reference(
                selected_intent.intent_id, selected_intent.canonical_bytes()
            )
            if (
                not isinstance(accepted, ConsequentialAcceptedCall)
                or not isinstance(accepted.external_effect_intent, Present)
                or accepted.external_effect_intent.head != expected_intent.head
                or accepted.external_effect_intent.fingerprint != expected_intent.fingerprint
            ):
                raise LoopRejected(
                    "call dispatch lacks exact selected acceptance and original intent"
                )
        resource_observation = resources.observe_call()
    else:
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
    if own is not None and not isinstance(own.mandate.origin, InitializedCallOrigin):
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


def captured_policy(captured: DispatchCapture) -> tuple[bytes, DispatchSemanticBinding]:
    """Interpret the exact captured issuer policy; this helper issues no authority."""
    grant = json.loads(captured.resources.grant_bytes)
    policy = grant.get("policy") if isinstance(grant, dict) else None
    if policy == policy_reference().model_dump(mode="json"):
        return policy_bytes(), SEMANTICS
    if policy == call_policy.policy_reference().model_dump(mode="json"):
        return call_policy.policy_bytes(), call_policy.SEMANTICS
    raise LoopRejected("captured resource has no registered dispatch policy")


def require_dispatch_scope(
    captured: DispatchCapture,
    resources: HermeticDispatchResources,
    mandate: DispatchMandateV2,
    own: ExternalActionIntentV2 | None,
    *,
    first_send: bool,
) -> None:
    policy, semantics = captured_policy(captured)
    policy_head = reference(semantics.normative_manifest, policy)
    if (
        mandate.tenant_id != captured.cut.tenant_id
        or mandate.principal_id != "hermetic-principal"
        or mandate.actor_id != "hermetic-principal"
        or mandate.operation_profile != policy_head
        or mandate.semantics != semantics
        or isinstance(mandate.origin, InitializedCallOrigin) != (semantics == call_policy.SEMANTICS)
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
        or (
            isinstance(mandate.origin, InitializedCallOrigin)
            and mandate.planning_revision
            != reference("dispatch.planning-scope", planning_scope(captured, own))
        )
    ):
        raise LoopRejected("current dispatch differs from immutable registered self-only mandate")
    # Fresh adoption cannot absorb an unresolved selected SEND into its baseline.
    # The closed self-only policy uses the same affected-party/resource scope as
    # its v1 predecessor; only latest attempts decide whether work is unresolved.
    from chiplog.capabilities.effects.contracts import EffectRecord
    from chiplog.capabilities.effects.dispatch_outcome_contracts import DispatchOutcomeRecordV2

    latest_states: dict[str, str] = {}
    v2_by_record = {row.record: row for row in captured.history.v2_records}
    for member in captured.history.members:
        retained = v2_by_record.get(member.record)
        if retained is not None:
            latest_states[member.intent.subject_id] = (
                "OUTCOME_UNKNOWN"
                if isinstance(retained, DispatchOutcomeRecordV2)
                and (
                    retained.snapshot.obligation.state == "OPEN"
                    or retained.snapshot.conflicting_evidence
                )
                else retained.snapshot.state
            )
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
            if grant["grant_id"] in resources.grant_identities:
                uses += 1
        if uses >= resources.cap:
            raise LoopRejected("offline SEND entitlement cap exhausted")


def source_inventory(
    captured: DispatchCapture,
    mandate: DispatchMandateV2,
    original_adoption_bytes: bytes,
    own: ExternalActionIntentV2 | None,
    *,
    run_source_version: str | None = None,
) -> DispatchSourceInventory:
    worker = captured.cut.worker
    if worker is None:
        raise LoopRejected("current dispatch worker absent")
    if own is not None:
        original = own.acquisition.original_sources.runtime_and_fence
        if not isinstance(original, CapturedSource):
            raise LoopRejected("original dispatch Run source is unavailable")
        if run_source_version is not None and run_source_version != original.source_version:
            raise LoopRejected("cannot override original dispatch Run source version")
        run_source_version = original.source_version
    if run_source_version is None:
        run_source_version = "2"
    if run_source_version not in {"2", "3"}:
        raise LoopRejected("unregistered dispatch Run source version")
    policy, semantics = captured_policy(captured)
    policy_head = reference(semantics.normative_manifest, policy)
    deadline = min(mandate.horizon.expires_at_ns, captured.observed_time_ns + 5_000_000_000)
    # The call issuance retains the full independently selected Run in captured.cut.
    # Bind that exact revision here without recursively embedding its prompt history
    # in every accepted record and immutable intent copy. Version 2 stays exact;
    # version 3 also uses the reference for new combined-profile legacy intents.
    run_source = (
        {
            "run_id": worker.run.run_id,
            "head": worker.run.head,
            "fingerprint": digest(worker.run.canonical_bytes()),
            "state": worker.run.state,
        }
        if run_source_version == "3" or isinstance(mandate.origin, InitializedCallOrigin)
        else worker.run.model_dump(mode="json")
    )
    values: dict[str, bytes] = {
        "trust": captured.principal,
        "planning": canonical([[name, raw.hex()] for name, raw in captured.planning_records]),
        "semantic_registry": policy,
        "original_adoption": original_adoption_bytes,
        "runtime_and_fence": canonical(
            {
                "run": run_source,
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
                source_version=run_source_version if role == "runtime_and_fence" else "2",
                owner_id="broker" if role != "semantic_registry" else "effects",
                reader_id="chiplog.composition.r16_dispatch_inputs.capture_dispatch",
                invalidation_manifest=policy_head,
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
    policy, semantics = captured_policy(captured)
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
            source_role_registry=reference(semantics.normative_manifest, policy),
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
        supported_semantics=semantics,
        clock_contract=CLOCK,
        clock_epoch=captured.resources.clock_epoch,
        observed_time_ns=captured.observed_time_ns,
        lease_expires_at_ns=deadline,
    )
