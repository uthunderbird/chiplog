"""Fresh denying observations; never reuses the historical SEND acquisition lease."""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.effects.contracts import CommandIdentity, ExactHead
from chiplog.capabilities.effects.denial_contracts import (
    BeforeSendDispositionV2Command,
    CurrentDenialInputs,
    DenialPreparationRequest,
    DenyingAuthority,
    DenyingSources,
)
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r16_denial_registry import (
    DenialIngress,
    HermeticDenialRegistry,
    denial_command_id,
)
from chiplog.composition.r16_effects import MaterializedEffectsCut, read_materialized_effects
from chiplog.composition.r16_effects_inputs import canonical, digest, effect_snapshot, reference
from chiplog.platform.broker import BrokerSession, PublicPortSuccess
from chiplog.platform.r7_trust import TrustOwnerCall
from chiplog.platform.read_ledger import BrokerReadState

if TYPE_CHECKING:
    from chiplog.composition.r14_runtime import R14PlanningRuntime

CLOCK = "chiplog.denial.monotonic-acquisition.v1"


@dataclass(frozen=True)
class DenialCapture:
    cut: MaterializedEffectsCut
    read_state: bytes
    loop_head: str | None
    sessions: tuple[BrokerSession, ...]
    registry: bytes
    principal_reference: bytes


def require_invocation(runtime: R14PlanningRuntime, observed: ObservedTrustCall) -> bytes:
    if runtime._trust_observation_guard(observed) is not None:
        raise LoopRejected("denying invocation is not currently authenticated")
    raw = observed.result.reference_bytes
    if raw is None:
        raise LoopRejected("denying invocation has no principal reference")
    principal = json.loads(raw)
    if (principal["tenant_id"], principal["principal_id"], principal["contour"]) != (
        runtime._tenant_id,
        "hermetic-principal",
        "CLI",
    ):
        raise LoopRejected("denying invocation has a foreign principal or contour")
    return raw


def capture_denial(
    runtime: R14PlanningRuntime, observed: ObservedTrustCall, worker_run_id: str
) -> DenialCapture:
    runtime._require_no_pending()
    runtime._check_database_identity()
    principal = require_invocation(runtime, observed)
    cut = read_materialized_effects(runtime, runtime._owner_decisions(), run_id=worker_run_id)
    if cut.worker is None or cut.worker.run.principal != "hermetic-principal":
        raise LoopRejected("current denying Run belongs to another principal")
    sessions = tuple(
        runtime._supervisor.runtime().session(owner)
        for owner in ("effects", "agent_loop", "deployment_trust")
    )
    if cut.worker.owner_session != sessions[1]:
        raise LoopRejected("denying worker generation changed")
    entries = runtime._loop_decisions().entries()
    return DenialCapture(
        cut,
        runtime._read_ledger.current_state(runtime._tenant_id).canonical_bytes(),
        entries[-1][0] if entries else None,
        sessions,
        HermeticDenialRegistry().canonical_bytes(),
        principal,
    )


def request_fingerprint(
    ingress: DenialIngress, authority: DenyingAuthority, expected: object, observed_time_ns: int
) -> str:
    return digest(canonical((ingress, authority, expected, observed_time_ns)))


def build_denial_request(
    ingress: DenialIngress,
    captured: DenialCapture,
    observed: ObservedTrustCall,
    *,
    observed_time_ns: int | None = None,
) -> DenialPreparationRequest:
    registry = HermeticDenialRegistry()
    cut, worker = captured.cut, captured.cut.worker
    if worker is None:
        raise LoopRejected("current denying worker missing")
    expected = effect_snapshot(cut)
    principal = json.loads(captured.principal_reference)["principal_id"]
    decision = registry.evaluate(ingress, expected, cut.tenant_id, principal)
    original = next(
        row.snapshot
        for row in reversed(expected.records)
        if row.snapshot.intent.intent_id == ingress.intent_id
    )
    intent = original.intent
    exact = ExactHead(
        subject_id=intent.intent_id,
        head=intent.intent_id + "/" + intent.fingerprint,
        fingerprint=intent.fingerprint,
    )
    now = time.monotonic_ns() if observed_time_ns is None else observed_time_ns
    deadline = min(now + 5_000_000_000, observed.request.budget.absolute_deadline_ns)
    if now >= deadline:
        raise LoopRejected("denying invocation expired before preparation")
    epoch = f"{captured.sessions[0].broker_epoch}:{captured.sessions[0].generation_id}"
    values = {
        "invocation": canonical(
            {
                "ingress": ingress,
                "reference_hex": captured.principal_reference.hex(),
                "trust_call": observed,
            }
        ),
        "principal_rights": canonical((captured.principal_reference, registry.reference, exact)),
        "operation_registry": registry.canonical_bytes(),
        "effects_history": compact_denial_cut(cut),
        "runtime_fence": canonical(
            (worker, captured.sessions, captured.read_state, captured.loop_head)
        ),
        "clock": canonical(
            {"contract": CLOCK, "epoch": epoch, "observed_ns": now, "deadline_ns": deadline}
        ),
        "decision": canonical({"ingress": ingress, "decision": decision}),
    }
    sources = DenyingSources(
        **{
            name: CapturedSource(
                source_id="effects.denial." + name,
                source_version="1",
                owner_id="broker",
                reader_id="chiplog.composition.r16_denial_inputs.capture_denial",
                invalidation_manifest=reference(
                    "effects.denial.invalidators." + name,
                    canonical(
                        (
                            registry.reference,
                            name,
                            "trust/storage/Run/session/registry/clock:writer-recapture",
                        )
                    ),
                ),
                head=reference("effects.denial." + name, raw),
                canonical_value=raw,
                clock_contract=CLOCK,
                clock_epoch=epoch,
                valid_until_ns=deadline,
            )
            for name, raw in values.items()
        }
    )
    authority = DenyingAuthority(
        schema_id="chiplog.effects.denying-authority.v1",
        tenant_id=cut.tenant_id,
        principal_id=principal,
        actor_id=principal,
        authenticated_session=reference(
            "effects.denial.authenticated-session", captured.principal_reference
        ),
        operation_profile=registry.reference,
        intent=exact,
        expected_attempt=ingress.expected_attempt,
        decision=decision,
        fence=worker.fence,
        clock_contract=CLOCK,
        clock_epoch=epoch,
        valid_until_ns=deadline,
        sources=sources,
    )
    identity = CommandIdentity(
        command_id=denial_command_id(cut.tenant_id, principal, ingress.act_id),
        fingerprint=request_fingerprint(ingress, authority, expected, now),
        expected_tenant_head=cut.tenant_frontier,
    )
    command = BeforeSendDispositionV2Command(
        schema_id="chiplog.effects.before-send-command.v2",
        identity=identity,
        intent=exact,
        expected_attempt=ingress.expected_attempt,
        authority=authority,
        authorization_to_retire=original.authorizations[-1]
        if original.state == "DISPATCH_AUTHORIZED" and original.authorizations
        else None,
    )
    return DenialPreparationRequest(
        schema_id="chiplog.effects.before-send-preparation.v2",
        command=command,
        expected=expected,
        current=CurrentDenialInputs(
            command_id=identity.command_id,
            command_fingerprint=identity.fingerprint,
            store_frontier=cut.tenant_frontier,
            authority=authority,
            supported_semantics=registry.semantics,
            fence=worker.fence,
            clock_contract=CLOCK,
            clock_epoch=epoch,
            observed_time_ns=now,
        ),
    )


def _retained_json(value: Any) -> Any:
    """Restore the explicit bytes wrapper before typed JSON decoding."""
    if isinstance(value, dict):
        if set(value) == {"exact_hex"}:
            return bytes.fromhex(value["exact_hex"]).decode("utf-8")
        return {key: _retained_json(member) for key, member in value.items()}
    if isinstance(value, list):
        return [_retained_json(member) for member in value]
    return value


def compact_denial_cut(cut: MaterializedEffectsCut) -> bytes:
    """Retain complete effects references and the exact current worker reference.

    Full effects records reside in request.expected; the current Run is retained
    in runtime_fence. This projection does not claim a complete Run inventory.
    """
    if cut.worker is None:
        raise ValueError("denial cut requires its exact worker")
    value = json.loads(canonical(cut))
    value["worker"] = reference("effects.denial.worker", canonical(cut.worker)).model_dump(
        mode="json"
    )
    del value["latest_runs"]
    for row in value["rows"]:
        del row["record"]["canonical_bytes"]
    return canonical(value)


def decode_retained_cut(request: DenialPreparationRequest) -> MaterializedEffectsCut:
    """Reconstitute effects and current worker, not the original full Run inventory."""
    from chiplog.platform._owner_publication_contracts import OwnerRecordBytes

    raw = request.command.authority.sources.effects_history.canonical_value
    value = json.loads(raw)
    worker = json.loads(request.command.authority.sources.runtime_fence.canonical_value)[0]
    if value["worker"] != reference("effects.denial.worker", canonical(worker)).model_dump(
        mode="json"
    ):
        raise ValueError("retained denial worker reference differs")
    if "latest_runs" in value:
        raise ValueError("denial projection cannot supply an independent Run inventory")
    value["worker"] = worker
    # This singleton only satisfies the internal cut shape for request rebuilding.
    # Current-worker provenance comes from runtime_fence and the selected journal.
    value["latest_runs"] = [worker["run"]]
    rows = value["rows"]
    if len(rows) != len(request.expected.records):
        raise ValueError("retained denial cut omits or adds a history row")
    identities: set[str] = set()
    for row, record in zip(rows, request.expected.records, strict=True):
        record_bytes = record.canonical_bytes()
        exact = OwnerRecordBytes(
            owner="effects",
            record_kind="effects." + record.kind,
            record_id=record.record.head,
            schema_id="chiplog.effects.record.v1",
            canonical_bytes=record_bytes,
            fingerprint=digest(record_bytes),
        )
        if (
            row["record"] != exact.model_dump(mode="json", exclude={"canonical_bytes"})
            or exact.record_id in identities
        ):
            raise ValueError("retained denial cut substitutes or duplicates a history row")
        identities.add(exact.record_id)
        row["record"] = exact.model_dump(mode="json")
    cut = TypeAdapter(MaterializedEffectsCut).validate_json(canonical(_retained_json(value)))
    if compact_denial_cut(cut) != raw:
        raise ValueError("retained denial cut is noncanonical or changes capture metadata")
    return cut


def _retained_trust_snapshot(raw: bytes) -> dict[str, Any]:
    """Interpret the registered trust journal vocabulary at its retained head."""
    state: dict[str, Any] = {
        "credential": None,
        "freshness": 0,
        "materialization_head": "",
        "phase": "UNAVAILABLE",
        "principal_id": None,
        "tenant_id": None,
        "trust_head": "",
    }
    previous = None
    identities: set[str] = set()

    def trust_digest(value: object) -> str:
        return digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())

    for decision_id, predecessor, encoded in json.loads(raw):
        if predecessor != previous or not isinstance(decision_id, str) or decision_id in identities:
            raise ValueError("retained trust journal chain is inconsistent")
        identities.add(decision_id)
        envelope_bytes = base64.b64decode(encoded, validate=True)
        envelope = json.loads(envelope_bytes)
        if (
            envelope["predecessor"] != previous
            or decision_id != digest((previous or "GENESIS").encode() + b"\x00" + envelope_bytes)
            or envelope_bytes
            != json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        ):
            raise ValueError("retained trust logical decision differs")
        previous = decision_id
        kind, payload = envelope["kind"], envelope["payload"]
        state["materialization_head"] = decision_id
        if kind == "INITIALIZE":
            state.update(
                tenant_id=payload["binding"]["tenant_id"],
                trust_head=trust_digest(payload["binding"]),
                phase="BOOTSTRAP_REQUIRED",
            )
        elif kind == "BOOTSTRAP":
            state.update(
                principal_id=payload["principal_id"],
                credential=payload["credential"],
                freshness=1,
                phase="ACTIVE",
            )
        elif kind in {"ROTATE_CREDENTIAL", "EMERGENCY_RECOVERY"}:
            state["credential"] = payload.get("credential", payload)
            state["freshness"] += 1
            state["phase"] = "ACTIVE"
        elif kind == "REVOKE" and isinstance(state["credential"], dict):
            state["credential"] = {**state["credential"], "revoked": True}
            state["freshness"] += 1
        elif kind in {
            "PRINCIPAL_CONTOUR_PREREQUISITE",
            "TRUST_TRANSITION_PREPARED",
            "TRUST_TRANSITION_READY",
            "TRUST_TRANSITION_ABORTED",
        }:
            state["phase"] = {
                "PRINCIPAL_CONTOUR_PREREQUISITE": "HOLD",
                "TRUST_TRANSITION_PREPARED": "PREPARED",
                "TRUST_TRANSITION_READY": "READY",
                "TRUST_TRANSITION_ABORTED": "ACTIVE",
            }[kind]
        elif kind == "TRUST_TRANSITION_ACCEPTED":
            state["trust_head"] = trust_digest(payload["binding"])
            state["phase"] = "ACTIVE"
        elif kind in {"OPERATOR_BINDING_KEY_ROTATION", "JOURNAL_ROOT_ROTATION"}:
            state["trust_head"] = trust_digest(payload)
        else:
            raise ValueError("unregistered historical denial trust decision")
    return state


def validate_retained_denial_sources(request: DenialPreparationRequest) -> None:
    """Reproduce original source interpretation without current time or live reads.

    Retained bytes describe the selected cut; their provenance still depends on
    the independently authenticated journal and the caller's complete-prefix check.
    """
    authority = request.command.authority
    invocation_raw = authority.sources.invocation.canonical_value
    invocation = json.loads(invocation_raw)
    ingress = DenialIngress.model_validate_json(canonical(invocation["ingress"]))
    observed = TypeAdapter(ObservedTrustCall).validate_json(
        canonical(_retained_json(invocation["trust_call"]))
    )
    cut = decode_retained_cut(request)
    fence_source = json.loads(authority.sources.runtime_fence.canonical_value)
    sessions = tuple(BrokerSession.model_validate_json(canonical(row)) for row in fence_source[1])
    read_bytes = bytes.fromhex(fence_source[2]["exact_hex"])
    read_state = BrokerReadState.model_validate_json(read_bytes)
    principal_raw = bytes.fromhex(invocation["reference_hex"])
    captured = DenialCapture(
        cut,
        read_bytes,
        fence_source[3],
        sessions,
        authority.sources.operation_registry.canonical_value,
        principal_raw,
    )
    worker = cut.worker
    if worker is None or len(sessions) != 3:
        raise ValueError("retained denial lacks its original worker sessions")
    run, fence, loop_session = worker.run, worker.fence, sessions[1]
    expected_worker = (
        f"{loop_session.broker_epoch}:{loop_session.generation_id}:{loop_session.session_id}"
    )
    if (
        run.state != "ACTIVE"
        or run.tenant != authority.tenant_id
        or run.principal != authority.principal_id
        or run.root_binding != "NOT_APPLICABLE"
        or run.worker_session != expected_worker
        or worker.owner_session != loop_session
        or fence != authority.fence
        or (fence.run_id, fence.run_head, fence.worker_session_id, fence.runtime_generation)
        != (run.run_id, run.head, expected_worker, loop_session.generation_id)
        or tuple(session.owner_id for session in sessions)
        != ("effects", "agent_loop", "deployment_trust")
        or any(
            (session.tenant_id, session.broker_epoch, session.generation_id)
            != (authority.tenant_id, loop_session.broker_epoch, loop_session.generation_id)
            for session in sessions
        )
        or read_state.owner_draining
        or (read_state.tenant_id, read_state.broker_epoch, read_state.owner_generation)
        != (authority.tenant_id, loop_session.broker_epoch, loop_session.generation_id)
        or read_bytes != read_state.canonical_bytes()
    ):
        raise ValueError("retained denying worker or read generation is inconsistent")
    sent, returned, result = observed.request, observed.response, observed.result
    payload = json.loads(sent.canonical_payload)
    trust_call = TrustOwnerCall(
        mode=payload["mode"],
        snapshot_bytes=base64.b64decode(payload["snapshot_bytes"], validate=True),
        request_bytes=base64.b64decode(payload["request_bytes"], validate=True),
    )
    if (
        sent.operation_id != "deployment_trust.authenticate"
        or sent.schema_id != "chiplog.deployment-trust.owner-call.v1"
        or sent.callee != sessions[2]
        or sent.caller
        != BrokerSession(
            tenant_id=authority.tenant_id,
            broker_epoch=loop_session.broker_epoch,
            generation_id=loop_session.generation_id,
            owner_id="broker",
            session_id=f"broker:{loop_session.generation_id}",
        )
        or not isinstance(returned, PublicPortSuccess)
        or returned.request_id != sent.request_id
        or returned.responder != sent.callee
        or returned.schema_id != "chiplog.deployment-trust.owner-result.v1"
        or returned.canonical_payload != result.canonical_bytes()
        or result.disposition != "VALID"
        or result.reason is not None
        or result.reference_bytes != principal_raw
        or trust_call.mode != "AUTHENTICATE"
        or trust_call.canonical_bytes() != sent.canonical_payload
        or trust_call.snapshot_bytes != observed.observation.snapshot_bytes
    ):
        raise ValueError("retained denying authentication call is inconsistent")
    # Owner snapshots use logical IDs; the physical journal head is separately
    # retained in the authenticated observation and is not the logical last ID.
    snapshot = _retained_trust_snapshot(trust_call.snapshot_bytes)
    authentication = json.loads(trust_call.request_bytes)
    credential = snapshot["credential"]
    expected_reference = {
        "contour": "CLI",
        "credential_head": credential["head"],
        "freshness_sequence": snapshot["freshness"],
        "materialization_head": snapshot["materialization_head"],
        "peer_credential": credential["peer_credential"],
        "principal_id": snapshot["principal_id"],
        "session_head": credential["session_head"],
        "source_head": "local",
        "tenant_id": snapshot["tenant_id"],
        "trust_head": snapshot["trust_head"],
    }
    if (
        snapshot["phase"] != "ACTIVE"
        or credential.get("revoked")
        or authentication["contour"] != "CLI"
        or any(
            authentication[field] != credential[field]
            for field in ("credential_id", "session_id", "peer_credential")
        )
        or json.loads(principal_raw) != expected_reference
        or (snapshot["tenant_id"], snapshot["principal_id"])
        != (authority.tenant_id, authority.principal_id)
    ):
        raise ValueError("retained denying authentication does not establish its principal")
    rebuilt = build_denial_request(
        ingress, captured, observed, observed_time_ns=request.current.observed_time_ns
    )
    if rebuilt != request:
        raise ValueError("retained denying source payloads differ from original interpretation")
