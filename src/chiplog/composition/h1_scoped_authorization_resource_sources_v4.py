"""Inert V4 reader for the current R16 roles of a selected g1 claim."""

from __future__ import annotations

from dataclasses import dataclass

from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead as DeliveryExactHead,
)
from chiplog.capabilities.agent_loop.delivery_contracts import ProviderRecipient
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.composition.h1_scoped_authorization_delivery_work_reader import (
    H1ScopedAuthorizationDeliveryWorkCaptureV1,
    H1ScopedAuthorizationDeliveryWorkHold,
    H1ScopedAuthorizationDeliveryWorkReaderV1,
)
from chiplog.composition.h1_scoped_authorization_sources_v3 import (
    H1ScopedAuthorizationResourceHold,
    H1ScopedAuthorizationResourceRolesV1,
    _resource_roles,
)
from chiplog.composition.h1_selected_output_sources import (
    H1SelectedOutputCapture,
    H1SelectedOutputSources,
)
from chiplog.composition.r16_dispatch_registry import ResourceObservation


@dataclass(frozen=True, slots=True)
class _ResourceRecordV4:
    delivery_work_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    observation: ResourceObservation
    roles: H1ScopedAuthorizationResourceRolesV1
    selected: H1SelectedOutputCapture


class H1ScopedAuthorizationResourceCaptureV4:
    """Opaque, reader-issued V4 R16 capture with no authority capability."""

    __slots__ = ("_record",)
    _record: _ResourceRecordV4

    def __init__(self) -> None:
        raise TypeError("H1 V4 authorization resource captures are reader-issued")

    def __copy__(self) -> H1ScopedAuthorizationResourceCaptureV4:
        raise TypeError("H1 V4 authorization resource captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ScopedAuthorizationResourceCaptureV4:
        del memo
        raise TypeError("H1 V4 authorization resource captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 V4 authorization resource captures cannot be serialized")


class H1ScopedAuthorizationResourceReaderV4:
    """Read endpoint, credential, and grant roles from one rechecked V4 cut."""

    __slots__ = ("_closed", "_issued", "_joined", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 V4 authorization resource reader requires installed runtime")
        joined = getattr(runtime, "_h1_scoped_authorization_delivery_work_reader_v1", None)
        if (
            type(joined) is not H1ScopedAuthorizationDeliveryWorkReaderV1
            or joined._runtime is not runtime
        ):
            raise TypeError("H1 V4 authorization resource reader requires joined work reader")
        self._runtime = runtime
        self._joined = joined
        self._issued: dict[
            int, tuple[H1ScopedAuthorizationResourceCaptureV4, _ResourceRecordV4]
        ] = {}
        self._closed = False

    def read(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedAuthorizationResourceCaptureV4:
        if type(capture) is not H1ScopedAuthorizationDeliveryWorkCaptureV1:
            raise TypeError("H1 V4 resource reader requires joined reader capture")
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._read_record_held(capture)
            issued = object.__new__(H1ScopedAuthorizationResourceCaptureV4)
            issued._record = record
            self._issued[id(issued)] = (issued, record)
            return issued

    def recheck_held(
        self, capture: H1ScopedAuthorizationResourceCaptureV4
    ) -> H1ScopedAuthorizationResourceCaptureV4:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_record_held(record.delivery_work_capture)
        if reread != record:
            raise H1ScopedAuthorizationResourceHold("H1 V4 resource sources changed")
        return capture

    def roles_held(
        self, capture: H1ScopedAuthorizationResourceCaptureV4
    ) -> H1ScopedAuthorizationResourceRolesV1:
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).roles

    def release(self, capture: H1ScopedAuthorizationResourceCaptureV4) -> None:
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
            raise H1ScopedAuthorizationResourceHold("H1 V4 authorization resource reader is closed")

    def _issued_record_held(
        self, capture: H1ScopedAuthorizationResourceCaptureV4
    ) -> _ResourceRecordV4:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationResourceCaptureV4
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationResourceHold("H1 V4 resource capture is not reader-issued")
        return issued[1]

    def _read_record_held(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> _ResourceRecordV4:
        """Authenticate all mutable inputs in one outer AuthorityGate hold."""
        self._runtime._authority_gate().require_held()
        try:
            evidence = self._joined.evidence_held(capture)
            scope = self._joined.scope_from_evidence_held(capture)
            joined_record = self._joined._issued_record_held(capture)
            effects = self._joined._effects._issued_record_held(joined_record.effects_capture)
            resources = self._runtime._require_dispatch_resources()
            observation = resources.observe()
            if (
                not resources.verify_historical(observation)
                or not resources.verify_current(observation)
                or observation != resources.observe()
            ):
                raise H1ScopedAuthorizationResourceHold("H1 V4 R16 observation is not current")
            grant = resources.grant_reference(observation)
            recipient = resources.recipient(observation)
            selected = H1SelectedOutputSources(self._runtime).capture_scope_selected_current(scope)
        except (H1ScopedAuthorizationDeliveryWorkHold, TypeError, ValueError) as error:
            raise H1ScopedAuthorizationResourceHold("H1 V4 sources are unavailable") from error
        if selected is None:
            raise H1ScopedAuthorizationResourceHold("H1 V4 selected R16 source is unavailable")
        fresh_grant = DeliveryExactHead(
            identity=grant.subject_id, head=grant.head, fingerprint=grant.fingerprint
        )
        fresh_recipient = ProviderRecipient(
            provider_id=recipient.provider,
            account_id=recipient.account,
            recipient_id=recipient.recipient,
            endpoint=DeliveryExactHead(
                identity=recipient.endpoint.subject_id,
                head=recipient.endpoint.head,
                fingerprint=recipient.endpoint.fingerprint,
            ),
            canonical_address=recipient.canonical_address,
            credential_binding=DeliveryExactHead(
                identity=recipient.credential_binding.subject_id,
                head=recipient.credential_binding.head,
                fingerprint=recipient.credential_binding.fingerprint,
            ),
        )
        issuance = effects.selected.selected_h1.issuance
        retained = getattr(getattr(issuance, "effects_call", None), "request", None)
        retained = getattr(retained, "retained_origin", None)
        decision = effects.selected.selected_h1.decision
        source = evidence.source
        if (
            type(issuance) is not H1CompletionIssuanceV4
            or issuance.effects_call.request.selected_scope.scope != scope
            or selected.resource_observation != observation
            or selected.resource_grant != fresh_grant
            or selected.verified.recipient != scope.recipient
            or selected.verified.recipient != fresh_recipient
            or scope.recipient != fresh_recipient
            or selected.verified.recipient.endpoint != fresh_recipient.endpoint
            or selected.verified.recipient.canonical_address != fresh_recipient.canonical_address
            or selected.verified.recipient.credential_binding != fresh_recipient.credential_binding
            or observation.clock_epoch != evidence.current_claim.binding.claimant_clock.clock_epoch
            or source.tenant_id != issuance.effects_call.route.tenant_id
            or source.intent_id != effects.locator.intent_id
            or source.selected_decision_id != decision.decision_id
            or source.selected_decision_fingerprint != decision.decision_fingerprint
            or retained is None
            or retained.initialization_envelope_bytes != selected.initialization_envelope_bytes
            or retained.admitted_record_bytes != selected.admitted_record_bytes
            or retained.selected_admitted_record_ref != selected.selected_admitted_record_ref
            or retained.authentication_result_bytes != selected.authentication_result_bytes
        ):
            raise H1ScopedAuthorizationResourceHold(
                "H1 V4 selected scope, R16, origin, or delivery-work claim differs"
            )
        return _ResourceRecordV4(
            delivery_work_capture=capture,
            observation=observation,
            roles=_resource_roles(observation, grant),
            selected=selected,
        )


__all__ = [
    "H1ScopedAuthorizationResourceCaptureV4",
    "H1ScopedAuthorizationResourceReaderV4",
]
