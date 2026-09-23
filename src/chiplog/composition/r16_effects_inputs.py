"""Broker-owned PlanEffect source acquisition and exact owner-input construction.

The caller holds the bundle gate. These values are retained by a private issuer;
constructing an equivalent DTO is not an invocation or a publication permit.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from chiplog.adapters.driven.effects_queries import _decode_row, _validate_denial_predecessor
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.contracts import (
    AdoptedAuthorityAct,
    AuthorityBinding,
    AuthorityRead,
    CommandIdentity,
    CurrentEffectInputs,
    EffectPreparationRequest,
    EffectRecord,
    EffectSnapshot,
    EffectStoreSnapshot,
    ExactHead,
    ExternalActionIntent,
    OrdinaryPurpose,
    PreparedEffectPublication,
    PublishPlanEffectCommand,
)
from chiplog.capabilities.planning._r8_authority import decode_trace
from chiplog.composition.r16_effects import (
    MaterializedEffectsCut,
    PreparedEffectAdoption,
    R16EffectsProducer,
    _decode_request,
    read_materialized_effects,
)
from chiplog.composition.r16_effects_registry import HermeticPlanEffectRegistry
from chiplog.platform._owner_publication_contracts import OwnerRecordBytes
from chiplog.platform.broker import BrokerSession, PublicPortRejected

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime


def canonical(value: object) -> bytes:
    def wire(item: object) -> object:
        if isinstance(item, BaseModel):
            return item.model_dump(mode="json")
        if isinstance(item, bytes):
            return {"exact_hex": item.hex()}
        if is_dataclass(item) and not isinstance(item, type):
            return wire(asdict(item))
        if isinstance(item, dict):
            return {key: wire(member) for key, member in item.items()}
        if isinstance(item, (tuple, list)):
            return [wire(member) for member in item]
        return item

    return json.dumps(
        wire(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def reference(subject: str, raw: bytes) -> ExactHead:
    fingerprint = digest(raw)
    return ExactHead(subject_id=subject, head=subject + "/" + fingerprint, fingerprint=fingerprint)


@dataclass(frozen=True)
class EffectSources:
    cut: MaterializedEffectsCut
    read_state: bytes
    loop_head: str | None
    sessions: tuple[BrokerSession, ...]
    registry: bytes


def capture_sources(
    runtime: R14PlanningRuntime,
    adoption: PreparedEffectAdoption,
    registry: HermeticPlanEffectRegistry,
) -> EffectSources:
    runtime._require_no_pending()
    runtime._check_database_identity()
    if runtime._trust_observation_guard(adoption.observed_trust) is not None:
        raise LoopRejected("effect authentication source is no longer current")
    R16EffectsProducer(runtime)._require_current_preparation(adoption.planning)
    if tuple(d for d in runtime._displays() if d.display_id == adoption.display.display_id) != (
        adoption.display,
    ):
        raise LoopRejected("effect display changed")
    if time.monotonic_ns() >= adoption.binding.valid_until_ns:
        raise LoopRejected("original effect display expired")
    sent, returned = adoption.planning.sent_call, adoption.planning.returned_result
    if (
        sent is None
        or returned is None
        or isinstance(returned, PublicPortRejected)
        or sent.canonical_payload != adoption.planning.request_bytes
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.planning.public.result.v1"
        or time.monotonic_ns() >= sent.budget.absolute_deadline_ns
    ):
        raise LoopRejected("effect planning output lacks its exact live owner invocation")
    cut = read_materialized_effects(
        runtime, runtime._owner_decisions(), run_id=adoption.binding.run_id
    )
    worker = cut.worker
    if (
        worker is None
        or worker.run.head != adoption.binding.run_head
        or cut.tenant_frontier != adoption.planning.expected_tenant_head
    ):
        raise LoopRejected("effect Run or planning predecessor changed")
    sessions = tuple(
        runtime._supervisor.runtime().session(owner)
        for owner in ("planning", "effects", "agent_loop", "deployment_trust")
    )
    if adoption.planning.owner_session != sessions[0] or worker.owner_session != sessions[2]:
        raise LoopRejected("effect owner generation changed")
    entries = runtime._loop_decisions().entries()
    return EffectSources(
        cut,
        runtime._read_ledger.current_state(runtime._tenant_id).canonical_bytes(),
        entries[-1][0] if entries else None,
        sessions,
        registry.canonical_bytes(),
    )


def effect_snapshot(cut: MaterializedEffectsCut) -> EffectStoreSnapshot:
    records: list[EffectRecord] = []
    latest: dict[str, EffectRecord] = {}
    commands: set[str] = set()
    for row in cut.rows:
        record = _decode_row(row.record, cut.tenant_id)
        prior = latest.get(record.snapshot.intent.intent_id)
        if (
            record.command.command_id in commands
            or record.command.expected_tenant_head != row.commit_sequence - 1
            or record.predecessor != (None if prior is None else prior.record)
            or (prior is not None and prior.snapshot.intent != record.snapshot.intent)
            or (
                prior is None
                and record.kind
                not in {
                    "INTENT_ACCEPTED",
                    "PLAN_EFFECT_PUBLISHED",
                    "DELIVERY_PREPARED",
                    "RECOVERY_INTENT_PUBLISHED",
                }
            )
        ):
            raise LoopRejected("effect history has an invalid original or successor")
        _validate_denial_predecessor(record, prior)
        commands.add(record.command.command_id)
        records.append(record)
        latest[record.snapshot.intent.intent_id] = record
    return EffectStoreSnapshot(
        tenant_id=cut.tenant_id, tenant_head=cut.tenant_frontier, records=tuple(records)
    )


def planning_records(adoption: PreparedEffectAdoption) -> tuple[OwnerRecordBytes, ...]:
    proposal = json.loads(adoption.planning.owner_result_bytes)
    return tuple(
        OwnerRecordBytes(
            owner="planning",
            record_kind=item["record_type_id"],
            record_id=item["record_id"],
            schema_id="chiplog.planning.record.v1",
            canonical_bytes=base64.b64decode(item["canonical_bytes"], validate=True),
            fingerprint=digest(base64.b64decode(item["canonical_bytes"], validate=True)),
        )
        for item in proposal["records"]
    )


def _compact_materialized_cut(cut: MaterializedEffectsCut) -> object:
    """Version 2 keeps ordered exact references; expected retains full records."""
    value = json.loads(canonical(cut))
    for row in value["rows"]:
        del row["record"]["canonical_bytes"]
    return value


def build_effect_request(
    command_id: str,
    adoption: PreparedEffectAdoption,
    sources: EffectSources,
    registry: HermeticPlanEffectRegistry,
) -> EffectPreparationRequest:
    snapshot = effect_snapshot(sources.cut)
    binding, planning = adoption.binding, adoption.planning
    trust = json.loads(planning.trust_reference_bytes)
    policy = registry.evaluate(
        binding, snapshot, tenant_id=trust["tenant_id"], principal_id=trust["principal_id"]
    )
    if policy.blocking_effect_heads:
        raise LoopRejected("unresolved effects block a fresh ordinary effect")
    trace = decode_trace(_decode_request(planning.request_bytes).authority_trace_bytes)
    original_raw = base64.b64decode(binding.planning_request_base64, validate=True)
    if original_raw != planning.request_bytes:
        raise LoopRejected("effect adoption replaced original planning input bytes")
    sent = planning.sent_call
    if sent is None:
        raise LoopRejected("missing planning invocation")
    deadline = min(
        binding.valid_until_ns,
        adoption.observed_trust.request.budget.absolute_deadline_ns,
        sent.budget.absolute_deadline_ns,
        *(read.valid_until_ns for read in trace.reads),
    )
    if time.monotonic_ns() >= deadline:
        raise LoopRejected("effect source lease expired")
    reads = [
        AuthorityRead(
            source_id=read.source_id,
            source_version=read.source_version,
            head=ExactHead(
                subject_id=read.source_id, head=read.head, fingerprint=digest(read.canonical_value)
            ),
            generation=read.generation,
            frontier=read.frontier,
            valid_until_ns=read.valid_until_ns,
            canonical_value=read.canonical_value,
        )
        for read in trace.reads
    ]
    for name, value in (
        ("effects.authenticated-invocation", adoption.observed_trust),
        ("effects.original-adoption", (adoption.display, adoption.ingress)),
        ("effects.materialized-cut", _compact_materialized_cut(sources.cut)),
        ("effects.read-state", sources.read_state),
        ("effects.loop-history", sources.loop_head),
        ("effects.owner-sessions", sources.sessions),
        ("effects.policy-registry", sources.registry),
    ):
        version = "2" if name == "effects.materialized-cut" else "1"
        raw = canonical({"schema": name + ".v" + version, "value": value})
        reads.append(
            AuthorityRead(
                source_id=name,
                source_version=version,
                head=reference(name, raw),
                generation=sources.sessions[0].generation_id,
                frontier=digest(sources.read_state),
                valid_until_ns=deadline,
                canonical_value=raw,
            )
        )
    for source in policy.references:
        reads.append(
            AuthorityRead(
                source_id=source.source_id,
                source_version=source.source_version,
                head=source.head,
                generation=sources.sessions[0].generation_id,
                frontier=digest(sources.read_state),
                valid_until_ns=deadline,
                canonical_value=source.canonical_value,
            )
        )
    records = planning_records(adoption)
    result = json.loads(planning.owner_result_bytes)["result"]
    revision_id = result["revision_id"]["value"]
    revision = next(row for row in records if row.record_id == revision_id)
    plan_ref = ExactHead(subject_id=revision_id, head=revision_id, fingerprint=revision.fingerprint)
    authorization = reference(
        "effects.adoption-authorization",
        canonical(
            (
                adoption.observed_trust,
                adoption.ingress,
                policy,
                sources.registry,
            )
        ),
    )
    authority = AuthorityBinding(
        tenant_id=trust["tenant_id"],
        principal_id=trust["principal_id"],
        actor_id=trust["principal_id"],
        authenticated_session=ExactHead(
            subject_id=trust["session_head"],
            head=trust["session_head"],
            fingerprint=digest(canonical((trust, adoption.observed_trust.observation))),
        ),
        act=AdoptedAuthorityAct(
            kind="EXACT_PROPOSAL_ADOPTION",
            proposal=reference(binding.proposal_id, binding.proposal.canonical_bytes()),
            display_digest=adoption.display.display_digest,
            adoption=reference(
                adoption.ingress.adoption_act_id, adoption.ingress.canonical_bytes()
            ),
            ingress=reference("effects.cli-adoption", adoption.ingress.canonical_bytes()),
            interpretation=reference(
                "effects.sealed-interpretation",
                canonical(
                    (
                        sources.cut.worker,
                        binding.proposal,
                    )
                ),
            ),
        ),
        planning_revision=plan_ref,
        authorization_evidence=authorization,
        authority_sources=tuple(row.head for row in reads),
        affected_party_constraints=policy.affected_party_constraints,
        hold_conflict_order=policy.hold_conflict_order,
        dependencies=policy.dependencies,
        factual_assertion_evidence=policy.factual_assertion_evidence,
        verification_contradiction=policy.verification_contradiction,
        authority_applicability=policy.authority_applicability,
        consequence_scope=policy.consequence_scope,
        communication_mandate=policy.communication_mandate,
        disclosure_projection=policy.disclosure_projection,
        channel_class=policy.channel_class,
        interaction_context=reference(
            "effects.interaction",
            canonical(
                (
                    sources.cut.worker,
                    adoption.display,
                    adoption.ingress,
                )
            ),
        ),
        recipient=registry.recipient,
        reads=tuple(reads),
        registry_inputs=registry.registry_inputs,
        valid_until_ns=deadline,
    )
    payload = binding.proposal.payload()
    intent = ExternalActionIntent(
        intent_id=command_id + "/intent",
        fingerprint="pending",
        authority=authority,
        semantics=registry.semantics,
        payload=payload,
        effect_fingerprint=digest(payload),
        idempotency_fence_key=command_id + "/transmission",
        inseparable_bundle_members=policy.bundle_members,
        purpose=OrdinaryPurpose(kind="ORDINARY_EFFECT"),
    )
    body = intent.model_dump(mode="json")
    del body["fingerprint"]
    intent = intent.model_copy(update={"fingerprint": digest(canonical(body))})
    worker = sources.cut.worker
    if worker is None:
        raise LoopRejected("effect creation requires the current worker")
    command = PublishPlanEffectCommand(
        identity=CommandIdentity(
            command_id=command_id, fingerprint="pending", expected_tenant_head=snapshot.tenant_head
        ),
        intent=intent,
        planning_publication=plan_ref,
        planning_owner_bytes=planning.owner_result_bytes,
        complete_publication_manifest=(
            *(
                ExactHead(subject_id=r.record_id, head=r.record_id, fingerprint=r.fingerprint)
                for r in records
            ),
            ExactHead(
                subject_id=intent.intent_id,
                head=intent.intent_id + "/" + intent.fingerprint,
                fingerprint=intent.fingerprint,
            ),
        ),
        fence=worker.fence,
    )
    command_body = command.model_dump(mode="json")
    del command_body["identity"]["fingerprint"]
    command = command.model_copy(
        update={
            "identity": command.identity.model_copy(
                update={
                    "fingerprint": digest(
                        canonical(
                            {"schema": "chiplog.plan-effect.command.v1", "value": command_body}
                        )
                    ),
                }
            )
        }
    )
    current = CurrentEffectInputs(
        command_id=command_id,
        command_fingerprint=command.identity.fingerprint,
        store_frontier=snapshot.tenant_head,
        observed_time_ns=time.monotonic_ns(),
        authority=authority,
        supported_semantics=registry.semantics,
        fence=worker.fence,
        authority_decision=authorization,
        blocking_effect_heads=policy.blocking_effect_heads,
        current_original_ambiguity_heads=(),
        initialized_call=None,
        active_run_head=worker.run.head,
        independently_verified_safe_proof=None,
        authenticated_evidence=None,
        original_reducer_semantics=None,
        authorized_reconciler=None,
    )
    return EffectPreparationRequest(
        operation="effects.publish_plan_effect",
        command_bytes=command.canonical_bytes(),
        expected=snapshot,
        current=current,
    )


def require_initial_output(
    request: EffectPreparationRequest, proposed: PreparedEffectPublication
) -> None:
    """Verify the closed initial wire contract; never execute a later reducer step."""
    command = PublishPlanEffectCommand.model_validate_json(request.command_bytes)
    snapshot = EffectSnapshot(
        intent=command.intent,
        attempt=command.planning_publication,
        state="INTENT_RECORDED",
        authorizations=(),
        transmissions=(),
        evidence=(),
        unresolved_obligations=(),
        evidence_records=(),
        recovery_obligation=None,
    )
    data = snapshot.model_dump(mode="json")
    del data["attempt"]
    attempt = reference(
        command.intent.intent_id + "/attempt",
        canonical(
            {
                "command": command.identity.model_dump(mode="json"),
                "predecessor": None,
                "snapshot": data,
            }
        ),
    )
    snapshot = snapshot.model_copy(update={"attempt": attempt})
    body = {
        "command": command.identity.model_dump(mode="json"),
        "predecessor": None,
        "kind": "PLAN_EFFECT_PUBLISHED",
        "snapshot": snapshot.model_dump(mode="json"),
        "source_command": request.command_bytes.hex(),
    }
    expected = PreparedEffectPublication(
        record=EffectRecord(
            record=reference("effects/" + command.identity.command_id, canonical(body)),
            command=command.identity,
            predecessor=None,
            kind="PLAN_EFFECT_PUBLISHED",
            snapshot=snapshot,
            source_command=request.command_bytes,
        ),
        exact_companion_manifest=command.complete_publication_manifest,
        expected_store=request.expected,
    )
    if proposed != expected:
        raise LoopRejected("effects owner result differs from the exact initial publication")
