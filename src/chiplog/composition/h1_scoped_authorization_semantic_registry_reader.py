"""Inert V22 reader for an exact selected V4 H1 semantic-registry pair."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from chiplog.capabilities.effects.dispatch_authority_contracts import CapturedSource
from chiplog.capabilities.effects.h1_scoped_dispatch_semantics import (
    H1_SCOPED_DISPATCH_V2_SEMANTICS,
    PREPARE_SCOPED_AUTHORIZATION_V3,
    h1_scoped_dispatch_v2_semantic_registry_capture,
    require_h1_scoped_dispatch_operation,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.composition.h1_scoped_authorization_clock_reader import (
    H1ScopedAuthorizationClockCaptureV1,
    H1ScopedAuthorizationClockHold,
    H1ScopedAuthorizationClockReaderV1,
)
from chiplog.composition.h1_scoped_authorization_delivery_work_reader import (
    H1ScopedAuthorizationDeliveryWorkCaptureV1,
    H1ScopedAuthorizationDeliveryWorkHold,
    H1ScopedAuthorizationDeliveryWorkReaderV1,
)


class H1ScopedAuthorizationSemanticRegistryHold(PermissionError):
    """The selected V4 semantic-registry evidence is unavailable or stale."""


@dataclass(frozen=True, slots=True)
class H1ScopedAuthorizationSemanticRegistryRoleV1:
    """Original and current V2 semantic captures; this grants no authorization or SEND."""

    original: CapturedSource
    current: CapturedSource


@dataclass(frozen=True, slots=True)
class _SemanticRegistryRecord:
    delivery_work_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    clock_capture: H1ScopedAuthorizationClockCaptureV1
    role: H1ScopedAuthorizationSemanticRegistryRoleV1


class H1ScopedAuthorizationSemanticRegistryCaptureV1:
    """Opaque capture issued by one installed V22 semantic-registry reader."""

    __slots__ = ("_record",)
    _record: _SemanticRegistryRecord

    def __init__(self) -> None:
        raise TypeError("H1 V22 semantic-registry captures are reader-issued")

    def __copy__(self) -> H1ScopedAuthorizationSemanticRegistryCaptureV1:
        raise TypeError("H1 V22 semantic-registry captures cannot be copied")

    def __deepcopy__(
        self, memo: dict[int, object]
    ) -> H1ScopedAuthorizationSemanticRegistryCaptureV1:
        del memo
        raise TypeError("H1 V22 semantic-registry captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 V22 semantic-registry captures cannot be serialized")


class H1ScopedAuthorizationSemanticRegistryReaderV1:
    """Authenticate exact V2 registry captures from a selected V4/g1 cut."""

    __slots__ = ("_clock", "_closed", "_issued", "_joined", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 V22 semantic-registry reader requires installed runtime")
        joined = getattr(runtime, "_h1_scoped_authorization_delivery_work_reader_v1", None)
        clock = getattr(runtime, "_h1_scoped_authorization_clock_reader_v1", None)
        if (
            type(joined) is not H1ScopedAuthorizationDeliveryWorkReaderV1
            or joined._runtime is not runtime
            or type(clock) is not H1ScopedAuthorizationClockReaderV1
            or clock._runtime is not runtime
        ):
            raise TypeError("H1 V22 semantic-registry reader requires mounted V4 readers")
        self._runtime = runtime
        self._joined = joined
        self._clock = clock
        self._issued: dict[
            int,
            tuple[
                H1ScopedAuthorizationSemanticRegistryCaptureV1,
                _SemanticRegistryRecord,
            ],
        ] = {}
        self._closed = False

    def read(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedAuthorizationSemanticRegistryCaptureV1:
        if type(capture) is not H1ScopedAuthorizationDeliveryWorkCaptureV1:
            raise TypeError("H1 V22 semantic-registry reader requires joined reader capture")
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            clock_capture = self._clock.read(capture)
            try:
                record = self._read_record_held(capture, clock_capture)
            except Exception:
                with suppress(H1ScopedAuthorizationClockHold):
                    self._clock.release(clock_capture)
                raise
            issued = object.__new__(H1ScopedAuthorizationSemanticRegistryCaptureV1)
            issued._record = record
            self._issued[id(issued)] = (issued, record)
            return issued

    def recheck_held(
        self, capture: H1ScopedAuthorizationSemanticRegistryCaptureV1
    ) -> H1ScopedAuthorizationSemanticRegistryCaptureV1:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_record_held(record.delivery_work_capture, record.clock_capture)
        if reread != record:
            raise H1ScopedAuthorizationSemanticRegistryHold(
                "H1 V22 semantic-registry sources changed"
            )
        return capture

    def role_held(
        self, capture: H1ScopedAuthorizationSemanticRegistryCaptureV1
    ) -> H1ScopedAuthorizationSemanticRegistryRoleV1:
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).role

    def release(self, capture: H1ScopedAuthorizationSemanticRegistryCaptureV1) -> None:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._issued_record_held(capture)
            del self._issued[id(capture)]
            with suppress(H1ScopedAuthorizationClockHold):
                self._clock.release(record.clock_capture)

    def close(self) -> None:
        with self._runtime._authority_gate().hold():
            if self._closed:
                return
            self._closed = True
            records = tuple(record for _, record in self._issued.values())
            self._issued.clear()
            for record in records:
                with suppress(H1ScopedAuthorizationClockHold):
                    self._clock.release(record.clock_capture)

    def _require_open_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1ScopedAuthorizationSemanticRegistryHold(
                "H1 V22 semantic-registry reader is closed"
            )

    def _issued_record_held(
        self, capture: H1ScopedAuthorizationSemanticRegistryCaptureV1
    ) -> _SemanticRegistryRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationSemanticRegistryCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationSemanticRegistryHold(
                "H1 V22 semantic-registry capture is not reader-issued"
            )
        return issued[1]

    def _read_record_held(
        self,
        delivery_work_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1,
        clock_capture: H1ScopedAuthorizationClockCaptureV1,
    ) -> _SemanticRegistryRecord:
        self._runtime._authority_gate().require_held()
        try:
            self._joined.recheck_held(delivery_work_capture)
            clock = self._clock.role_held(clock_capture)
            joined = self._joined._issued_record_held(delivery_work_capture)
            evidence = joined.evidence
            effects = self._joined._effects._issued_record_held(joined.effects_capture)
            issuance = effects.selected.selected_h1.issuance
            if type(issuance) is not H1CompletionIssuanceV4:
                raise TypeError("selected issuance is not V4")
            original_sources = issuance.effects_call.request.intent.acquisition.original_sources
            original = original_sources.semantic_registry
            lease = evidence.current_claim.binding.claimant_clock.lease_expires_at_ns
            if type(original) is not CapturedSource or type(lease) is not int:
                raise TypeError("semantic registry or g1 lease is malformed")
            current = h1_scoped_dispatch_v2_semantic_registry_capture(
                clock_contract=clock.clock_contract,
                clock_epoch=clock.clock_epoch,
                valid_until_ns=lease,
            )
            require_h1_scoped_dispatch_operation(
                semantics=H1_SCOPED_DISPATCH_V2_SEMANTICS,
                operation=PREPARE_SCOPED_AUTHORIZATION_V3,
                original=original,
                current=current,
                clock_contract=clock.clock_contract,
                clock_epoch=clock.clock_epoch,
                valid_until_ns=lease,
            )
        except (
            H1ScopedAuthorizationDeliveryWorkHold,
            H1ScopedAuthorizationClockHold,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1ScopedAuthorizationSemanticRegistryHold(
                "H1 V22 semantic-registry sources are unavailable"
            ) from error
        return _SemanticRegistryRecord(
            delivery_work_capture=delivery_work_capture,
            clock_capture=clock_capture,
            role=H1ScopedAuthorizationSemanticRegistryRoleV1(original=original, current=current),
        )


__all__ = [
    "H1ScopedAuthorizationSemanticRegistryCaptureV1",
    "H1ScopedAuthorizationSemanticRegistryHold",
    "H1ScopedAuthorizationSemanticRegistryReaderV1",
    "H1ScopedAuthorizationSemanticRegistryRoleV1",
]
