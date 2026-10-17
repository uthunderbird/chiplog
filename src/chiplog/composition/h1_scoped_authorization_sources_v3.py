"""Native selected-effects source for the H1 V3 authorization producer.

This reader is deliberately narrower than an authorization producer.  It
reopens one caller-named locator through the installed selected-sources owner,
then retains an opaque capture of the exact H1 intent and its complete Effects
cut.  It creates neither current inputs nor an Effects owner request.

The first capture is the native source for ``effects_history``.  The installed
resource reader below supplies endpoint, credential-lifecycle, and deployment-
entitlement values from a separate authorization-time R16 observation.  The
remaining seven authorization roles stay unavailable until they have their own
installed readers; callers cannot fill any role with DTOs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import cast

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as DeliveryExactHead
from chiplog.capabilities.effects.contracts import (
    ExactHead as EffectsExactHead,
)
from chiplog.capabilities.effects.contracts import (
    ProviderRecipient as EffectsProviderRecipient,
)
from chiplog.capabilities.effects.fences import Absent
from chiplog.capabilities.effects.h1_producer_source_contracts import (
    H1PreparedDeliveryMandateCandidateV1,
    H1ProducerNotApplicableV1,
    require_h1_prepared_delivery_candidate,
    require_h1_prepared_delivery_sources,
)
from chiplog.capabilities.effects.h1_scoped_preparation import prepare_h1_scoped_delivery_v2
from chiplog.capabilities.effects.h1_scoped_preparation_v2_contracts import (
    PreparedH1ScopedDeliveryV2,
)
from chiplog.capabilities.effects.scoped_delivery_source_contracts import (
    decode_selected_delivery_intent,
)
from chiplog.capabilities.effects.scoped_dispatch_contracts import ScopedPreSendCut
from chiplog.capabilities.effects.scoped_intent_record_contracts import make_scoped_intent_member
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.composition.h1_scoped_delivery_authority import (
    H1ScopedDeliveryAuthorityCapture,
    H1ScopedDeliveryAuthorityReader,
    H1ScopedDeliveryAuthorityViolation,
    _AuthorityRecord,
)
from chiplog.composition.r16_dispatch_custody import HistoricalDispatchCustody
from chiplog.composition.r16_dispatch_registry import (
    ResourceObservation,
    historical_grant_reference,
    historical_recipient,
    verify_historical_observation,
)
from chiplog.composition.scoped_delivery_selected_sources import (
    ScopedDeliverySelectedSources,
    ScopedDeliverySelectedSourcesError,
    ScopedDeliverySelectedSourcesResult,
)


class H1ScopedAuthorizationSourceHold(ValueError):
    """The selected source cannot prove the initial authorization cut."""


@dataclass(frozen=True, slots=True)
class H1ScopedAuthorizationLocatorV1:
    """An untrusted name for a selected H1 V3 intent.

    The locator has no authority: the installed reader resolves it again
    against the selected owner journal and materialized Effects records.
    """

    intent_id: str
    act_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.intent_id) is not str or not self.intent_id:
            raise TypeError("H1 scoped authorization locator needs an intent id")
        if self.act_id is not None and (type(self.act_id) is not str or not self.act_id):
            raise TypeError("H1 scoped authorization locator act id is invalid")


@dataclass(frozen=True, slots=True)
class _EffectsHistoryRecord:
    locator: H1ScopedAuthorizationLocatorV1
    selected: ScopedDeliverySelectedSourcesResult
    observed: ScopedPreSendCut


class H1ScopedAuthorizationEffectsHistoryCaptureV1:
    """Opaque reader-issued capture of the selected initial Effects cut."""

    __slots__ = ("_record",)
    _record: _EffectsHistoryRecord


@dataclass(frozen=True, slots=True)
class H1ScopedAuthorizationHistoricalMandateRolesV1:
    """The two historical H1 exceptions derived from one retained V4 exchange.

    These are source observations only.  They neither fill a complete producer
    inventory nor authorize an Effects transition or provider delivery.
    """

    candidate: H1PreparedDeliveryMandateCandidateV1
    planning: H1ProducerNotApplicableV1
    original_adoption: H1ProducerNotApplicableV1


class H1ScopedAuthorizationEffectsHistoryReaderV1:
    """Installed source owner for the authorization ``effects_history`` role."""

    __slots__ = ("_closed", "_issued", "_runtime", "_selected")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 authorization source reader requires installed runtime")
        self._runtime = runtime
        self._selected = ScopedDeliverySelectedSources(runtime)
        self._closed = False
        self._issued: dict[
            int, tuple[H1ScopedAuthorizationEffectsHistoryCaptureV1, _EffectsHistoryRecord]
        ] = {}

    def capture(
        self, locator: H1ScopedAuthorizationLocatorV1
    ) -> H1ScopedAuthorizationEffectsHistoryCaptureV1:
        if type(locator) is not H1ScopedAuthorizationLocatorV1:
            raise TypeError("H1 authorization source requires a locator only")
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._read_initial_held(locator)
            capture = object.__new__(H1ScopedAuthorizationEffectsHistoryCaptureV1)
            capture._record = record
            self._issued[id(capture)] = (capture, record)
            return capture

    def recheck_held(
        self, capture: H1ScopedAuthorizationEffectsHistoryCaptureV1
    ) -> H1ScopedAuthorizationEffectsHistoryCaptureV1:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_initial_held(record.locator)
        if reread != record:
            raise H1ScopedAuthorizationSourceHold(
                "selected H1 V3 intent or complete Effects cut changed"
            )
        return capture

    def observed_held(
        self, capture: H1ScopedAuthorizationEffectsHistoryCaptureV1
    ) -> ScopedPreSendCut:
        """Return the reader-built cut while preserving capture ownership."""
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).observed

    def historical_mandate_roles_held(
        self, capture: H1ScopedAuthorizationEffectsHistoryCaptureV1
    ) -> H1ScopedAuthorizationHistoricalMandateRolesV1:
        """Reconstruct the V22-only candidate and its two inert role exceptions.

        The caller supplies no mandate, source inventory, or clock.  The exact
        selected V4 request/result supplies every retained input; reopening the
        selected physical row under the authority gate prevents stale captures
        from being relabelled as historical role observations.
        """
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        self.recheck_held(capture)
        record = self._issued_record_held(capture)
        try:
            issuance = record.selected.selected_h1.issuance
            if type(issuance) is not H1CompletionIssuanceV4:
                raise ValueError("selected issuance is not V4")
            call = issuance.effects_call
            result = issuance.effects_result
            rebuilt = prepare_h1_scoped_delivery_v2(call)
            if type(rebuilt) is not PreparedH1ScopedDeliveryV2 or rebuilt != result:
                raise ValueError("retained V4 result does not equal V2 reconstruction")
            request = call.request
            selected_intent = decode_selected_delivery_intent(record.selected.intent)
            expected_member = make_scoped_intent_member(result.intent_request.intent)
            source = record.selected.intent
            if (
                record.locator.intent_id != request.intent_id
                or request.intent_id != result.intent_request.intent.intent_id
                or selected_intent != result.intent_request.intent
                or source.subject.subject_id != expected_member.record_id
                or source.subject.head != expected_member.record_id
                or source.subject.fingerprint != expected_member.fingerprint
                or source.physical_record.subject_id != expected_member.record_id
                or source.physical_record.head != expected_member.record_id
                or source.physical_record.fingerprint != expected_member.fingerprint
                or source.schema_id != expected_member.schema_id
                or source.canonical_record_bytes != expected_member.canonical_record_bytes
            ):
                raise ValueError("selected physical intent differs from retained V4 exchange")
            candidate = request.current.immutable_mandate_candidate
            basis_bytes = candidate.prepared_delivery_basis_bytes
            mandate_bytes = candidate.canonical_mandate_bytes
            require_h1_prepared_delivery_candidate(
                candidate,
                basis_bytes=basis_bytes,
                grant_anchor=candidate.grant_anchor,
                policy_anchor=candidate.policy_anchor,
                mandate_bytes=mandate_bytes,
            )
            require_h1_prepared_delivery_sources(
                request.original_sources,
                basis_bytes=basis_bytes,
                grant_anchor=candidate.grant_anchor,
                policy_anchor=candidate.policy_anchor,
                mandate_bytes=mandate_bytes,
            )
            require_h1_prepared_delivery_sources(
                request.current.sources,
                basis_bytes=basis_bytes,
                grant_anchor=candidate.grant_anchor,
                policy_anchor=candidate.policy_anchor,
                mandate_bytes=mandate_bytes,
            )
            planning = request.original_sources.planning
            original_adoption = request.original_sources.original_adoption
            if (
                type(planning) is not H1ProducerNotApplicableV1
                or type(original_adoption) is not H1ProducerNotApplicableV1
                or planning.role != "planning"
                or original_adoption.role != "original_adoption"
                or planning.candidate != candidate
                or original_adoption.candidate != candidate
            ):
                raise ValueError("retained H1 exception roles differ from candidate")
            return H1ScopedAuthorizationHistoricalMandateRolesV1(
                candidate=candidate,
                planning=planning,
                original_adoption=original_adoption,
            )
        except (TypeError, ValueError, AttributeError) as error:
            raise H1ScopedAuthorizationSourceHold(
                "selected H1 V4 mandate candidate or historical role observations differ"
            ) from error

    def release(self, capture: H1ScopedAuthorizationEffectsHistoryCaptureV1) -> None:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            self._issued_record_held(capture)
            del self._issued[id(capture)]

    def close(self) -> None:
        """Revoke every outstanding source capture as its runtime unmounts."""
        with self._runtime._authority_gate().hold():
            if self._closed:
                return
            self._closed = True
            self._issued.clear()

    def _require_open_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1ScopedAuthorizationSourceHold("H1 authorization source reader is closed")

    def _issued_record_held(
        self, capture: H1ScopedAuthorizationEffectsHistoryCaptureV1
    ) -> _EffectsHistoryRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationEffectsHistoryCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationSourceHold(
                "H1 authorization source capture is not reader-issued"
            )
        return issued[1]

    def _read_initial_held(self, locator: H1ScopedAuthorizationLocatorV1) -> _EffectsHistoryRecord:
        self._runtime._authority_gate().require_held()
        try:
            selected = self._selected.read(intent_id=locator.intent_id, act_id=locator.act_id)
            original = decode_selected_delivery_intent(selected.intent)
        except (ScopedDeliverySelectedSourcesError, TypeError, ValueError) as error:
            raise H1ScopedAuthorizationSourceHold(
                "selected H1 V3 intent cannot be authenticated from native Effects sources"
            ) from error
        # ``ScopedDeliverySelectedSources`` has checked complete selected SQL /
        # owner membership and first-SEND absence.  This reader makes the only
        # initial authorization cut that may later be supplied to Effects.
        observed = ScopedPreSendCut(
            original_intent=original,
            selected_intent=selected.intent,
            current_parent=selected.intent,
            state="INTENT_RECORDED",
            authorization=Absent(),
        )
        if (
            observed.original_intent.intent_id != locator.intent_id
            or observed.current_parent != observed.selected_intent
            or selected.initial_authorization.intent_id != locator.intent_id
            or selected.initial_authorization.kind != "INITIAL_AUTHORIZATION_ABSENT"
            or selected.first_send.intent_id != locator.intent_id
            or selected.first_send.kind != "FIRST_SEND_ABSENT"
        ):
            raise H1ScopedAuthorizationSourceHold(
                "selected H1 V3 initial authorization cut differs"
            )
        return _EffectsHistoryRecord(locator, selected, observed)


class H1ScopedAuthorizationResourceHold(PermissionError):
    """The installed authorization-time R16 resource cut is unavailable."""


@dataclass(frozen=True, slots=True)
class H1ScopedAuthorizationResourceRoleV1:
    """One exact byte role from a signed R16 observation, without a time claim."""

    role: str
    head: DeliveryExactHead
    canonical_value: bytes


@dataclass(frozen=True, slots=True)
class H1ScopedAuthorizationResourceRolesV1:
    """The three resource roles retained by an issued reader capture.

    ``clock_epoch`` is an authenticated epoch label only.  It intentionally
    carries neither an observed timestamp nor a lease: the R16 signature does
    not cover either fact.
    """

    endpoint: H1ScopedAuthorizationResourceRoleV1
    credential_lifecycle: H1ScopedAuthorizationResourceRoleV1
    deployment_entitlement: H1ScopedAuthorizationResourceRoleV1
    clock_epoch: str


@dataclass(frozen=True, slots=True)
class _ResourceRecord:
    effects_capture: H1ScopedAuthorizationEffectsHistoryCaptureV1
    authority_capture: H1ScopedDeliveryAuthorityCapture
    observation: ResourceObservation
    roles: H1ScopedAuthorizationResourceRolesV1


class H1ScopedAuthorizationResourceCaptureV1:
    """Opaque, non-copyable authorization-time resource source capture."""

    __slots__ = ("_record",)
    _record: _ResourceRecord

    def __init__(self) -> None:
        raise TypeError("H1 authorization resource captures are reader-issued")

    def __copy__(self) -> H1ScopedAuthorizationResourceCaptureV1:
        raise TypeError("H1 authorization resource captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ScopedAuthorizationResourceCaptureV1:
        del memo
        raise TypeError("H1 authorization resource captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 authorization resource captures cannot be serialized")

    @property
    def roles(self) -> H1ScopedAuthorizationResourceRolesV1:
        return self._record.roles


class H1ScopedAuthorizationResourceReaderV1:
    """Installed reader for the three current R16 authorization source roles.

    Callers supply only captures issued by this runtime's selected-effects and
    scoped-authority readers.  In particular, a ResourceObservation DTO is
    never accepted as an authority input.
    """

    __slots__ = ("_authority_reader", "_closed", "_effects_reader", "_issued", "_runtime")

    def __init__(
        self,
        runtime: CommonCliExecutionRuntime,
        effects_reader: H1ScopedAuthorizationEffectsHistoryReaderV1,
        authority_reader: H1ScopedDeliveryAuthorityReader,
    ) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 authorization resource reader requires installed runtime")
        if (
            type(effects_reader) is not H1ScopedAuthorizationEffectsHistoryReaderV1
            or effects_reader._runtime is not runtime
        ):
            raise TypeError("H1 authorization resource reader requires its effects reader")
        if (
            type(authority_reader) is not H1ScopedDeliveryAuthorityReader
            or authority_reader._runtime is not runtime
        ):
            raise TypeError("H1 authorization resource reader requires its authority reader")
        self._runtime = runtime
        self._effects_reader = effects_reader
        self._authority_reader = authority_reader
        self._issued: dict[int, tuple[H1ScopedAuthorizationResourceCaptureV1, _ResourceRecord]] = {}
        self._closed = False

    def read(
        self,
        *,
        effects_capture: H1ScopedAuthorizationEffectsHistoryCaptureV1,
        authority_capture: H1ScopedDeliveryAuthorityCapture,
    ) -> H1ScopedAuthorizationResourceCaptureV1:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._read_record_held(effects_capture, authority_capture)
            capture = object.__new__(H1ScopedAuthorizationResourceCaptureV1)
            capture._record = record
            self._issued[id(capture)] = (capture, record)
            return capture

    def recheck_held(
        self, capture: H1ScopedAuthorizationResourceCaptureV1
    ) -> H1ScopedAuthorizationResourceCaptureV1:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_record_held(record.effects_capture, record.authority_capture)
        if reread != record:
            raise H1ScopedAuthorizationResourceHold(
                "H1 authorization-time resource sources changed"
            )
        return capture

    def roles_held(
        self, capture: H1ScopedAuthorizationResourceCaptureV1
    ) -> H1ScopedAuthorizationResourceRolesV1:
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).roles

    def release(self, capture: H1ScopedAuthorizationResourceCaptureV1) -> None:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            self._issued_record_held(capture)
            del self._issued[id(capture)]

    def close(self) -> None:
        with self._runtime._authority_gate().hold():
            self._closed = True
            self._issued.clear()

    def _require_open_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1ScopedAuthorizationResourceHold("H1 authorization resource reader is closed")

    def _issued_record_held(
        self, capture: H1ScopedAuthorizationResourceCaptureV1
    ) -> _ResourceRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationResourceCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationResourceHold(
                "H1 authorization resource capture is not reader-issued"
            )
        return issued[1]

    def _authority_record_held(self, capture: H1ScopedDeliveryAuthorityCapture) -> _AuthorityRecord:
        try:
            record = self._authority_reader._issued_record_held(capture)
            self._authority_reader._verify_record_held(record)
            return record
        except (H1ScopedDeliveryAuthorityViolation, TypeError, ValueError) as error:
            raise H1ScopedAuthorizationResourceHold(
                "H1 scoped authority capture is not current or reader-issued"
            ) from error

    def _read_record_held(
        self,
        effects_capture: H1ScopedAuthorizationEffectsHistoryCaptureV1,
        authority_capture: H1ScopedDeliveryAuthorityCapture,
    ) -> _ResourceRecord:
        self._runtime._authority_gate().require_held()
        effects = self._effects_reader._issued_record_held(effects_capture)
        # Reopen the complete selected cut every time; it is the source of the
        # original intent and first-SEND absence, not a caller-provided DTO.
        self._effects_reader.recheck_held(effects_capture)
        authority = self._authority_record_held(authority_capture)
        resources = self._runtime._require_dispatch_resources()
        observation = resources.observe()
        if (
            not resources.verify_historical(observation)
            or not resources.verify_current(observation)
            or observation != resources.observe()
        ):
            raise H1ScopedAuthorizationResourceHold(
                "authorization-time resource observation is not exact and current"
            )
        try:
            grant = resources.grant_reference(observation)
            recipient = resources.recipient(observation)
        except (TypeError, ValueError) as error:
            raise H1ScopedAuthorizationResourceHold(
                "authorization-time resource grant or recipient is unavailable"
            ) from error
        self._validate_joins_held(effects, authority, observation, grant, recipient)
        roles = _resource_roles(observation, grant)
        return _ResourceRecord(effects_capture, authority_capture, observation, roles)

    @staticmethod
    def _validate_joins_held(
        effects: _EffectsHistoryRecord,
        authority: _AuthorityRecord,
        observation: ResourceObservation,
        grant: EffectsExactHead,
        recipient: EffectsProviderRecipient,
    ) -> None:
        """Join selected intent, fresh authority, and the exact R16 cut."""
        projection = authority.projection
        selected = authority.selected_sources
        scope = projection.selected_scope.scope
        issuance = effects.selected.selected_h1.issuance
        from chiplog.composition.h1_completion_issuance import (
            H1CompletionIssuanceV3,
            H1CompletionIssuanceV4,
        )

        if type(issuance) not in (H1CompletionIssuanceV3, H1CompletionIssuanceV4):
            raise H1ScopedAuthorizationResourceHold("selected intent does not have a V3 source")
        issuance = cast(H1CompletionIssuanceV3 | H1CompletionIssuanceV4, issuance)
        resource_grant = DeliveryExactHead(
            identity=grant.subject_id, head=grant.head, fingerprint=grant.fingerprint
        )
        resource_recipient = DeliveryExactHead(
            identity=recipient.endpoint.subject_id,
            head=recipient.endpoint.head,
            fingerprint=recipient.endpoint.fingerprint,
        )
        if (
            effects.observed.original_intent.intent_id != effects.locator.intent_id
            or issuance.effects_call.request.selected_scope.scope != scope
            or issuance.effects_call.request.retained_origin != projection.retained_origin
            or selected.resource_grant != resource_grant
            or selected.verified.recipient != scope.recipient
            or scope.selected_resource_observation_ref.signed_observation_fingerprint
            != selected.verified.selected_resource_observation_ref.signed_observation_fingerprint
            or authority.clock_epoch != observation.clock_epoch
            or recipient.provider != "hermetic-effects"
            or recipient.account != "hermetic-account"
            or recipient.recipient != "hermetic-principal"
            or scope.recipient.endpoint != resource_recipient
        ):
            raise H1ScopedAuthorizationResourceHold(
                "selected intent, authority scope, recipient, grant, or resource observation "
                "differs"
            )


def _resource_roles(
    observation: ResourceObservation, grant: EffectsExactHead
) -> H1ScopedAuthorizationResourceRolesV1:
    """Make role DTOs from one signed observation without inventing time facts."""

    def source(role: str, raw: bytes) -> H1ScopedAuthorizationResourceRoleV1:
        digest = hashlib.sha256(raw).hexdigest()
        head = DeliveryExactHead(
            identity=f"chiplog.h1-scoped-authorization.{role}.v1",
            head=digest,
            fingerprint=digest,
        )
        return H1ScopedAuthorizationResourceRoleV1(role, head, raw)

    grant_bytes = observation.grant_bytes
    if getattr(grant, "fingerprint", None) != hashlib.sha256(grant_bytes).hexdigest():
        raise H1ScopedAuthorizationResourceHold("authorization-time grant head differs")
    return H1ScopedAuthorizationResourceRolesV1(
        endpoint=source("endpoint", observation.endpoint_bytes),
        credential_lifecycle=source("credential-lifecycle", observation.credential_bytes),
        deployment_entitlement=source("deployment-entitlement", grant_bytes),
        clock_epoch=observation.clock_epoch,
    )


def verify_historical_scoped_authorization_resource_signature_v1(
    custody: HistoricalDispatchCustody,
    observation: ResourceObservation,
) -> H1ScopedAuthorizationResourceRolesV1:
    """Authenticate retained R16 bytes without live resources, a clock, or an owner.

    No retained authorization-selection record binds this observation to an
    intent or an owner predecessor yet.  This function consequently proves
    only its historical R16 signature and exact role bytes; it must not be
    used to mark a selected authorization binder successful.
    """
    if not verify_historical_observation(custody, observation):
        raise H1ScopedAuthorizationResourceHold("historical resource observation signature differs")
    try:
        grant = historical_grant_reference(custody, observation)
        historical_recipient(custody, observation)
        json.loads(observation.endpoint_bytes)
        json.loads(observation.credential_bytes)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise H1ScopedAuthorizationResourceHold(
            "historical resource observation differs"
        ) from error
    return _resource_roles(observation, grant)


__all__ = [
    "H1ScopedAuthorizationEffectsHistoryCaptureV1",
    "H1ScopedAuthorizationEffectsHistoryReaderV1",
    "H1ScopedAuthorizationHistoricalMandateRolesV1",
    "H1ScopedAuthorizationLocatorV1",
    "H1ScopedAuthorizationResourceCaptureV1",
    "H1ScopedAuthorizationResourceHold",
    "H1ScopedAuthorizationResourceReaderV1",
    "H1ScopedAuthorizationResourceRoleV1",
    "H1ScopedAuthorizationResourceRolesV1",
    "H1ScopedAuthorizationSourceHold",
    "verify_historical_scoped_authorization_resource_signature_v1",
]
