"""One H0 runtime: selected retained CLI custody to a native created Run."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal, cast

from pydantic import TypeAdapter

from chiplog.adapters.driven.loop_hermetic import HermeticModel
from chiplog.architecture.r7_runtime import (
    R14_R17_H1_LOCAL_EFFECTS_J7_PRODUCTION_MANIFEST,
    R14_R17_H1_LOCAL_EFFECTS_PRODUCTION_MANIFEST,
)
from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallAuthorityObservation,
    CallSubjectHead,
)
from chiplog.capabilities.agent_loop.completion_owner_record_contracts import (
    DELIVERY_ACCEPTANCE_SCHEMA,
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
from chiplog.capabilities.agent_loop.post_terminal_record_contracts import (
    EDGE_SCHEMA,
    EPOCH_SCHEMA,
    LEASE_SCHEMA,
    ROLLOVER_SCHEMA,
    SELECTOR_SCHEMA,
    SUBJECT_SCHEMA,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.recovery_record_contracts import AGENT_LOOP_OWNER
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
from chiplog.capabilities.deployment_trust.operator_policy_authorization_contracts import (
    SignedOperatorPolicyAuthorizationV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_owner_contracts import (  # noqa: E501
    AuthorizePreparedSelfDeliveryPolicyCallV1,
    PreparedSelfDeliveryPolicyProposalV1,
    PreparedSelfDeliveryPolicyRejectedV1,
    PreparedSelfDeliveryPolicyResultV1,
)
from chiplog.capabilities.effects.h1_local_preparation_record_contracts import (
    SCHEMA_ID as H1_LOCAL_INTENT_SCHEMA,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    ACCEPTED_ENTRY_SCHEMA,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    OWNER as CONVERSATION_OWNER,
)
from chiplog.composition.common_execution_driver_contracts import (
    AcceptedTerminalDetailV1,
    AdvanceExecutionRequestV1,
    CommonExecutionResultV1,
    DriveInputRequestV1,
    DriverCommandIdentityV1,
    ExecutionDriverRejectedV1,
    ExecutionPendingReceiptV1,
    LookupExecutionRequestV1,
    SelectedExecutionReceiptV1,
    UncertainExecutionPublicationV1,
)
from chiplog.composition.r7_planning import _open_runtime
from chiplog.composition.r14_cancellation_contracts import CancelCallSubmission
from chiplog.composition.r14_execution_cancellation_contracts import ExecutionCancelledCallReceipt
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
from chiplog.platform.prepared_self_delivery_policy_lineage import (
    AuthenticatedPreparedSelfDeliveryPolicy,
)
from chiplog.platform.r7_trust import TrustOwnerCall
from chiplog.platform.r7_trust_durability import FrozenTrustObservation

R17_RETAINED_READER_ID = RETAINED_CLI_READER_ID


class _DriverConflict(Exception):
    pass


class _FinalizationReceiptIntegrityError(RuntimeError):
    """A private finalization readback cannot be projected as a public receipt."""


_H1ScopeRole = Literal["scope_issue", "scope_current"]


@dataclass(frozen=True, slots=True)
class _H1ScopeWireKey:
    role: _H1ScopeRole
    request_id: str
    caller: BrokerSession
    callee: BrokerSession


@dataclass(frozen=True, slots=True)
class _H1ScopeWire:
    """One exact, bounded broker exchange retained for an H1 private owner."""

    sent: PublicPortCall
    returned: PublicPortResult
    sent_at_ns: int
    returned_at_ns: int

class _H1LiveCompletionMount:
    """Private installation owner for the one live H1 root issuer."""

    def __init__(self, installed_authority: object) -> None:
        self._authority: Any = installed_authority
        self._runtime: CommonCliExecutionRuntime | None = None
        self._revoked = False

    def _bind_runtime(self, opened: CommonCliExecutionRuntime) -> None:
        if self._runtime is not None:
            raise RuntimeError("live H1 completion mount is already bound")
        self._runtime = opened

    def _revoke_all(self) -> None:
        self._authority._revoke_all()
        self._revoked = True

    def _assert_root_binding(self, journal: object, issuer: object) -> None:
        """Prove the installed writer graph before the journal retains its root."""
        from chiplog.composition.h1_delivery_evidence_journal import H1DeliveryEvidenceJournal

        runtime = self._runtime
        if runtime is None:
            raise RuntimeError("live H1 completion mount has no runtime")
        if type(journal) is not H1DeliveryEvidenceJournal or journal._closed:
            raise RuntimeError("live H1 root journal differs or is closed")
        installed_journal = cast(Any, journal)
        if self._revoked or getattr(issuer, "_revoked", None) is not False:
            raise RuntimeError("live H1 root issuer is revoked")
        if self._authority is not issuer:
            raise RuntimeError("live H1 root issuer differs from its mount")
        coordinator = getattr(runtime, "_h1_live_publication_coordinator", None)
        if (
            getattr(runtime, "_h1_live_completion_mount", None) is not self
            or getattr(runtime, "_h1_live_publication_authority", None) is not issuer
            or getattr(runtime, "_h1_delivery_evidence_journal", None) is not journal
            or getattr(coordinator, "_authority", None) is not issuer
            or getattr(coordinator, "_appender", None) is not runtime._appender
            or getattr(coordinator, "_journal", None) is not runtime._owner_decisions()
        ):
            raise RuntimeError("live H1 root runtime mount differs")
        gate = runtime._authority_gate()
        store = runtime._appender._materializer
        if (
            installed_journal._journal.authority_gate is not gate
            or installed_journal._mount.authority_gate is not gate
            or getattr(store, "authority_gate", None) is not gate
            or getattr(store, "_owner_publication_resolver", None) is not issuer
        ):
            raise RuntimeError("live H1 root gate or store resolver differs")
        gate.require_held()


@dataclass(frozen=True, slots=True)
class _H1ScopeAppendReceipt:
    """One gate-held trust transition, retained only for the installed H1 issuer."""

    request_id: str
    before: FrozenTrustObservation
    after: FrozenTrustObservation
    decision_id: str
    scope: HermeticOutputScopeV1
    disposition: Literal["ISSUED", "REPLAY"]


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
                (AGENT_LOOP_OWNER, DELIVERY_ACCEPTANCE_SCHEMA),
                (AGENT_LOOP_OWNER, "chiplog.execution.terminal-manifest.v1"),
                (AGENT_LOOP_OWNER, "chiplog.agent-loop.execution-record.v3"),
                (AGENT_LOOP_OWNER, SUBJECT_SCHEMA),
                (AGENT_LOOP_OWNER, EPOCH_SCHEMA),
                (AGENT_LOOP_OWNER, SELECTOR_SCHEMA),
                (AGENT_LOOP_OWNER, LEASE_SCHEMA),
                (AGENT_LOOP_OWNER, ROLLOVER_SCHEMA),
                (AGENT_LOOP_OWNER, EDGE_SCHEMA),
                (CONVERSATION_OWNER, ACCEPTED_ENTRY_SCHEMA),
                ("effects", H1_LOCAL_INTENT_SCHEMA),
            )
        )
    )
    # Invocation-local wire captures are consumed only by the private H1
    # publication authority.  The public scope methods still return their
    # domain DTOs; callers never receive broker frames as authority evidence.
    _h1_scope_wires: dict[_H1ScopeWireKey, _H1ScopeWire | None]
    _h1_scope_append_receipts: dict[str, _H1ScopeAppendReceipt]
    _h1_delivery_evidence_journal: Any | None
    _h1_postseal_recovery_journal: Any | None
    _h1_recovery_mount: Any | None
    _h1_postseal_recovery_coordinator: Any | None
    _h1_preissuance_registration_source_port: Any | None
    _h1_first_path_sources: Any | None
    _h1_native_member_sources: Any | None
    _h1_installed_worker_evidence_owner: Any | None
    _h1_pre_request_member_evidence: Any | None
    _h1_pre_request_worker_evidence: Any | None
    _h1_completion_exchange_registry: Any | None
    _h1_conversation_source_port: Any | None
    _h1_live_invocation_source: Any | None
    _h1_live_readplan_source: Any | None
    _j7_operator_policy_key_pin: Any | None

    async def authorize_prepared_self_delivery_policy(
        self, canonical_signed_source_bytes: bytes, *, request_id: str
    ) -> AuthenticatedPreparedSelfDeliveryPolicy:
        """Authorize and append one J7 policy through the mounted trust owner.

        The caller supplies only the signed source.  The broker captures every
        mutable authority input under its gate, releases that gate for owner
        IPC, then repeats the whole cut before the durable append.
        """
        if type(canonical_signed_source_bytes) is not bytes or not canonical_signed_source_bytes:
            raise PermissionError("J7 authorization requires nonempty signed source bytes")
        if type(request_id) is not str or not request_id:
            raise ValueError("J7 authorization requires a nonempty request id")
        try:
            signed = SignedOperatorPolicyAuthorizationV1.model_validate_json(
                canonical_signed_source_bytes
            )
            if signed.canonical_bytes() != canonical_signed_source_bytes:
                raise ValueError("signed operator source is not canonical")
        except (TypeError, ValueError) as error:
            raise PermissionError("J7 signed operator source is invalid") from error

        gate = self._authority_gate()
        with gate.hold():
            pin = getattr(self, "_j7_operator_policy_key_pin", None)
            if pin is None:
                raise PermissionError("J7 operator policy pin is unavailable")
            try:
                pin.assert_current()
            except Exception as error:
                raise PermissionError("J7 operator policy pin is not current") from error
            payload = signed.payload
            if (
                payload.tenant_id != self._tenant_id
                or payload.tenant_id != pin.binding.tenant_id
                or payload.database_id != pin.binding.database_id
                or payload.policy_id != pin.binding.policy_id
                or payload.operator_key_id != pin.binding.operator_key_id
            ):
                raise PermissionError("J7 signed source scope differs from installed pin")
            frozen = self._trust.capture_verified_observation()
            entries = self._trust._journal.entries()
            if not entries:
                raise RuntimeError("J7 trust journal is empty")
            decision_id, _, decision_bytes = entries[-1]
            logical_entries = self._trust.owner_snapshot_entries()
            if not logical_entries:
                raise RuntimeError("J7 trust snapshot is empty")
            observation = HermeticTrustObservationV1(
                physical_journal_head=ExactHead(
                    identity="deployment-trust/journal",
                    head=decision_id,
                    fingerprint=hashlib.sha256(decision_bytes).hexdigest(),
                ),
                logical_snapshot_head=logical_entries[-1][0],
            )
            latest = self._trust.latest_prepared_self_delivery_policy(
                payload.tenant_id, payload.database_id, payload.policy_id
            )
            call = AuthorizePreparedSelfDeliveryPolicyCallV1(
                canonical_signed_source_bytes=canonical_signed_source_bytes,
                snapshot_bytes=frozen.snapshot_bytes,
                expected_trust_observation=observation,
                latest_policy_anchor=None if latest is None else latest.anchor,
                latest_policy_bytes=None if latest is None else latest.policy.canonical_bytes(),
            )
            callee = self._supervisor.runtime().session("deployment_trust")
            broker_call = PublicPortCall(
                operation_id="deployment_trust.authorize_prepared_external_self_delivery_policy",
                request_id=request_id,
                caller=BrokerSession(
                    tenant_id=self._tenant_id,
                    broker_epoch=callee.broker_epoch,
                    generation_id=callee.generation_id,
                    owner_id="broker",
                    session_id="broker:" + callee.generation_id,
                ),
                callee=callee,
                schema_id="chiplog.deployment-trust.authorize-self-delivery-policy-call.v1",
                canonical_payload=call.canonical_bytes(),
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    absolute_deadline_ns=time.monotonic_ns() + 5_000_000_000,
                    policy_version=1,
                ),
            )

        response = await self._supervisor.runtime().call(broker_call)
        if (
            not isinstance(response, PublicPortSuccess)
            or response.request_id != broker_call.request_id
            or response.responder != callee
            or response.schema_id
            != "chiplog.deployment-trust.self-delivery-policy-result.v1"
            or time.monotonic_ns() >= broker_call.budget.absolute_deadline_ns
        ):
            raise PermissionError("J7 owner response is not authenticated and current")
        try:
            result = TypeAdapter(PreparedSelfDeliveryPolicyResultV1).validate_json(
                response.canonical_payload
            )
            if result.canonical_bytes() != response.canonical_payload:
                raise ValueError("owner result is not canonical")
            if result.call_sha256 != hashlib.sha256(call.canonical_bytes()).hexdigest():
                raise ValueError("owner result call SHA256 mismatch")
        except (TypeError, ValueError) as error:
            raise PermissionError("J7 owner result is invalid") from error
        if isinstance(result, PreparedSelfDeliveryPolicyRejectedV1):
            raise PermissionError("J7 owner denied authorization: " + result.reason)
        proposal = result
        if not isinstance(proposal, PreparedSelfDeliveryPolicyProposalV1):
            raise PermissionError("J7 owner result has an unknown disposition")
        try:
            proposal.check_pinned_call(call)
        except (TypeError, ValueError) as error:
            raise PermissionError("J7 owner proposal is invalid") from error

        with gate.hold():
            pin = getattr(self, "_j7_operator_policy_key_pin", None)
            if pin is None:
                raise PermissionError("J7 operator policy pin is unavailable")
            try:
                pin.assert_current()
            except Exception as error:
                raise PermissionError(
                    "J7 operator policy pin changed during authorization"
                ) from error
            if (
                self._trust.capture_verified_observation() != frozen
                or self._supervisor.runtime().session("deployment_trust") != callee
                or self._trust.latest_prepared_self_delivery_policy(
                    signed.payload.tenant_id,
                    signed.payload.database_id,
                    signed.payload.policy_id,
                )
                != latest
            ):
                raise PermissionError("J7 authorization inputs became stale")
            try:
                proposal.check_pinned_call(call)
                authenticated = self._trust.append_prepared_self_delivery_policy(call, proposal)
            except (TypeError, ValueError, RuntimeError) as error:
                raise PermissionError("J7 policy append was rejected") from error
            if authenticated.policy != proposal.policy:
                raise RuntimeError("J7 durable policy differs from owner proposal")
            return authenticated

    async def cancel_execution_call(
        self, peer: str, submission: CancelCallSubmission
    ) -> ExecutionCancelledCallReceipt:
        """Mounted native cancellation port; authority remains in its focused runtime."""
        from chiplog.composition.r14_execution_cancellation_runtime import cancel_execution_call

        return await cancel_execution_call(self, peer, submission)

    def _take_h1_scope_append_receipt(
        self, request_id: str, issued: IssuedHermeticOutputScopeV1
    ) -> _H1ScopeAppendReceipt:
        receipt = getattr(self, "_h1_scope_append_receipts", {}).pop(request_id, None)
        if (
            receipt is None
            or receipt.decision_id != issued.anchor.decision.head
            or receipt.scope.revision != issued.revision
            or self._trust.capture_verified_observation() != receipt.after
        ):
            raise LoopRejected("H1 scope append receipt is absent or stale")
        return cast(_H1ScopeAppendReceipt, receipt)

    @staticmethod
    def _purge_expired_h1_scope_wires(
        wires: dict[_H1ScopeWireKey, _H1ScopeWire | None],
    ) -> None:
        now = time.monotonic_ns()
        for key in tuple(wires):
            captured = wires[key]
            if captured is not None and captured.sent.budget.absolute_deadline_ns <= now:
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
        self,
        key: _H1ScopeWireKey,
        call: PublicPortCall,
        result: PublicPortResult,
        *,
        sent_at_ns: int,
        returned_at_ns: int,
    ) -> None:
        wires = self._h1_scope_wires
        if (
            wires.get(key, "missing") is not None
            or key != self._h1_scope_key(key.role, call)
            or sent_at_ns > returned_at_ns
            or returned_at_ns >= call.budget.absolute_deadline_ns
        ):
            wires.pop(key, None)
            raise LoopRejected("H1 scope capture reservation differs")
        if result.request_id != call.request_id or result.responder != call.callee:
            wires.pop(key, None)
            raise LoopRejected("H1 scope capture response differs from sent frame")
        wires[key] = _H1ScopeWire(call, result, sent_at_ns, returned_at_ns)

    def _discard_h1_scope_wire(self, key: _H1ScopeWireKey) -> None:
        getattr(self, "_h1_scope_wires", {}).pop(key, None)

    def _take_h1_scope_wire(
        self,
        role: _H1ScopeRole,
        *,
        request_id: str,
        caller: BrokerSession,
        callee: BrokerSession,
    ) -> _H1ScopeWire:
        """Consume one exact private H1 scope exchange without cross-call reuse."""
        key = _H1ScopeWireKey(role, request_id, caller, callee)
        wires: dict[_H1ScopeWireKey, _H1ScopeWire | None] = getattr(
            self, "_h1_scope_wires", {}
        )
        self._purge_expired_h1_scope_wires(wires)
        captured = wires.pop(key, None)
        if captured is None:
            raise LoopRejected("H1 scope capture is absent or still in flight")
        call, result = captured.sent, captured.returned
        if key != self._h1_scope_key(role, call):
            raise LoopRejected("H1 scope capture key differs from its sent frame")
        if result.request_id != call.request_id or result.responder != call.callee:
            raise LoopRejected("H1 scope capture response differs from sent frame")
        return captured

    def _take_h1_scope_wire_for_request(
        self, role: _H1ScopeRole, *, request_id: str
    ) -> _H1ScopeWire:
        """Consume a uniquely reserved private scope wire without caller-provided sessions."""
        wires: dict[_H1ScopeWireKey, _H1ScopeWire | None] = getattr(
            self, "_h1_scope_wires", {}
        )
        self._purge_expired_h1_scope_wires(wires)
        matches = [key for key in wires if key.role == role and key.request_id == request_id]
        if len(matches) != 1:
            raise LoopRejected("H1 scope capture is absent or ambiguous")
        key = matches[0]
        return self._take_h1_scope_wire(
            role, request_id=request_id, caller=key.caller, callee=key.callee
        )

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
            broker_runtime = self._supervisor.runtime()
            sent_at_ns = time.monotonic_ns()
            response = await broker_runtime.call(request)
            returned_at_ns = time.monotonic_ns()
        except BaseException:
            self._discard_h1_scope_wire(capture_key)
            raise
        try:
            self._record_h1_scope_wire(
                capture_key,
                request,
                response,
                sent_at_ns=sent_at_ns,
                returned_at_ns=returned_at_ns,
            )
        except LoopRejected:
            return NonIssuedHermeticOutputScopeV1(disposition="STALE")
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
                scope_bytes = json.dumps(
                    envelope["payload"]["scope"],
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
                existing = HermeticOutputScopeV1.model_validate_json(scope_bytes)
                if existing.canonical_bytes() != scope_bytes:
                    raise ValueError("historical H1 scope is not canonical")
                if (
                    existing.database_id == candidate.scope.database_id
                    and existing.scope_id == candidate.scope.scope_id
                    and existing.revision == candidate.scope.revision
                ):
                    if existing != candidate.scope:
                        return NonIssuedHermeticOutputScopeV1(disposition="DENIED")
                    authenticated = self._trust.historical_hermetic_output_scope(existing)
                    if (
                        authenticated is None
                        or authenticated.decision_id != existing_id
                        or authenticated.scope != existing
                    ):
                        return NonIssuedHermeticOutputScopeV1(disposition="STALE")
                    issued = self._issued_h1_result(existing_id, existing, disposition="REPLAY")
                    receipts = getattr(self, "_h1_scope_append_receipts", None)
                    if receipts is None:
                        receipts = self._h1_scope_append_receipts = {}
                    receipts[request_id] = _H1ScopeAppendReceipt(
                        request_id, frozen, frozen, existing_id, existing, "REPLAY"
                    )
                    return issued
            before_entries = self._trust._journal.entries()
            before_records = self._trust._materializer.records()
            decision_id, _, _ = self._trust.append_hermetic_output_scope(
                candidate.scope.canonical_bytes()
            )
            after = self._trust.capture_verified_observation()
            after_entries = self._trust._journal.entries()
            after_records = self._trust._materializer.records()
            expected_records = self._trust._expected_records(
                decision_id,
                "HERMETIC_OUTPUT_SCOPE_V1",
                {"scope": candidate.scope.model_dump(mode="json")},
            )
            if (
                after_entries[:-1] != before_entries
                or len(after_entries) != len(before_entries) + 1
                or after_entries[-1][0] != decision_id
                or after_records != before_records + expected_records
            ):
                raise RuntimeError("H1 scope append receipt has an unrelated durable mutation")
            issued = self._issued_h1_result(decision_id, candidate.scope, disposition="ISSUED")
            receipts = getattr(self, "_h1_scope_append_receipts", None)
            if receipts is None:
                receipts = self._h1_scope_append_receipts = {}
            receipts[request_id] = _H1ScopeAppendReceipt(
                request_id, frozen, after, decision_id, candidate.scope, "ISSUED"
            )
            return issued

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
        """Return only the domain DTO; broker frames stay private to H1 consumers."""
        try:
            result, _ = await self._read_current_hermetic_output_scope_with_wire(request)
        except LoopRejected:
            return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
        return result

    async def _read_current_hermetic_output_scope_with_wire(
        self, request: ReadCurrentHermeticExecutionScopeV1
    ) -> tuple[CurrentHermeticExecutionScopeResultV1, _H1ScopeWire]:
        """Read one scope and consume the exact invocation-local owner exchange.

        The public DTO method deliberately cannot expose or later rediscover this
        frame.  A private H1 consumer gets the one frame reserved by this call.
        """
        gate = self._authority_gate()

        with gate.hold():
            snapshot = self._trust.capture_verified_observation().snapshot_bytes
            callee = self._supervisor.runtime().session("deployment_trust")
            request_id = (
                "h1-current:"
                + hashlib.sha256(request.canonical_bytes()).hexdigest()
                + ":"
                + secrets.token_hex(16)
            )
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
        except LoopRejected as error:
            raise LoopRejected("H1 current scope capture cannot be reserved") from error
        try:
            broker_runtime = self._supervisor.runtime()
            sent_at_ns = time.monotonic_ns()
            owner_response = await broker_runtime.call(broker_call)
            returned_at_ns = time.monotonic_ns()
        except BaseException:
            self._discard_h1_scope_wire(capture_key)
            raise
        with gate.hold():
            self._record_h1_scope_wire(
                capture_key,
                broker_call,
                owner_response,
                sent_at_ns=sent_at_ns,
                returned_at_ns=returned_at_ns,
            )
            exact_wire = self._take_h1_scope_wire(
                capture_key.role,
                request_id=capture_key.request_id,
                caller=capture_key.caller,
                callee=capture_key.callee,
            )
            exact_response = exact_wire.returned
        owner_response = exact_response
        if (
            not isinstance(owner_response, PublicPortSuccess)
            or owner_response.request_id != broker_call.request_id
            or owner_response.responder != callee
            or owner_response.schema_id
            != "chiplog.deployment-trust.current-hermetic-output-scope-result.v1"
        ):
            return (
                NonCurrentHermeticExecutionScopeV1(disposition="STALE"),
                exact_wire,
            )
        try:
            owner_candidate = H1OwnerCurrentCandidateV1.model_validate_json(
                owner_response.canonical_payload
            )
            if owner_candidate.canonical_bytes() != owner_response.canonical_payload:
                return (
                    NonCurrentHermeticExecutionScopeV1(disposition="STALE"),
                    exact_wire,
                )
            owner_candidate.check_pinned_call(current_call)
        except ValueError:
            return (
                NonCurrentHermeticExecutionScopeV1(disposition="DENIED"),
                exact_wire,
            )
        with gate.hold():
            return (
                self._validate_current_hermetic_output_scope_held(
                    request, owner_candidate, callee=callee
                ),
                exact_wire,
            )

    def _replay_current_hermetic_output_scope_held(
        self,
        request: ReadCurrentHermeticExecutionScopeV1,
        owner_candidate: H1OwnerCurrentCandidateV1,
        *,
        callee: BrokerSession,
    ) -> CurrentHermeticExecutionScopeResultV1:
        """Revalidate one retained CURRENT candidate at the installed H1 final fence."""
        gate = self._authority_gate()
        gate.require_held()
        port = getattr(self, "_h1_preissuance_registration_source_port", None)
        resources = self._require_dispatch_resources()
        if (
            getattr(port, "_runtime", None) is not self
            or getattr(port, "_gate", None) is not gate
            or resources._require_gate() is not gate
        ):
            raise LoopRejected("H1 current replay requires the installed runtime shared gate")
        result: CurrentHermeticExecutionScopeResultV1 = (
            self._validate_current_hermetic_output_scope_held(
            request, owner_candidate, callee=callee
            )
        )
        return result

    def _validate_current_hermetic_output_scope_held(
        self,
        request: ReadCurrentHermeticExecutionScopeV1,
        owner_candidate: H1OwnerCurrentCandidateV1,
        *,
        callee: BrokerSession,
    ) -> CurrentHermeticExecutionScopeResultV1:
        """Recompute a pinned owner candidate from the one gate-held live cut."""
        self._authority_gate().require_held()
        from chiplog.composition.h1_selected_output_sources import H1SelectedOutputSources

        frozen = self._trust.capture_verified_observation()
        if not self._matches_h1_trust_observation(request.expected_trust_observation):
            return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
        anchor = request.source_anchor
        try:
            authenticated = self._trust.current_hermetic_output_scope(anchor)
        except RuntimeError, TypeError:
            return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
        decision = next(
            (
                raw
                for decision_id, _, raw in self._trust._journal.entries()
                if decision_id == anchor.decision.head
            ),
            None,
        )
        if decision is None or hashlib.sha256(decision).hexdigest() != anchor.decision.fingerprint:
            return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
        record = self._trust._materializer.record(anchor.decision.head, anchor.record_ordinal)
        if record is None or hashlib.sha256(record).hexdigest() != anchor.record.fingerprint:
            return NonCurrentHermeticExecutionScopeV1(disposition="STALE")
        record_identity = "trust-record:" + anchor.decision.head + ":" + str(anchor.record_ordinal)
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
            or scope != authenticated.scope
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
        expected = CurrentHermeticExecutionScopeV1(
            disposition="CURRENT",
            scope_ref=scope_ref,
            source_anchor=anchor,
            selector_generation=0,
            ordered_current_source_refs=(
                request.admitted_authentication_ref,
                anchor.decision,
                anchor.record,
            ),
        )
        if owner_candidate.current != expected:
            return NonCurrentHermeticExecutionScopeV1(disposition="DENIED")
        return expected

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
                    recovered = await self._resume_h1_postseal_recovery(
                        request.identity, request.original_driver_command_fingerprint
                    )
                    if recovered is not None:
                        return recovered
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
                "hermetic-ingress", run.run_id, captured.head, profile="H1_V2"
            )
            if sealed.state != "ACTIVE":
                raise LoopRejected("first-path complete seal is not an active Run")
            recovered = await self._resume_h1_postseal_recovery(
                request.identity, request.original_driver_command_fingerprint
            )
            if recovered is not None:
                return recovered
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

    async def finalize_execution(
        self, original_identity: DriverCommandIdentityV1, original_fingerprint: str
    ) -> CommonExecutionResultV1:
        """Run the separate installed H1 finalizer for one authenticated H0 input.

        Unlike ``advance_execution``, this never creates or resumes recovery
        evidence.  The coordinator first reconciles a selected/pending terminal
        publication under its cross-process lease; only proven absence can enter
        the installed B/P finalization continuation.
        """
        from chiplog.composition.h1_postseal_recovery_coordinator import (
            H1PostSealRecoveryCoordinatorError,
            H1PostSealRecoveryPublicationIntegrityError,
            _H1FinalizationRejection,
        )
        from chiplog.platform._owner_publication_contracts import PublicationRejected
        from chiplog.platform.owner_publications import OwnerPublicationUncertain

        try:
            await self._execution_actor("hermetic-ingress")
            found = self._find(original_identity, original_fingerprint)
            if found is None:
                return ExecutionDriverRejectedV1(
                    identity=original_identity,
                    original_driver_command_fingerprint=original_fingerprint,
                    code="HOLD",
                    reason="selected H0 Run is unavailable for finalization",
                )
            coordinator = getattr(self, "_h1_postseal_recovery_coordinator", None)
            if coordinator is None:
                raise LoopRejected("installed H1 post-seal recovery coordinator is absent")
            # The coordinator returns an authority-internal broker outcome.
            # The selected broker decision needs its exact physical readback
            # before it can become a public terminal receipt.
            outcome = await coordinator.finalize_selected(original_identity, original_fingerprint)
            if type(outcome) is _H1FinalizationRejection:
                publication = outcome.publication
                if type(publication) is not PublicationRejected:
                    raise _FinalizationReceiptIntegrityError(
                        "finalization rejection outcome differs"
                    )
                return ExecutionDriverRejectedV1(
                    identity=original_identity,
                    original_driver_command_fingerprint=original_fingerprint,
                    code=publication.kind,
                    reason=publication.reason,
                )
            return self._project_finalization_receipt(
                found[0], original_identity, original_fingerprint, outcome
            )
        except _DriverConflict as error:
            return ExecutionDriverRejectedV1(
                identity=original_identity,
                original_driver_command_fingerprint=original_fingerprint,
                code="CONFLICT",
                reason=str(error),
            )
        except _FinalizationReceiptIntegrityError as error:
            return ExecutionDriverRejectedV1(
                identity=original_identity,
                original_driver_command_fingerprint=original_fingerprint,
                code="INTEGRITY_FAULT",
                reason=str(error),
            )
        except H1PostSealRecoveryPublicationIntegrityError as error:
            return ExecutionDriverRejectedV1(
                identity=original_identity,
                original_driver_command_fingerprint=original_fingerprint,
                code="INTEGRITY_FAULT",
                reason=str(error),
            )
        except OwnerPublicationUncertain as error:
            return UncertainExecutionPublicationV1(
                identity=original_identity,
                original_driver_command_fingerprint=original_fingerprint,
                operation="h1.finalize_execution",
                reason=str(error),
            )
        except (
            H1PostSealRecoveryCoordinatorError,
            LoopRejected,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as error:
            return ExecutionDriverRejectedV1(
                identity=original_identity,
                original_driver_command_fingerprint=original_fingerprint,
                code="HOLD",
                reason=str(error),
            )

    def _project_finalization_receipt(
        self,
        evidence: RetainedInboxExecutionInitialization,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        outcome: object,
    ) -> SelectedExecutionReceiptV1:
        """Project only the authority's exact terminal physical readback."""
        from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
        from chiplog.composition.h1_live_publication_authority import (
            _H1SelectedTerminalReadback,
        )
        from chiplog.composition.h1_postseal_recovery_coordinator import _H1FinalizationOutcome
        from chiplog.platform._owner_publication_contracts import JournalSelectedPublication
        from chiplog.platform._sqlite import PhysicalPublicationCommand
        from chiplog.platform.owner_publications import SelectedOwnerDecision

        if type(outcome) is not _H1FinalizationOutcome:
            raise _FinalizationReceiptIntegrityError("finalization outcome differs")
        publication, readback = outcome.publication, outcome.readback
        if (
            type(publication) is not JournalSelectedPublication
            or type(readback) is not _H1SelectedTerminalReadback
            or publication.kind not in ("COMMITTED", "EXACT_REPLAY")
        ):
            raise _FinalizationReceiptIntegrityError("finalization readback is unavailable")
        selected = readback.selected_owner_decision
        command = readback.physical_command
        terminal = readback.terminal_run
        if (
            type(selected) is not SelectedOwnerDecision
            or type(command) is not PhysicalPublicationCommand
        ):
            raise _FinalizationReceiptIntegrityError("finalization physical readback is untyped")
        if (
            type(readback.retained_h0) is not RetainedInboxExecutionInitialization
            or readback.retained_h0 != evidence
        ):
            raise _FinalizationReceiptIntegrityError("finalization retained H0 differs")
        try:
            driver = DriveInputRequestV1.model_validate_json(evidence.driver_request_bytes)
        except ValueError as error:
            raise _FinalizationReceiptIntegrityError(
                "finalization retained H0 is invalid"
            ) from error
        if (
            driver.canonical_bytes() != evidence.driver_request_bytes
            or driver.identity != identity
            or driver.original_driver_command_fingerprint() != original_fingerprint
            or evidence.driver_request_fingerprint != original_fingerprint
        ):
            raise _FinalizationReceiptIntegrityError("finalization retained H0 caller differs")
        if (
            selected.decision_id != publication.decision_id
            or selected.decision_head != publication.decision_head
            or selected.decision_fingerprint != publication.decision_fingerprint
            or selected.resulting_commitment != publication.resulting_commitment
            or selected.tenant_commit_sequence != publication.tenant_commit_sequence
            or command.idempotency_key != publication.command_id
            or type(terminal) is not ExecutionRunRecord
            or terminal.run_id != evidence.proposal.run.run_id
            or terminal.state != "SUCCEEDED"
            or terminal.event != "ExecutionCompleted"
            or not any(
                record.canonical_bytes == terminal.canonical_bytes() for record in command.records
            )
        ):
            raise _FinalizationReceiptIntegrityError("finalization physical readback differs")
        for head in (
            readback.acceptance_head,
            readback.delivery_manifest_head,
            readback.committed_conversation_projection_head,
        ):
            if type(head) is not ExactHead:
                raise _FinalizationReceiptIntegrityError(
                    "finalization terminal detail head differs"
                )

        admitted = evidence.request.admitted

        def ingress_head(value: CallSubjectHead) -> Head:
            if type(value) is not CallSubjectHead or type(value.revision) is not Present:
                raise _FinalizationReceiptIntegrityError("finalization retained H0 head differs")
            return Head(
                identity=value.subject_id,
                head=value.revision.head,
                fingerprint=value.revision.fingerprint,
            )

        def public_head(value: ExactHead) -> Head:
            return Head(identity=value.identity, head=value.head, fingerprint=value.fingerprint)

        return SelectedExecutionReceiptV1(
            disposition=publication.kind,
            identity=identity,
            original_driver_command_fingerprint=original_fingerprint,
            selected_ingress_decision=ingress_head(admitted.selected_decision),
            selected_custody=ingress_head(admitted.custody),
            selected_admitted_input=ingress_head(admitted.inbox),
            stable_run_lineage_id=terminal.run_id,
            selected_run_head=Head(
                identity=terminal.head,
                head=terminal.head,
                fingerprint=hashlib.sha256(terminal.canonical_bytes()).hexdigest(),
            ),
            selected_run_state=terminal.state,
            selected_journal_decision=Head(
                identity=publication.command_id,
                head=publication.decision_head,
                fingerprint=publication.decision_fingerprint,
            ),
            commit_sequence=publication.tenant_commit_sequence,
            phase="TERMINAL",
            terminal_detail=AcceptedTerminalDetailV1(
                acceptance_head=public_head(readback.acceptance_head),
                delivery_manifest_head=public_head(readback.delivery_manifest_head),
                committed_conversation_projection_head=public_head(
                    readback.committed_conversation_projection_head
                ),
            ),
        )

    async def _resume_h1_postseal_recovery(
        self, identity: DriverCommandIdentityV1, original_fingerprint: str
    ) -> ExecutionDriverRejectedV1 | None:
        """Run the installed ROOT-only recovery boundary without changing receipt shape."""
        from chiplog.composition.h1_postseal_recovery_coordinator import (
            H1PostSealRecoveryCoordinatorError,
        )

        coordinator = getattr(self, "_h1_postseal_recovery_coordinator", None)
        if coordinator is None:
            if getattr(self, "_h1_recovery_mount", None) is not None:
                raise LoopRejected("installed H1 post-seal recovery coordinator is absent")
            return None
        try:
            await coordinator.resume_selected(identity, original_fingerprint)
        except H1PostSealRecoveryCoordinatorError as error:
            return ExecutionDriverRejectedV1(
                identity=identity,
                original_driver_command_fingerprint=original_fingerprint,
                code="HOLD",
                reason=str(error),
            )
        return None

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


@asynccontextmanager
async def open_installed_h1_runtime(
    launch: object,
    *,
    resources: HermeticDispatchResources,
    responses: tuple[bytes, ...] = (),
) -> AsyncIterator[CommonCliExecutionRuntime]:
    """Open the one enrolled H1 slot without bootstrap or caller-selected storage."""
    # These imports must remain local: the private port imports this module's
    # canonical runtime class and a module-level reciprocal import is cyclic.
    from chiplog.composition.h1_launch_enrollment import InstalledH1Launch
    from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority
    from chiplog.composition.h1_runtime_preissuance_port import _H1RuntimePreissuancePort
    from chiplog.platform.owner_publications import BrokerPublicationCoordinator

    if type(launch) is not InstalledH1Launch:
        raise TypeError("installed H1 runtime requires an installed launch")
    launch.assert_current()
    installed_trust = launch._slot._trust.verify()
    if installed_trust is None or installed_trust.phase != "ACTIVE":
        raise ValueError("installed H1 runtime requires preexisting ACTIVE trust")

    model = HermeticModel(responses)
    evidence_reader: list[Any] = []
    enrolled_mount: list[Any] = []
    recovery_journal: list[Any] = []
    recovery_mount: list[Any] = []
    authority = H1LivePublicationAuthority()
    authority_gate: Any | None = None
    root_bound = False

    live_mount = _H1LiveCompletionMount(authority)

    def revoke_and_unbind_live_authority() -> None:
        """Close the future capability owner before releasing its exact root issuer."""
        nonlocal root_bound
        live_mount._revoke_all()
        if not root_bound:
            return
        if authority_gate is None or len(evidence_reader) != 1:
            raise RuntimeError("installed live H1 root cleanup is incomplete")
        with cast(Any, authority_gate).hold():
            evidence_reader[0]._unbind_private_root_issuer(authority)
        root_bound = False

    def preflight_roles(gate: object) -> tuple[object, object]:
        # The launch owns both protected role markers.  Validate both before
        # _open_runtime constructs any mutable runtime sidecars.
        return (
            launch.open_enrolled_evidence_mount(gate),
            launch.open_enrolled_recovery_mount(gate),
        )

    def bind_installed_roles(store: object, gate: object, mounted: object) -> None:
        from chiplog.composition.h1_delivery_evidence_journal import H1DeliveryEvidenceJournal
        from chiplog.composition.h1_launch_enrollment import (
            EnrolledH1EvidenceMount,
            EnrolledH1RecoveryMount,
        )
        from chiplog.composition.h1_postseal_recovery import H1PostSealRecoveryJournal

        if type(mounted) is not tuple or len(mounted) != 2:
            raise RuntimeError("installed H1 role mounts differ")
        role_mounts = cast(tuple[object, object], mounted)
        if (
            type(role_mounts[0]) is not EnrolledH1EvidenceMount
            or type(role_mounts[1]) is not EnrolledH1RecoveryMount
            or role_mounts[0].authority_gate is not gate
            or role_mounts[1].authority_gate is not gate
            or getattr(store, "authority_gate", None) is not gate
            or role_mounts[0].tenant_id != launch._slot.tenant_id
            or role_mounts[1].tenant_id != launch._slot.tenant_id
        ):
            raise RuntimeError("installed H1 role mount gate or tenant differs")
        evidence_mount = role_mounts[0]
        mounted_recovery = role_mounts[1]
        nonlocal authority_gate
        launch.custody.bind_authority_gate(gate, launch.database_path, launch.database_identity)
        authority_gate = gate
        enrolled_mount.append(evidence_mount)
        recovery_mount.append(mounted_recovery)
        journal = H1DeliveryEvidenceJournal.open_enrolled(evidence_mount)
        evidence_reader.append(journal)
        try:
            # This existing-only opener scans the complete authenticated
            # prefix under the exact runtime gate before it is retained.
            recovery_journal.append(H1PostSealRecoveryJournal.open_enrolled(mounted_recovery))
            cast(Any, store)._mount_owner_publication_resolver(authority)
        except BaseException:
            for opened in recovery_journal:
                opened.close()
            recovery_journal.clear()
            revoke_and_unbind_live_authority()
            raise

    runtime: CommonCliExecutionRuntime | None = None

    def installed_runtime_setup(opened: object) -> None:
        nonlocal root_bound, runtime
        runtime = cast(CommonCliExecutionRuntime, opened)
        runtime._bind_h1_historical_custody_path(resources._custody_path)
        if hasattr(runtime, "_j7_operator_policy_key_pin"):
            raise RuntimeError("installed J7 operator policy pin is already mounted")
        # The supervisor derives the trusted tenant/database scope from its
        # authenticated startup state.  An absent or invalid provisioned pin
        # leaves H1 available, but the public J7 route denies closed.
        runtime._j7_operator_policy_key_pin = (
            runtime._supervisor._install_j7_operator_policy_key_pin()
        )
        if len(evidence_reader) != 1 or len(recovery_journal) != 1 or len(recovery_mount) != 1:
            raise RuntimeError("installed H1 role reader is absent")
        runtime._h1_delivery_evidence_journal = evidence_reader[0]
        # Retain the one authenticated recovery graph before any later B
        # owner could publish a recovery callable.
        runtime._h1_recovery_mount = recovery_mount[0]
        runtime._h1_postseal_recovery_journal = recovery_journal[0]
        from chiplog.composition.h1_postseal_recovery_coordinator import (
            _H1PostSealRecoveryCoordinator,
        )

        runtime._h1_postseal_recovery_coordinator = _H1PostSealRecoveryCoordinator(runtime)
        if any(
            hasattr(runtime, name)
            for name in (
                "_h1_live_completion_mount",
                "_h1_live_publication_authority",
                "_h1_live_publication_coordinator",
            )
        ):
            raise RuntimeError("installed H1 live publication authority is already mounted")
        live_mount._bind_runtime(runtime)
        runtime._h1_live_completion_mount = live_mount  # type: ignore[attr-defined]
        runtime._h1_live_publication_authority = authority  # type: ignore[attr-defined]
        runtime._h1_live_publication_coordinator = BrokerPublicationCoordinator(  # type: ignore[attr-defined]
            runtime._appender, authority, runtime._owner_decisions()
        )
        if authority_gate is None:
            raise RuntimeError("installed live H1 root gate is absent")
        with cast(Any, authority_gate).hold():
            evidence_reader[0]._bind_private_root_issuer(authority, live_mount)
        root_bound = True
        if getattr(runtime, "_h1_preissuance_registration_source_port", None) is not None:
            raise RuntimeError("installed H1 runtime port is already mounted")
        # Startup validates selected H1 completions before _open_runtime yields.
        # Its historical P reader needs this installed owner at that point.
        runtime._h1_preissuance_registration_source_port = _H1RuntimePreissuancePort(
            runtime, launch
        )
        if getattr(runtime, "_h1_conversation_source_port", None) is not None:
            raise RuntimeError("installed H1 conversation source is already mounted")
        from chiplog.composition.h1_conversation_sources import H1ConversationSources

        runtime._h1_conversation_source_port = H1ConversationSources(runtime)

    with _configured(resources):
        try:
            async with _open_runtime(
                launch.database_path,
                tenant_id=launch._slot.tenant_id,
                operator_secret=b"r13-hermetic-only",
                runtime_type=CommonCliExecutionRuntime,
                manifest=R14_R17_H1_LOCAL_EFFECTS_J7_PRODUCTION_MANIFEST,
                extra_leaves={
                    "model": model,
                    "effects_transport": resources.require_original_provider(),
                },
                preflight=preflight_roles,
                store_setup=bind_installed_roles,
                runtime_setup=installed_runtime_setup,
            ) as opened:
                runtime = cast(CommonCliExecutionRuntime, opened)
                runtime._execution_model = model
                model.session = runtime
                launch.assert_current()
                from chiplog.composition.h1_preseal_native_source import H1PresealNativeSource

                if getattr(runtime, "_h1_preseal_native_source", None) is not None:
                    raise RuntimeError("installed H1 preseal native source is already mounted")
                preseal_native_source = H1PresealNativeSource(runtime)
                runtime._h1_preseal_native_source = preseal_native_source  # type: ignore[attr-defined]
                port = cast(
                    _H1RuntimePreissuancePort,
                    runtime._h1_preissuance_registration_source_port,
                )
                worker_owner: Any | None = None
                member_evidence: Any | None = None
                worker_evidence: Any | None = None
                decision_owner: Any | None = None
                completion_exchange_registry: Any | None = None
                conversation_sources: Any | None = runtime._h1_conversation_source_port
                live_enrollment: Any | None = None
                live_invocation_source: Any | None = None
                live_readplan_source: Any | None = None
                try:
                    from chiplog.composition.h1_completion_exchange_registry import (
                        H1CompletionExchangeRegistry,
                    )
                    from chiplog.composition.h1_delivery_evidence_journal import (
                        H1DeliveryEvidenceJournal,
                    )
                    from chiplog.composition.h1_first_path_sources import H1FirstPathSources
                    from chiplog.composition.h1_launch_enrollment import EnrolledH1EvidenceMount
                    from chiplog.composition.h1_live_completion_enrollment import (
                        _H1LiveCompletionEnrollment,
                    )
                    from chiplog.composition.h1_live_invocation_source import (
                        H1LiveInvocationSource,
                    )
                    from chiplog.composition.h1_live_readplan_source import H1LiveReadPlanSource
                    from chiplog.composition.h1_native_member_sources import H1NativeMemberSources
                    from chiplog.composition.h1_pre_request_member_evidence import (
                        H1PreRequestMemberEvidence,
                    )
                    from chiplog.composition.h1_pre_request_worker_evidence import (
                        H1PreRequestWorkerEvidence,
                    )
                    from chiplog.composition.h1_worker_evidence import (
                        _H1InstalledWorkerEvidenceOwner,
                    )

                    if (
                        len(enrolled_mount) != 1
                        or type(enrolled_mount[0]) is not EnrolledH1EvidenceMount
                    ):
                        raise RuntimeError("installed enrolled evidence mount is absent")
                    mount = enrolled_mount[0]
                    first_path = H1FirstPathSources(runtime)
                    native_sources = H1NativeMemberSources(runtime, first_path)
                    worker_owner = _H1InstalledWorkerEvidenceOwner(
                        runtime, runtime._supervisor, launch, mount, native_sources
                    )
                    if any(
                        getattr(runtime, name, None) is not None
                        for name in (
                            "_h1_first_path_sources",
                            "_h1_native_member_sources",
                            "_h1_installed_worker_evidence_owner",
                            "_h1_pre_request_member_evidence",
                            "_h1_pre_request_worker_evidence",
                            "_h1_completion_exchange_registry",
                            "_h1_live_completion_enrollment",
                            "_h1_live_invocation_source",
                            "_h1_live_readplan_source",
                        )
                    ):
                        raise RuntimeError("installed H1 worker sources are already mounted")
                    runtime._h1_first_path_sources = first_path
                    runtime._h1_native_member_sources = native_sources
                    completion_exchange_registry = H1CompletionExchangeRegistry(
                        runtime=runtime,
                        native_sources=runtime._h1_native_member_sources,
                        scope_port=runtime._h1_preissuance_registration_source_port,
                    )
                    runtime._h1_completion_exchange_registry = completion_exchange_registry
                    journal = cast(H1DeliveryEvidenceJournal, runtime._h1_delivery_evidence_journal)
                    member_evidence = H1PreRequestMemberEvidence(
                        journal,
                        runtime._h1_native_member_sources,
                        port,
                    )
                    runtime._h1_pre_request_member_evidence = member_evidence
                    runtime._h1_installed_worker_evidence_owner = worker_owner
                    worker_evidence = H1PreRequestWorkerEvidence(
                        journal,
                        runtime._h1_native_member_sources,
                        runtime._h1_installed_worker_evidence_owner,
                    )
                    runtime._h1_pre_request_worker_evidence = worker_evidence
                    from chiplog.composition.h1_preseal_pe_decision import (
                        H1PresealPEDecisionOwner,
                    )

                    if getattr(runtime, "_h1_preseal_pe_decision_owner", None) is not None:
                        raise RuntimeError("installed H1 P/E decision owner is already mounted")
                    decision_owner = H1PresealPEDecisionOwner(
                        runtime,
                        preseal_native_source,
                        port,
                        member_evidence,
                        worker_owner,
                    )
                    runtime._h1_preseal_pe_decision_owner = decision_owner  # type: ignore[attr-defined]
                    live_enrollment = _H1LiveCompletionEnrollment(
                        runtime=runtime,
                        first_path_sources=runtime._h1_first_path_sources,
                        native_sources=runtime._h1_native_member_sources,
                        scope_port=runtime._h1_preissuance_registration_source_port,
                        conversation_sources=runtime._h1_conversation_source_port,
                        completion_registry=runtime._h1_completion_exchange_registry,
                        authority=runtime._h1_live_publication_authority,  # type: ignore[attr-defined]
                        publication_mount=runtime._h1_live_completion_mount,  # type: ignore[attr-defined]
                        gate=runtime._authority_gate(),
                    )
                    runtime._h1_live_completion_enrollment = live_enrollment  # type: ignore[attr-defined]
                    live_invocation_source = H1LiveInvocationSource(runtime)
                    runtime._h1_live_invocation_source = live_invocation_source
                    live_readplan_source = H1LiveReadPlanSource(runtime)
                    runtime._h1_live_readplan_source = live_readplan_source
                    authority._bind_installed_runtime(runtime)
                    yield runtime
                finally:
                    # This owner holds capabilities issued by the native, P, and E
                    # owners below.  Revoke it while all of them are still live.
                    if decision_owner is not None:
                        decision_owner.revoke()
                    if hasattr(runtime, "_h1_preseal_pe_decision_owner"):
                        del runtime._h1_preseal_pe_decision_owner
                    coordinator = getattr(runtime, "_h1_postseal_recovery_coordinator", None)
                    if coordinator is not None:
                        coordinator.close()
                    if hasattr(runtime, "_h1_postseal_recovery_coordinator"):
                        del runtime._h1_postseal_recovery_coordinator
                    if live_invocation_source is not None:
                        live_invocation_source._revoke_all()
                    if hasattr(runtime, "_h1_live_invocation_source"):
                        del runtime._h1_live_invocation_source
                    if live_readplan_source is not None:
                        live_readplan_source._revoke_all()
                    if hasattr(runtime, "_h1_live_readplan_source"):
                        del runtime._h1_live_readplan_source
                    if live_enrollment is not None:
                        live_enrollment._revoke_all()
                    if hasattr(runtime, "_h1_live_completion_enrollment"):
                        del runtime._h1_live_completion_enrollment
                    if conversation_sources is not None:
                        conversation_sources._revoke_all()
                    if hasattr(runtime, "_h1_conversation_source_port"):
                        del runtime._h1_conversation_source_port
                    if completion_exchange_registry is not None:
                        completion_exchange_registry._revoke_all()
                    if hasattr(runtime, "_h1_completion_exchange_registry"):
                        del runtime._h1_completion_exchange_registry
                    if member_evidence is not None:
                        member_evidence._revoke_all()
                    if hasattr(runtime, "_h1_pre_request_member_evidence"):
                        del runtime._h1_pre_request_member_evidence
                    if worker_evidence is not None:
                        worker_evidence._revoke_all()
                    if hasattr(runtime, "_h1_pre_request_worker_evidence"):
                        del runtime._h1_pre_request_worker_evidence
                    if worker_owner is not None:
                        worker_owner._revoke_all()
                    for name in (
                        "_h1_installed_worker_evidence_owner",
                        "_h1_native_member_sources",
                        "_h1_first_path_sources",
                    ):
                        if hasattr(runtime, name):
                            delattr(runtime, name)
                    del runtime._h1_preissuance_registration_source_port
                    preseal_native_source.revoke()
                    delattr(runtime, "_h1_preseal_native_source")
                    revoke_and_unbind_live_authority()
                    for name in (
                        "_h1_live_publication_coordinator",
                        "_h1_live_publication_authority",
                        "_h1_live_completion_mount",
                    ):
                        if hasattr(runtime, name):
                            delattr(runtime, name)
        finally:
            # Covers failures before _open_runtime can yield its runtime.  On
            # the ordinary path the inner finally already released this while
            # the appender remained open.
            revoke_and_unbind_live_authority()
            if runtime is not None:
                retained_decision_owner = getattr(runtime, "_h1_preseal_pe_decision_owner", None)
                if retained_decision_owner is not None:
                    retained_decision_owner.revoke()
                    delattr(runtime, "_h1_preseal_pe_decision_owner")
                coordinator = getattr(runtime, "_h1_postseal_recovery_coordinator", None)
                if coordinator is not None:
                    coordinator.close()
                if hasattr(runtime, "_h1_postseal_recovery_coordinator"):
                    del runtime._h1_postseal_recovery_coordinator
                retained_invocation_source = getattr(runtime, "_h1_live_invocation_source", None)
                if retained_invocation_source is not None:
                    retained_invocation_source._revoke_all()
                    del runtime._h1_live_invocation_source
                retained_readplan_source = getattr(runtime, "_h1_live_readplan_source", None)
                if retained_readplan_source is not None:
                    retained_readplan_source._revoke_all()
                    del runtime._h1_live_readplan_source
                retained_conversation_source = getattr(
                    runtime, "_h1_conversation_source_port", None
                )
                if retained_conversation_source is not None:
                    retained_conversation_source._revoke_all()
                    del runtime._h1_conversation_source_port
                if hasattr(runtime, "_h1_postseal_recovery_journal"):
                    del runtime._h1_postseal_recovery_journal
                if hasattr(runtime, "_h1_recovery_mount"):
                    del runtime._h1_recovery_mount
                retained_preseal_native_source = getattr(runtime, "_h1_preseal_native_source", None)
                if retained_preseal_native_source is not None:
                    retained_preseal_native_source.revoke()
                    delattr(runtime, "_h1_preseal_native_source")
                if hasattr(runtime, "_h1_preissuance_registration_source_port"):
                    del runtime._h1_preissuance_registration_source_port
            for opened in recovery_journal:
                opened.close()
            for mounted_recovery in recovery_mount:
                mounted_recovery.close()
            for reader in evidence_reader:
                reader.close()
            if authority_gate is not None:
                launch.custody.unbind_authority_gate(authority_gate)
            if runtime is not None and hasattr(runtime, "_h1_delivery_evidence_journal"):
                del runtime._h1_delivery_evidence_journal
