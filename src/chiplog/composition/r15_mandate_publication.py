"""Authenticated local administrator adoption of one bounded execution mandate."""

from __future__ import annotations

import base64
import json
import secrets
from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present, TrustedClockProofRef
from chiplog.capabilities.agent_loop.scheduler_contracts import SchedulerContextRef
from chiplog.capabilities.agent_loop.scheduler_execution_contracts import (
    ScheduledSystemMandateScopeV2,
    ScheduledSystemMandateV2,
)
from chiplog.capabilities.agent_loop.scheduler_mandate_lifecycle_contracts import (
    IssueScheduledMandateV1,
    MandateAdministrativeSourceV1,
    MandateGenesisAbsencesV1,
    ProposedScheduledSystemMandateV2,
)
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BATCH_SCHEMA,
    BatchPreparationCut,
    MandateIssuancePreparationRequest,
    PreparedSchedulerBatch,
    prepare_mandate_issuance,
)
from chiplog.composition.r15_mandate_contracts import (
    MandateClockPreview,
    MandateIssuanceAdoption,
)
from chiplog.composition.r15_mandate_scope_registry import (
    EXECUTION_SERVICE_IDENTITY,
    RegisteredMandateScopeSources,
    registered_scope_sources,
    validate_closed_scope,
)
from chiplog.composition.r15_scheduler_authority import (
    SchedulerFreshSources,
    SchedulerPublicationAuthority,
)
from chiplog.composition.r15_scheduler_history import configuration_snapshot, read_loop_snapshot
from chiplog.composition.r15_scheduler_publication import _authenticate
from chiplog.composition.r15_scheduler_registry import (
    SchedulerConfigurationAdoption,
    _canonical,
    _digest,
    _frame,
    capture_generation_sources,
)
from chiplog.composition.r15_tick_clock_v1 import registered_clock_source
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    BrokerPublicationResult,
    ExactRecordHead,
    ExactReplayQuery,
    InvocationProofRef,
    JournalSelectedPublication,
    ObservedAbsence,
    ObservedPresence,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    PublicationRejected,
    SingleOwnerBatch,
    WorkerAuthentication,
)
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortRejected
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
)

if TYPE_CHECKING:
    from chiplog.composition.r7_planning import ObservedTrustCall
    from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime

_OPERATION = "scheduler.issue_mandate"
_PREPARATION_SCHEMA = "chiplog.scheduler.mandate-issuance-preparation.v1"
_ISSUANCE_SCHEMA = "chiplog.scheduler.mandate-issuance-evidence.v1"


def _command_id(act: str) -> str:
    return "r15-mandate-issue:" + _digest(_canonical(["hermetic-tenant", act]))


def _adopted_scope(adoption: MandateIssuanceAdoption) -> ScheduledSystemMandateScopeV2:
    scope = ScheduledSystemMandateScopeV2.model_validate_json(adoption.canonical_scope_bytes)
    if scope.canonical_bytes() != adoption.canonical_scope_bytes:
        raise LoopRejected("mandate scope adoption is not canonical")
    if adoption.canonical_policy_bytes != registered_scope_sources().administrative_policy_bytes:
        raise LoopRejected("mandate administrative issuance policy differs")
    return scope


def _call_head(subject: str, ref: Present) -> CallSubjectHead:
    return CallSubjectHead(subject_id=subject, revision=ref)


def _admin_source(
    runtime: R15SchedulerRuntime,
    observed: ObservedTrustCall,
    adoption: MandateIssuanceAdoption,
    sources: SchedulerFreshSources,
    scope: ScheduledSystemMandateScopeV2,
    preview: MandateClockPreview,
    registered: RegisteredMandateScopeSources,
) -> MandateAdministrativeSourceV1:
    cut, generations = sources.startup.cut, sources.generations
    evidence = json.loads(generations.evidence_bytes)
    trusted = evidence["verified_trust"]
    if (
        evidence["trust_reference"]["principal_id"] != "hermetic-principal"
        or evidence["trust_reference"]["tenant_id"] != "hermetic-tenant"
    ):
        raise LoopRejected("authenticated local administrator differs from issuance policy")
    act_digest = _digest(adoption.canonical_bytes())
    cut_bytes = _canonical(
        {
            "tenant": cut.tenant_id,
            "frontier": cut.tenant_frontier,
            "commitment": cut.materialization_commitment,
            "journal": cut.independent_journal_head,
            "physical": [cut.physical_database_path, cut.physical_device, cut.physical_inode],
            "generation_evidence": _digest(generations.evidence_bytes),
            "scope_sources": [
                registered.recipient.model_dump(mode="json"),
                registered.delivery.model_dump(mode="json"),
                registered.disclosure.model_dump(mode="json"),
            ],
        }
    )
    frames = (
        _canonical(_frame(observed.request)),
        _canonical(_frame(observed.response)),
        _canonical(evidence["authentication"]["observation"]),
    )
    body = _canonical(
        {
            "schema": "chiplog.scheduler.mandate-administrative-source.v1",
            "act": adoption.model_dump(mode="json"),
            "scope": scope.model_dump(mode="json"),
            "policy": registered.administrative_policy.model_dump(mode="json"),
            "cut": base64.b64encode(cut_bytes).decode(),
            "frames": [base64.b64encode(frame).decode() for frame in frames],
            "clock": preview.model_dump(mode="json"),
        }
    )
    digest = _digest(body)
    trust_body = _canonical(trusted)
    trust_ref = Present(head=trusted["trust_head"], fingerprint=_digest(trust_body))
    return MandateAdministrativeSourceV1(
        authenticated_admin_act=Present(head="r15-admin-act:" + act_digest, fingerprint=act_digest),
        authority_registry=_call_head(
            "r15-scheduler-source-registry", sources.startup.registration.reference
        ),
        current_authority_head=_call_head(
            "r15-current-authority",
            Present(
                head=generations.authority_epoch,
                fingerprint=_digest(generations.authority_epoch.encode()),
            ),
        ),
        issuance_policy=registered.administrative_policy,
        tenant_id=runtime._tenant_id,
        database_id=f"{cut.physical_database_path}:{cut.physical_device}:{cut.physical_inode}",
        active_contour=_call_head("r15-active-contour", trust_ref),
        trusted_clock=preview.proof,
        source_cut_fingerprint=_digest(cut_bytes),
        broker_session_id=generations.agent_session.session_id,
        canonical_source_frames=frames,
        source_record_id="r15-admin-source:" + digest,
        canonical_source_bytes=body,
        source_reference=Present(head="r15-admin-source:" + digest, fingerprint=digest),
        semantic_scope=scope,
    )


def _validate_clock(
    runtime: R15SchedulerRuntime, preview: MandateClockPreview, observed: ObservedTrustCall
) -> None:
    if registered_clock_source(runtime._tick_clock) != preview.source:
        raise LoopRejected("mandate registered clock source changed")
    now = runtime._tick_clock.observe()
    if (
        now.unix_ns < preview.reading.unix_ns
        or now.monotonic_ns < preview.reading.monotonic_ns
        or now.monotonic_ns >= preview.valid_until_monotonic_ns
        or now.monotonic_ns >= observed.request.budget.absolute_deadline_ns
    ):
        raise LoopRejected("mandate issuance clock regressed or expired")


def _clock_fence(sources: SchedulerFreshSources) -> str:
    cut = sources.startup.cut
    generations = sources.generations
    return _digest(
        _canonical(
            {
                "tenant": cut.tenant_id,
                "frontier": cut.tenant_frontier,
                "commitment": cut.materialization_commitment,
                "journal": cut.independent_journal_head,
                "physical": [cut.physical_database_path, cut.physical_device, cut.physical_inode],
                "registry": sources.startup.registration.reference.model_dump(mode="json"),
                "authority_epoch": generations.authority_epoch,
                "broker_generation": generations.broker_generation,
                "runtime_graph_generation": generations.runtime_graph_generation,
            }
        )
    )


async def preview_mandate_clock(
    runtime: R15SchedulerRuntime,
    peer: str,
    adoption_act_id: str,
    scope_without_proof_bytes: bytes,
) -> MandateClockPreview:
    try:
        proposed = json.loads(scope_without_proof_bytes)
        if (
            not isinstance(proposed, dict)
            or not isinstance(proposed.get("horizon"), dict)
            or "issuance_clock_proof" in proposed["horizon"]
            or _canonical(proposed) != scope_without_proof_bytes
        ):
            raise ValueError("mandate scope proposal is not canonical without proof")
    except (ValueError, TypeError) as error:
        raise LoopRejected("invalid mandate scope proposal") from error
    observed = await _authenticate(runtime, peer)
    authority = SchedulerPublicationAuthority(
        runtime,
        observed,
        SchedulerConfigurationAdoption(adoption_act_id=adoption_act_id, command_bytes=b"preview"),
    )
    sources = authority.capture_fresh()
    generations = sources.generations
    source = registered_clock_source(runtime._tick_clock)
    reading = runtime._tick_clock.observe()
    nonce = secrets.token_hex(32)
    proof = TrustedClockProofRef(
        proof_id="r15-mandate-clock:" + nonce,
        proof_fingerprint=_digest(
            _canonical(
                {
                    "nonce": nonce,
                    "source": source.model_dump(mode="json"),
                    "reading": reading.model_dump(mode="json"),
                    "generation": _digest(generations.evidence_bytes),
                }
            )
        ),
        proof_version="chiplog.scheduler.mandate-clock-proof.v1",
        clock_contract_version=source.contract_version,
        fence_fingerprint=_clock_fence(sources),
        command_id=_command_id(adoption_act_id),
        command_payload_fingerprint=_digest(scope_without_proof_bytes),
        submission_id="r15-mandate-submission:" + nonce,
    )
    proposed["horizon"]["issuance_clock_proof"] = proof.model_dump(mode="json")
    scope = ScheduledSystemMandateScopeV2.model_validate_json(_canonical(proposed))
    if len(sources.startup.index.schedules) != 1:
        raise LoopRejected("mandate preview requires one selected schedule")
    current = configuration_snapshot(
        sources.startup, sources.startup.index.schedules[0].schedule_id
    )
    validate_closed_scope(scope, current, registered_scope_sources())
    preview = MandateClockPreview(
        adoption_act_id=adoption_act_id,
        canonical_scope_bytes=scope.canonical_bytes(),
        proof=proof,
        source=source,
        reading=reading,
        valid_until_monotonic_ns=min(
            reading.monotonic_ns + 30_000_000_000,
            observed.request.budget.absolute_deadline_ns,
        ),
    )
    runtime._mandate_clock_issuances[proof.proof_id] = (preview, observed)
    return preview


def _validate_selected_batch(batch: SingleOwnerBatch) -> MandateIssuanceAdoption:
    if (
        batch.operation != _OPERATION
        or not isinstance(batch.authentication, WorkerAuthentication)
        or batch.authentication.applicability_schema != _ISSUANCE_SCHEMA
        or _digest(batch.authentication.applicability_bytes)
        != batch.authentication.applicability_fingerprint
        or batch.command.schema_id != _PREPARATION_SCHEMA
    ):
        raise ValueError("unregistered mandate issuance envelope")
    evidence = json.loads(batch.authentication.applicability_bytes)
    adoption = MandateIssuanceAdoption.model_validate_json(_canonical(evidence["adoption"]))
    adopted_scope = _adopted_scope(adoption)
    request = MandateIssuancePreparationRequest.model_validate_json(batch.command.canonical_bytes)
    issued = IssueScheduledMandateV1.model_validate_json(request.command_bytes)
    admin = MandateAdministrativeSourceV1.model_validate_json(
        _canonical(evidence["administrative_source"])
    )
    source_body = json.loads(admin.canonical_source_bytes)
    if (
        evidence["schema"] != _ISSUANCE_SCHEMA
        or evidence["policy_bytes"]
        != base64.b64encode(registered_scope_sources().administrative_policy_bytes).decode()
        or request.operation != batch.operation
        or request.canonical_bytes() != batch.command.canonical_bytes
        or request.cut.registry_head != batch.expected.registry_head
        or request.cut.registry_fingerprint != batch.expected.registry_fingerprint
        or request.cut.tenant_frontier != batch.expected.tenant_frontier
        or request.cut.materialization_commitment
        != batch.expected.expected_materialization_commitment
        or batch.identity.command_id != _command_id(adoption.adoption_act_id)
        or batch.identity.command_fingerprint != _digest(request.command_bytes)
        or request.command_bytes != issued.canonical_bytes()
        or issued.proposed_mandate.mandate.scope != adopted_scope
        or issued.administrative_source != admin
        or source_body["act"] != adoption.model_dump(mode="json")
        or source_body["scope"] != adopted_scope.model_dump(mode="json")
        or source_body["policy"]
        != registered_scope_sources().administrative_policy.model_dump(mode="json")
        or source_body["clock"] != evidence["preview"]
        or tuple(base64.b64decode(frame, validate=True) for frame in source_body["frames"])
        != admin.canonical_source_frames
        or admin.issuance_policy != registered_scope_sources().administrative_policy
        or admin.trusted_clock != adopted_scope.horizon.issuance_clock_proof
        or tuple(batch.complete_records)
        != tuple(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind=row.record_kind,
                record_id=row.record_id,
                schema_id=row.schema_id,
                canonical_bytes=base64.b64decode(row.canonical_base64, validate=True),
                fingerprint=row.fingerprint,
            )
            for row in prepare_mandate_issuance(request).records
        )
    ):
        raise ValueError("selected mandate issuance differs from original adopted source")
    return adoption


class MandatePublicationAuthority(SchedulerPublicationAuthority):
    def __init__(
        self,
        runtime: R15SchedulerRuntime,
        observed: ObservedTrustCall,
        adoption: MandateIssuanceAdoption,
        preview: MandateClockPreview | None,
    ) -> None:
        self._runtime = runtime
        self._observed = observed
        self._adoption = adoption
        self._preview = preview
        self._is_genesis = False
        self._operations = (_OPERATION,)
        self._command_id = _command_id(adoption.adoption_act_id)
        self._invocations = {}
        self._sources = []
        self._prepared = []
        self._originals: list[tuple[PublicationIdentity, OwnerCommandBytes]] = []
        self._original_admin: MandateAdministrativeSourceV1 | None = None

    def retain_original(self, batch: SingleOwnerBatch) -> None:
        if _validate_selected_batch(batch) != self._adoption:
            raise LoopRejected("selected mandate issuance belongs to another adoption")
        self._originals.append((batch.identity, batch.command))

    def capture_fresh(self) -> SchedulerFreshSources:
        sources = super().capture_fresh()
        scope = _adopted_scope(self._adoption)
        if len(sources.startup.index.schedules) != 1:
            raise LoopRejected("mandate issuance requires one selected schedule")
        current = configuration_snapshot(
            sources.startup, sources.startup.index.schedules[0].schedule_id
        )
        validate_closed_scope(scope, current, registered_scope_sources())
        if any(
            item.mandate.scope.mandate_id == scope.mandate_id
            for item in sources.startup.index.mandates
        ):
            raise LoopRejected("mandate identity already selected")
        if self._preview is not None:
            if scope.horizon.issuance_clock_proof != self._preview.proof:
                raise LoopRejected("mandate adopted clock proof differs")
            if (
                self._preview.canonical_scope_bytes != self._adoption.canonical_scope_bytes
                or self._preview.adoption_act_id != self._adoption.adoption_act_id
                or self._preview.proof.fence_fingerprint != _clock_fence(sources)
                or self._preview.proof.command_id != self._command_id
            ):
                raise LoopRejected("mandate preview scope or source fence changed")
            _validate_clock(self._runtime, self._preview, self._observed)
            if scope.horizon.expires_at_unix_ns <= self._runtime._tick_clock.observe().unix_ns:
                raise LoopRejected("mandate horizon already expired")
            if (
                self._original_admin is not None
                and _admin_source(
                    self._runtime,
                    self._observed,
                    self._adoption,
                    sources,
                    scope,
                    self._preview,
                    registered_scope_sources(),
                )
                != self._original_admin
            ):
                raise LoopRejected("mandate administrative source changed at writer cut")
        return sources

    def issue_invocation(
        self, identity: PublicationIdentity, command: OwnerCommandBytes
    ) -> InvocationProofRef:
        if (identity, command) not in self._originals:
            raise LoopRejected("mandate invocation has no privately retained original")
        session = capture_generation_sources(self._runtime, self._observed).agent_session
        nonce = secrets.token_hex(32)
        proof = InvocationProofRef(
            issuance_id=nonce,
            issuance_fingerprint=_digest(
                _canonical(
                    {
                        "nonce": nonce,
                        "identity": identity.model_dump(mode="json"),
                        "command": command.model_dump(mode="json"),
                    }
                )
            ),
            broker_epoch=str(session.broker_epoch),
            broker_session=session.session_id,
            runtime_generation=session.generation_id,
            operation_subject=identity.command_id,
        )
        self._invocations[nonce] = (proof, identity, command, _OPERATION)
        return proof

    def _matches_original(self, request: SingleOwnerBatch) -> bool:
        return _validate_selected_batch(request) == self._adoption


def _manifest(request: MandateIssuancePreparationRequest) -> AuthoritativeReadManifest:
    cut = request.cut
    command = IssueScheduledMandateV1.model_validate_json(request.command_bytes)
    scope = command.proposed_mandate.mandate.scope
    heads = (
        *(
            ObservedPresence(
                head=ExactRecordHead(
                    owner="agent_loop",
                    record_kind=kind,
                    subject_id=scope.schedule_head.subject_id,
                    record_id=ref.revision.head,
                    fingerprint=ref.revision.fingerprint,
                )
            )
            for kind, ref in (
                ("scheduler.schedule-definition", scope.schedule_head),
                ("scheduler.missed-policy", scope.missed_policy_head),
                ("scheduler.interval-bound", scope.bound_head),
            )
        ),
        *(
            ObservedAbsence(owner="agent_loop", record_kind=kind, subject_id=scope.mandate_id)
            for kind in (
                "scheduler.system-mandate",
                "scheduler.mandate-budget",
                "scheduler.mandate-revocation",
            )
        ),
    )
    values = {
        "tenant_id": cut.tenant_id,
        "tenant_frontier": cut.tenant_frontier,
        "expected_materialization_commitment": cut.materialization_commitment,
        "registry_head": cut.registry_head,
        "registry_fingerprint": cut.registry_fingerprint,
        "ordered_heads": [item.model_dump(mode="json") for item in heads],
    }
    return AuthoritativeReadManifest(
        tenant_id=cut.tenant_id,
        tenant_frontier=cut.tenant_frontier,
        expected_materialization_commitment=cut.materialization_commitment,
        registry_head=cut.registry_head,
        registry_fingerprint=cut.registry_fingerprint,
        ordered_heads=heads,
        complete_manifest_fingerprint=_digest(_canonical(values)),
    )


async def publish_mandate(
    runtime: R15SchedulerRuntime, peer: str, adoption: MandateIssuanceAdoption
) -> BrokerPublicationResult:
    adoption = MandateIssuanceAdoption.model_validate_json(adoption.canonical_bytes())
    scope = _adopted_scope(adoption)
    observed = await _authenticate(runtime, peer)
    retained = runtime._mandate_clock_issuances.get(scope.horizon.issuance_clock_proof.proof_id)
    preview = retained[0] if retained is not None else None
    if preview is not None:
        before = json.loads(retained[1].result.reference_bytes)
        after = json.loads(observed.result.reference_bytes)
        stable = (
            "tenant_id",
            "principal_id",
            "contour",
            "credential_head",
            "session_head",
            "trust_head",
            "materialization_head",
            "source_head",
            "peer_credential",
        )
        if any(before[key] != after[key] for key in stable):
            raise LoopRejected("mandate preview administrator changed")
    authority = MandatePublicationAuthority(runtime, observed, adoption, preview)
    journal = runtime._owner_decisions()
    coordinator = BrokerPublicationCoordinator(runtime._appender, authority, journal)
    command_id = _command_id(adoption.adoption_act_id)
    with runtime._authority_gate().hold():
        selected = journal.lookup(runtime._tenant_id, command_id)
        if selected is not None:
            original = selected.prepared.request
            if not isinstance(original, SingleOwnerBatch):
                raise LoopRejected("selected mandate envelope differs")
            try:
                authority.retain_original(original)
            except ValueError, LoopRejected:
                return PublicationRejected(
                    kind="CONFLICT",
                    tenant_id=runtime._tenant_id,
                    command_id=command_id,
                    reason="changed exact mandate adoption",
                )
            invocation = authority.issue_invocation(original.identity, original.command)
            replay = coordinator.lookup_exact(
                ExactReplayQuery(
                    identity=original.identity,
                    operation=original.operation,
                    current_invocation=invocation,
                    original_commands=(original.command,),
                )
            )
            if isinstance(replay, JournalSelectedPublication):
                read_loop_snapshot(runtime)
                return replay
            if not isinstance(replay, PublicationRejected):
                raise LoopRejected("selected mandate has no exact replay")
            if replay.kind != "HOLD" or authority.materialization_state(selected) != "ABSENT":
                return replay
    if selected is not None:
        result = await coordinator.recover_selected(runtime._tenant_id, command_id)
        if isinstance(result, JournalSelectedPublication):
            read_loop_snapshot(runtime)
        return result
    if preview is None:
        raise LoopRejected("mandate clock proof was not privately issued")
    sources = authority.capture_fresh()
    registered = registered_scope_sources()
    admin = _admin_source(runtime, observed, adoption, sources, scope, preview, registered)
    authority._original_admin = admin
    mandate = ScheduledSystemMandateV2(scope=scope)
    raw_mandate = mandate.canonical_bytes()
    record_id = "r15-system-mandate:" + _digest(_canonical([scope.mandate_id, command_id]))
    command = IssueScheduledMandateV1(
        command_id=command_id,
        mandate_id=scope.mandate_id,
        proposed_mandate=ProposedScheduledSystemMandateV2(
            proposed_record_id=record_id,
            canonical_mandate_bytes=raw_mandate,
            mandate=mandate,
            external_reference=Present(head=record_id, fingerprint=_digest(raw_mandate)),
        ),
        expected_absences=MandateGenesisAbsencesV1(
            mandate_record=Absent(), budget_record=Absent(), revocation_record=Absent()
        ),
        administrative_source=admin,
    )
    nonce = secrets.token_hex(32)
    cut = sources.startup.cut
    context = SchedulerContextRef(
        tenant_id=runtime._tenant_id,
        service_identity=EXECUTION_SERVICE_IDENTITY,
        session_id=sources.generations.agent_session.session_id,
        mandate_head=registered.administrative_policy.revision.head,
        issuance_id="r15-mandate-context:" + nonce,
        issuance_fingerprint=_digest(
            _canonical({"nonce": nonce, "admin": admin.model_dump(mode="json")})
        ),
    )
    request = MandateIssuancePreparationRequest(
        context=context,
        cut=BatchPreparationCut(
            tenant_id=cut.tenant_id,
            tenant_frontier=cut.tenant_frontier,
            materialization_commitment=cut.materialization_commitment,
            registry_head=sources.startup.registration.reference.head,
            registry_fingerprint=sources.startup.registration.reference.fingerprint,
            submission_id=preview.proof.submission_id,
            authorized_context=context,
            authorized_command_fingerprint=_digest(command.canonical_bytes()),
            authority_proof=Present(
                head="r15-mandate-cut:" + nonce, fingerprint=admin.source_cut_fingerprint
            ),
        ),
        command_bytes=command.canonical_bytes(),
    )
    session = sources.generations.agent_session
    sent = PublicPortCall(
        operation_id="scheduler.prepare_mandate_issuance",
        request_id="mandate:" + nonce,
        caller=BrokerSession(
            tenant_id=runtime._tenant_id,
            broker_epoch=session.broker_epoch,
            generation_id=session.generation_id,
            owner_id="broker",
            session_id=f"broker:{session.generation_id}",
        ),
        callee=session,
        schema_id=_PREPARATION_SCHEMA,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=observed.request.budget.absolute_deadline_ns,
        ),
    )
    response = await runtime._supervisor.runtime().call(sent)
    with runtime._authority_gate().hold():
        if authority.capture_fresh() != sources:
            raise LoopRejected("mandate administrative source changed during preparation")
        if (
            isinstance(response, PublicPortRejected)
            or response.request_id != sent.request_id
            or response.responder != sent.callee
            or response.schema_id != BATCH_SCHEMA
            or response.canonical_payload != prepare_mandate_issuance(request).canonical_bytes()
        ):
            raise LoopRejected(
                f"mandate owner response differs from original preparation: {response!r}"
            )
        prepared = PreparedSchedulerBatch.model_validate_json(response.canonical_payload)
        evidence = _canonical(
            {
                "schema": _ISSUANCE_SCHEMA,
                "adoption": adoption.model_dump(mode="json"),
                "policy_bytes": base64.b64encode(registered.administrative_policy_bytes).decode(),
                "administrative_source": admin.model_dump(mode="json"),
                "preview": preview.model_dump(mode="json"),
                "owner_request": _frame(sent),
                "owner_response": _frame(response),
            }
        )
        identity = PublicationIdentity(
            tenant_id=runtime._tenant_id,
            command_id=command_id,
            command_fingerprint=_digest(request.command_bytes),
            canonicalization_version="chiplog.owner-publication.v1",
        )
        owner_command = OwnerCommandBytes(
            owner="agent_loop",
            schema_id=_PREPARATION_SCHEMA,
            canonical_bytes=request.canonical_bytes(),
            fingerprint=_digest(request.canonical_bytes()),
        )
        authority._originals.append((identity, owner_command))
        invocation = authority.issue_invocation(identity, owner_command)
        authentication = WorkerAuthentication(
            invocation=invocation,
            applicability_schema=_ISSUANCE_SCHEMA,
            applicability_bytes=evidence,
            applicability_fingerprint=_digest(evidence),
        )
        records = tuple(
            OwnerRecordBytes(
                owner="agent_loop",
                record_kind=row.record_kind,
                record_id=row.record_id,
                schema_id=row.schema_id,
                canonical_bytes=base64.b64decode(row.canonical_base64, validate=True),
                fingerprint=row.fingerprint,
            )
            for row in prepared.records
        )
        expected = _manifest(request)
        values = {
            "kind": "SINGLE_OWNER",
            "operation": _OPERATION,
            "identity": identity.model_dump(mode="json"),
            "authentication": authentication.model_dump(mode="json"),
            "expected": expected.model_dump(mode="json"),
            "command": owner_command.model_dump(mode="json"),
            "complete_records": [row.model_dump(mode="json") for row in records],
        }
        batch = SingleOwnerBatch(
            operation=_OPERATION,
            identity=identity,
            authentication=authentication,
            expected=expected,
            command=owner_command,
            complete_records=records,
            complete_batch_fingerprint=_digest(_canonical(values)),
        )
        _validate_selected_batch(batch)
        authority.register_prepared(batch, sources)
    result = await coordinator.commit(batch)
    if isinstance(result, JournalSelectedPublication):
        read_loop_snapshot(runtime)
    return result
