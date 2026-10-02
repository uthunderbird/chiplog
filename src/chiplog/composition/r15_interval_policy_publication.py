"""Broker-owned, separately selected R15.1 interval service administration."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Literal, cast

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_configuration import policy_head, schedule_head
from chiplog.capabilities.deployment_trust._r7_process import _evaluate, _TrustOwnerCall
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.composition.r15_interval_policy import (
    IntervalPolicyAdminSource,
    IntervalPolicyAdoptionRecord,
    IntervalPolicyAdoptionRequest,
    PreparedIntervalPolicyAdoption,
    administrative_policy_head,
    adopted_mandate,
    mandate_reference,
    prepare_interval_policy_adoption,
    selected_mandate,
    validate_historical_interval_policy,
    validate_mandate_configuration,
)
from chiplog.composition.r15_interval_service_auth import IntervalExecutionAuthority
from chiplog.composition.r15_scheduler_authority import (
    SchedulerFreshSources,
    SchedulerPublicationAuthority,
)
from chiplog.composition.r15_scheduler_history import configuration_snapshot
from chiplog.composition.r15_scheduler_registry import (
    SchedulerConfigurationAdoption,
    _canonical,
    _digest,
    _frame,
    capture_generation_sources,
)
from chiplog.composition.r15_tick_contracts import TickSourceCut
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactReplayQuery,
    InvocationProofRef,
    JournalSelectedPublication,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.broker import PublicPortRejected
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    OwnerDecisionJournal,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
)

if TYPE_CHECKING:
    from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime

OPERATION: Literal["r15.adopt_interval_service_policy"] = "r15.adopt_interval_service_policy"
REQUEST_SCHEMA = "chiplog.r15.interval-service-adoption-request.v3"
RECORD_SCHEMA = "chiplog.r15.interval-service-adoption.v3"
EVIDENCE_SCHEMA = "chiplog.r15.interval-service-publication-evidence.v3"


def _source_cut(sources: SchedulerFreshSources) -> TickSourceCut:
    cut = sources.startup.cut
    if cut.independent_journal_head is None:
        raise LoopRejected("interval policy requires selected journal history")
    return TickSourceCut(
        tenant_id=cut.tenant_id,
        database_id=f"{cut.physical_database_path}:{cut.physical_device}:{cut.physical_inode}",
        database_path=cut.physical_database_path,
        database_device=cut.physical_device,
        database_inode=cut.physical_inode,
        tenant_frontier=cut.tenant_frontier,
        materialization_commitment=cut.materialization_commitment,
        independent_journal_head=cut.independent_journal_head,
        deletion_fence_generation=sources.fence_generation,
        deletion_fence_frontier=sources.fence_frontier,
    )


def _source(
    authority: _Authority, request: IntervalPolicyAdoptionRequest
) -> IntervalPolicyAdminSource:
    sources = authority.capture_fresh()
    mandate = adopted_mandate(request)
    configuration = configuration_snapshot(sources.startup, mandate.schedule.schedule_id)
    validate_mandate_configuration(mandate, configuration)
    if (
        configuration.schedule is None
        or configuration.policy is None
        or configuration.bound is None
    ):
        raise LoopRejected("interval policy selected configuration incomplete")
    generation = json.loads(sources.generations.evidence_bytes)
    observed = authority._observed
    return IntervalPolicyAdminSource(
        principal_id=generation["trust_reference"]["principal_id"],
        admin_session_id="hermetic-session",
        administrative_policy=administrative_policy_head(),
        authority_head=sources.startup.registration.reference,
        trust_observation_bytes=_canonical(generation["authentication"]["observation"]),
        trust_request_frame=_canonical(_frame(observed.request)),
        trust_response_frame=_canonical(_frame(observed.response)),
        request_fingerprint=_digest(request.canonical_bytes()),
        cut=_source_cut(sources),
        configuration=configuration,
        selected_schedule=schedule_head(configuration.schedule),
        selected_missed_policy=policy_head(configuration.policy),
        selected_bound=configuration.bound,
    )


def _authority_record(authority: IntervalExecutionAuthority, policy: Present) -> OwnerRecordBytes:
    raw = _canonical(authority.record(policy))
    return OwnerRecordBytes(
        owner="broker_scheduler",
        record_kind="r15.interval-execution-authority",
        record_id="r15-interval-execution-authority-v3:" + _digest(raw),
        schema_id=authority.schema_id,
        canonical_bytes=raw,
        fingerprint=_digest(raw),
    )


def _validate_original_authentication(batch: SingleOwnerBatch, body: dict[str, object]) -> None:
    auth = batch.authentication
    if not isinstance(auth, WorkerAuthentication):
        raise LoopRejected("interval policy original authentication absent")
    generation = json.loads(
        base64.b64decode(cast(str, body["generation_preimages"]), validate=True)
    )
    observed = generation["authentication"]
    sent, response = observed["request"], observed["response"]
    trust = generation["trust_reference"]
    session = generation["broker_generation_preimage"]["agent_session"]
    call_raw = base64.b64decode(sent["canonical_payload_base64"], validate=True)
    call_body = json.loads(call_raw)
    call = _TrustOwnerCall(
        mode=call_body["mode"],
        request_bytes=base64.b64decode(call_body["request_bytes"], validate=True),
        snapshot_bytes=base64.b64decode(call_body["snapshot_bytes"], validate=True),
    )
    result = _evaluate(call)
    trust_request = json.loads(call.request_bytes)
    prepared = PreparedIntervalPolicyAdoption.model_validate_json(_canonical(body["prepared"]))
    record = validate_historical_interval_policy(prepared)
    source = record.source
    if (
        source.configuration.schedule is None
        or source.configuration.policy is None
        or source.configuration.bound is None
    ):
        raise LoopRejected("interval policy original configuration incomplete")
    proof = auth.invocation
    if (
        call.mode != "AUTHENTICATE"
        or call.canonical_bytes() != call_raw
        or call.snapshot_bytes
        != base64.b64decode(observed["observation"]["snapshot_base64"], validate=True)
        or result.disposition != "VALID"
        or result.reference_bytes is None
        or result.canonical_bytes()
        != base64.b64decode(response["canonical_payload_base64"], validate=True)
        or result.canonical_bytes() != base64.b64decode(observed["result_base64"], validate=True)
        or json.loads(result.reference_bytes) != trust
        or trust_request["session_id"] != "hermetic-session"
        or trust_request["credential_id"] != "hermetic-credential"
        or source.trust_request_frame != _canonical(sent)
        or source.trust_response_frame != _canonical(response)
        or source.trust_observation_bytes != _canonical(observed["observation"])
        or sent["operation_id"] != "deployment_trust.authenticate"
        or response["disposition"] != "SUCCESS"
        or response["request_id"] != sent["request_id"]
        or response["responder"] != sent["callee"]
        or sent["callee"]["owner_id"] != "deployment_trust"
        or (trust["tenant_id"], trust["principal_id"], trust["contour"], trust["source_head"])
        != ("hermetic-tenant", "hermetic-principal", "CLI", "local")
        or (
            proof.broker_epoch,
            proof.broker_session,
            proof.runtime_generation,
            proof.operation_subject,
        )
        != (
            str(session["broker_epoch"]),
            session["session_id"],
            session["generation_id"],
            batch.identity.command_id,
        )
        or proof.issuance_fingerprint
        != _digest(
            _canonical(
                [
                    proof.issuance_id,
                    batch.identity.model_dump(mode="json"),
                    batch.command.model_dump(mode="json"),
                    auth.applicability_fingerprint,
                ]
            )
        )
    ):
        raise LoopRejected("interval policy original trust or invocation provenance differs")


def validate_selected_interval_policy(
    batch: RegisteredPublication,
) -> PreparedIntervalPolicyAdoption | None:
    if batch.operation != OPERATION:
        return None
    if (
        not isinstance(batch, SingleOwnerBatch)
        or not isinstance(batch.authentication, WorkerAuthentication)
        or batch.authentication.applicability_schema != EVIDENCE_SCHEMA
        or _digest(batch.authentication.applicability_bytes)
        != batch.authentication.applicability_fingerprint
    ):
        raise LoopRejected("interval policy publication envelope differs")
    body = json.loads(batch.authentication.applicability_bytes)
    if _canonical(body) != batch.authentication.applicability_bytes:
        raise LoopRejected("interval policy publication evidence is not canonical")
    prepared = PreparedIntervalPolicyAdoption.model_validate_json(_canonical(body["prepared"]))
    record = validate_historical_interval_policy(prepared)
    authority = IntervalExecutionAuthority.model_validate_json(
        _canonical(body["execution_authority"])
    )
    request_raw = _canonical(
        {
            "schema": REQUEST_SCHEMA,
            "request": record.request.model_dump(mode="json"),
            "execution_authority": authority.model_dump(mode="json"),
        }
    )
    policy_ref = Present(head=prepared.record_id, fingerprint=prepared.fingerprint)
    expected_records = (
        OwnerRecordBytes(
            owner="broker_scheduler",
            record_kind="r15.interval-service-adoption",
            record_id=prepared.record_id,
            schema_id=RECORD_SCHEMA,
            canonical_bytes=prepared.canonical_record_bytes,
            fingerprint=prepared.fingerprint,
        ),
        _authority_record(authority, policy_ref),
    )
    if (
        batch.identity.tenant_id != record.mandate.tenant_id
        or batch.expected.tenant_id != record.mandate.tenant_id
        or batch.identity.command_id != prepared.command_id
        or batch.identity.command_fingerprint != _digest(request_raw)
        or batch.command.owner != "broker_scheduler"
        or batch.command.schema_id != REQUEST_SCHEMA
        or batch.command.canonical_bytes != request_raw
        or batch.command.fingerprint != _digest(request_raw)
        or batch.complete_records != expected_records
        or batch.complete_batch_fingerprint
        != _digest(
            _canonical(batch.model_dump(mode="json", exclude={"complete_batch_fingerprint"}))
        )
    ):
        raise LoopRejected("interval policy exact selected command or records differ")
    _validate_original_authentication(batch, body)
    return prepared


def validate_selected_interval_policy_decision(
    decision: SelectedOwnerDecision, decisions: tuple[SelectedOwnerDecision, ...]
) -> None:
    prepared = validate_selected_interval_policy(decision.prepared.request)
    if prepared is None:
        return
    record = validate_historical_interval_policy(prepared)
    source = record.source
    request = decision.prepared.request
    if (
        source.configuration.schedule is None
        or source.configuration.policy is None
        or source.configuration.bound is None
    ):
        raise LoopRejected("interval policy original configuration incomplete")
    if (
        source.cut.tenant_frontier != request.expected.tenant_frontier
        or source.cut.materialization_commitment != decision.prepared.predecessor_commitment
        or source.authority_head
        != Present(
            head=request.expected.registry_head, fingerprint=request.expected.registry_fingerprint
        )
    ):
        raise LoopRejected("interval policy selected predecessor differs")
    earlier = tuple(
        d for d in decisions if d.tenant_commit_sequence < decision.tenant_commit_sequence
    )
    if any(item.prepared.request.operation == OPERATION for item in earlier):
        raise LoopRejected("interval service policy was already selected")
    for expected, raw in (
        (source.selected_schedule.head, source.configuration.schedule.canonical_bytes()),
        (source.selected_missed_policy.head, source.configuration.policy.canonical_bytes()),
        (source.selected_bound.head_id, source.configuration.bound.canonical_bytes()),
    ):
        matches = [
            d
            for d in earlier
            if d.prepared.request.operation
            in (
                "scheduler.genesis",
                "scheduler.amend_schedule",
                "scheduler.amend_policy",
                "scheduler.replace_bound",
            )
            and any(
                row.record_id == expected
                and row.fingerprint == _digest(raw)
                and row.canonical_bytes == raw
                for row in d.prepared.request.complete_records
            )
        ]
        if len(matches) != 1:
            raise LoopRejected("interval policy configuration has no exact preceding selection")


class _Authority(SchedulerPublicationAuthority):
    _runtime: R15SchedulerRuntime

    def __init__(
        self, runtime: R15SchedulerRuntime, observed: ObservedTrustCall, command_id: str
    ) -> None:
        super().__init__(
            runtime,
            observed,
            SchedulerConfigurationAdoption(
                adoption_act_id=command_id, command_bytes=b"interval-policy"
            ),
        )
        self._operations = (OPERATION,)
        self._command_id = command_id
        self._originals: list[tuple[PublicationIdentity, OwnerCommandBytes]] = []
        self._evidence_fingerprints: dict[str, str] = {}
        self._recheck: Callable[[], None] | None = None
        self._clock = runtime._tick_clock

    def capture_fresh(self) -> SchedulerFreshSources:
        if self._runtime._tick_clock is not self._clock:
            raise LoopRejected("interval policy registered clock changed")
        return super().capture_fresh()

    def issue_invocation(
        self, identity: PublicationIdentity, command: OwnerCommandBytes
    ) -> InvocationProofRef:
        if (identity, command) not in self._originals:
            raise LoopRejected("interval policy original command was not privately retained")
        session = capture_generation_sources(self._runtime, self._observed).agent_session
        nonce = secrets.token_hex(32)
        proof = InvocationProofRef(
            issuance_id=nonce,
            issuance_fingerprint=_digest(
                _canonical(
                    [
                        nonce,
                        identity.model_dump(mode="json"),
                        command.model_dump(mode="json"),
                        self._evidence_fingerprints[command.fingerprint],
                    ]
                )
            ),
            broker_epoch=str(session.broker_epoch),
            broker_session=session.session_id,
            runtime_generation=session.generation_id,
            operation_subject=identity.command_id,
        )
        self._invocations[nonce] = (proof, identity, command, OPERATION)
        return proof

    def _matches_original(self, request: SingleOwnerBatch) -> bool:
        return validate_selected_interval_policy(request) is not None

    def check_prepared(
        self, prepared: PreparedOwnerPublication
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        result = super().check_prepared(prepared)
        if result is not None:
            return result
        try:
            if self._recheck is None:
                return "DENIED"
            self._recheck()
        except ValueError, LoopRejected:
            return "STALE"
        return None


class _SelectingJournal:
    def __init__(self, journal: OwnerDecisionJournal, authority: _Authority) -> None:
        self._journal, self._authority = journal, authority

    def lookup(self, tenant_id: str, command_id: str) -> SelectedOwnerDecision | None:
        return self._journal.lookup(tenant_id, command_id)

    def select(
        self, prepared: PreparedOwnerPublication, resulting_commitment: str
    ) -> SelectedOwnerDecision:
        refusal = self._authority.check_prepared(prepared)
        if refusal is not None:
            raise LoopRejected(
                "interval policy source or clock changed before selection: " + refusal
            )
        return self._journal.select(prepared, resulting_commitment)

    def materialized(self, decision: SelectedOwnerDecision) -> None:
        self._journal.materialized(decision)


def _manifest(sources: SchedulerFreshSources) -> AuthoritativeReadManifest:
    cut, registry = sources.startup.cut, sources.startup.registration.reference
    values = dict(
        tenant_id=cut.tenant_id,
        tenant_frontier=cut.tenant_frontier,
        expected_materialization_commitment=cut.materialization_commitment,
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        ordered_heads=[],
    )
    return AuthoritativeReadManifest(
        tenant_id=cut.tenant_id,
        tenant_frontier=cut.tenant_frontier,
        expected_materialization_commitment=cut.materialization_commitment,
        registry_head=registry.head,
        registry_fingerprint=registry.fingerprint,
        ordered_heads=(),
        complete_manifest_fingerprint=_digest(_canonical(values)),
    )


async def _authenticate_admin(runtime: R15SchedulerRuntime, peer: str) -> ObservedTrustCall:
    if peer != "hermetic-ingress":
        raise LoopRejected("interval policy requires local hermetic administrator ingress")
    observation = runtime._trust.capture_verified_observation()
    request = runtime._trust_call_request(
        "AUTHENTICATE",
        {
            "contour": "CLI",
            "credential_id": "hermetic-credential",
            "peer_credential": f"uid:{os.getuid()}",
            "session_id": "hermetic-session",
        },
        snapshot_bytes=observation.snapshot_bytes,
    )
    request = request.model_copy(
        update={
            "budget": request.budget.model_copy(
                update={"absolute_deadline_ns": time.monotonic_ns() + 30_000_000_000}
            )
        }
    )
    response = await runtime._supervisor.runtime().call(request)
    if (
        isinstance(response, PublicPortRejected)
        or response.request_id != request.request_id
        or response.responder != request.callee
        or response.schema_id != "chiplog.deployment-trust.owner-result.v1"
    ):
        raise LoopRejected("interval policy administrator authentication IPC rejected")
    observed = ObservedTrustCall(
        observation, request, response, runtime._decode_trust_result(response.canonical_payload)
    )
    capture_generation_sources(runtime, observed)
    return observed


def selected_interval_policy(
    runtime: R15SchedulerRuntime,
) -> tuple[PreparedIntervalPolicyAdoption, Present]:
    prepared, _, _ = selected_interval_policy_from_decisions(
        runtime._owner_decisions().snapshot().decisions
    )
    return prepared, Present(head=prepared.record_id, fingerprint=prepared.fingerprint)


def selected_interval_authority(runtime: R15SchedulerRuntime) -> IntervalExecutionAuthority:
    _, authority, _ = selected_interval_policy_from_decisions(
        runtime._owner_decisions().snapshot().decisions
    )
    return authority


def selected_interval_policy_from_decisions(
    decisions: tuple[SelectedOwnerDecision, ...],
) -> tuple[PreparedIntervalPolicyAdoption, IntervalExecutionAuthority, SelectedOwnerDecision]:
    """Read the one exact selected adoption and its separately recorded public key."""
    candidates = [d for d in decisions if d.prepared.request.operation == OPERATION]
    if len(candidates) != 1:
        raise LoopRejected("one separately selected interval service policy is required")
    decision = candidates[0]
    prepared = validate_selected_interval_policy(decision.prepared.request)
    authentication = decision.prepared.request.authentication
    if prepared is None or not isinstance(authentication, WorkerAuthentication):
        raise LoopRejected("selected interval service adoption differs")
    body = json.loads(authentication.applicability_bytes)
    authority = IntervalExecutionAuthority.model_validate_json(
        _canonical(body["execution_authority"])
    )
    reference = Present(head=prepared.record_id, fingerprint=prepared.fingerprint)
    if decision.prepared.request.complete_records[1] != _authority_record(authority, reference):
        raise LoopRejected("selected interval execution authority differs")
    return prepared, authority, decision


def validate_selected_interval_service_decision(
    decision: SelectedOwnerDecision, decisions: tuple[SelectedOwnerDecision, ...]
) -> None:
    """Join service evidence to a preceding selected policy and its public key."""
    request = decision.prepared.request
    if (
        request.operation != "scheduler.decide_interval"
        or not isinstance(request.authentication, WorkerAuthentication)
        or request.authentication.applicability_schema
        != "chiplog.scheduler.interval-service-issuance.v3"
    ):
        return
    from chiplog.composition.r15_interval_service import validate_service_interval_batch

    if not isinstance(request, SingleOwnerBatch):
        raise LoopRejected("selected interval service envelope differs")
    evidence = validate_service_interval_batch(request)
    earlier = tuple(
        item for item in decisions if item.tenant_commit_sequence < decision.tenant_commit_sequence
    )
    prepared, authority, selected = selected_interval_policy_from_decisions(earlier)
    record = validate_historical_interval_policy(prepared)
    reference = Present(head=prepared.record_id, fingerprint=prepared.fingerprint)
    prior_cycles = sum(
        1
        for item in earlier
        if item.prepared.request.operation == "scheduler.decide_interval"
        and isinstance(item.prepared.request.authentication, WorkerAuthentication)
        and item.prepared.request.authentication.applicability_schema
        == "chiplog.scheduler.interval-service-issuance.v3"
        and validate_service_interval_batch(item.prepared.request).selected_policy == reference
    )
    if (
        selected.tenant_commit_sequence >= decision.tenant_commit_sequence
        or evidence.selected_policy != reference
        or evidence.selected_mandate_bytes != selected_mandate(record.request).canonical_bytes()
        or evidence.execution_identity_bytes != authority.canonical_bytes()
        or evidence.schedule_id != record.mandate.schedule.schedule_id
        or prior_cycles >= record.mandate.max_cycles
    ):
        raise LoopRejected(
            "interval service evidence differs from preceding selected administration"
        )


async def adopt_interval_policy(
    runtime: R15SchedulerRuntime,
    peer: str,
    request: IntervalPolicyAdoptionRequest,
    execution_authority: IntervalExecutionAuthority,
) -> BrokerPublicationResult:
    request = IntervalPolicyAdoptionRequest.model_validate_json(request.canonical_bytes())
    execution_authority = IntervalExecutionAuthority.model_validate_json(
        execution_authority.canonical_bytes()
    )
    raw = _canonical(
        {
            "schema": REQUEST_SCHEMA,
            "request": request.model_dump(mode="json"),
            "execution_authority": execution_authority.model_dump(mode="json"),
        }
    )
    observed = await _authenticate_admin(runtime, peer)
    authority = _Authority(
        runtime, observed, "r15-interval-service-adopt:" + request.adoption_act_id
    )
    coordinator = BrokerPublicationCoordinator(
        runtime._appender, authority, runtime._owner_decisions()
    )
    with runtime._authority_gate().hold():
        authority.capture_fresh()
        selected = runtime._owner_decisions().lookup(runtime._tenant_id, authority._command_id)
        if selected is not None:
            original = selected.prepared.request
            if not isinstance(original, SingleOwnerBatch) or not isinstance(
                original.authentication, WorkerAuthentication
            ):
                raise LoopRejected("selected interval policy envelope differs")
            validate_selected_interval_policy(original)
            if original.command.canonical_bytes != raw:
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=runtime._tenant_id,
                    command_id=authority._command_id,
                    reason="changed exact interval policy command",
                )
            authority._originals.append((original.identity, original.command))
            authority._evidence_fingerprints[original.command.fingerprint] = (
                original.authentication.applicability_fingerprint
            )
            proof = authority.issue_invocation(original.identity, original.command)
            result = coordinator.lookup_exact(
                ExactReplayQuery(
                    identity=original.identity,
                    operation=original.operation,
                    current_invocation=proof,
                    original_commands=(original.command,),
                )
            )
            if isinstance(result, JournalSelectedPublication):
                return result
            if not isinstance(result, PublicationRejected):
                raise LoopRejected("selected interval policy has no exact replay")
            if result.kind != "HOLD" or authority.materialization_state(selected) != "ABSENT":
                return result
    if selected is not None:
        return await coordinator.recover_selected(runtime._tenant_id, authority._command_id)
    if any(
        d.prepared.request.operation == OPERATION
        for d in runtime._owner_decisions().snapshot().decisions
    ):
        raise LoopRejected("interval service policy already selected")

    mandate = adopted_mandate(request)
    sources = authority.capture_fresh()
    source = _source(authority, request)
    reading = runtime._tick_clock.observe()
    record = IntervalPolicyAdoptionRecord(
        request=request,
        mandate=mandate,
        mandate_head=mandate_reference(mandate),
        source=source,
        reading=reading,
        valid_until_monotonic_ns=reading.monotonic_ns + 30_000_000_000,
    )
    prepared = prepare_interval_policy_adoption(record)

    def recheck() -> None:
        if _source(authority, request) != source:
            raise LoopRejected("interval policy selected source changed")
        now = runtime._tick_clock.observe()
        if (
            now.unix_ns < reading.unix_ns
            or now.monotonic_ns < reading.monotonic_ns
            or now.monotonic_ns >= record.valid_until_monotonic_ns
            or now.unix_ns >= mandate.expires_at_unix_ns
        ):
            raise LoopRejected("interval policy clock changed or expired")

    authority._recheck = recheck
    sources = authority.capture_fresh()
    identity = PublicationIdentity(
        tenant_id=runtime._tenant_id,
        command_id=prepared.command_id,
        command_fingerprint=_digest(raw),
        canonicalization_version="chiplog.owner-publication.v1",
    )
    command = OwnerCommandBytes(
        owner="broker_scheduler",
        schema_id=REQUEST_SCHEMA,
        canonical_bytes=raw,
        fingerprint=_digest(raw),
    )
    authority._originals.append((identity, command))
    evidence = _canonical(
        {
            "prepared": prepared.model_dump(mode="json"),
            "execution_authority": execution_authority.model_dump(mode="json"),
            "generation_preimages": base64.b64encode(sources.generations.evidence_bytes).decode(),
        }
    )
    authority._evidence_fingerprints[command.fingerprint] = _digest(evidence)
    authentication = WorkerAuthentication(
        invocation=authority.issue_invocation(identity, command),
        applicability_schema=EVIDENCE_SCHEMA,
        applicability_bytes=evidence,
        applicability_fingerprint=_digest(evidence),
    )
    records = (
        OwnerRecordBytes(
            owner="broker_scheduler",
            record_kind="r15.interval-service-adoption",
            record_id=prepared.record_id,
            schema_id=RECORD_SCHEMA,
            canonical_bytes=prepared.canonical_record_bytes,
            fingerprint=prepared.fingerprint,
        ),
        _authority_record(
            execution_authority, Present(head=prepared.record_id, fingerprint=prepared.fingerprint)
        ),
    )
    expected = _manifest(sources)
    values = dict(
        kind="SINGLE_OWNER",
        operation=OPERATION,
        identity=identity.model_dump(mode="json"),
        authentication=authentication.model_dump(mode="json"),
        expected=expected.model_dump(mode="json"),
        command=command.model_dump(mode="json"),
        complete_records=[r.model_dump(mode="json") for r in records],
    )
    batch = SingleOwnerBatch(
        operation=OPERATION,
        identity=identity,
        authentication=authentication,
        expected=expected,
        command=command,
        complete_records=records,
        complete_batch_fingerprint=_digest(_canonical(values)),
    )
    validate_selected_interval_policy(batch)
    authority.register_prepared(batch, sources)
    return await BrokerPublicationCoordinator(
        runtime._appender, authority, _SelectingJournal(runtime._owner_decisions(), authority)
    ).commit(batch)
