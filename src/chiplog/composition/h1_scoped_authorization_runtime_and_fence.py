"""Inert V22 runtime-and-fence source composed from selected g1 and clock evidence."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, replace

from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
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


class H1ScopedAuthorizationRuntimeAndFenceHold(PermissionError):
    """The V22 runtime-and-fence evidence is unavailable or stale."""


@dataclass(frozen=True, slots=True)
class H1ScopedAuthorizationRuntimeAndFenceRoleV1:
    """Inert exact runtime evidence.  It cannot create current authorization."""

    selected_source: object
    selected_decision_id: str
    selected_decision_fingerprint: str
    physical_g1_bytes: bytes
    claimant_holder_id: str
    claimant_session_id: str
    broker_epoch: str
    runtime_generation: str
    clock_epoch: str
    observed_at_ns: int
    lease_expires_at_ns: int


@dataclass(frozen=True, slots=True)
class _RuntimeAndFenceRecord:
    joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    clock_capture: H1ScopedAuthorizationClockCaptureV1
    role: H1ScopedAuthorizationRuntimeAndFenceRoleV1


class H1ScopedAuthorizationRuntimeCaptureV1:
    """Opaque capture issued by one installed V22 runtime reader."""

    __slots__ = ("_record",)
    _record: _RuntimeAndFenceRecord

    def __init__(self) -> None:
        raise TypeError("H1 V22 runtime-and-fence captures are reader-issued")

    def __copy__(self) -> H1ScopedAuthorizationRuntimeCaptureV1:
        raise TypeError("H1 V22 runtime-and-fence captures cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ScopedAuthorizationRuntimeCaptureV1:
        del memo
        raise TypeError("H1 V22 runtime-and-fence captures cannot be copied")

    def __reduce__(self) -> str:
        raise TypeError("H1 V22 runtime-and-fence captures cannot be serialized")


class H1ScopedAuthorizationRuntimeReaderV1:
    """Read only the selected runtime and fence evidence under ``AuthorityGate``."""

    __slots__ = ("_clock", "_closed", "_issued", "_joined", "_runtime")

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 V22 runtime-and-fence reader requires installed runtime")
        joined = getattr(runtime, "_h1_scoped_authorization_delivery_work_reader_v1", None)
        clock = getattr(runtime, "_h1_scoped_authorization_clock_reader_v1", None)
        if (
            type(joined) is not H1ScopedAuthorizationDeliveryWorkReaderV1
            or joined._runtime is not runtime
            or type(clock) is not H1ScopedAuthorizationClockReaderV1
            or clock._runtime is not runtime
        ):
            raise TypeError("H1 V22 runtime-and-fence reader requires mounted V4 readers")
        self._runtime = runtime
        self._joined = joined
        self._clock = clock
        self._issued: dict[
            int, tuple[H1ScopedAuthorizationRuntimeCaptureV1, _RuntimeAndFenceRecord]
        ] = {}
        self._closed = False

    def read(
        self, joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1
    ) -> H1ScopedAuthorizationRuntimeCaptureV1:
        if type(joined_capture) is not H1ScopedAuthorizationDeliveryWorkCaptureV1:
            raise TypeError("H1 V22 runtime-and-fence reader requires joined reader capture")
        with self._runtime._authority_gate().hold():
            self._require_open_held()
            clock_capture = self._clock.read(joined_capture)
            try:
                record = self._read_record_held(joined_capture, clock_capture, previous_now=None)
            except Exception:
                with suppress(H1ScopedAuthorizationClockHold):
                    self._clock.release(clock_capture)
                raise
            capture = object.__new__(H1ScopedAuthorizationRuntimeCaptureV1)
            capture._record = record
            self._issued[id(capture)] = (capture, record)
            return capture

    def recheck_held(
        self, capture: H1ScopedAuthorizationRuntimeCaptureV1
    ) -> H1ScopedAuthorizationRuntimeCaptureV1:
        self._runtime._authority_gate().require_held()
        self._require_open_held()
        record = self._issued_record_held(capture)
        reread = self._read_record_held(
            record.joined_capture, record.clock_capture, previous_now=record.role.observed_at_ns
        )
        if reread.role != replace(record.role, observed_at_ns=reread.role.observed_at_ns):
            raise H1ScopedAuthorizationRuntimeAndFenceHold(
                "H1 V22 runtime-and-fence sources changed"
            )
        capture._record = reread
        self._issued[id(capture)] = (capture, reread)
        return capture

    def role_held(
        self, capture: H1ScopedAuthorizationRuntimeCaptureV1
    ) -> H1ScopedAuthorizationRuntimeAndFenceRoleV1:
        self._runtime._authority_gate().require_held()
        self.recheck_held(capture)
        return self._issued_record_held(capture).role

    def release(self, capture: H1ScopedAuthorizationRuntimeCaptureV1) -> None:
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
            raise H1ScopedAuthorizationRuntimeAndFenceHold(
                "H1 V22 runtime-and-fence reader is closed"
            )

    def _issued_record_held(
        self, capture: H1ScopedAuthorizationRuntimeCaptureV1
    ) -> _RuntimeAndFenceRecord:
        issued = self._issued.get(id(capture))
        if (
            type(capture) is not H1ScopedAuthorizationRuntimeCaptureV1
            or issued is None
            or issued[0] is not capture
            or issued[1] is not capture._record
        ):
            raise H1ScopedAuthorizationRuntimeAndFenceHold(
                "H1 V22 runtime-and-fence capture is not reader-issued"
            )
        return issued[1]

    def _read_record_held(
        self,
        joined_capture: H1ScopedAuthorizationDeliveryWorkCaptureV1,
        clock_capture: H1ScopedAuthorizationClockCaptureV1,
        *,
        previous_now: int | None,
    ) -> _RuntimeAndFenceRecord:
        self._runtime._authority_gate().require_held()
        try:
            self._joined.recheck_held(joined_capture)
            clock = self._clock.role_held(clock_capture)
            evidence = self._joined._issued_record_held(joined_capture).evidence
            claim = evidence.current_claim
            binding = claim.binding.claimant_clock
            g1_bytes = claim.g1.canonical_bytes()
        except (
            H1ScopedAuthorizationDeliveryWorkHold,
            H1ScopedAuthorizationClockHold,
            AttributeError,
            TypeError,
            ValueError,
        ) as error:
            raise H1ScopedAuthorizationRuntimeAndFenceHold(
                "H1 V22 runtime-and-fence sources are unavailable"
            ) from error
        if (
            type(g1_bytes) is not bytes
            or type(clock.observed_at_ns) is not int
            or (previous_now is not None and clock.observed_at_ns < previous_now)
            or binding.clock_epoch != clock.clock_epoch
            or type(clock.valid_until_ns) is not int
            or clock.valid_until_ns > binding.lease_expires_at_ns
        ):
            raise H1ScopedAuthorizationRuntimeAndFenceHold(
                "H1 V22 runtime-and-fence clock or claim differs"
            )
        return _RuntimeAndFenceRecord(
            joined_capture=joined_capture,
            clock_capture=clock_capture,
            role=H1ScopedAuthorizationRuntimeAndFenceRoleV1(
                selected_source=evidence.source,
                selected_decision_id=claim.claim_decision_id,
                selected_decision_fingerprint=claim.claim_decision_fingerprint,
                physical_g1_bytes=g1_bytes,
                claimant_holder_id=binding.holder_id,
                claimant_session_id=binding.session_id,
                broker_epoch=binding.broker_epoch,
                runtime_generation=binding.runtime_generation,
                clock_epoch=clock.clock_epoch,
                observed_at_ns=clock.observed_at_ns,
                lease_expires_at_ns=binding.lease_expires_at_ns,
            ),
        )


__all__ = [
    "H1ScopedAuthorizationRuntimeAndFenceHold",
    "H1ScopedAuthorizationRuntimeAndFenceRoleV1",
    "H1ScopedAuthorizationRuntimeCaptureV1",
    "H1ScopedAuthorizationRuntimeReaderV1",
]
