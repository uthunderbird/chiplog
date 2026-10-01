"""Inert V22 reader for the bounded clock role of a selected V4/g1 cut."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass

from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_scoped_authorization_delivery_work_reader import (
    H1ScopedAuthorizationDeliveryWorkCaptureV1,
    H1ScopedAuthorizationDeliveryWorkHold,
    H1ScopedAuthorizationDeliveryWorkReaderV1,
)
from chiplog.composition.h1_scoped_authorization_resource_sources_v4 import (
    H1ScopedAuthorizationResourceCaptureV4,
    H1ScopedAuthorizationResourceReaderV4,
)
from chiplog.composition.h1_scoped_authorization_sources_v3 import H1ScopedAuthorizationResourceHold
from chiplog.composition.r16_dispatch_registry import CLOCK


class H1ScopedAuthorizationClockHold(PermissionError):
    """The selected V4/g1 clock cut is unavailable or no longer current."""


@dataclass(frozen=True, slots=True)
class H1ScopedAuthorizationClockRoleV1:
    """An inert bounded clock observation; it grants neither authorization nor SEND."""

    clock_contract: str
    clock_epoch: str
    observed_at_ns: int
    valid_until_ns: int


@dataclass(frozen=True, slots=True)
class _ClockRecord:
    joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    resource_capture: H1ScopedAuthorizationResourceCaptureV4
    source: object
    current_claim: object
    horizon: object
    role: H1ScopedAuthorizationClockRoleV1


class H1ScopedAuthorizationClockCaptureV1:
    """Opaque reader-issued V22 clock capture."""

    __slots__ = ("_record",)
    _record: _ClockRecord

    def __init__(self) -> None:
        raise TypeError("H1 V22 clock captures are reader-issued")

    def __copy__(self) -> H1ScopedAuthorizationClockCaptureV1:
        raise TypeError("H1 V22 clock captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ScopedAuthorizationClockCaptureV1:
        del memo
        raise TypeError("H1 V22 clock captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 V22 clock captures cannot be serialized")


class H1ScopedAuthorizationClockReaderV1:
    """Read the original mandate clock window from one gate-held V4/g1 cut."""

    __slots__ = ("_closed", "_issued", "_joined", "_resources", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 V22 clock reader requires installed runtime")
        joined = getattr(runtime, "_h1_scoped_authorization_delivery_work_reader_v1", None)
        resources = getattr(runtime, "_h1_scoped_authorization_resource_reader_v4", None)
        if (
            type(joined) is not H1ScopedAuthorizationDeliveryWorkReaderV1
            or joined._runtime is not runtime
            or type(resources) is not H1ScopedAuthorizationResourceReaderV4
            or resources._runtime is not runtime
        ):
            raise TypeError("H1 V22 clock reader requires mounted V4 readers")
        self._runtime = runtime
        self._joined = joined
        self._resources = resources
        self._issued: dict[int, tuple[H1ScopedAuthorizationClockCaptureV1, _ClockRecord]] = {}
        self._closed = False

    def read(
        self, capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedAuthorizationClockCaptureV1:
        if type(capture) is not H1ScopedAuthorizationDeliveryWorkCaptureV1:
            raise TypeError("H1 V22 clock reader requires joined reader capture")
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            resource_capture = self._resources.read(capture)
            try:
                record = self._read_record_held(capture, resource_capture, previous_now=None)
            except Exception:
                self._resources.release(resource_capture)
                raise
            issued = object.__new__(H1ScopedAuthorizationClockCaptureV1)
            issued._record = record
            self._issued[id(issued)] = (issued, record)
            return issued

    def recheck_held(
        self, capture: H1ScopedAuthorizationClockCaptureV1
    ) -> H1ScopedAuthorizationClockCaptureV1:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_record_held(
            record.joined_capture, record.resource_capture, previous_now=record.role.observed_at_ns
        )
        if (
            reread.source != record.source
            or reread.current_claim != record.current_claim
            or reread.horizon != record.horizon
            or reread.role.clock_contract != record.role.clock_contract
            or reread.role.clock_epoch != record.role.clock_epoch
            or reread.role.valid_until_ns != record.role.valid_until_ns
        ):
            raise H1ScopedAuthorizationClockHold("H1 V22 clock sources changed")
        capture._record = reread
        self._issued[id(capture)] = (capture, reread)
        return capture

    def role_held(
        self, capture: H1ScopedAuthorizationClockCaptureV1
    ) -> H1ScopedAuthorizationClockRoleV1:
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).role

    def release(self, capture: H1ScopedAuthorizationClockCaptureV1) -> None:
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            record = self._issued_record_held(capture)
            del self._issued[id(capture)]
            # The dependent reader may already have revoked this capture.
            with suppress(H1ScopedAuthorizationResourceHold):
                self._resources.release(record.resource_capture)

    def close(self) -> None:
        with self._runtime._authority_gate().hold():
            if self._closed:
                return
            self._closed = True
            records = tuple(record for _, record in self._issued.values())
            self._issued.clear()
            for record in records:
                # Teardown may follow independent dependency revocation.
                with suppress(H1ScopedAuthorizationResourceHold):
                    self._resources.release(record.resource_capture)

    def _require_open_held(self) -> None:
        self._runtime._authority_gate().require_held()
        if self._closed:
            raise H1ScopedAuthorizationClockHold("H1 V22 clock reader is closed")

    def _issued_record_held(self, capture: H1ScopedAuthorizationClockCaptureV1) -> _ClockRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationClockCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationClockHold("H1 V22 clock capture is not reader-issued")
        return issued[1]

    def _read_record_held(
        self,
        joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1,
        resource_capture: H1ScopedAuthorizationResourceCaptureV4,
        *,
        previous_now: int | None,
    ) -> _ClockRecord:
        self._runtime._authority_gate().require_held()
        try:
            first_epoch, first_now = self._registered_clock_held()
            self._resources.recheck_held(resource_capture)
            resource_record = self._resources._issued_record_held(resource_capture)
            if resource_record.delivery_work_capture is not joined_capture:
                raise H1ScopedAuthorizationClockHold("H1 V22 resource capture differs")
            roles = resource_record.roles
            evidence = self._joined._issued_record_held(joined_capture).evidence
            effects = self._joined._effects._issued_record_held(
                self._joined._issued_record_held(joined_capture).effects_capture
            )
            horizon = effects.observed.original_intent.mandate.horizon
            final_epoch, final_now = self._registered_clock_held()
        except (
            H1ScopedAuthorizationDeliveryWorkHold,
            H1ScopedAuthorizationResourceHold,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1ScopedAuthorizationClockHold("H1 V22 clock sources are unavailable") from error
        lease = evidence.current_claim.binding.claimant_clock.lease_expires_at_ns
        epoch = evidence.current_claim.binding.claimant_clock.clock_epoch
        if (
            type(first_now) is not int
            or type(final_now) is not int
            or type(first_epoch) is not str
            or not first_epoch
            or first_epoch != final_epoch
            or first_now > final_now
            or (previous_now is not None and first_now < previous_now)
            or horizon.clock_contract != CLOCK
            or horizon.clock_epoch != first_epoch
            or roles.clock_epoch != first_epoch
            or epoch != first_epoch
            or type(lease) is not int
            or type(horizon.not_before_ns) is not int
            or type(horizon.expires_at_ns) is not int
            or not horizon.not_before_ns <= final_now < min(horizon.expires_at_ns, lease)
        ):
            raise H1ScopedAuthorizationClockHold("H1 V22 clock horizon, epoch, or lease differs")
        return _ClockRecord(
            joined_capture=joined_capture,
            resource_capture=resource_capture,
            source=evidence.source,
            current_claim=evidence.current_claim,
            horizon=horizon,
            role=H1ScopedAuthorizationClockRoleV1(
                clock_contract=CLOCK,
                clock_epoch=first_epoch,
                observed_at_ns=final_now,
                valid_until_ns=min(horizon.expires_at_ns, lease),
            ),
        )

    def _registered_clock_held(self) -> tuple[str, int]:
        """Use only the R16 dispatch-resource clock registered by its contract."""
        self._runtime._authority_gate().require_held()
        resources = self._runtime._require_dispatch_resources()
        epoch, now = resources.clock()
        if type(epoch) is not str or not epoch or type(now) is not int or now < 0:
            raise H1ScopedAuthorizationClockHold("H1 V22 registered clock is invalid")
        return epoch, now


__all__ = [
    "H1ScopedAuthorizationClockCaptureV1",
    "H1ScopedAuthorizationClockHold",
    "H1ScopedAuthorizationClockReaderV1",
    "H1ScopedAuthorizationClockRoleV1",
]
