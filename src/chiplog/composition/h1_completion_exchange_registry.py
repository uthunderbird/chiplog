"""Private retained evidence for one completed B first-path owner exchange.

This is deliberately a past-stage registry.  It does not issue a request,
perform owner IPC, or establish a current policy decision.  Its only writer is
the B session after that session has retained a canonical successful completion
reply and its preflight has retained the native and P scope capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.delivery_contracts import AcceptedDelivery
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead as LoopHead
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
    decode_first_path_completion_request,
    first_path_completion_request_fingerprint,
)
from chiplog.capabilities.deployment_trust.h1_broker_evidence_contracts import (
    H1RetainedSelectedWrapperV1,
)
from chiplog.capabilities.effects.fences import NonSchedulerFence as EffectsNonSchedulerFence
from chiplog.capabilities.effects.h1_local_preparation_contracts import H1SelectedScopeSourceV1
from chiplog.capabilities.effects.scoped_intent_contracts import PreparedDeliveryBasisV3
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_first_path_sources import H1FirstPathCapture, H1FirstPathSources
from chiplog.composition.h1_native_member_sources import (
    H1CurrentNativeMemberSourceCut,
    H1NativeMemberSources,
)
from chiplog.platform.broker import PublicPortCall, PublicPortSuccess

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


class H1CompletionExchangeRegistryViolation(ValueError):
    """A value did not originate in this registry's retained B session."""


class _CompletionScopeReplayPort(Protocol):
    def _replay_completion_scope(self, capability: object, native_cut: object) -> object: ...

    def _replay_completion_effects_source(
        self,
        scope_cap: object,
        native_cap: object,
        delivery_receipt: object,
        first_path: object,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class _CompletionExchangeRecord:
    """Strong identity-held record; its fields are not a public authority DTO."""

    session: object
    first_path: H1FirstPathCapture
    exchange: H1CompletionOwnerExchangeV1
    native_cap: H1CurrentNativeMemberSourceCut
    scope_cap: object
    delivery_receipt: object
    original_identity: DriverCommandIdentityV1
    original_fingerprint: str
    selected_seal: CallSubjectHead
    historical_p_evidence: object


class H1PreparedDeliveryCapture:
    """Registry-issued identity token for one retained, live B completion."""

    __slots__ = ("_projection", "_record")
    _record: _CompletionExchangeRecord
    _projection: H1PreparedDeliveryProjection

    def __init__(self) -> None:
        raise TypeError("H1 prepared delivery captures are registry-issued")

    def __copy__(self) -> H1PreparedDeliveryCapture:
        raise TypeError("H1 prepared delivery captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1PreparedDeliveryCapture:
        del memo
        raise TypeError("H1 prepared delivery captures cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 prepared delivery captures cannot be serialized")


class H1HistoricalPreparedDeliveryCapture:
    """Registry-issued handle for P/B evidence captured before a J7 ISSUE."""

    __slots__ = ("_record",)
    _record: object

    def __init__(self) -> None:
        raise TypeError("H1 historical prepared delivery captures are registry-issued")

    def __copy__(self) -> H1HistoricalPreparedDeliveryCapture:
        raise TypeError("H1 historical prepared delivery captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1HistoricalPreparedDeliveryCapture:
        del memo
        raise TypeError("H1 historical prepared delivery captures cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 historical prepared delivery captures cannot be serialized")


@dataclass(frozen=True, slots=True)
class H1PreparedDeliveryProjection:
    """Inert live replay of one loop-prepared delivery; it grants no action."""

    basis: PreparedDeliveryBasisV3
    selected_scope: H1SelectedScopeSourceV1
    retained_origin: H1RetainedSelectedWrapperV1
    fence: EffectsNonSchedulerFence
    tenant_id: str
    database_id: str
    principal_id: str
    worker_session_id: str
    original_run: LoopHead
    captured_attempt: LoopHead
    delivery: AcceptedDelivery


class _AuthenticatedCompletionExchangeInputs:
    """Inert view issued only by :class:`H1CompletionExchangeRegistry`."""

    __slots__ = ("_record",)
    _record: _CompletionExchangeRecord

    def __init__(self) -> None:
        raise TypeError("H1 completion exchange inputs are registry-issued")

    def __copy__(self) -> _AuthenticatedCompletionExchangeInputs:
        raise TypeError("H1 completion exchange inputs cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> _AuthenticatedCompletionExchangeInputs:
        del memo
        raise TypeError("H1 completion exchange inputs cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 completion exchange inputs cannot be serialized")

    @property
    def session_identity(self) -> object:
        return self._record.session

    @property
    def first_path(self) -> H1FirstPathCapture:
        return self._record.first_path

    @property
    def native_cap(self) -> H1CurrentNativeMemberSourceCut:
        return self._record.native_cap

    @property
    def scope_cap(self) -> object:
        return self._record.scope_cap

    @property
    def sent(self) -> PublicPortCall:
        return self._record.exchange.sent

    @property
    def returned(self) -> PublicPortSuccess:
        returned = self._record.exchange.returned
        assert isinstance(returned, PublicPortSuccess)
        return returned

    @property
    def request_bytes(self) -> bytes:
        return self.sent.canonical_payload

    @property
    def result_bytes(self) -> bytes:
        return self.returned.canonical_payload

    @property
    def sent_at_ns(self) -> int:
        return self._record.exchange.sent_at_ns

    @property
    def returned_at_ns(self) -> int:
        return self._record.exchange.returned_at_ns


class H1CompletionExchangeRegistry:
    """One mounted runtime's identity-keyed completed-completion table.

    ``native_sources`` and ``scope_port`` are concrete installed owners, not
    callbacks.  They are replayed during registration so B cannot bind a
    session-held completion reply to copied or stale native/P capabilities.
    """

    __slots__ = (
        "_closed",
        "_historical_prepared_captures",
        "_native_sources",
        "_prepared_captures",
        "_records",
        "_runtime",
        "_scope_port",
    )

    def __init__(
        self,
        *,
        runtime: CommonCliExecutionRuntime,
        native_sources: H1NativeMemberSources,
        scope_port: object,
    ) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 completion registry requires the installed common CLI runtime")
        if (
            type(native_sources) is not H1NativeMemberSources
            or native_sources._runtime is not runtime
        ):
            raise TypeError("H1 completion registry requires the installed native source owner")
        if scope_port is not getattr(
            runtime, "_h1_preissuance_registration_source_port", None
        ) or not callable(getattr(scope_port, "_replay_completion_scope", None)):
            raise TypeError("H1 completion registry requires the installed P scope owner")
        self._runtime = runtime
        self._native_sources = native_sources
        self._scope_port: _CompletionScopeReplayPort = cast(_CompletionScopeReplayPort, scope_port)
        self._records: list[_CompletionExchangeRecord] = []
        self._prepared_captures: dict[
            int, tuple[H1PreparedDeliveryCapture, _CompletionExchangeRecord]
        ] = {}
        self._historical_prepared_captures: dict[
            int, tuple[H1HistoricalPreparedDeliveryCapture, object]
        ] = {}
        self._closed = False

    def _revoke_all(self) -> None:
        """Make this runtime-bound past-stage table permanently unusable."""
        self._records.clear()
        getattr(self, "_prepared_captures", {}).clear()
        getattr(self, "_historical_prepared_captures", {}).clear()
        self._closed = True

    def _register_actual_success(self, session: object) -> None:
        """Retain the exact successful B session exchange, or fail closed.

        No request/result/capability argument is accepted here.  All evidence
        is read from the exact B session's retained private state.
        """
        if getattr(self, "_closed", False):
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry is closed and revoked"
            )
        from chiplog.composition.h1_completion_preparation_session import (
            H1CompletionPreparationSession,
            H1CompletionSessionCut,
        )

        if type(session) is not H1CompletionPreparationSession:
            raise H1CompletionExchangeRegistryViolation("H1 completion registry session is foreign")
        cut = session._cut
        preflight = getattr(session, "_preflight", None)
        exchange = session._completion_exchange
        if type(cut) is not H1CompletionSessionCut or preflight is None:
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry preflight is absent"
            )
        first_path = cut.first_path
        native_cap = getattr(preflight, "native_cap", None)
        scope_cap = getattr(preflight, "scope_cap", None)
        delivery_receipt = getattr(preflight, "delivery_receipt", None)
        request = getattr(preflight, "request", None)
        if (
            type(first_path) is not H1FirstPathCapture
            or getattr(preflight, "first_path", None) is not first_path
            or type(native_cap) is not H1CurrentNativeMemberSourceCut
            or delivery_receipt is None
            or type(request) is not PrepareExecutionCompletionFirstPathV2
            or exchange is None
        ):
            raise H1CompletionExchangeRegistryViolation("H1 completion registry binding is absent")
        if native_cap._capture is not first_path or request.source != first_path.source:
            raise H1CompletionExchangeRegistryViolation("H1 completion registry source differs")
        if (
            type(cut.original_identity) is not DriverCommandIdentityV1
            or type(cut.selected_seal) is not CallSubjectHead
            or not isinstance(cut.original_fingerprint, str)
            or len(cut.original_fingerprint) != 64
            or cut.selected_seal != first_path.source.selected_response_seal
        ):
            raise H1CompletionExchangeRegistryViolation("H1 completion registry locator differs")
        self._require_success(exchange, request)
        # Both concrete owners retain their own private identity tables.  The
        # return values are intentionally discarded: replay is the authority
        # check, while this registry only retains the exact capabilities.
        self._native_sources.replay_current(native_cap)
        self._scope_port._replay_completion_scope(scope_cap, native_cap)
        replay_delivery = getattr(self._scope_port, "_replay_delivery_inputs", None)
        if not callable(replay_delivery):
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry delivery replay is absent"
            )
        replay_delivery(delivery_receipt, first_path)
        if any(record.session is session for record in self._records):
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry session is duplicate"
            )
        capture_historical = getattr(
            self._scope_port, "_capture_j7_historical_completion_evidence", None
        )
        if not callable(capture_historical):
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry historical P capture is absent"
            )
        try:
            historical_p_evidence = capture_historical(
                scope_cap, native_cap, delivery_receipt, first_path
            )
        except (TypeError, ValueError, AttributeError) as error:
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry historical P capture differs"
            ) from error
        self._records.append(
            _CompletionExchangeRecord(
                session,
                first_path,
                exchange,
                native_cap,
                scope_cap,
                delivery_receipt,
                cut.original_identity,
                cut.original_fingerprint,
                cut.selected_seal,
                historical_p_evidence,
            )
        )

    def _replay_completion_exchange(
        self, first_path: object, exchange: object
    ) -> _AuthenticatedCompletionExchangeInputs:
        """Return only a retained exact identity triple; never reconstruct one."""
        if getattr(self, "_closed", False):
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry is closed and revoked"
            )
        for record in self._records:
            if record.first_path is first_path and record.exchange is exchange:
                self._native_sources.replay_current(record.native_cap)
                self._scope_port._replay_completion_scope(record.scope_cap, record.native_cap)
                inputs = object.__new__(_AuthenticatedCompletionExchangeInputs)
                inputs._record = record
                return inputs
        raise H1CompletionExchangeRegistryViolation("H1 completion exchange is not B-registered")

    def capture_prepared_delivery_current(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1PreparedDeliveryCapture:
        """Capture one exactly located, currently replayable B completion."""
        if self._closed:
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry is closed and revoked"
            )
        if (
            type(original_identity) is not DriverCommandIdentityV1
            or not isinstance(original_fingerprint, str)
            or len(original_fingerprint) != 64
            or type(selected_seal) is not CallSubjectHead
        ):
            raise H1CompletionExchangeRegistryViolation("H1 prepared delivery locator differs")
        with self._runtime._authority_gate().hold():
            self._require_mounted_current_held()
            record = self._find_prepared_delivery_record_held(
                original_identity, original_fingerprint, selected_seal
            )
            projection = self._project_prepared_delivery_held(record)
            capture = object.__new__(H1PreparedDeliveryCapture)
            capture._record = record
            capture._projection = projection
            self._prepared_captures[id(capture)] = (capture, record)
            return capture

    def replay_prepared_delivery_current(
        self, capture: H1PreparedDeliveryCapture
    ) -> H1PreparedDeliveryProjection:
        """Reauthenticate and replay an opaque prepared-delivery capture."""
        if self._closed:
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry is closed and revoked"
            )
        with self._runtime._authority_gate().hold():
            self._require_mounted_current_held()
            issued = self._prepared_captures.get(id(capture))
            if (
                type(capture) is not H1PreparedDeliveryCapture
                or issued is None
                or issued[0] is not capture
                or issued[1] is not capture._record
            ):
                raise H1CompletionExchangeRegistryViolation(
                    "H1 prepared delivery capture is not registry-issued"
                )
            projection = self._project_prepared_delivery_held(issued[1])
            if projection != capture._projection:
                raise H1CompletionExchangeRegistryViolation(
                    "H1 prepared delivery replay differs from capture"
                )
            return projection

    def capture_prepared_delivery_historical(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1HistoricalPreparedDeliveryCapture:
        """Return a handle to P/B evidence captured when this B session was live."""
        if self._closed:
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry is closed and revoked"
            )
        if (
            type(original_identity) is not DriverCommandIdentityV1
            or not isinstance(original_fingerprint, str)
            or len(original_fingerprint) != 64
            or type(selected_seal) is not CallSubjectHead
        ):
            raise H1CompletionExchangeRegistryViolation(
                "H1 historical prepared delivery locator differs"
            )
        with self._runtime._authority_gate().hold():
            self._require_mounted_current_held()
            record = self._find_prepared_delivery_record_held(
                original_identity, original_fingerprint, selected_seal
            )
            capture = object.__new__(H1HistoricalPreparedDeliveryCapture)
            capture._record = record
            self._historical_prepared_captures[id(capture)] = (capture, record)
            return capture

    def replay_prepared_delivery_historical(
        self, capture: H1HistoricalPreparedDeliveryCapture
    ) -> H1PreparedDeliveryProjection:
        """Replay the original B/P cut after J7 has advanced the live trust head."""
        if self._closed:
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry is closed and revoked"
            )
        with self._runtime._authority_gate().hold():
            self._require_mounted_current_held()
            issued = self._historical_prepared_captures.get(id(capture))
            if (
                type(capture) is not H1HistoricalPreparedDeliveryCapture
                or issued is None
                or issued[0] is not capture
                or issued[1] is not capture._record
            ):
                raise H1CompletionExchangeRegistryViolation(
                    "H1 historical prepared delivery capture is not registry-issued"
                )
            record = issued[1]
            if type(record) is _CompletionExchangeRecord:
                return self._project_prepared_delivery_historical_held(record)
            from chiplog.composition.h1_v3_recovery_historical_source import (
                H1V3RecoveredPreparedDelivery,
                H1V3RecoveryHistoricalSource,
                H1V3RecoveryHistoricalSourceError,
            )

            if type(record) is not H1V3RecoveredPreparedDelivery:
                raise H1CompletionExchangeRegistryViolation(
                    "H1 historical prepared delivery record is unknown"
                )
            try:
                replayed = H1V3RecoveryHistoricalSource(self._runtime).replay(record)
                return self._project_recovered_prepared_delivery_held(replayed)
            except H1V3RecoveryHistoricalSourceError as error:
                raise H1CompletionExchangeRegistryViolation(
                    "H1 recovered historical prepared delivery replay differs"
                ) from error

    def capture_recovered_prepared_delivery_historical(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1HistoricalPreparedDeliveryCapture:
        """Capture V3 B evidence from its enrolled durable recovery records."""
        if self._closed:
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry is closed and revoked"
            )
        if (
            type(original_identity) is not DriverCommandIdentityV1
            or not isinstance(original_fingerprint, str)
            or len(original_fingerprint) != 64
            or type(selected_seal) is not CallSubjectHead
        ):
            raise H1CompletionExchangeRegistryViolation(
                "H1 recovered historical prepared delivery locator differs"
            )
        from chiplog.composition.h1_v3_recovery_historical_source import (
            H1V3RecoveryHistoricalSource,
            H1V3RecoveryHistoricalSourceError,
        )

        with self._runtime._authority_gate().hold():
            self._require_mounted_current_held()
            try:
                record = H1V3RecoveryHistoricalSource(self._runtime).capture(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                self._project_recovered_prepared_delivery_held(record)
            except H1V3RecoveryHistoricalSourceError as error:
                raise H1CompletionExchangeRegistryViolation(
                    "H1 recovered historical prepared delivery is unavailable"
                ) from error
            capture = object.__new__(H1HistoricalPreparedDeliveryCapture)
            capture._record = record
            self._historical_prepared_captures[id(capture)] = (capture, record)
            return capture

    def _require_mounted_current_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if (
            self._closed
            or getattr(self._runtime, "_h1_completion_exchange_registry", None) is not self
            or self._native_sources._runtime is not self._runtime
            or self._scope_port
            is not getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
        ):
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry is not mounted and current"
            )

    def _find_prepared_delivery_record_held(
        self,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> _CompletionExchangeRecord:
        self._runtime._authority_gate().require_held()
        matches = [
            record
            for record in self._records
            if record.original_identity == original_identity
            and record.original_fingerprint == original_fingerprint
            and record.selected_seal == selected_seal
        ]
        if len(matches) != 1:
            raise H1CompletionExchangeRegistryViolation(
                "H1 prepared delivery locator does not identify one B registration"
            )
        return matches[0]

    def _project_prepared_delivery_held(
        self, record: _CompletionExchangeRecord
    ) -> H1PreparedDeliveryProjection:
        """Replay A/P/B evidence and construct only inert loop-derived values."""
        self._runtime._authority_gate().require_held()
        from chiplog.capabilities.effects.h1_prepared_delivery_basis import (
            derive_h1_prepared_delivery_basis,
        )
        from chiplog.composition.h1_completion_preparation_session import (
            H1CompletionPreparationSession,
            H1CompletionSessionCut,
        )
        from chiplog.composition.h1_runtime_preissuance_port import (
            _AuthenticatedCompletionEffectsSource,
        )

        session = record.session
        if type(session) is not H1CompletionPreparationSession:
            raise H1CompletionExchangeRegistryViolation("H1 registered session differs")
        cut = session._cut
        preflight = session._preflight
        if (
            type(cut) is not H1CompletionSessionCut
            or cut.first_path is not record.first_path
            or cut.original_identity != record.original_identity
            or cut.original_fingerprint != record.original_fingerprint
            or cut.selected_seal != record.selected_seal
            or preflight is None
            or getattr(preflight, "native_cap", None) is not record.native_cap
            or getattr(preflight, "scope_cap", None) is not record.scope_cap
            or getattr(preflight, "delivery_receipt", None) is not record.delivery_receipt
            or session._completion_exchange is not record.exchange
        ):
            raise H1CompletionExchangeRegistryViolation("H1 registered completion differs")

        try:
            self._native_sources.replay_current(record.native_cap)
            scope = self._scope_port._replay_completion_scope(
                record.scope_cap, record.native_cap
            )
            replay_delivery = getattr(self._scope_port, "_replay_delivery_inputs", None)
            if not callable(replay_delivery):
                raise TypeError("delivery receipt replay is unavailable")
            replay_delivery(record.delivery_receipt, record.first_path)
            sources = getattr(self._runtime, "_h1_first_path_sources", None)
            if type(sources) is not H1FirstPathSources or sources._runtime is not self._runtime:
                raise TypeError("first-path sources are unavailable")
            request_builder = getattr(sources, "_prepare_first_path_completion_request", None)
            if not callable(request_builder):
                raise TypeError("first-path request builder is unavailable")
            request = decode_first_path_completion_request(record.exchange.sent.canonical_payload)
            rebuilt = request_builder(record.first_path, record.delivery_receipt)
            if (
                type(rebuilt) is not PrepareExecutionCompletionFirstPathV2
                or rebuilt.canonical_bytes() != record.exchange.sent.canonical_payload
                or request.canonical_bytes() != record.exchange.sent.canonical_payload
            ):
                raise ValueError("retained completion request differs")
            self._require_success(record.exchange, request)
            returned = record.exchange.returned
            assert isinstance(returned, PublicPortSuccess)
            prepared = PreparedExecutionCompletion.model_validate_json(returned.canonical_payload)
            if prepared.canonical_bytes() != returned.canonical_payload:
                raise ValueError("retained completion reply is not canonical")
            expected = prepare_first_path_execution_completion(request)
            if type(expected) is not PreparedExecutionCompletion or prepared != expected:
                raise ValueError("retained completion reply differs from pure preparation")
            basis = derive_h1_prepared_delivery_basis(request, prepared)
            deliveries = prepared.delivery.manifest.ordered_deliveries
            if (
                len(deliveries) != 1
                or not isinstance(deliveries[0], AcceptedDelivery)
                or not deliveries[0].rendered_bytes
                or getattr(deliveries[0].selection, "recipient", None) is None
            ):
                raise ValueError("H1 prepared delivery is not sole and rendered")
            run = request.run
            attempt = request.selected_attempt
            scope_recipient = getattr(scope, "recipient", None)
            scope_policy = getattr(getattr(scope, "scope", None), "disclosure_policy", None)
            if (
                deliveries[0].selection.recipient != run.origin.recipient
                or deliveries[0].selection.recipient != scope_recipient
                or deliveries[0].policy != getattr(scope_policy, "ref", None)
            ):
                raise ValueError("H1 prepared delivery recipient or policy differs from scope")
            effects_source = self._scope_port._replay_completion_effects_source(
                record.scope_cap,
                record.native_cap,
                record.delivery_receipt,
                record.first_path,
            )
            if type(effects_source) is not _AuthenticatedCompletionEffectsSource:
                raise TypeError("H1 prepared delivery effects source is invalid")
            selected_scope = effects_source.selected_scope
            retained_origin = effects_source.retained_origin
            fence = effects_source.fence
            selected = selected_scope.scope
            if (
                retained_origin.initialization_envelope_bytes
                != record.first_path.initialization_envelope_bytes
                or selected != getattr(scope, "scope", None)
                or selected.tenant_id != run.tenant
                or selected.database_id != request.source.database_id
                or selected.principal_id != run.principal
                or selected.worker_session_id != run.worker_session
                or deliveries[0].selection.recipient != selected.recipient
                or deliveries[0].policy != selected.disclosure_policy.ref
                or fence.canonical_bytes() != request.fence.canonical_bytes()
            ):
                raise ValueError("H1 prepared delivery effects source differs")
            return H1PreparedDeliveryProjection(
                basis=basis,
                selected_scope=selected_scope,
                retained_origin=retained_origin,
                fence=fence,
                tenant_id=run.tenant,
                database_id=request.source.database_id,
                principal_id=run.principal,
                worker_session_id=run.worker_session,
                original_run=LoopHead(
                    identity=run.run_id, head=run.head, fingerprint=run.digest()
                ),
                captured_attempt=LoopHead(
                    identity=attempt.subject_id,
                    head=attempt.revision.head,
                    fingerprint=attempt.revision.fingerprint,
                ),
                delivery=deliveries[0],
            )
        except (TypeError, ValueError, AttributeError) as error:
            raise H1CompletionExchangeRegistryViolation(
                "H1 prepared delivery replay is not current"
            ) from error

    def _project_prepared_delivery_historical_held(
        self, record: _CompletionExchangeRecord
    ) -> H1PreparedDeliveryProjection:
        """Join P's pre-ISSUE evidence to the exact retained B exchange only."""
        self._runtime._authority_gate().require_held()
        from chiplog.capabilities.effects.h1_prepared_delivery_basis import (
            derive_h1_prepared_delivery_basis,
        )
        from chiplog.composition.h1_completion_preparation_session import (
            H1CompletionPreparationSession,
            H1CompletionSessionCut,
        )
        from chiplog.composition.h1_runtime_preissuance_port import (
            _AuthenticatedCompletionEffectsSource,
        )

        session = record.session
        if type(session) is not H1CompletionPreparationSession:
            raise H1CompletionExchangeRegistryViolation("H1 registered historical session differs")
        cut = session._cut
        preflight = session._preflight
        if (
            type(cut) is not H1CompletionSessionCut
            or cut.first_path is not record.first_path
            or cut.original_identity != record.original_identity
            or cut.original_fingerprint != record.original_fingerprint
            or cut.selected_seal != record.selected_seal
            or preflight is None
            or getattr(preflight, "native_cap", None) is not record.native_cap
            or getattr(preflight, "scope_cap", None) is not record.scope_cap
            or getattr(preflight, "delivery_receipt", None) is not record.delivery_receipt
            or session._completion_exchange is not record.exchange
        ):
            raise H1CompletionExchangeRegistryViolation(
                "H1 registered historical completion differs"
            )
        replay = getattr(self._scope_port, "_replay_j7_historical_completion_evidence", None)
        if not callable(replay):
            raise H1CompletionExchangeRegistryViolation(
                "H1 historical prepared delivery P replay is absent"
            )
        try:
            scope, effects_source, delivery_observation = replay(record.historical_p_evidence)
            if type(effects_source) is not _AuthenticatedCompletionEffectsSource:
                raise TypeError("historical effects source is invalid")
            request = decode_first_path_completion_request(record.exchange.sent.canonical_payload)
            if request.canonical_bytes() != record.exchange.sent.canonical_payload:
                raise ValueError("retained historical completion request is noncanonical")
            if (
                request.source != record.first_path.source
                or request.delivery != delivery_observation
                or request.fence.canonical_bytes() != effects_source.fence.canonical_bytes()
            ):
                raise ValueError("retained historical completion request differs")
            self._require_success(record.exchange, request)
            returned = record.exchange.returned
            assert isinstance(returned, PublicPortSuccess)
            prepared = PreparedExecutionCompletion.model_validate_json(returned.canonical_payload)
            if prepared.canonical_bytes() != returned.canonical_payload:
                raise ValueError("retained historical completion reply is noncanonical")
            expected = prepare_first_path_execution_completion(request)
            if type(expected) is not PreparedExecutionCompletion or prepared != expected:
                raise ValueError(
                    "retained historical completion reply differs from pure preparation"
                )
            basis = derive_h1_prepared_delivery_basis(request, prepared)
            deliveries = prepared.delivery.manifest.ordered_deliveries
            if (
                len(deliveries) != 1
                or not isinstance(deliveries[0], AcceptedDelivery)
                or not deliveries[0].rendered_bytes
                or getattr(deliveries[0].selection, "recipient", None) is None
            ):
                raise ValueError("H1 historical prepared delivery is not sole and rendered")
            run, attempt = request.run, request.selected_attempt
            selected_scope = effects_source.selected_scope
            retained_origin = effects_source.retained_origin
            fence = effects_source.fence
            selected = selected_scope.scope
            if (
                deliveries[0].selection.recipient != run.origin.recipient
                or deliveries[0].selection.recipient != scope.recipient
                or deliveries[0].policy != scope.policy_ref
                or retained_origin.initialization_envelope_bytes
                != record.first_path.initialization_envelope_bytes
                or selected != scope.scope
                or selected.tenant_id != run.tenant
                or selected.database_id != request.source.database_id
                or selected.principal_id != run.principal
                or selected.worker_session_id != run.worker_session
                or deliveries[0].selection.recipient != selected.recipient
                or deliveries[0].policy != selected.disclosure_policy.ref
                or fence.canonical_bytes() != request.fence.canonical_bytes()
            ):
                raise ValueError("H1 historical prepared delivery joins differ")
            return H1PreparedDeliveryProjection(
                basis=basis,
                selected_scope=selected_scope,
                retained_origin=retained_origin,
                fence=fence,
                tenant_id=run.tenant,
                database_id=request.source.database_id,
                principal_id=run.principal,
                worker_session_id=run.worker_session,
                original_run=LoopHead(
                    identity=run.run_id, head=run.head, fingerprint=run.digest()
                ),
                captured_attempt=LoopHead(
                    identity=attempt.subject_id,
                    head=attempt.revision.head,
                    fingerprint=attempt.revision.fingerprint,
                ),
                delivery=deliveries[0],
            )
        except (TypeError, ValueError, AttributeError) as error:
            raise H1CompletionExchangeRegistryViolation(
                "H1 historical prepared delivery replay differs"
            ) from error

    def _project_recovered_prepared_delivery_held(
        self, record: object
    ) -> H1PreparedDeliveryProjection:
        """Build the inert B projection from durable V3 records and historical P/E."""
        self._runtime._authority_gate().require_held()
        from chiplog.capabilities.effects.h1_prepared_delivery_basis import (
            derive_h1_prepared_delivery_basis,
        )
        from chiplog.composition.h1_v3_recovery_historical_source import (
            H1V3RecoveredPreparedDelivery,
        )

        if type(record) is not H1V3RecoveredPreparedDelivery:
            raise H1CompletionExchangeRegistryViolation(
                "H1 recovered historical prepared delivery record differs"
            )
        try:
            request = record.completion_request
            prepared = record.completion_result
            effects_source = record.effects_source
            if (
                request.canonical_bytes() != record.pinned_records[1].canonical_bytes
                or prepared.canonical_bytes() != record.pinned_records[2].canonical_bytes
                or len(record.pinned_records) != 5
            ):
                raise ValueError("recovered completion records differ")
            basis = derive_h1_prepared_delivery_basis(request, prepared)
            deliveries = prepared.delivery.manifest.ordered_deliveries
            if (
                len(deliveries) != 1
                or not isinstance(deliveries[0], AcceptedDelivery)
                or not deliveries[0].rendered_bytes
                or getattr(deliveries[0].selection, "recipient", None) is None
            ):
                raise ValueError("recovered prepared delivery is not sole and rendered")
            selected_scope = effects_source.selected_scope
            retained_origin = effects_source.retained_origin
            selected = selected_scope.scope
            run, attempt = request.run, request.selected_attempt
            if (
                deliveries[0].selection.recipient != run.origin.recipient
                or deliveries[0].selection.recipient != selected.recipient
                or deliveries[0].policy != selected.disclosure_policy.ref
                or selected.tenant_id != run.tenant
                or selected.database_id != request.source.database_id
                or selected.principal_id != run.principal
                or selected.worker_session_id != run.worker_session
                or record.fence.canonical_bytes() != request.fence.canonical_bytes()
                or not retained_origin.initialization_envelope_bytes
            ):
                raise ValueError("recovered prepared delivery joins differ")
            return H1PreparedDeliveryProjection(
                basis=basis,
                selected_scope=selected_scope,
                retained_origin=retained_origin,
                fence=record.fence,
                tenant_id=run.tenant,
                database_id=request.source.database_id,
                principal_id=run.principal,
                worker_session_id=run.worker_session,
                original_run=LoopHead(
                    identity=run.run_id, head=run.head, fingerprint=run.digest()
                ),
                captured_attempt=LoopHead(
                    identity=attempt.subject_id,
                    head=attempt.revision.head,
                    fingerprint=attempt.revision.fingerprint,
                ),
                delivery=deliveries[0],
            )
        except (AttributeError, TypeError, ValueError, IndexError) as error:
            raise H1CompletionExchangeRegistryViolation(
                "H1 recovered historical prepared delivery projection differs"
            ) from error

    @staticmethod
    def _require_success(
        exchange: H1CompletionOwnerExchangeV1,
        request: PrepareExecutionCompletionFirstPathV2,
    ) -> None:
        if type(exchange) is not H1CompletionOwnerExchangeV1 or exchange.role != "completion":
            raise H1CompletionExchangeRegistryViolation("H1 completion exchange role differs")
        sent = exchange.sent
        returned = exchange.returned
        if (
            type(sent) is not PublicPortCall
            or type(returned) is not PublicPortSuccess
            or sent.operation_id != "agent_loop.prepare_first_path_completion"
            or sent.schema_id != request.schema_id
            or sent.canonical_payload != request.canonical_bytes()
            or sent.caller.owner_id != "broker"
            or sent.callee.owner_id != "agent_loop"
            or returned.request_id != sent.request_id
            or returned.responder != sent.callee
            or returned.schema_id != "chiplog.agent-loop.prepared-execution-completion-result.v1"
            or not (0 <= exchange.sent_at_ns <= exchange.returned_at_ns)
            or exchange.returned_at_ns >= sent.budget.absolute_deadline_ns
        ):
            raise H1CompletionExchangeRegistryViolation("H1 completion exchange differs")
        try:
            result = PreparedExecutionCompletion.model_validate_json(returned.canonical_payload)
        except ValueError as error:
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion reply is malformed"
            ) from error
        if (
            result.canonical_bytes() != returned.canonical_payload
            or result.source_request_fingerprint
            != first_path_completion_request_fingerprint(request)
        ):
            raise H1CompletionExchangeRegistryViolation("H1 completion reply differs")


__all__ = [
    "H1CompletionExchangeRegistry",
    "H1CompletionExchangeRegistryViolation",
    "H1HistoricalPreparedDeliveryCapture",
    "H1PreparedDeliveryCapture",
    "H1PreparedDeliveryProjection",
]
