"""One H0 runtime: selected retained CLI custody to a native created Run."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.execution_initialization_contracts import (
    ExecutionInitializationCut,
    PrepareInboxExecution,
    SelectedAdmittedRunInput,
)
from chiplog.capabilities.agent_loop.execution_transition_contracts import CreateExecutionRun
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    BrokerSelectedH1EvidenceV1,
    H1BrokerRouteBindingV1,
    H1CurrentBrokerRouteV1,
    H1OwnerCandidateCallV1,
    H1OwnerCandidateV1,
    H1OwnerCurrentCallV1,
    H1OwnerCurrentCandidateV1,
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeResultV1,
    CurrentHermeticExecutionScopeV1,
    HermeticOutputScopeAnchorV1,
    HermeticOutputScopeV1,
    HermeticTrustObservationV1,
    IssuedHermeticOutputScopeV1,
    IssueHermeticOutputScopeResultV1,
    IssueHermeticOutputScopeV1,
    NonCurrentHermeticExecutionScopeV1,
    NonIssuedHermeticOutputScopeV1,
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.composition.common_execution_driver_contracts import (
    AdvanceExecutionRequestV1,
    CommonExecutionResultV1,
    DriveInputRequestV1,
    ExecutionDriverRejectedV1,
    ExecutionPendingReceiptV1,
    LookupExecutionRequestV1,
    SelectedExecutionReceiptV1,
)
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r14_execution_complete_seal_records import (
    EXECUTION_COMPLETE_SEAL_OPERATION,
)
from chiplog.composition.r14_execution_inbox_records import (
    RetainedInboxExecutionInitialization,
    inbox_initialization_command,
)
from chiplog.composition.r14_loop_history import read_execution_history
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources, ResourceObservation
from chiplog.composition.r16_dispatch_runtime import ExecutionDispatchRuntime, _configured
from chiplog.composition.r17_authenticated_records import decode_authentication
from chiplog.composition.r17_ingress_registry import RETAINED_CLI_READER_ID
from chiplog.composition.r17_ingress_runtime import R17IngressRuntime
from chiplog.platform._ingress_contracts import Head
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortResult,
    PublicPortSuccess,
)
from chiplog.platform.ingress_custody_records import canonical, reference
from chiplog.platform.ingress_transition_contracts import RetainedIngressSource
from chiplog.platform.r7_trust import TrustOwnerCall

R17_RETAINED_READER_ID = RETAINED_CLI_READER_ID


class _DriverConflict(Exception):
    pass


_H1ScopeRole = Literal["scope_issue", "scope_current"]


@dataclass(frozen=True, slots=True)
class _H1ScopeWireKey:
    role: _H1ScopeRole
    request_id: str
    caller: BrokerSession
    callee: BrokerSession


def _call_head(value: Head) -> CallSubjectHead:
    return CallSubjectHead(
        subject_id=value.identity,
        revision=Present(head=value.head, fingerprint=value.fingerprint),
    )


def _loop_head(value: Any) -> ExactHead:
    return ExactHead(identity=value.subject_id, head=value.head, fingerprint=value.fingerprint)


class CommonCliExecutionRuntime(R17IngressRuntime, ExecutionDispatchRuntime):
    """Cooperative ingress/dispatch assembly sharing exactly one writer and gate."""

    _record_contracts: ClassVar[dict[str, str]] = {
        **R17IngressRuntime._record_contracts,
        **ExecutionDispatchRuntime._record_contracts,
    }
    _record_schema_variants: ClassVar[tuple[tuple[str, str], ...]] = tuple(
        dict.fromkeys(
            (
                *R17IngressRuntime._record_schema_variants,
                *ExecutionDispatchRuntime._record_schema_variants,
            )
        )
    )
    # Invocation-local wire captures are consumed only by the private H1
    # publication authority.  The public scope methods still return their
    # domain DTOs; callers never receive broker frames as authority evidence.
    _h1_scope_wires: dict[_H1ScopeWireKey, tuple[PublicPortCall, PublicPortResult] | None]

    @staticmethod
    def _purge_expired_h1_scope_wires(
        wires: dict[_H1ScopeWireKey, tuple[PublicPortCall, PublicPortResult] | None],
    ) -> None:
        now = time.monotonic_ns()
        for key in tuple(wires):
            captured = wires[key]
            if captured is not None and captured[0].budget.absolute_deadline_ns <= now:
                del wires[key]

    @staticmethod
    def _h1_scope_key(role: _H1ScopeRole, call: PublicPortCall) -> _H1ScopeWireKey:
        expected_operation = {
            "scope_issue": "deployment_trust.issue_hermetic_output_scope",
            "scope_current": "deployment_trust.read_current_hermetic_output_scope",
        }[role]
        if call.operation_id != expected_operation:
            raise LoopRejected("H1 scope capture has a substituted owner operation")
        if call.schema_id != "chiplog.deployment-trust.owner-call.v1":
            raise LoopRejected("H1 scope capture has a substituted owner schema")
        return _H1ScopeWireKey(role, call.request_id, call.caller, call.callee)

    def _reserve_h1_scope_wire(self, role: _H1ScopeRole, call: PublicPortCall) -> _H1ScopeWireKey:
        key = self._h1_scope_key(role, call)
        wires = getattr(self, "_h1_scope_wires", None)
        if wires is None:
            wires = {}
            self._h1_scope_wires = wires
        self._purge_expired_h1_scope_wires(wires)
        if key in wires:
            raise LoopRejected("H1 scope capture request is already in flight")
        if len(wires) >= 64:
            raise LoopRejected("H1 scope capture ledger is full")
        wires[key] = None
        return key

    def _record_h1_scope_wire(
        self, key: _H1ScopeWireKey, call: PublicPortCall, result: PublicPortResult
    ) -> None:
        wires = self._h1_scope_wires
        if wires.get(key, "missing") is not None or key != self._h1_scope_key(key.role, call):
            raise LoopRejected("H1 scope capture reservation differs")
        if result.request_id != call.request_id or result.responder != call.callee:
            raise LoopRejected("H1 scope capture response differs from sent frame")
        wires[key] = (call, result)

    def _discard_h1_scope_wire(self, key: _H1ScopeWireKey) -> None:
        getattr(self, "_h1_scope_wires", {}).pop(key, None)

    def _take_h1_scope_wire(
        self,
        role: _H1ScopeRole,
        *,
        request_id: str,
        caller: BrokerSession,
        callee: BrokerSession,
    ) -> tuple[PublicPortCall, PublicPortResult]:
        """Consume one exact private H1 scope exchange without cross-call reuse."""
        key = _H1ScopeWireKey(role, request_id, caller, callee)
        wires: dict[_H1ScopeWireKey, tuple[PublicPortCall, PublicPortResult] | None] = getattr(
            self, "_h1_scope_wires", {}
        )
        self._purge_expired_h1_scope_wires(wires)
        captured = wires.pop(key, None)
        if captured is None:
            raise LoopRejected("H1 scope capture is absent or still in flight")
        call, result = captured
        if key != self._h1_scope_key(role, call):
            raise LoopRejected("H1 scope capture key differs from its sent frame")
        if result.request_id != call.request_id or result.responder != call.callee:
            raise LoopRejected("H1 scope capture response differs from sent frame")
        return captured

    async def issue_hermetic_output_scope(
        self, intent: IssueHermeticOutputScopeV1, *, request_id: str
    ) -> IssueHermeticOutputScopeResultV1:
        """Broker-only two-phase H1 issue path; no caller-built evidence is authority."""
        gate = self._authority_gate()
        from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources

        sources = H1SelectedOutputSources(self)
        with gate.hold():
            frozen = self._trust.capture_verified_observation()
            entries = self._trust._journal.entries()
            if not entries:
                return NonIssuedHermeticOutputScopeV1(disposition="STALE")
            decision_id, _, decision_bytes = entries[-1]
            logical = self._trust.owner_snapshot_entries()[-1][0]
            observation = HermeticTrustObservationV1(
                physical_journal_head=ExactHead(
                    identity="deployment-trust/journal",
                    head=decision_id,
                    fingerprint=hashlib.sha256(decision_bytes).hexdigest(),
                ),
                logical_snapshot_head=logical,
            )
            if intent.expected_trust_observation != observation:
                return NonIssuedHermeticOutputScopeV1(disposition="STALE")
            captured = sources.capture_selected_current(
                intent.selected_resource_observation_ref,
                intent.admitted_authentication_ref,
                intent.authenticated_cli_ref,
            )
            if captured is None:
                return NonIssuedHermeticOutputScopeV1(disposition="STALE")
            callee = self._supervisor.runtime().session("deployment_trust")
            route = H1BrokerRouteBindingV1(
                tenant_id="hermetic-tenant",
                database_id=intent.database_id,
                worker_session_id=intent.worker_session_id,
                broker_epoch=callee.broker_epoch,
                runtime_generation=callee.generation_id,
                broker_session_id="broker:" + callee.generation_id,
                owner_session_id=callee.session_id,
                request_id=request_id,
            )
            retained = H1RetainedSelectedWrapperV1(
                initialization_envelope_bytes=captured.initialization_envelope_bytes,
                admitted_record_bytes=captured.admitted_record_bytes,
                selected_admitted_record_ref=captured.selected_admitted_record_ref,
                authentication_result_bytes=captured.authentication_result_bytes,
                admitted_record_digest=hashlib.sha256(captured.admitted_record_bytes).hexdigest(),
            )
            evidence = BrokerSelectedH1EvidenceV1(
                route=route,
                selected_request_bytes=intent.canonical_bytes(),
                request_digest=hashlib.sha256(intent.canonical_bytes()).hexdigest(),
                retained=retained,
                retained_wrapper_digest=hashlib.sha256(retained.canonical_bytes()).hexdigest(),
                recipient=captured.verified.recipient,
                trust_observation=observation,
                trust_snapshot_digest=hashlib.sha256(frozen.snapshot_bytes).hexdigest(),
            )
            call = H1OwnerCandidateCallV1(
                evidence=evidence,
                evidence_digest=hashlib.sha256(evidence.canonical_bytes()).hexdigest(),
            )
        wire = TrustOwnerCall(
            mode="ISSUE_HERMETIC_OUTPUT_SCOPE_V1",
            snapshot_bytes=frozen.snapshot_bytes,
            request_bytes=call.canonical_bytes(),
        )
        request = PublicPortCall(
            operation_id="deployment_trust.issue_hermetic_output_scope",
            request_id=request_id,
            caller=BrokerSession(
                tenant_id="hermetic-tenant",
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id="broker:" + callee.generation_id,
            ),
            callee=callee,
            schema_id="chiplog.deployment-trust.owner-call.v1",
            canonical_payload=wire.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                policy_version=1,
            ),
        )
        try:
            capture_key = self._reserve_h1_scope_wire("scope_issue", request)
        except LoopRejected:
            return NonIssuedHermeticOutputScopeV1(disposition="STALE")
        try:
            response = await self._supervisor.runtime().call(request)
        except BaseException:
            self._discard_h1_scope_wire(capture_key)
            raise
        self._record_h1_scope_wire(capture_key, request, response)
        if (
            not isinstance(response, PublicPortSuccess)
            or response.request_id != request_id
            or response.responder != callee
        ):
            return NonIssuedHermeticOutputScopeV1(disposition="STALE")
        try:
            candidate = H1OwnerCandidateV1.model_validate_json(response.canonical_payload)
            if candidate.canonical_bytes() != response.canonical_payload:
                raise ValueError("noncanonical owner result")
            candidate.check_pinned_call(call)
        except ValueError:
            return NonIssuedHermeticOutputScopeV1(disposition="DENIED")
        with gate.hold():
            if self._trust.capture_verified_observation() != frozen:
                return NonIssuedHermeticOutputScopeV1(disposition="STALE")
            current = sources.capture_selected_current(
                intent.selected_resource_observation_ref,
                intent.admitted_authentication_ref,
                intent.authenticated_cli_ref,
            )
            if (
                current != captured
                or self._supervisor.runtime().session("deployment_trust") != callee
            ):
                return NonIssuedHermeticOutputScopeV1(disposition="STALE")
            for existing_id, _, raw in self._trust._journal.entries():
                envelope = json.loads(raw)
                if envelope.get("kind") != "HERMETIC_OUTPUT_SCOPE_V1":
                    continue
                existing = HermeticOutputScopeV1.model_validate(envelope["payload"]["scope"])
                if (
                    existing.database_id == candidate.scope.database_id
                    and existing.scope_id == candidate.scope.scope_id
                    and existing.revision == candidate.scope.revision
                ):
                    if existing != candidate.scope:
                        return NonIssuedHermeticOutputScopeV1(disposition="DENIED")
                    return self._issued_h1_result(existing_id, existing, disposition="REPLAY")
            decision_id, _, _ = self._trust.append_hermetic_output_scope(
                candidate.scope.canonical_bytes()
            )
            return self._issued_h1_result(decision_id, candidate.scope, disposition="ISSUED")

    def _issued_h1_result(
        self,
        decision_id: str,
        scope: HermeticOutputScopeV1,
        *,
        disposition: Literal["ISSUED", "REPLAY"],
    ) -> IssuedHermeticOutputScopeV1:
        decision = next(
            raw
            for current_id, _, raw in self._trust._journal.entries()
            if current_id == decision_id
        )
        ordinal = 1
        record = self._trust._materializer.record(decision_id, ordinal)
        if record is None:
            raise RuntimeError("issued H1 scope has no materialized scope record")
        anchor = HermeticOutputScopeAnchorV1(
            owner_id="deployment_trust",
            decision=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(decision).hexdigest(),
            ),
            record_ordinal=ordinal,
            record_type_id="chiplog.deployment_trust.hermetic_output_scope",
            schema_id="chiplog.deployment_trust.record.v1",
            record=ExactHead(
                identity="trust-record:" + decision_id + ":" + str(ordinal),
                head="trust-record:"
                + decision_id
                + ":"
                + str(ordinal)
                + "/"
                + hashlib.sha256(record).hexdigest(),
                fingerprint=hashlib.sha256(record).hexdigest(),
            ),
            scope_revision=scope.revision,
            predecessor=scope.predecessor,
            selected_resource_observation_ref=scope.selected_resource_observation_ref,
        )
        scope_bytes = scope.canonical_bytes()
        return IssuedHermeticOutputScopeV1(
            disposition=disposition,
            anchor=anchor,
            scope_head=ExactHead(
                identity=scope.scope_id,
                head=scope.scope_id + "/" + hashlib.sha256(scope_bytes).hexdigest(),
                fingerprint=hashlib.sha256(scope_bytes).hexdigest(),
            ),
            revision=scope.revision,
        )

    async def read_current_hermetic_output_scope(
        self, request: ReadCurrentHermeticExecutionScopeV1
    ) -> CurrentHermeticExecutionScopeResultV1:
        """Authenticate a physical scope record and its selected sources at one fence."""
        gate = self._authority_gate()
        from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources

        with gate.hold():
            snapshot = self._trust.capture_verified_observation().snapshot_bytes
            callee = self._supervisor.runtime().session("deployment_trust")
            request_id = "h1-current:" + hashlib.sha256(request.canonical_bytes()).hexdigest()
            route = H1CurrentBrokerRouteV1(
                tenant_id="hermetic-tenant",
                broker_epoch=callee.broker_epoch,
                runtime_generation=callee.generation_id,
                broker_session_id="broker:" + callee.generation_id,
                owner_session_id=callee.session_id,
                request_id=request_id,
            )
            current_call = H1OwnerCurrentCallV1(
                route=route,
                read_request_bytes=request.canonical_bytes(),
                request_digest=hashlib.sha256(request.canonical_bytes()).hexdigest(),
            )
        owner_call = TrustOwnerCall(
            mode="READ_CURRENT_HERMETIC_OUTPUT_SCOPE_V1",
            snapshot_bytes=snapshot,
            request_bytes=current_call.canonical_bytes(),
        )
        broker_call = PublicPortCall(
            operation_id="deployment_trust.read_current_hermetic_output_scope",
            request_id=request_id,
            caller=BrokerSession(
                tenant_id="hermetic-tenant",
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id="broker:" + callee.generation_id,
            ),
            callee=callee,
            schema_id="chiplog.deployment-trust.owner-call.v1",
            canonical_payload=owner_call.canonical_bytes(),
            budget=CallBudget(
                remaining_calls=1,
                remaining_depth=1,
                absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                policy_version=1,
            ),
        )
        try:
            capture_key = self._reserve_h1_scope_wire("scope_current", broker_call)
        except LoopRejected:
            return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
        try:
            owner_response = await self._supervisor.runtime().call(broker_call)
        except BaseException:
            self._discard_h1_scope_wire(capture_key)
            raise
        self._record_h1_scope_wire(capture_key, broker_call, owner_response)
        if (
            not isinstance(owner_response, PublicPortSuccess)
            or owner_response.request_id != broker_call.request_id
            or owner_response.responder != callee
        ):
            return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
        try:
            owner_candidate = H1OwnerCurrentCandidateV1.model_validate_json(
                owner_response.canonical_payload
            )
            if owner_candidate.canonical_bytes() != owner_response.canonical_payload:
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            owner_candidate.check_pinned_call(current_call)
        except ValueError:
            return NonCurrentHermeticExecutionScopeV1(disposition="DENIED")
        with gate.hold():
            frozen = self._trust.capture_verified_observation()
            if not self._matches_h1_trust_observation(request.expected_trust_observation):
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            anchor = request.source_anchor
            decision = next(
                (
                    raw
                    for decision_id, _, raw in self._trust._journal.entries()
                    if decision_id == anchor.decision.head
                ),
                None,
            )
            if (
                decision is None
                or hashlib.sha256(decision).hexdigest() != anchor.decision.fingerprint
            ):
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            record = self._trust._materializer.record(anchor.decision.head, anchor.record_ordinal)
            if record is None or hashlib.sha256(record).hexdigest() != anchor.record.fingerprint:
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            record_identity = (
                "trust-record:" + anchor.decision.head + ":" + str(anchor.record_ordinal)
            )
            if (
                anchor.record.identity != record_identity
                or anchor.record.head != record_identity + "/" + anchor.record.fingerprint
            ):
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            try:
                envelope = json.loads(record)
                if (
                    envelope.get("record_type_id") != anchor.record_type_id
                    or envelope.get("schema_id") != anchor.schema_id
                    or envelope.get("decision_id") != anchor.decision.head
                    or envelope.get("operation_kind") != "HERMETIC_OUTPUT_SCOPE_V1"
                ):
                    raise ValueError("scope record type differs")
                scope = HermeticOutputScopeV1.model_validate_json(
                    json.dumps(envelope["scope"], sort_keys=True, separators=(",", ":"))
                )
            except KeyError, TypeError, ValueError:
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            scope_bytes = scope.canonical_bytes()
            scope_ref = ExactHead(
                identity=scope.scope_id,
                head=scope.scope_id + "/" + hashlib.sha256(scope_bytes).hexdigest(),
                fingerprint=hashlib.sha256(scope_bytes).hexdigest(),
            )
            if (
                scope_ref != request.expected_scope_ref
                or scope.revision != request.expected_revision
                or scope.database_id != request.database_id
                or scope.scope_id != request.scope_id
                or scope.worker_session_id != request.expected_worker_session_id
                or scope.admitted_authentication != request.admitted_authentication_ref
                or scope.selected_resource_observation_ref
                != request.selected_resource_observation_ref
                or scope.authenticated_cli_state.trust_binding_digest
                != request.authenticated_cli_ref.trust_head
                or scope.authenticated_cli_state.credential_head
                != request.authenticated_cli_ref.credential_head
                or scope.authenticated_cli_state.session_head
                != request.authenticated_cli_ref.session_head
            ):
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            current = H1SelectedOutputSources(self).capture_selected_current(
                request.selected_resource_observation_ref,
                request.admitted_authentication_ref,
                request.authenticated_cli_ref,
            )
            if current is None or self._trust.capture_verified_observation() != frozen:
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            if scope.recipient != current.verified.recipient:
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            if self._supervisor.runtime().session("deployment_trust") != callee:
                return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
            return CurrentHermeticExecutionScopeV1(
                disposition="CURRENT",
                scope_ref=scope_ref,
                source_anchor=anchor,
                selector_generation=0,
                ordered_current_source_refs=(
                    current.verified.admitted_authentication_ref,
                    current.verified.recipient.endpoint,
                    current.verified.recipient.credential_binding,
                ),
            )

    def _matches_h1_trust_observation(self, expected: HermeticTrustObservationV1) -> bool:
        entries = self._trust._journal.entries()
        if not entries:
            return False
        decision_id, _, raw = entries[-1]
        return expected == HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            logical_snapshot_head=self._trust.owner_snapshot_entries()[-1][0],
        )

    def _selected_input(
        self,
        request: DriveInputRequestV1,
        observation: ResourceObservation,
        *,
        historical: bool = False,
    ) -> SelectedAdmittedRunInput:
        source = request.selected_source
        if source.kind != "CLI_RETAINED_SELECTED_SOURCE_V1":
            raise PermissionError("only the deployed retained CLI route is mounted")
        admitted = self.read_admitted_inbox(source.original_ingress_identity.command_id)
        if admitted is None:
            raise PermissionError("selected admitted inbox is absent")
        record = admitted.record
        profile = record.command.profile
        retained = RetainedIngressSource(
            source=record.command.retention.proof,
            reader_id=R17_RETAINED_READER_ID,
            schema_id="chiplog.ingress.retained-source-observation.v1",
            canonical_source_bytes=record.command.retention.observation_bytes,
        )
        if (
            request.identity.tenant_id != self._tenant_id
            or request.identity.database_id != profile.database_id
            or profile != self._ingress_profile()
            or source.expected_reader_id != R17_RETAINED_READER_ID
            or source.source_binding != record.command.token.source
            or source.selected_ingress_decision != admitted.selected_decision
            or source.source_head != retained.source
            or source.retained_source != retained
            or request.identity.original_ingress_request_fingerprint
            != record.inbox.authentication_request_fingerprint
        ):
            raise PermissionError("driver source differs from independently selected R17 inbox")
        _, authenticated = decode_authentication(record.command)
        raw = record.inbox.raw_bytes
        prompt = raw.decode("utf-8")
        if not prompt:
            raise ValueError("selected CLI prompt is empty")
        resources = self._require_dispatch_resources()
        resource_recipient = (
            resources.historical_recipient(observation)
            if historical
            else resources.recipient(observation)
        )
        if (
            authenticated.reference.tenant_id != self._tenant_id
            or authenticated.reference.principal_id != resource_recipient.recipient
        ):
            raise PermissionError("authenticated CLI principal differs from registered recipient")
        recipient = ProviderRecipient(
            provider_id=resource_recipient.provider,
            account_id=resource_recipient.account,
            recipient_id=resource_recipient.recipient,
            endpoint=_loop_head(resource_recipient.endpoint),
            canonical_address=resource_recipient.canonical_address,
            credential_binding=_loop_head(resource_recipient.credential_binding),
        )
        token = reference(
            "ingress.token:" + record.command.token.token_id,
            canonical(record.command.token),
        )
        authentication = record.inbox.authentication
        return SelectedAdmittedRunInput(
            tenant_id=profile.tenant_id,
            database_id=profile.database_id,
            source_class="CLI",
            source_contract=_call_head(profile.head()),
            token=_call_head(token),
            custody=_call_head(admitted.physical_record),
            inbox=_call_head(record.custody.inbox),
            selected_decision=_call_head(admitted.selected_decision),
            physical_record=_call_head(admitted.physical_record),
            commit_sequence=admitted.commit_sequence,
            raw_input_bytes=raw,
            custody_schema=record.schema_id,
            canonical_custody_record=json.dumps(
                record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode(),
            inbox_schema=record.inbox.schema_id,
            canonical_inbox_record=json.dumps(
                record.inbox.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode(),
            source_authentication=_call_head(authentication.proof),
            authentication_schema="chiplog.cli.custody-decision.v1",
            canonical_authentication=record.command.authentication_result_bytes,
            normalization=_call_head(record.custody.inbox),
            normalization_schema=record.inbox.schema_id,
            canonical_normalization_record=json.dumps(
                record.inbox.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode(),
            normalized_prompt=prompt,
            principal_id=authenticated.reference.principal_id,
            contour_head=authentication.principal_contour.head,
            origin=OriginSelection(
                ingress_binding=ExactHead(**record.custody.inbox.model_dump()), recipient=recipient
            ),
        )

    @staticmethod
    def _run_id(admitted: SelectedAdmittedRunInput) -> str:
        raw = json.dumps(
            [admitted.tenant_id, admitted.database_id, admitted.inbox.subject_id],
            separators=(",", ":"),
        ).encode()
        return "run:inbox:" + hashlib.sha256(b"chiplog.h0.run.v1\x00" + raw).hexdigest()

    async def drive_input(self, request: DriveInputRequestV1) -> CommonExecutionResultV1:
        try:
            await self._execution_actor("hermetic-ingress")
            existing = self._find(request.identity, request.original_driver_command_fingerprint())
            if existing is not None:
                return self._receipt(*existing, "EXACT_REPLAY")
            observation = self._require_dispatch_resources().observe()
            admitted = self._selected_input(request, observation)
            run_id = self._run_id(admitted)
            for _, _, raw in self._loop_decisions().entries():
                entry = json.loads(raw)
                value = entry.get("inbox_initialization")
                if isinstance(value, str):
                    evidence = RetainedInboxExecutionInitialization.model_validate_json(value)
                    if evidence.proposal.run.run_id == run_id:
                        raise _DriverConflict(
                            "selected inbox already belongs to another driver command"
                        )
            snapshot = read_execution_history(self)
            create = CreateExecutionRun(
                command_id="create:inbox:" + run_id.rsplit(":", 1)[-1],
                tenant=self._tenant_id,
                principal=admitted.principal_id,
                run_id=run_id,
                prompt=admitted.normalized_prompt,
                policy=BudgetPolicy(),
                origin=admitted.origin,
                contour_head=admitted.contour_head,
                policy_head="hermetic-policy-v1",
                worker_session=self.current_worker(),
            )
            endpoint = _call_head(observation_to_head(observation))
            cut = ExecutionInitializationCut(
                tenant_id=self._tenant_id,
                database_id=admitted.database_id,
                tenant_commit_sequence=snapshot.tenant_head,
                materialization_commitment=self._commitment_journal.load(self._tenant_id)
                or "0" * 64,
                initial_run_absence=Absent(),
                worker_session_id=self.current_worker(),
                runtime_generation=self.current_worker(),
                authority_registry=endpoint,
                sources=(
                    CallAuthorityObservation(
                        source_id="dispatch-recipient",
                        family="RECIPIENT",
                        source=endpoint,
                        generation=observation.clock_epoch,
                        frontier=str(snapshot.tenant_head),
                        canonical_value_base64=base64.b64encode(
                            observation.endpoint_bytes
                        ).decode(),
                        observed_at_ns=time.monotonic_ns(),
                        valid_until_ns=time.monotonic_ns() + 60_000_000_000,
                    ),
                ),
            )
            prepared = PrepareInboxExecution(create=create, admitted=admitted, cut=cut)
            run = await self._publish_selected_inbox_initialization(
                "hermetic-ingress",
                prepared,
                driver_request_bytes=request.canonical_bytes(),
                driver_request_fingerprint=request.original_driver_command_fingerprint(),
                dispatch_observation=observation,
                source_guard=lambda: self._selected_input(request, observation) == admitted,
            )
            retained = self._find(request.identity, request.original_driver_command_fingerprint())
            if retained is None or retained[0].proposal.run != run:
                raise LoopRejected("selected inbox initialization disappeared")
            return self._receipt(*retained, "COMMITTED")
        except UnicodeDecodeError:
            return self._reject(request, "INVALID_INPUT", "selected CLI prompt is not UTF-8")
        except ValueError as error:
            return self._reject(request, "INVALID_INPUT", str(error))
        except PermissionError as error:
            return self._reject(request, "DENIED", str(error))
        except _DriverConflict as error:
            return self._reject(request, "CONFLICT", str(error))
        except LoopRejected as error:
            return self._reject(request, "STALE", str(error))

    async def lookup_execution(self, request: LookupExecutionRequestV1) -> CommonExecutionResultV1:
        try:
            await self._execution_actor("hermetic-ingress")
            found = self._find(request.identity, request.original_driver_command_fingerprint)
        except _DriverConflict as error:
            return ExecutionDriverRejectedV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                code="CONFLICT",
                reason=str(error),
            )
        except LoopRejected as error:
            return ExecutionDriverRejectedV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                code="DENIED",
                reason=str(error),
            )
        if found is None:
            return ExecutionPendingReceiptV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                phase="NO_RUN",
            )
        return self._receipt(*found, "EXACT_REPLAY")

    async def advance_execution(
        self, request: AdvanceExecutionRequestV1
    ) -> CommonExecutionResultV1:
        """Advance one selected H0 Run through the mounted capture/seal first path."""
        try:
            await self._execution_actor("hermetic-ingress")
            found = self._find(request.identity, request.original_driver_command_fingerprint)
            if found is None:
                return ExecutionDriverRejectedV1(
                    identity=request.identity,
                    original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                    code="HOLD",
                    reason="selected H0 Run is unavailable for advance",
                )
            evidence, _ = found
            run = evidence.proposal.run
            expected = Head(
                identity=run.head,
                head=run.head,
                fingerprint=hashlib.sha256(run.canonical_bytes()).hexdigest(),
            )
            if request.expected_selected_run_head != expected:
                return ExecutionDriverRejectedV1(
                    identity=request.identity,
                    original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                    code="STALE",
                    reason="expected selected H0 Run head differs",
                )
            lineage = [
                item for item in read_execution_history(self).records if item.run_id == run.run_id
            ]
            if not lineage or lineage[0] != run:
                return ExecutionDriverRejectedV1(
                    identity=request.identity,
                    original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                    code="STALE",
                    reason="selected H0 Run lineage differs",
                )
            current = lineage[-1]
            if current != run:
                if current.state == "ACTIVE" and current.event == "ModelCompletionPrepared":
                    return self._running_receipt(evidence, current, disposition="EXACT_REPLAY")
                return ExecutionDriverRejectedV1(
                    identity=request.identity,
                    original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                    code="HOLD",
                    reason="selected H0 Run already advanced beyond the mounted first path",
                )
            if run.state != "CREATED":
                return ExecutionDriverRejectedV1(
                    identity=request.identity,
                    original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                    code="STALE",
                    reason="selected H0 Run is not CREATED",
                )
            started = await self.begin_execution("hermetic-ingress", run.run_id, run.head)
            captured = await self.capture_execution("hermetic-ingress", run.run_id, started.head)
            sealed = await self.seal_execution_complete(
                "hermetic-ingress", run.run_id, captured.head
            )
            if sealed.state != "ACTIVE":
                raise LoopRejected("first-path complete seal is not an active Run")
            return self._running_receipt(evidence, sealed, disposition="COMMITTED")
        except _DriverConflict as error:
            return ExecutionDriverRejectedV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                code="CONFLICT",
                reason=str(error),
            )
        except LoopRejected as error:
            return ExecutionDriverRejectedV1(
                identity=request.identity,
                original_driver_command_fingerprint=request.original_driver_command_fingerprint,
                code="STALE",
                reason=str(error),
            )

    def _running_receipt(
        self,
        evidence: RetainedInboxExecutionInitialization,
        run: object,
        *,
        disposition: Literal["COMMITTED", "EXACT_REPLAY"],
    ) -> SelectedExecutionReceiptV1:
        """Project a selected physical complete-seal, retaining the H0 ingress binding."""
        from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord

        if not isinstance(run, ExecutionRunRecord):
            raise LoopRejected("first-path seal did not produce a native Run")
        selected: tuple[str, bytes] | None = None
        for decision_id, _, raw in self._loop_decisions().entries():
            entry = json.loads(raw)
            if entry.get("kind") != "DECIDED":
                continue
            command = self._publication(entry)
            if (
                command.operation_kind == EXECUTION_COMPLETE_SEAL_OPERATION
                and command.idempotency_key == run.head
            ):
                if selected is not None:
                    raise LoopRejected("multiple selected complete seals for native Run")
                state, actual, anchored = self._selected_physical_state(command)
                if state != "COMPLETE" or actual != anchored:
                    raise LoopRejected("selected complete seal is not materialized")
                physical_run = [
                    record for record in command.records if record.record_id == run.head
                ]
                if (
                    len(physical_run) != 1
                    or physical_run[0].owner != "agent_loop"
                    or physical_run[0].canonical_bytes != run.canonical_bytes()
                    or physical_run[0].fingerprint
                    != hashlib.sha256(run.canonical_bytes()).hexdigest()
                ):
                    raise LoopRejected("selected complete seal Run physical member differs")
                selected = (decision_id, raw)
        if selected is None:
            raise LoopRejected("selected complete seal decision is absent")
        lineage = [
            item for item in read_execution_history(self).records if item.run_id == run.run_id
        ]
        if lineage[0] != evidence.proposal.run or lineage[-1] != run:
            raise LoopRejected("selected complete seal Run lineage differs from H0 admission")
        decision_id, raw = selected
        wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
        admitted = evidence.request.admitted

        def ingress_head(value: CallSubjectHead) -> Head:
            assert isinstance(value.revision, Present)
            return Head(
                identity=value.subject_id,
                head=value.revision.head,
                fingerprint=value.revision.fingerprint,
            )

        return SelectedExecutionReceiptV1(
            disposition=disposition,
            identity=wire.identity,
            original_driver_command_fingerprint=evidence.driver_request_fingerprint,
            selected_ingress_decision=ingress_head(admitted.selected_decision),
            selected_custody=ingress_head(admitted.custody),
            selected_admitted_input=ingress_head(admitted.inbox),
            stable_run_lineage_id=run.run_id,
            selected_run_head=Head(
                identity=run.head,
                head=run.head,
                fingerprint=hashlib.sha256(run.canonical_bytes()).hexdigest(),
            ),
            selected_run_state=run.state,
            selected_journal_decision=Head(
                identity=run.head,
                head=decision_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            commit_sequence=self._publication(json.loads(raw)).expected_head + 1,
            phase="RUNNING",
        )

    def _find(
        self, identity: object, fingerprint: str
    ) -> tuple[RetainedInboxExecutionInitialization, str] | None:
        for decision_id, _, raw in self._loop_decisions().entries():
            entry = json.loads(raw)
            value = entry.get("inbox_initialization")
            if entry.get("kind") != "DECIDED":
                if isinstance(value, str):
                    raise LoopRejected("unselected inbox initialization evidence is present")
                continue
            if (
                entry.get("operation_kind") == "agent_loop.inbox-initialization.v1"
                and not isinstance(value, str)
            ) or (
                isinstance(value, str)
                and entry.get("operation_kind") != "agent_loop.inbox-initialization.v1"
            ):
                raise LoopRejected("selected inbox initialization operation/evidence differs")
            if not isinstance(value, str):
                continue
            evidence = RetainedInboxExecutionInitialization.model_validate_json(value)
            command = self._publication(entry)
            if (
                evidence.canonical_bytes().decode() != value
                or command != inbox_initialization_command(evidence)
                or command.idempotency_key != evidence.proposal.run.head
                or command.expected_head != evidence.expected_head
                or entry.get("predecessor") != evidence.predecessor_commitment
            ):
                raise LoopRejected("selected inbox initialization evidence differs")
            wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
            if wire.identity == identity:
                if evidence.driver_request_fingerprint != fingerprint:
                    raise _DriverConflict(
                        "driver command identity has another immutable fingerprint"
                    )
                self._validate_historical(evidence, wire)
                state, actual, anchored = self._selected_physical_state(command)
                if state != "COMPLETE" or actual != anchored:
                    raise LoopRejected("selected inbox initialization is not materialized")
                if evidence.proposal.run not in read_execution_history(self).records:
                    raise LoopRejected("selected native inbox Run is absent")
                return evidence, decision_id
        return None

    def _validate_historical(
        self, evidence: RetainedInboxExecutionInitialization, wire: DriveInputRequestV1
    ) -> None:
        observation = ResourceObservation(
            evidence.dispatch_grant_bytes,
            evidence.dispatch_credential_bytes,
            evidence.dispatch_endpoint_bytes,
            evidence.dispatch_clock_epoch,
            evidence.dispatch_signature,
        )
        recipient = self._require_dispatch_resources().historical_recipient(observation)
        origin = evidence.request.admitted.origin
        if (
            (
                recipient.provider,
                recipient.account,
                recipient.recipient,
                recipient.canonical_address,
            )
            != (
                origin.recipient.provider_id,
                origin.recipient.account_id,
                origin.recipient.recipient_id,
                origin.recipient.canonical_address,
            )
            or _loop_head(recipient.endpoint) != origin.recipient.endpoint
            or _loop_head(recipient.credential_binding) != origin.recipient.credential_binding
        ):
            raise LoopRejected("historical selected recipient differs")
        admitted = self.read_admitted_inbox(wire.identity.original_ingress_identity.command_id)
        if admitted is None:
            raise LoopRejected("historical selected inbox is absent")
        decoded = self._selected_input(wire, observation, historical=True)
        if decoded != evidence.request.admitted:
            raise LoopRejected("historical selected R17 input differs")

    def _receipt(
        self,
        evidence: RetainedInboxExecutionInitialization,
        decision_id: str,
        disposition: Literal["COMMITTED", "EXACT_REPLAY"],
    ) -> SelectedExecutionReceiptV1:
        wire = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
        run, admitted = evidence.proposal.run, evidence.request.admitted
        command = inbox_initialization_command(evidence)

        def ingress_head(value: CallSubjectHead) -> Head:
            assert isinstance(value.revision, Present)
            return Head(
                identity=value.subject_id,
                head=value.revision.head,
                fingerprint=value.revision.fingerprint,
            )

        return SelectedExecutionReceiptV1(
            disposition=disposition,
            identity=wire.identity,
            original_driver_command_fingerprint=evidence.driver_request_fingerprint,
            selected_ingress_decision=ingress_head(admitted.selected_decision),
            selected_custody=ingress_head(admitted.custody),
            selected_admitted_input=ingress_head(admitted.inbox),
            stable_run_lineage_id=run.run_id,
            selected_run_head=Head(
                identity=run.head,
                head=run.head,
                fingerprint=hashlib.sha256(run.canonical_bytes()).hexdigest(),
            ),
            selected_run_state=run.state,
            selected_journal_decision=Head(
                identity=command.idempotency_key,
                head=decision_id,
                fingerprint=hashlib.sha256(
                    next(
                        raw
                        for key, _, raw in self._loop_decisions().entries()
                        if key == decision_id
                    )
                ).hexdigest(),
            ),
            commit_sequence=command.expected_head + 1,
            phase="INITIALIZED",
        )

    def _reject(
        self,
        request: DriveInputRequestV1,
        code: Literal["HOLD", "CONFLICT", "STALE", "DENIED", "INVALID_INPUT", "INTEGRITY_FAULT"],
        reason: str,
    ) -> ExecutionDriverRejectedV1:
        return ExecutionDriverRejectedV1(
            identity=request.identity,
            original_driver_command_fingerprint=request.original_driver_command_fingerprint(),
            code=code,
            reason=reason,
        )


def observation_to_head(observation: ResourceObservation) -> Head:
    return reference("hermetic-dispatch-observation", observation.endpoint_bytes)


@asynccontextmanager
async def open_common_cli_execution_runtime(
    database: Path, *, resources: HermeticDispatchResources, responses: tuple[bytes, ...] = ()
) -> AsyncIterator[CommonCliExecutionRuntime]:
    model = HermeticModel(responses)
    with _configured(resources):
        async with _open_runtime(
            database,
            tenant_id="hermetic-tenant",
            operator_secret=b"r13-hermetic-only",
            runtime_type=CommonCliExecutionRuntime,
            manifest=R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST,
            extra_leaves={
                "model": model,
                "effects_transport": resources.require_original_provider(),
            },
            runtime_setup=lambda opened: cast(
                CommonCliExecutionRuntime, opened
            )._bind_h1_historical_custody_path(resources._custody_path),
        ) as opened:
            runtime = cast(CommonCliExecutionRuntime, opened)
            runtime._execution_model = model
            model.session = runtime
            if runtime._trust.verify() is None:
                await runtime.bootstrap(
                    database_instance_id="hermetic-database",
                    principal_id="hermetic-principal",
                    credential_id="hermetic-credential",
                    session_id="hermetic-session",
                    token="hermetic-bootstrap",
                )
            yield runtime
