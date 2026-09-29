"""Current J7 authority reader for an already retained H1 B completion.

The reader owns the join between historical B evidence and present trust, R17,
and R16 state.  It issues an opaque broker capture only; neither the public
evidence DTO nor a historical lifecycle observation is accepted as authority.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    CurrentHermeticExecutionScopeV1,
    HermeticTrustObservationV1,
    ReadCurrentHermeticExecutionScopeV1,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_contracts import (
    PreparedExternalDeliveryGrantAnchorV2,
)
from chiplog.capabilities.deployment_trust.prepared_external_delivery_grant_owner_contracts import (
    prepared_delivery_basis_head_v3,
)
from chiplog.capabilities.effects.h1_local_preparation_contracts import H1SelectedScopeSourceV1
from chiplog.composition.h1_completion_exchange_registry import (
    H1HistoricalPreparedDeliveryCapture,
    H1PreparedDeliveryProjection,
)
from chiplog.composition.h1_selected_output_sources import (
    H1SelectedOutputCapture,
    H1SelectedOutputSources,
)

if TYPE_CHECKING:
    from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
        H1ScopedDeliveryAuthorityEvidenceV1,
    )
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


class H1ScopedDeliveryAuthorityViolation(PermissionError):
    """The historical B capture no longer joins to complete current authority."""


@dataclass(frozen=True, slots=True)
class _AuthorityRecord:
    historical_capture: H1HistoricalPreparedDeliveryCapture
    grant_id: str | None
    reconstructed: bool
    pinned_grant_anchor: PreparedExternalDeliveryGrantAnchorV2 | None
    projection: H1PreparedDeliveryProjection
    fresh_scope_request: ReadCurrentHermeticExecutionScopeV1
    fresh_scope_result: CurrentHermeticExecutionScopeV1
    fresh_scope_wire: Any
    evidence: H1ScopedDeliveryAuthorityEvidenceV1
    selected_sources: H1SelectedOutputCapture
    clock_epoch: str
    now_ns: int


class H1ScopedDeliveryAuthorityCapture:
    """Identity-held broker result; construction and serialization are forbidden."""

    __slots__ = ("_record",)
    _record: _AuthorityRecord

    def __init__(self) -> None:
        raise TypeError("H1 scoped delivery authority captures are reader-issued")

    def __copy__(self) -> H1ScopedDeliveryAuthorityCapture:
        raise TypeError("H1 scoped delivery authority captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ScopedDeliveryAuthorityCapture:
        del memo
        raise TypeError("H1 scoped delivery authority captures cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 scoped delivery authority captures cannot be serialized")

    @property
    def evidence(self) -> H1ScopedDeliveryAuthorityEvidenceV1:
        return self._record.evidence

    @property
    def historical_projection(self) -> H1PreparedDeliveryProjection:
        return self._record.projection

    @property
    def selected_sources(self) -> H1SelectedOutputCapture:
        return self._record.selected_sources

    @property
    def clock(self) -> tuple[str, int]:
        return self._record.clock_epoch, self._record.now_ns


class H1ScopedDeliveryAuthorityReader:
    """Broker-owned current authority reader for one registered historical B cut."""

    __slots__ = ("_issued", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 scoped authority requires the installed common CLI runtime")
        self._runtime = runtime
        self._issued: dict[int, tuple[H1ScopedDeliveryAuthorityCapture, _AuthorityRecord]] = {}

    async def read(
        self, *, historical_b_capture: H1HistoricalPreparedDeliveryCapture, grant_id: str
    ) -> H1ScopedDeliveryAuthorityCapture:
        """Read a current authority cut after one fresh owner current-scope IPC.

        No AuthorityGate is held across the owner await.  The returned owner
        wire is replayed together with every other source under the gate before
        an opaque capture is issued.
        """
        if type(historical_b_capture) is not H1HistoricalPreparedDeliveryCapture:
            raise H1ScopedDeliveryAuthorityViolation("historical B capture is not registry-issued")
        if type(grant_id) is not str or not grant_id:
            raise ValueError("grant_id must be a nonempty string")
        with self._runtime._authority_gate().hold():
            projection, request = self._capture_historical_and_current_scope_request_held(
                historical_b_capture, grant_id
            )
        try:
            result, wire = await self._runtime._read_current_hermetic_output_scope_with_wire(
                request
            )
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation("fresh H1 scope owner read failed") from error
        if not isinstance(result, CurrentHermeticExecutionScopeV1):
            raise H1ScopedDeliveryAuthorityViolation("fresh H1 scope is not current")
        with self._runtime._authority_gate().hold():
            return self._issue_held(
                historical_b_capture, grant_id, projection, request, result, wire
            )

    async def read_reconstructed(
        self, *, historical_b_capture: H1HistoricalPreparedDeliveryCapture
    ) -> H1ScopedDeliveryAuthorityCapture:
        """Read recovered V3 authority without caller-selected grant identity."""
        return await self._read_reconstructed(historical_b_capture=historical_b_capture)

    async def _read_reconstructed(
        self,
        *,
        historical_b_capture: H1HistoricalPreparedDeliveryCapture,
        expected_grant_anchor: PreparedExternalDeliveryGrantAnchorV2 | None = None,
    ) -> H1ScopedDeliveryAuthorityCapture:
        if type(historical_b_capture) is not H1HistoricalPreparedDeliveryCapture:
            raise H1ScopedDeliveryAuthorityViolation("historical B capture is not registry-issued")
        with self._runtime._authority_gate().hold():
            projection, request = self._capture_reconstructed_historical_and_scope_request_held(
                historical_b_capture
            )
        try:
            result, wire = await self._runtime._read_current_hermetic_output_scope_with_wire(
                request
            )
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation("fresh H1 scope owner read failed") from error
        if not isinstance(result, CurrentHermeticExecutionScopeV1):
            raise H1ScopedDeliveryAuthorityViolation("fresh H1 scope is not current")
        with self._runtime._authority_gate().hold():
            return self._issue_reconstructed_held(
                historical_b_capture,
                projection,
                request,
                result,
                wire,
                expected_grant_anchor=expected_grant_anchor,
            )

    def recheck(
        self, capture: H1ScopedDeliveryAuthorityCapture
    ) -> H1ScopedDeliveryAuthorityCapture:
        """Revalidate a reader-issued cut immediately around a consuming IPC boundary."""
        with self._runtime._authority_gate().hold():
            record = self._issued_record_held(capture)
            self._verify_record_held(record)
            return capture

    async def refresh(
        self, capture: H1ScopedDeliveryAuthorityCapture
    ) -> H1ScopedDeliveryAuthorityCapture:
        """Obtain a new owner current-scope proof after a trust-head advance."""
        with self._runtime._authority_gate().hold():
            record = self._issued_record_held(capture)
        if record.reconstructed:
            assert record.pinned_grant_anchor is not None
            return await self._read_reconstructed(
                historical_b_capture=record.historical_capture,
                expected_grant_anchor=record.pinned_grant_anchor,
            )
        assert record.grant_id is not None
        return await self.read(
            historical_b_capture=record.historical_capture, grant_id=record.grant_id
        )

    def _capture_historical_and_current_scope_request_held(
        self, historical_capture: H1HistoricalPreparedDeliveryCapture, grant_id: str
    ) -> tuple[H1PreparedDeliveryProjection, ReadCurrentHermeticExecutionScopeV1]:
        self._runtime._authority_gate().require_held()
        projection = self._historical_projection_held(historical_capture)
        grant = self._current_grant_held(projection, grant_id)
        observation = self._trust_observation_held()
        request = projection.selected_scope.current_request.model_copy(
            update={"expected_trust_observation": observation}
        )
        if request.source_anchor != projection.selected_scope.current_request.source_anchor:
            raise H1ScopedDeliveryAuthorityViolation("fresh H1 scope source anchor differs")
        if grant.grant.selected_policy_anchor.policy.identity == "":
            raise H1ScopedDeliveryAuthorityViolation("current grant policy anchor is invalid")
        return projection, request

    def _capture_reconstructed_historical_and_scope_request_held(
        self, historical_capture: H1HistoricalPreparedDeliveryCapture
    ) -> tuple[H1PreparedDeliveryProjection, ReadCurrentHermeticExecutionScopeV1]:
        """Capture only historical B and fresh scope inputs before the owner IPC."""
        self._runtime._authority_gate().require_held()
        self._require_reconstructed_historical_capture_held(historical_capture)
        projection = self._historical_projection_held(historical_capture)
        observation = self._trust_observation_held()
        request = projection.selected_scope.current_request.model_copy(
            update={"expected_trust_observation": observation}
        )
        if request.source_anchor != projection.selected_scope.current_request.source_anchor:
            raise H1ScopedDeliveryAuthorityViolation("fresh H1 scope source anchor differs")
        return projection, request

    def _issue_held(
        self,
        historical_capture: H1HistoricalPreparedDeliveryCapture,
        grant_id: str,
        projection: H1PreparedDeliveryProjection,
        request: ReadCurrentHermeticExecutionScopeV1,
        result: CurrentHermeticExecutionScopeV1,
        wire: Any,
    ) -> H1ScopedDeliveryAuthorityCapture:
        self._runtime._authority_gate().require_held()
        record = self._build_record_held(
            historical_capture, grant_id, projection, request, result, wire
        )
        capture = object.__new__(H1ScopedDeliveryAuthorityCapture)
        capture._record = record
        self._issued[id(capture)] = (capture, record)
        return capture

    def _issue_reconstructed_held(
        self,
        historical_capture: H1HistoricalPreparedDeliveryCapture,
        projection: H1PreparedDeliveryProjection,
        request: ReadCurrentHermeticExecutionScopeV1,
        result: CurrentHermeticExecutionScopeV1,
        wire: Any,
        *,
        expected_grant_anchor: PreparedExternalDeliveryGrantAnchorV2 | None,
    ) -> H1ScopedDeliveryAuthorityCapture:
        self._runtime._authority_gate().require_held()
        record = self._build_reconstructed_record_held(
            historical_capture,
            projection,
            request,
            result,
            wire,
            expected_grant_anchor=expected_grant_anchor,
        )
        capture = object.__new__(H1ScopedDeliveryAuthorityCapture)
        capture._record = record
        self._issued[id(capture)] = (capture, record)
        return capture

    def _issued_record_held(self, capture: H1ScopedDeliveryAuthorityCapture) -> _AuthorityRecord:
        self._runtime._authority_gate().require_held()
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedDeliveryAuthorityCapture
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedDeliveryAuthorityViolation("authority capture is not reader-issued")
        return issued[1]

    def _build_record_held(
        self,
        historical_capture: H1HistoricalPreparedDeliveryCapture,
        grant_id: str,
        projection: H1PreparedDeliveryProjection,
        request: ReadCurrentHermeticExecutionScopeV1,
        result: CurrentHermeticExecutionScopeV1,
        wire: Any,
    ) -> _AuthorityRecord:
        replayed = self._historical_projection_held(historical_capture)
        if replayed != projection:
            raise H1ScopedDeliveryAuthorityViolation(
                "historical B projection changed during scope read"
            )
        selected_scope = projection.selected_scope.model_copy(
            update={"current_request": request, "current_result": result}
        )
        self._verify_fresh_scope_wire_held(request, result, wire, selected_scope)
        selected = self._selected_sources_held(projection, selected_scope)
        grant = self._current_grant_held(projection, grant_id)
        policy = self._runtime._trust.current_signed_prepared_self_delivery_policy(
            grant.grant.selected_policy_anchor
        )
        epoch, now_ns = self._runtime._require_dispatch_resources().clock()
        self._validate_joins(projection, selected_scope, selected, grant, policy, epoch, now_ns)
        evidence = self._evidence(grant, policy)
        return _AuthorityRecord(
            historical_capture=historical_capture,
            grant_id=grant_id,
            reconstructed=False,
            pinned_grant_anchor=None,
            projection=projection,
            fresh_scope_request=request,
            fresh_scope_result=result,
            fresh_scope_wire=wire,
            evidence=evidence,
            selected_sources=selected,
            clock_epoch=epoch,
            now_ns=now_ns,
        )

    def _verify_record_held(self, record: _AuthorityRecord) -> None:
        self._runtime._authority_gate().require_held()
        if record.reconstructed:
            rebuilt = self._build_reconstructed_record_held(
                record.historical_capture,
                record.projection,
                record.fresh_scope_request,
                record.fresh_scope_result,
                record.fresh_scope_wire,
                expected_grant_anchor=record.pinned_grant_anchor,
            )
            if rebuilt.pinned_grant_anchor != record.pinned_grant_anchor:
                raise H1ScopedDeliveryAuthorityViolation(
                    "reconstructed prepared delivery grant anchor differs"
                )
            return
        assert record.grant_id is not None
        self._build_record_held(
            record.historical_capture,
            record.grant_id,
            record.projection,
            record.fresh_scope_request,
            record.fresh_scope_result,
            record.fresh_scope_wire,
        )

    def _build_reconstructed_record_held(
        self,
        historical_capture: H1HistoricalPreparedDeliveryCapture,
        projection: H1PreparedDeliveryProjection,
        request: ReadCurrentHermeticExecutionScopeV1,
        result: CurrentHermeticExecutionScopeV1,
        wire: Any,
        *,
        expected_grant_anchor: PreparedExternalDeliveryGrantAnchorV2 | None,
    ) -> _AuthorityRecord:
        """Rebuild V3 authority from historical B facts and current owner state."""
        self._runtime._authority_gate().require_held()
        self._require_reconstructed_historical_capture_held(historical_capture)
        replayed = self._historical_projection_held(historical_capture)
        if replayed != projection:
            raise H1ScopedDeliveryAuthorityViolation(
                "historical B projection changed during scope read"
            )
        selected_scope = projection.selected_scope.model_copy(
            update={"current_request": request, "current_result": result}
        )
        self._verify_fresh_scope_wire_held(request, result, wire, selected_scope)
        selected = self._selected_sources_held(projection, selected_scope)
        selector = self._reconstructed_grant_selector_held(projection, selected_scope, selected)
        grant = self._current_reconstructed_grant_held(selector)
        if expected_grant_anchor is not None and grant.anchor != expected_grant_anchor:
            raise H1ScopedDeliveryAuthorityViolation(
                "reconstructed prepared delivery grant anchor differs"
            )
        policy = self._runtime._trust.current_signed_prepared_self_delivery_policy(
            grant.grant.selected_policy_anchor
        )
        epoch, now_ns = self._runtime._require_dispatch_resources().clock()
        self._validate_joins(projection, selected_scope, selected, grant, policy, epoch, now_ns)
        evidence = self._evidence(grant, policy)
        return _AuthorityRecord(
            historical_capture=historical_capture,
            grant_id=None,
            reconstructed=True,
            pinned_grant_anchor=grant.anchor,
            projection=projection,
            fresh_scope_request=request,
            fresh_scope_result=result,
            fresh_scope_wire=wire,
            evidence=evidence,
            selected_sources=selected,
            clock_epoch=epoch,
            now_ns=now_ns,
        )

    def _require_reconstructed_historical_capture_held(
        self, capture: H1HistoricalPreparedDeliveryCapture
    ) -> None:
        """Keep the grant-free route exclusive to durable SCOPED_V3 recovery."""
        self._runtime._authority_gate().require_held()
        try:
            from chiplog.composition.h1_v3_recovery_historical_source import (
                H1V3RecoveredPreparedDelivery,
            )

            if type(capture._record) is not H1V3RecoveredPreparedDelivery:
                raise TypeError("historical capture is not recovered SCOPED_V3 evidence")
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation(
                "historical B capture is not recovered SCOPED_V3 evidence"
            ) from error

    def _historical_projection_held(
        self, capture: H1HistoricalPreparedDeliveryCapture
    ) -> H1PreparedDeliveryProjection:
        registry = getattr(self._runtime, "_h1_completion_exchange_registry", None)
        replay = getattr(registry, "replay_prepared_delivery_historical", None)
        if not callable(replay):
            raise H1ScopedDeliveryAuthorityViolation(
                "H1 historical completion registry is unavailable"
            )
        try:
            projection = replay(capture)
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation(
                "historical B projection is unavailable"
            ) from error
        if type(projection) is not H1PreparedDeliveryProjection:
            raise H1ScopedDeliveryAuthorityViolation("historical B projection has the wrong type")
        authenticated_scope = self._runtime._trust.historical_hermetic_output_scope(
            projection.selected_scope.scope
        )
        if authenticated_scope is None:
            raise H1ScopedDeliveryAuthorityViolation("historical H1 scope is not authenticated")
        return projection

    def _current_grant_held(self, projection: H1PreparedDeliveryProjection, grant_id: str) -> Any:
        trust = self._runtime._trust
        latest = trust.latest_prepared_external_delivery_grant(
            projection.tenant_id, projection.database_id, grant_id
        )
        if latest is None:
            raise H1ScopedDeliveryAuthorityViolation("current prepared delivery grant is absent")
        try:
            return trust.current_signed_prepared_external_delivery_grant(latest.anchor)
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation(
                "current prepared delivery grant is invalid"
            ) from error

    def _reconstructed_grant_selector_held(
        self,
        projection: H1PreparedDeliveryProjection,
        selected_scope: H1SelectedScopeSourceV1,
        selected: H1SelectedOutputCapture,
    ) -> Any:
        """Build the grant-selection preimage only from replayed and read sources."""
        self._runtime._authority_gate().require_held()
        try:
            from chiplog.capabilities.deployment_trust import (
                prepared_external_delivery_grant_owner_contracts as grant_contracts,
            )

            return grant_contracts.ReconstructedPreparedExternalDeliveryGrantSelectorV1(
                basis=projection.basis,
                scope_anchor=selected_scope.anchor,
                scope=selected_scope.scope,
                selected_decision_bytes=selected_scope.selected_decision_bytes,
                selected_record_bytes=selected_scope.selected_record_bytes,
                retained_origin=projection.retained_origin,
                selected_source=selected.selected_source,
                fence=projection.fence,
                original_run=projection.original_run,
                captured_attempt=projection.captured_attempt,
                accepted_delivery=projection.delivery,
                resource_grant=selected.resource_grant,
                canonical_resource_grant_bytes=selected.resource_grant_bytes,
            )
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation(
                "reconstructed prepared delivery selector is invalid"
            ) from error

    def _current_reconstructed_grant_held(self, selector: Any) -> Any:
        self._runtime._authority_gate().require_held()
        try:
            method = (
                self._runtime._trust.current_signed_prepared_external_delivery_grant_for_reconstructed_evidence
            )
            return method(selector)
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation(
                "current reconstructed prepared delivery grant is invalid"
            ) from error

    def _trust_observation_held(self) -> HermeticTrustObservationV1:
        trust = self._runtime._trust
        trust.capture_verified_observation()
        entries = trust._journal.entries()
        logical = trust.owner_snapshot_entries()
        if not entries or not logical:
            raise H1ScopedDeliveryAuthorityViolation("current trust lineage is empty")
        decision_id, _, raw = entries[-1]
        return HermeticTrustObservationV1(
            physical_journal_head=ExactHead(
                identity="deployment-trust/journal",
                head=decision_id,
                fingerprint=hashlib.sha256(raw).hexdigest(),
            ),
            logical_snapshot_head=logical[-1][0],
        )

    def _verify_fresh_scope_wire_held(
        self,
        request: ReadCurrentHermeticExecutionScopeV1,
        result: CurrentHermeticExecutionScopeV1,
        wire: Any,
        selected_scope: H1SelectedScopeSourceV1,
    ) -> None:
        replay = getattr(self._runtime, "_replay_current_hermetic_output_scope_held", None)
        port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
        verify_wire = getattr(port, "_require_fresh_current_wire", None)
        sent = getattr(wire, "sent", None)
        callee = getattr(sent, "callee", None)
        if not callable(replay) or not callable(verify_wire) or callee is None:
            raise H1ScopedDeliveryAuthorityViolation("fresh H1 scope proof is unavailable")
        try:
            candidate = getattr(wire, "returned", None)
            payload = getattr(candidate, "canonical_payload", None)
            if not isinstance(payload, bytes):
                raise ValueError("fresh scope response bytes are absent")
            from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
                H1OwnerCurrentCandidateV1,
            )

            parsed = H1OwnerCurrentCandidateV1.model_validate_json(payload)
            if (
                parsed.canonical_bytes() != payload
                or replay(request, parsed, callee=callee) != result
            ):
                raise ValueError("fresh scope replay differs")
            verify_wire(wire, selected_scope)
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation("fresh H1 scope proof differs") from error

    def _selected_sources_held(
        self,
        projection: H1PreparedDeliveryProjection,
        selected_scope: H1SelectedScopeSourceV1,
    ) -> H1SelectedOutputCapture:
        scope = selected_scope.scope
        selected = H1SelectedOutputSources(self._runtime).capture_scope_selected_current(scope)
        if selected is None:
            raise H1ScopedDeliveryAuthorityViolation("current H1 R17/R16 sources are unavailable")
        retained = projection.retained_origin
        if (
            selected.initialization_envelope_bytes != retained.initialization_envelope_bytes
            or selected.admitted_record_bytes != retained.admitted_record_bytes
            or selected.selected_admitted_record_ref != retained.selected_admitted_record_ref
            or selected.authentication_result_bytes != retained.authentication_result_bytes
            or selected.selected_source.selected_initialization
            != scope.selected_resource_observation_ref.selected_initialization
            or selected.selected_source.admitted_authentication != scope.admitted_authentication
            or selected.verified.recipient != projection.delivery.selection.recipient
        ):
            raise H1ScopedDeliveryAuthorityViolation("current H1 R17/R16 sources differ from B")
        return selected

    @staticmethod
    def _validate_joins(
        projection: H1PreparedDeliveryProjection,
        selected_scope: H1SelectedScopeSourceV1,
        selected: H1SelectedOutputCapture,
        grant: Any,
        policy: Any,
        epoch: str,
        now_ns: int,
    ) -> None:
        scope = grant.grant.scope
        mandate = scope.mandate
        terms = policy.policy.terms
        current_scope = selected_scope.scope
        if (
            grant.grant.selected_policy_anchor != policy.anchor
            or grant.grant.tenant_id != projection.tenant_id
            or grant.grant.database_id != projection.database_id
            or policy.policy.tenant_id != projection.tenant_id
            or policy.policy.database_id != projection.database_id
            or scope.principal_id != projection.principal_id
            or scope.worker_session_id != projection.worker_session_id
            or scope.contour_head != current_scope.contour_head
            or scope.authenticated_credential_head
            != current_scope.authenticated_cli_state.credential_head
            or scope.authenticated_session_head
            != current_scope.authenticated_cli_state.session_head
            or scope.selected_source != selected.selected_source
            or scope.resources.resource_grant != selected.resource_grant
            or scope.resources.recipient != projection.delivery.selection.recipient
            or scope.resources.signature_domain != "dispatch-resources.v1"
            or scope.resources.signed_observation_fingerprint
            != current_scope.selected_resource_observation_ref.signed_observation_fingerprint
            or scope.resources.clock_epoch != epoch
            or mandate.original_run != projection.original_run
            or mandate.captured_attempt != projection.captured_attempt
            or mandate.preparation_basis != prepared_delivery_basis_head_v3(projection.basis)
            or mandate.delivery_id != projection.delivery.delivery_id
            or mandate.payload_digest != projection.delivery.render_digest
            or mandate.payload_byte_length != len(projection.delivery.rendered_bytes)
            or mandate.clock_epoch != epoch
            or mandate.clock_contract != "chiplog.dispatch.monotonic.v2"
            or not mandate.not_before_ns <= now_ns < mandate.expires_at_ns
            or terms.principal_id != projection.principal_id
            or terms.channel_id != "hermetic-local"
            or terms.recipient != projection.delivery.selection.recipient
            or terms.payload_digest != projection.delivery.render_digest
            or terms.payload_byte_length != len(projection.delivery.rendered_bytes)
            or terms.clock_epoch != epoch
            or terms.clock_contract != mandate.clock_contract
            or not terms.not_before_ns <= now_ns < terms.expires_at_ns
            or terms.communication_permission != "PREPARED_EXTERNAL_SEND"
            or terms.disclosure_permission != "EXACT_RENDERED_PAYLOAD"
            or terms.self_recipient_semantics != "OPERATOR_ATTESTED_SELF"
            or terms.payload_class != "NonAuthoritativeText"
            or "CLI" not in terms.source_classes
            or "ORIGIN_EXACT" not in terms.selection_modes
        ):
            raise H1ScopedDeliveryAuthorityViolation("current delivery authority joins differ")

    def _evidence(self, grant: Any, policy: Any) -> H1ScopedDeliveryAuthorityEvidenceV1:
        try:
            from chiplog.capabilities.effects.h1_scoped_preparation_contracts import (
                H1ScopedDeliveryAuthorityEvidenceV1,
            )

            return H1ScopedDeliveryAuthorityEvidenceV1(
                grant_anchor=grant.anchor,
                canonical_grant_bytes=grant.grant.canonical_bytes(),
                policy_anchor=policy.anchor,
                canonical_policy_bytes=policy.policy.canonical_bytes(),
                trust_observation=self._trust_observation_held(),
            )
        except Exception as error:
            raise H1ScopedDeliveryAuthorityViolation(
                "H1 authority evidence cannot be built"
            ) from error


__all__ = [
    "H1ScopedDeliveryAuthorityCapture",
    "H1ScopedDeliveryAuthorityReader",
    "H1ScopedDeliveryAuthorityViolation",
]
