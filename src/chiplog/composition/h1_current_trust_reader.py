"""Inert, fresh H1 trust observation for one joined V4/current-g1 capture."""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar, cast

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticTrustObservationV1,
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantAnchorV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_policy_contracts import (
    PreparedExternalSelfDeliveryPolicyAnchorV1,
)
from chiplog.capabilities.effects.contracts import ExactHead as DispatchExactHead
from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.h1_local_preparation_contracts import H1SelectedScopeSourceV1
from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
    H1ScopedDeliveryAuthorityEvidenceV1,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_scoped_authorization_delivery_work_reader import (
    H1ScopedAuthorizationDeliveryWorkCaptureV1,
    H1ScopedAuthorizationDeliveryWorkHold,
    H1ScopedAuthorizationDeliveryWorkReaderV1,
)
from chiplog.platform.prepared_external_delivery_grant_lineage import (
    AuthenticatedPreparedExternalDeliveryGrant,
)
from chiplog.platform.prepared_self_delivery_policy_lineage import (
    AuthenticatedPreparedSelfDeliveryPolicy,
)
from chiplog.platform.r7_trust import encode_trust_journal


class H1CurrentTrustHold(RuntimeError):
    """The exact V4/current-g1 cut or its fresh owner proof is unavailable."""


@dataclass(frozen=True, slots=True)
class _CurrentTrustRecord:
    """Private retained inputs; the joined capture remains borrower-owned."""

    joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    selected_scope: H1SelectedScopeSourceV1
    authority_evidence: object
    request: ReadCurrentHermeticExecutionScopeV1


class H1CurrentTrustCaptureV1:
    """Opaque capture issued by one installed current-trust reader."""

    __slots__ = ("_record",)
    _record: _CurrentTrustRecord

    def __init__(self) -> None:
        raise TypeError("H1 current-trust captures are reader-issued")

    def __copy__(self) -> H1CurrentTrustCaptureV1:
        raise TypeError("H1 current-trust captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1CurrentTrustCaptureV1:
        del memo
        raise TypeError("H1 current-trust captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 current-trust captures cannot be serialized")


T = TypeVar("T")


class H1CurrentTrustReaderV1:
    """Observe trust freshly; it never grants authority or transports a SEND."""

    __slots__ = ("_closed", "_issued", "_joined", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 current-trust reader requires installed runtime")
        joined = getattr(runtime, "_h1_scoped_authorization_delivery_work_reader_v1", None)
        if (
            type(joined) is not H1ScopedAuthorizationDeliveryWorkReaderV1
            or joined._runtime is not runtime
        ):
            raise TypeError("H1 current-trust reader requires mounted V4/current-g1 reader")
        self._runtime = runtime
        self._joined = joined
        self._issued: dict[int, tuple[H1CurrentTrustCaptureV1, _CurrentTrustRecord]] = {}
        self._closed = False

    def capture(
        self, joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1CurrentTrustCaptureV1:
        """Borrow the mounted joined cut; do not take ownership of its release."""
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._capture_held(joined_capture)
            capture = object.__new__(H1CurrentTrustCaptureV1)
            capture._record = record
            self._issued[id(capture)] = (capture, record)
            return capture

    async def with_current_held(
        self, capture: H1CurrentTrustCaptureV1, consume: Callable[[CapturedSource], T]
    ) -> T:
        """Run one synchronous consumer after a new owner proof and held recheck."""
        if not callable(consume):
            raise TypeError("H1 current-trust consumer must be callable")
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._issued_record_held(capture)
            fresh_record = self._capture_held(record.joined_capture)
            if fresh_record != record:
                raise H1CurrentTrustHold("H1 current-trust inputs changed before owner read")
            request = record.request

        try:
            result, wire = await self._runtime._read_current_hermetic_output_scope_with_wire(
                request
            )
        except BaseException:
            # The runtime discards its invocation-local wire on every failure.
            raise

        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._issued_record_held(capture)
            fresh_record = self._capture_held(record.joined_capture)
            if fresh_record != record:
                raise H1CurrentTrustHold("H1 current-trust inputs changed during owner read")
            if type(result) is not CurrentHermeticExecutionScopeV1:
                raise H1CurrentTrustHold("fresh H1 current scope is not CURRENT")
            selected = self._fresh_selected_scope_held(record.selected_scope, request, result)
            self._verify_exact_wire_held(wire, selected, request)
            role = self._replay_and_role_held(record, wire, request, result)
            consumed = consume(role)
            if inspect.isawaitable(consumed):
                close = getattr(consumed, "close", None)
                if callable(close):
                    close()
                raise TypeError("H1 current-trust consumer must not return an awaitable")
            return consumed

    def release(self, capture: H1CurrentTrustCaptureV1) -> None:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            self._issued_record_held(capture)
            del self._issued[id(capture)]

    def close(self) -> None:
        with self._runtime._authority_gate().hold():
            if self._closed:
                return
            self._closed = True
            self._issued.clear()

    def _require_open_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1CurrentTrustHold("H1 current-trust reader is closed")

    def _issued_record_held(self, capture: H1CurrentTrustCaptureV1) -> _CurrentTrustRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1CurrentTrustCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1CurrentTrustHold("H1 current-trust capture is not reader-issued")
        return issued[1]

    def _capture_held(
        self, joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> _CurrentTrustRecord:
        self._runtime._authority_gate().require_held()
        try:
            self._joined.recheck_held(joined_capture)
            selected = self._selected_scope_held(joined_capture)
            authority_evidence = self._authority_evidence_held(joined_capture)
            observation = self._trust_observation_held()
            request = selected.current_request.model_copy(
                update={"expected_trust_observation": observation}
            )
            if request.source_anchor != selected.current_request.source_anchor:
                raise ValueError("fresh scope anchor differs")
            self._validate_current_authority_held(authority_evidence)
        except (
            H1ScopedAuthorizationDeliveryWorkHold,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1CurrentTrustHold("H1 current-trust inputs are unavailable") from error
        return _CurrentTrustRecord(joined_capture, selected, authority_evidence, request)

    def _selected_scope_held(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1SelectedScopeSourceV1:
        record = self._joined._issued_record_held(capture)
        effects = self._joined._effects._issued_record_held(record.effects_capture)
        issuance = effects.selected.selected_h1.issuance
        selected = getattr(getattr(issuance, "effects_call", None), "request", None)
        selected_scope = getattr(selected, "selected_scope", None)
        if type(selected_scope) is not H1SelectedScopeSourceV1:
            raise H1CurrentTrustHold("selected V4 scope is unavailable")
        if self._joined.scope_held(capture) != selected_scope.scope:
            raise H1CurrentTrustHold("selected V4 scope differs from joined reader")
        return selected_scope

    def _authority_evidence_held(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedDeliveryAuthorityEvidenceV1:
        record = self._joined._issued_record_held(capture)
        effects = self._joined._effects._issued_record_held(record.effects_capture)
        issuance = effects.selected.selected_h1.issuance
        evidence = getattr(getattr(issuance, "effects_result", None), "authority_evidence", None)
        if type(evidence) is not H1ScopedDeliveryAuthorityEvidenceV1:
            raise H1CurrentTrustHold("selected V4 grant and policy evidence is unavailable")
        return evidence

    def _trust_observation_held(self) -> HermeticTrustObservationV1:
        trust = self._runtime._trust
        frozen = trust.capture_verified_observation()
        entries = trust._journal.entries()
        logical = trust.owner_snapshot_entries()
        if (
            not entries
            or not logical
            or frozen.journal_head != entries[-1][0]
            or encode_trust_journal(logical) != frozen.snapshot_bytes
            or trust.capture_verified_observation() != frozen
        ):
            raise H1CurrentTrustHold("current trust lineage is empty")
        decision_id, _, raw = entries[-1]
        return HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            logical_snapshot_head=logical[-1][0],
        )

    def _validate_current_authority_held(
        self, evidence: object
    ) -> tuple[AuthenticatedPreparedExternalDeliveryGrant, AuthenticatedPreparedSelfDeliveryPolicy]:
        if type(evidence) is not H1ScopedDeliveryAuthorityEvidenceV1:
            raise H1CurrentTrustHold("selected V4 grant or policy evidence is invalid")
        grant_anchor: PreparedExternalDeliveryGrantAnchorV2 = evidence.grant_anchor
        policy_anchor: PreparedExternalSelfDeliveryPolicyAnchorV1 = evidence.policy_anchor
        grant_bytes = evidence.canonical_grant_bytes
        policy_bytes = evidence.canonical_policy_bytes
        try:
            grant = self._runtime._trust.current_signed_prepared_external_delivery_grant(
                grant_anchor
            )
            policy = self._runtime._trust.current_signed_prepared_self_delivery_policy(
                policy_anchor
            )
        except Exception as error:
            raise H1CurrentTrustHold("selected V4 grant or policy is not current") from error
        if (
            grant.anchor != grant_anchor
            or policy.anchor != policy_anchor
            or grant.grant.canonical_bytes() != grant_bytes
            or policy.policy.canonical_bytes() != policy_bytes
            or grant.grant.status != "ACTIVE"
            or policy.policy.status != "ACTIVE"
            or grant.grant.selected_policy_anchor != policy_anchor
        ):
            raise H1CurrentTrustHold("selected V4 grant or policy differs from its exact anchor")
        return grant, policy

    @staticmethod
    def _fresh_selected_scope_held(
        selected: H1SelectedScopeSourceV1,
        request: ReadCurrentHermeticExecutionScopeV1,
        result: CurrentHermeticExecutionScopeV1,
    ) -> H1SelectedScopeSourceV1:
        fresh = selected.model_copy(update={"current_request": request, "current_result": result})
        if (
            fresh.anchor != selected.anchor
            or fresh.scope != selected.scope
            or fresh.selected_decision_bytes != selected.selected_decision_bytes
            or fresh.selected_record_bytes != selected.selected_record_bytes
        ):
            raise H1CurrentTrustHold("fresh H1 scope altered immutable selected fields")
        return fresh

    def _verify_exact_wire_held(
        self,
        wire: object,
        selected: H1SelectedScopeSourceV1,
        request: ReadCurrentHermeticExecutionScopeV1,
    ) -> None:
        port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
        verify = getattr(port, "_require_fresh_current_wire", None)
        if not callable(verify):
            raise H1CurrentTrustHold("fresh H1 current wire verifier is unavailable")
        try:
            verify(wire, selected)
        except Exception as error:
            raise H1CurrentTrustHold("fresh H1 current wire differs") from error
        candidate = getattr(wire, "returned", None)
        payload = getattr(candidate, "canonical_payload", None)
        if not isinstance(payload, bytes):
            raise H1CurrentTrustHold("fresh H1 current candidate bytes are absent")
        from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
            H1OwnerCurrentCandidateV1,
        )

        try:
            parsed = H1OwnerCurrentCandidateV1.model_validate_json(payload)
        except ValueError as error:
            raise H1CurrentTrustHold("fresh H1 current candidate is malformed") from error
        if parsed.canonical_bytes() != payload or parsed.current != selected.current_result:
            raise H1CurrentTrustHold("fresh H1 current candidate differs")
        # `_require_fresh_current_wire` has decoded and compared this request; this
        # explicit equality keeps the reader's fresh request boundary visible.
        if selected.current_request != request:
            raise H1CurrentTrustHold("fresh H1 current request differs")

    def _replay_and_role_held(
        self,
        record: _CurrentTrustRecord,
        wire: object,
        request: ReadCurrentHermeticExecutionScopeV1,
        result: CurrentHermeticExecutionScopeV1,
    ) -> CapturedSource:
        returned = getattr(wire, "returned", None)
        sent = getattr(wire, "sent", None)
        payload = getattr(returned, "canonical_payload", None)
        callee = getattr(sent, "callee", None)
        if not isinstance(payload, bytes) or callee is None:
            raise H1CurrentTrustHold("fresh H1 current wire is incomplete")
        from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
            H1OwnerCurrentCandidateV1,
        )

        try:
            candidate = H1OwnerCurrentCandidateV1.model_validate_json(payload)
            replayed = self._runtime._replay_current_hermetic_output_scope_held(
                request, candidate, callee=callee
            )
        except Exception as error:
            raise H1CurrentTrustHold("fresh H1 current replay is unavailable") from error
        if replayed != result:
            raise H1CurrentTrustHold("fresh H1 current replay differs")
        grant, policy = self._validate_current_authority_held(record.authority_evidence)
        epoch, now_ns = self._runtime._require_dispatch_resources().clock()
        deadline = getattr(getattr(sent, "budget", None), "absolute_deadline_ns", None)
        bounded_deadline = self._authority_deadline_held(
            record.joined_capture, grant, policy, epoch, now_ns, deadline
        )
        if type(epoch) is not str or not epoch:
            raise H1CurrentTrustHold("fresh H1 trust role clock is invalid")
        observation = request.expected_trust_observation
        return CapturedSource(
            source_id="chiplog.h1.current-trust.v1",
            source_version="v1",
            owner_id="deployment_trust",
            reader_id="chiplog.h1.current-trust-reader.v1",
            invalidation_manifest=DispatchExactHead(
                subject_id=observation.physical_journal_head.identity,
                head=observation.physical_journal_head.head,
                fingerprint=observation.physical_journal_head.fingerprint,
            ),
            head=DispatchExactHead(
                subject_id=result.scope_ref.identity,
                head=result.scope_ref.head,
                fingerprint=result.scope_ref.fingerprint,
            ),
            canonical_value=result.canonical_bytes(),
            clock_contract="chiplog.dispatch.monotonic.v2",
            clock_epoch=epoch,
            valid_until_ns=bounded_deadline,
        )

    def _authority_deadline_held(
        self,
        joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1,
        grant: AuthenticatedPreparedExternalDeliveryGrant,
        policy: AuthenticatedPreparedSelfDeliveryPolicy,
        epoch: object,
        now_ns: object,
        ipc_deadline: object,
    ) -> int:
        """Intersect the fresh IPC budget with every current authority horizon."""
        try:
            evidence = self._joined.evidence_held(joined_capture)
            lease = evidence.current_claim.binding.claimant_clock.lease_expires_at_ns
            g1 = evidence.current_claim.g1
            if g1.lease_expires_at_ns != lease:
                raise ValueError("physical g1 lease differs")
            scope = self._selected_scope_held(joined_capture).scope
            mandate = grant.grant.scope.mandate
            terms = policy.policy.terms
            raw_values = (
                ipc_deadline,
                lease,
                mandate.expires_at_ns,
                terms.expires_at_ns,
            )
            if (
                type(epoch) is not str
                or not epoch
                or type(now_ns) is not int
                or any(type(value) is not int for value in raw_values)
                or mandate.clock_epoch != epoch
                or terms.clock_epoch != epoch
                or evidence.current_claim.binding.claimant_clock.clock_epoch != epoch
                or scope.authenticated_cli_state is None
                or not mandate.not_before_ns <= now_ns < mandate.expires_at_ns
                or not terms.not_before_ns <= now_ns < terms.expires_at_ns
                or not now_ns < lease
                or not now_ns < cast(int, ipc_deadline)
            ):
                raise ValueError("authority horizon differs")
        except (
            H1ScopedAuthorizationDeliveryWorkHold,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1CurrentTrustHold("fresh H1 trust role deadline is unavailable") from error
        values = cast(tuple[int, int, int, int], raw_values)
        return min(values)


__all__ = [
    "H1CurrentTrustCaptureV1",
    "H1CurrentTrustHold",
    "H1CurrentTrustReaderV1",
]
