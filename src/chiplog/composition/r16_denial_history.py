"""Exact historical denial validation at the original selected cut, never fresh issuance."""

import base64
import json

from chiplog.adapters.driven.effects_queries import _decode_row, _validate_denial_predecessor
from chiplog.capabilities.agent_loop.contracts import RunRecord
from chiplog.capabilities.effects.contracts import EffectRecord, PreparedEffectPublication
from chiplog.capabilities.effects.denial import prepare_denial
from chiplog.capabilities.effects.denial_contracts import DenialPreparationRequest
from chiplog.capabilities.effects.fences import NonSchedulerFence
from chiplog.composition.r16_denial_inputs import (
    CLOCK,
    decode_retained_cut,
    request_fingerprint,
    validate_retained_denial_sources,
)
from chiplog.composition.r16_denial_registry import (
    DenialIngress,
    HermeticDenialRegistry,
    denial_command_id,
)
from chiplog.composition.r16_effects_inputs import canonical, digest, reference
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    ExactRecordHead,
    ObservedPresence,
    OwnerRecordBytes,
    RegisteredPublication,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.owner_decision_journal import OwnerJournalIntegrityError, OwnerJournalSnapshot
from chiplog.platform.owner_publications import source_commands

PREPARATION_SCHEMA = "chiplog.effects.before-send-preparation.v2"


def retained_ingress(request: DenialPreparationRequest) -> DenialIngress:
    value = json.loads(request.command.authority.sources.invocation.canonical_value)
    return DenialIngress.model_validate(value["ingress"])


def denial_record(prepared: PreparedEffectPublication) -> OwnerRecordBytes:
    raw = prepared.record.canonical_bytes()
    return OwnerRecordBytes(
        owner="effects",
        record_kind="effects.BEFORE_SEND_DISPOSITION",
        record_id=prepared.record.record.head,
        schema_id="chiplog.effects.record.v1",
        canonical_bytes=raw,
        fingerprint=digest(raw),
    )


def denial_manifest(request: DenialPreparationRequest) -> AuthoritativeReadManifest:
    authority = request.command.authority
    if not isinstance(authority.fence, NonSchedulerFence):
        raise ValueError("denying worker profile is not registered for this interpreter")
    cut = decode_retained_cut(request)
    worker = json.loads(authority.sources.runtime_fence.canonical_value)[0]
    run = RunRecord.model_validate_json(canonical(worker["run"]))
    if (run.run_id, run.head, run.principal) != (
        authority.fence.run_id,
        authority.fence.run_head,
        authority.principal_id,
    ):
        raise ValueError("historical denying Run identity differs")
    heads = (
        ObservedPresence(
            head=ExactRecordHead(
                owner="agent_loop",
                record_kind="Run",
                subject_id=run.run_id,
                record_id=run.head,
                fingerprint=digest(run.canonical_bytes()),
            )
        ),
        *(
            ObservedPresence(
                head=ExactRecordHead(
                    owner="effects",
                    record_kind=row.kind,
                    subject_id=row.snapshot.intent.intent_id,
                    record_id=row.record.head,
                    fingerprint=row.record.fingerprint,
                )
            )
            for row in request.expected.records
        ),
    )
    if (
        cut.tenant_id != request.expected.tenant_id
        or cut.tenant_frontier != request.expected.tenant_head
    ):
        raise ValueError("historical denying cut differs from expected history")
    captured_records = tuple(
        _decode_row(row.record, request.expected.tenant_id) for row in cut.rows
    )
    if captured_records != request.expected.records:
        raise ValueError("historical source omits or substitutes effects history")
    return AuthoritativeReadManifest(
        tenant_id=request.expected.tenant_id,
        tenant_frontier=request.expected.tenant_head,
        expected_materialization_commitment=cut.materialization_commitment,
        registry_head=authority.operation_profile.head,
        registry_fingerprint=authority.operation_profile.fingerprint,
        ordered_heads=heads,
        complete_manifest_fingerprint=digest(
            canonical((request.expected, authority.sources, heads))
        ),
    )


def validate_denial_batch(
    batch: SingleOwnerBatch, prefix: tuple[EffectRecord, ...]
) -> DenialPreparationRequest:
    if (
        batch.operation != "effects.before_send"
        or batch.command.owner != "effects"
        or batch.command.schema_id != PREPARATION_SCHEMA
    ):
        raise ValueError("unregistered historical denial batch")
    raw = batch.command.canonical_bytes
    request = DenialPreparationRequest.model_validate_json(raw)
    validate_retained_denial_sources(request)
    authority, identity = request.command.authority, request.command.identity
    registry = HermeticDenialRegistry()
    ingress = retained_ingress(request)
    invocation = json.loads(authority.sources.invocation.canonical_value)
    principal_raw = bytes.fromhex(invocation["reference_hex"])
    principal = json.loads(principal_raw)
    if (
        raw != request.canonical_bytes()
        or batch.command.fingerprint != digest(raw)
        or request.expected.records != prefix
        or authority.operation_profile != registry.reference
        or authority.sources.operation_registry.canonical_value != registry.canonical_bytes()
        or (authority.tenant_id, authority.principal_id, authority.actor_id)
        != (principal["tenant_id"], principal["principal_id"], principal["principal_id"])
        or principal["contour"] != "CLI"
        or authority.authenticated_session
        != reference("effects.denial.authenticated-session", principal_raw)
        or identity.command_id
        != denial_command_id(authority.tenant_id, authority.principal_id, ingress.act_id)
        or identity.fingerprint
        != request_fingerprint(
            ingress, authority, request.expected, request.current.observed_time_ns
        )
        or authority.decision
        != registry.evaluate(ingress, request.expected, authority.tenant_id, authority.principal_id)
        or authority.clock_contract != CLOCK
        or request.current.supported_semantics != registry.semantics
    ):
        raise ValueError("historical denial authority, input or original interpreter differs")
    for name in type(authority.sources).model_fields:
        source = getattr(authority.sources, name)
        if (
            source.source_id != "effects.denial." + name
            or source.source_version != "1"
            or source.owner_id != "broker"
            or source.reader_id != "chiplog.composition.r16_denial_inputs.capture_denial"
            or source.head != reference(source.source_id, source.canonical_value)
            or source.invalidation_manifest
            != reference(
                "effects.denial.invalidators." + name,
                canonical(
                    (
                        registry.reference,
                        name,
                        "trust/storage/Run/session/registry/clock:writer-recapture",
                    )
                ),
            )
        ):
            raise ValueError("historical denial source identity or bytes differ")
    prepared = prepare_denial(request)
    record = denial_record(prepared)
    authentication = batch.authentication
    fence = authority.fence.canonical_bytes()
    if (
        not isinstance(authentication, WorkerAuthentication)
        or authentication.applicability_schema != "chiplog.effects.worker-fence.v1"
        or authentication.applicability_bytes != fence
        or authentication.applicability_fingerprint != digest(fence)
        or authentication.invocation.operation_subject != identity.command_id
        or batch.identity.tenant_id != request.expected.tenant_id
        or batch.identity.command_id != identity.command_id
        or batch.identity.command_fingerprint != digest(raw)
        or batch.identity.canonicalization_version != "chiplog.owner-publication.v1"
        or batch.expected != denial_manifest(request)
        or batch.complete_records != (record,)
        or batch.complete_batch_fingerprint != digest(canonical((record,)))
    ):
        raise ValueError("historical denial singleton publication or exact owner bytes differ")
    previous = next(
        (row for row in reversed(prefix) if row.snapshot.intent.intent_id == ingress.intent_id),
        None,
    )
    _validate_denial_predecessor(prepared.record, previous)
    return request


def _has_denial_signal(batch: RegisteredPublication) -> bool:
    """Route by independent retained signals, without claiming unrelated semantics."""
    legacy = (
        isinstance(batch, SingleOwnerBatch)
        and batch.operation == "effects.before_send"
        and batch.command.schema_id == "chiplog.effects.prepare.v1"
    )
    signal = batch.operation == "effects.before_send" and not legacy
    for command in source_commands(batch):
        if command.schema_id == PREPARATION_SCHEMA:
            signal = True
        if command.owner == "effects":
            value = json.loads(command.canonical_bytes)
            if isinstance(value, dict) and value.get("schema_id") == PREPARATION_SCHEMA:
                signal = True
    for row in batch.complete_records:
        if row.owner != "effects":
            continue
        if not legacy and row.record_kind == "effects.BEFORE_SEND_DISPOSITION":
            signal = True
        body = json.loads(row.canonical_bytes)
        if not isinstance(body, dict):
            continue
        encoded = body.get("source_command")
        if encoded is not None:
            retained = json.loads(base64.b64decode(encoded, altchars=b"-_", validate=True))
            if not isinstance(retained, dict):
                raise ValueError("retained effects command is not an object")
            if retained.get("schema_id") == "chiplog.effects.before-send-command.v2":
                signal = True
        if not legacy and body.get("kind") == "BEFORE_SEND_DISPOSITION":
            signal = True
    return signal


def validate_selected_denials(snapshot: OwnerJournalSnapshot) -> None:
    prefix: list[EffectRecord] = []
    identity = "denial-history"
    try:
        routed = tuple(
            (decision, _has_denial_signal(decision.prepared.request))
            for decision in snapshot.decisions
        )
        if not any(signal for _, signal in routed):
            return
        for decision, denial_signal in routed:
            batch = decision.prepared.request
            identity = batch.identity.command_id
            records = tuple(
                _decode_row(raw, snapshot.tenant_id)
                for raw in batch.complete_records
                if raw.owner == "effects"
            )
            if denial_signal:
                if not isinstance(batch, SingleOwnerBatch):
                    raise ValueError("denial retained in a foreign batch kind")
                validate_denial_batch(batch, tuple(prefix))
                if decision.tenant_commit_sequence != batch.expected.tenant_frontier + 1:
                    raise ValueError("historical denial commit sequence differs")
            prefix.extend(records)
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise OwnerJournalIntegrityError(
            "validate_denial_history", snapshot.tenant_id, identity
        ) from error
