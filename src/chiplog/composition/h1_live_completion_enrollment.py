"""Private, runtime-owned enrollment for future live H1 completion minting.

This owner establishes provenance only.  It does not issue a publication
capability or make the mounted writer authority permissive.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from chiplog.composition.h1_completion_preparation_session import (
    H1CompletionPreparationSession,
    H1CompletionSessionCut,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathSources
from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority
from chiplog.composition.h1_native_member_sources import H1NativeMemberSources
from chiplog.composition.h1_recovery_execution_fence import _H1RecoveryExecutionLease
from chiplog.composition.h1_recovery_stage_source import H1RecoveryStageSource
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.broker import PublicPortCall


class H1LiveCompletionEnrollmentUnavailable(ValueError):
    """The caller did not originate from the one mounted H1 enrollment owner."""


class _H1LiveCompletionHandle:
    """Unserializable identity retained only by the enrollment table."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 live completion handles are enrollment-issued")

    def __copy__(self) -> _H1LiveCompletionHandle:
        raise TypeError("H1 live completion handles cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> _H1LiveCompletionHandle:
        del memo
        raise TypeError("H1 live completion handles cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 live completion handles cannot be serialized")


class _H1TerminalClearance:
    """Identity-held, one-shot terminal-admission record."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 terminal clearances are enrollment-issued")

    def __copy__(self) -> _H1TerminalClearance:
        raise TypeError("H1 terminal clearances cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> _H1TerminalClearance:
        del memo
        raise TypeError("H1 terminal clearances cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 terminal clearances cannot be serialized")


class _H1RecoveryClearance:
    """Identity-held, one-shot recovery-admission record."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 recovery clearances are enrollment-issued")

    def __copy__(self) -> _H1RecoveryClearance:
        raise TypeError("H1 recovery clearances cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> _H1RecoveryClearance:
        del memo
        raise TypeError("H1 recovery clearances cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 recovery clearances cannot be serialized")


class _H1LiveCompletionIssuance:
    """Opaque B-to-authority source; only enrollment may retain its identity.

    There deliberately is no producer yet.  The future B producer must create
    one only after the four retained owner exchanges and P's final fence, then
    retain its exact batch/root material in the enrollment table.  This marker
    itself never carries public-shaped issuance, batch, closure, or binding
    data.
    """

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("H1 live completion issuances are enrollment-issued")

    def __copy__(self) -> _H1LiveCompletionIssuance:
        raise TypeError("H1 live completion issuances cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> _H1LiveCompletionIssuance:
        del memo
        raise TypeError("H1 live completion issuances cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 live completion issuances cannot be serialized")


@dataclass
class _EnrollmentRecord:
    session: H1CompletionPreparationSession
    handle: _H1LiveCompletionHandle
    cut: H1CompletionSessionCut | None = None


@dataclass
class _ClearanceRecord:
    enrollment: _EnrollmentRecord
    sent: PublicPortCall
    terminal_call_fingerprint: str
    owner_frame_bytes: bytes
    scope_snapshot: object
    preterminal_wire: object
    state: Literal["RESERVED", "ADMITTED", "BURNED"] = "RESERVED"


@dataclass
class _RecoveryEnrollmentRecord:
    """One B session bound to the coordinator's mounted recovery source."""

    session: H1CompletionPreparationSession
    source: object
    context: object
    lease: object
    state: Literal["ACTIVE", "REVOKED"] = "ACTIVE"


@dataclass
class _RecoveryClearanceRecord:
    enrollment: _RecoveryEnrollmentRecord
    stage: Literal["COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK"]
    semantic_input: bytes
    semantic_digest: str
    sent: PublicPortCall
    call_fingerprint: str
    owner_frame_bytes: bytes
    state: Literal["RESERVED", "ADMITTED", "RECORDED", "BURNED"] = "RESERVED"
    exchange: object | None = None


@dataclass
class _IssuanceRecord:
    """Future one-use B source, retained only behind the mounted gate.

    This table is intentionally unpopulated until B and authority agree on an
    independently authenticated payload contract.  Keeping the transition
    shape here prevents a future caller from substituting a DTO or rebuilding
    source material outside enrollment.
    """

    enrollment: _EnrollmentRecord
    source: _H1LiveCompletionIssuance
    authority: H1LivePublicationAuthority
    state: Literal["RESERVED", "ISSUED", "CONSUMED", "BURNED"] = "RESERVED"


class _H1LiveCompletionEnrollment:
    """The sole installed factory and identity table for live H1 B sessions."""

    def __init__(
        self,
        *,
        runtime: object,
        first_path_sources: H1FirstPathSources,
        native_sources: H1NativeMemberSources,
        scope_port: object,
        conversation_sources: object,
        completion_registry: object,
        authority: H1LivePublicationAuthority,
        publication_mount: object,
        gate: AuthorityGate,
    ) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
        from chiplog.composition.h1_completion_exchange_registry import H1CompletionExchangeRegistry
        from chiplog.composition.h1_conversation_sources import H1ConversationSources
        from chiplog.composition.h1_runtime_preissuance_port import _H1RuntimePreissuancePort

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 live enrollment requires the canonical installed runtime")
        if (
            type(first_path_sources) is not H1FirstPathSources
            or first_path_sources._runtime is not runtime
            or type(native_sources) is not H1NativeMemberSources
            or native_sources._runtime is not runtime
            or type(scope_port) is not _H1RuntimePreissuancePort
            or type(conversation_sources) is not H1ConversationSources
            or conversation_sources._runtime is not runtime
            or type(completion_registry) is not H1CompletionExchangeRegistry
            or completion_registry._runtime is not runtime
            or type(authority) is not H1LivePublicationAuthority
            or gate is not runtime._authority_gate()
        ):
            raise TypeError("H1 live enrollment dependencies are not one mounted identity set")
        if (
            getattr(runtime, "_h1_first_path_sources", None) is not first_path_sources
            or getattr(runtime, "_h1_native_member_sources", None) is not native_sources
            or getattr(runtime, "_h1_preissuance_registration_source_port", None) is not scope_port
            or getattr(runtime, "_h1_conversation_source_port", None) is not conversation_sources
            or getattr(runtime, "_h1_completion_exchange_registry", None) is not completion_registry
            or getattr(runtime, "_h1_live_publication_authority", None) is not authority
            or getattr(runtime, "_h1_live_completion_mount", None) is not publication_mount
            or getattr(publication_mount, "_runtime", None) is not runtime
            or getattr(publication_mount, "_authority", None) is not authority
        ):
            raise ValueError("H1 live enrollment installation identities differ")
        self._runtime = runtime
        self._first_path_sources = first_path_sources
        self._native_sources = native_sources
        self._scope_port = scope_port
        self._conversation_sources = conversation_sources
        self._completion_registry = completion_registry
        self._authority = authority
        self._publication_mount = publication_mount
        self._gate = gate
        self._records: dict[int, _EnrollmentRecord] = {}
        self._clearances: dict[int, _ClearanceRecord] = {}
        self._recovery_records: dict[int, _RecoveryEnrollmentRecord] = {}
        self._recovery_clearances: dict[int, _RecoveryClearanceRecord] = {}
        # B has no real mint contract yet.  In particular, this is not a
        # generic capability registry: no public object can add an entry.
        self._issuances: dict[int, _IssuanceRecord] = {}
        self._closed = False

    def _open_session(self) -> H1CompletionPreparationSession:
        """Construct and identity-enroll B; caller-created B objects never enter here."""
        with self._gate.hold():
            self._require_open_and_mounted()
            session = H1CompletionPreparationSession(first_path_sources=self._first_path_sources)
            self._records[id(session)] = _EnrollmentRecord(
                session, object.__new__(_H1LiveCompletionHandle)
            )
            return session

    def _require_live(self, session: object, cut: object) -> None:
        """Bind B's post-capture cut and revalidate it through the mounted owners."""
        with self._gate.hold():
            record = self._require_record(session)
            if type(session) is not H1CompletionPreparationSession:
                raise H1LiveCompletionEnrollmentUnavailable("H1 completion session is foreign")
            enrolled_session = session
            if type(cut) is not H1CompletionSessionCut or enrolled_session._cut is not cut:
                raise H1LiveCompletionEnrollmentUnavailable("H1 live completion cut is foreign")
            if record.cut is None:
                record.cut = cut
            elif record.cut is not cut:
                raise H1LiveCompletionEnrollmentUnavailable("H1 live completion cut changed")
            if enrolled_session._sources is not self._first_path_sources:
                raise H1LiveCompletionEnrollmentUnavailable("H1 session source owner differs")
            if enrolled_session.check_current(cut) is not True:
                raise H1LiveCompletionEnrollmentUnavailable("H1 session source cut is stale")
            # Nested source checks may re-enter this RLock and revoke the
            # owner.  Recheck all identity and state facts before returning.
            if self._require_record(session) is not record or record.cut is not cut:
                raise H1LiveCompletionEnrollmentUnavailable("H1 enrollment changed during replay")

    def _open_recovery_session(
        self,
        *,
        source: H1RecoveryStageSource,
        context: object,
        lease: _H1RecoveryExecutionLease,
    ) -> H1CompletionPreparationSession:
        """Create one B session for the exact coordinator-owned recovery source."""
        with self._gate.hold():
            self._require_recovery_source_context_lease(source=source, context=context, lease=lease)
            if any(record.state == "ACTIVE" for record in self._recovery_records.values()):
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 recovery session is already registered"
                )
            session = H1CompletionPreparationSession(first_path_sources=self._first_path_sources)
            self._records[id(session)] = _EnrollmentRecord(
                session, object.__new__(_H1LiveCompletionHandle)
            )
            self._recovery_records[id(session)] = _RecoveryEnrollmentRecord(
                session, source, context, lease
            )
            return session

    def _require_recovery_session(
        self,
        *,
        session: object,
        source: H1RecoveryStageSource,
        context: object,
        lease: _H1RecoveryExecutionLease,
    ) -> None:
        """Require the exact source/context/lease/session recovery enrollment."""
        with self._gate.hold():
            record = self._require_recovery_record(session)
            if (
                record.source is not source
                or record.context is not context
                or record.lease is not lease
            ):
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 recovery source, context, or lease is not registered"
                )
            self._require_recovery_source_context_lease(source=source, context=context, lease=lease)
            if self._require_recovery_record(session) is not record:
                raise H1LiveCompletionEnrollmentUnavailable("H1 recovery session was revoked")

    def _reserve_recovery_clearance(
        self,
        *,
        session: object,
        stage: Literal["COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK"],
        semantic_input: bytes,
        sent: PublicPortCall,
    ) -> _H1RecoveryClearance:
        """Pin one canonical recovery request to its exact fresh broker frame."""
        with self._gate.hold():
            enrollment = self._require_recovery_record(session)
            self._require_recovery_record_current(enrollment)
            if (
                stage not in ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")
                or not isinstance(semantic_input, bytes)
                or not semantic_input
                or type(sent) is not PublicPortCall
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 recovery clearance inputs differ")
            if any(
                item.enrollment is enrollment and item.state != "BURNED"
                for item in self._recovery_clearances.values()
            ):
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 recovery session already has a clearance"
                )
            call_fingerprint, owner_frame_bytes = self._recovery_call_identity(sent)
            clearance = object.__new__(_H1RecoveryClearance)
            self._recovery_clearances[id(clearance)] = _RecoveryClearanceRecord(
                enrollment,
                stage,
                semantic_input,
                hashlib.sha256(b"chiplog.h1.recovery.semantic.v1\x00" + semantic_input).hexdigest(),
                sent,
                call_fingerprint,
                owner_frame_bytes,
            )
            return clearance

    def _recovery_admission_guard(
        self, clearance: _H1RecoveryClearance
    ) -> Callable[[PublicPortCall, bytes], None]:
        with self._gate.hold():
            self._require_recovery_clearance(clearance)

        def guard(sent: PublicPortCall, owner_frame_bytes: bytes) -> None:
            self._consume_recovery_clearance(
                clearance, sent=sent, owner_frame_bytes=owner_frame_bytes
            )

        return guard

    def _require_admitted_recovery(
        self, clearance: _H1RecoveryClearance, *, session: object, sent: PublicPortCall
    ) -> None:
        with self._gate.hold():
            record = self._require_recovery_clearance(clearance)
            if (
                record.state != "ADMITTED"
                or session is not record.enrollment.session
                or sent is not record.sent
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 recovery clearance is not admitted")
            self._require_recovery_record_current(record.enrollment)
            if (
                self._require_recovery_clearance(clearance) is not record
                or record.state != "ADMITTED"
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 recovery clearance was revoked")

    def _record_recovery_result(
        self, clearance: _H1RecoveryClearance, *, session: object, exchange: object
    ) -> None:
        """Retain only the actual exchange paired with the admitted frame."""
        from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1

        with self._gate.hold():
            record = self._require_recovery_clearance(clearance)
            self._require_admitted_recovery(clearance, session=session, sent=record.sent)
            if (
                type(exchange) is not H1CompletionOwnerExchangeV1
                or exchange.sent is not record.sent
            ):
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 recovery exchange differs from clearance"
                )
            if (
                self._require_recovery_clearance(clearance) is not record
                or record.state != "ADMITTED"
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 recovery clearance was revoked")
            record.exchange = exchange
            record.state = "RECORDED"

    def _burn_recovery_clearance(self, clearance: _H1RecoveryClearance) -> None:
        with self._gate.hold():
            record = self._require_recovery_clearance(clearance)
            record.state = "BURNED"

    def _revoke_recovery_session(self, session: object) -> None:
        with self._gate.hold():
            record = self._require_recovery_record(session)
            record.state = "REVOKED"
            for clearance in self._recovery_clearances.values():
                if clearance.enrollment is record:
                    clearance.state = "BURNED"

    def _consume_recovery_clearance(
        self, clearance: _H1RecoveryClearance, *, sent: PublicPortCall, owner_frame_bytes: bytes
    ) -> None:
        with self._gate.hold():
            record = self._require_recovery_clearance(clearance)
            if record.state != "RESERVED" or sent is not record.sent:
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 recovery clearance is one-use or not exact"
                )
            fingerprint, expected_frame = self._recovery_call_identity(sent)
            if (
                fingerprint != record.call_fingerprint
                or expected_frame != record.owner_frame_bytes
                or owner_frame_bytes != record.owner_frame_bytes
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 recovery call frame differs")
            try:
                record.enrollment.lease._require_admission_current()
                record.enrollment.source._require_current(record.enrollment.context)
            except (RuntimeError, TypeError, ValueError) as error:
                record.state = "BURNED"
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 recovery source or lease is no longer current"
                ) from error
            if (
                self._require_recovery_clearance(clearance) is not record
                or record.state != "RESERVED"
                or record.enrollment.state != "ACTIVE"
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 recovery clearance was revoked")
            record.state = "ADMITTED"

    def _reserve_terminal_clearance(
        self,
        *,
        session: object,
        cut: object,
        sent: PublicPortCall,
        scope_snapshot: object,
        preterminal_wire: object,
    ) -> _H1TerminalClearance:
        with self._gate.hold():
            # The full B-source replay already established this record's cut
            # before P issued its one-shot proof.  Do not repeat that replay
            # after the terminal frame exists: broker admission holds this
            # same gate through send and performs both B and P currentness
            # replays before it can mark the clearance admitted.
            record = self._require_bound_enrollment_cut(session, cut)
            if (
                type(sent) is not PublicPortCall
                or scope_snapshot is None
                or preterminal_wire is None
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 terminal clearance inputs differ")
            if any(
                item.enrollment is record and item.state != "BURNED"
                for item in self._clearances.values()
            ):
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 session already has terminal clearance"
                )
            from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity

            fingerprint, owner_frame_bytes = _terminal_call_identity(sent)
            clearance = object.__new__(_H1TerminalClearance)
            self._clearances[id(clearance)] = _ClearanceRecord(
                record, sent, fingerprint, owner_frame_bytes, scope_snapshot, preterminal_wire
            )
            return clearance

    def _terminal_admission_guard(
        self, clearance: object
    ) -> Callable[[PublicPortCall, bytes], None]:
        with self._gate.hold():
            self._require_clearance(clearance)

        def guard(sent: PublicPortCall, owner_frame_bytes: bytes) -> None:
            self._consume_terminal_clearance(
                clearance, sent=sent, owner_frame_bytes=owner_frame_bytes
            )

        return guard

    def _consume_terminal_clearance(
        self, clearance: object, *, sent: PublicPortCall, owner_frame_bytes: bytes
    ) -> None:
        with self._gate.hold():
            record = self._require_clearance(clearance)
            if record.state != "RESERVED" or sent is not record.sent:
                raise H1LiveCompletionEnrollmentUnavailable("H1 terminal clearance is not exact")
            from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity

            fingerprint, expected_frame = _terminal_call_identity(sent)
            if (
                fingerprint != record.terminal_call_fingerprint
                or expected_frame != record.owner_frame_bytes
                or owner_frame_bytes != record.owner_frame_bytes
            ):
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 terminal call changed after clearance"
                )
            self._require_live(record.enrollment.session, record.enrollment.cut)
            recheck = getattr(self._scope_port, "_check_terminal_clearance_current", None)
            if not callable(recheck):
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 terminal P clearance is unavailable"
                )
            recheck(clearance, sent)
            if self._require_clearance(clearance) is not record or record.state != "RESERVED":
                raise H1LiveCompletionEnrollmentUnavailable("H1 terminal clearance was revoked")
            record.state = "ADMITTED"

    def _burn_terminal_clearance(self, clearance: object) -> None:
        with self._gate.hold():
            record = self._require_clearance(clearance)
            record.state = "BURNED"

    def _require_admitted_terminal(
        self, clearance: object, *, session: object, sent: PublicPortCall
    ) -> None:
        with self._gate.hold():
            record = self._require_clearance(clearance)
            if (
                record.state != "ADMITTED"
                or session is not record.enrollment.session
                or sent is not record.sent
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 terminal clearance is not admitted")
            self._require_live(session, record.enrollment.cut)
            if self._require_clearance(clearance) is not record or record.state != "ADMITTED":
                raise H1LiveCompletionEnrollmentUnavailable("H1 terminal clearance was revoked")

    def _revoke_all(self) -> None:
        with self._gate.hold():
            if self._closed:
                return
            for record in self._clearances.values():
                record.state = "BURNED"
            for record in self._recovery_clearances.values():
                record.state = "BURNED"
            for record in self._recovery_records.values():
                record.state = "REVOKED"
            for issuance in self._issuances.values():
                issuance.state = "BURNED"
            self._clearances.clear()
            self._recovery_clearances.clear()
            self._recovery_records.clear()
            self._issuances.clear()
            self._records.clear()
            self._closed = True

    def _consume_authority_issuance(self, *, authority: object, source: object) -> None:
        """Freeze the only future authority-private admission boundary.

        A future authority calls this with its installed identity and the B
        source marker only.  It must receive retained exact material solely
        from the consumed record.  Until B supplies the four-exchange/final-P
        producer and authority supplies independent invocation/manifest/root
        capture, this boundary always denies before any authority, coordinator,
        or journal operation.
        """
        with self._gate.hold():
            self._require_open_and_mounted()
            if (
                authority is not self._authority
                or type(authority) is not H1LivePublicationAuthority
            ):
                raise H1LiveCompletionEnrollmentUnavailable("H1 live issuance authority is foreign")
            if getattr(self._authority, "_revoked", True):
                raise H1LiveCompletionEnrollmentUnavailable("H1 live issuance authority is revoked")
            record = self._issuances.get(id(source))
            if (
                type(source) is not _H1LiveCompletionIssuance
                or record is None
                or record.source is not source
            ):
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 live issuance source is not enrollment-issued"
                )
            if record.authority is not authority or record.state != "ISSUED":
                raise H1LiveCompletionEnrollmentUnavailable(
                    "H1 live issuance source is unavailable"
                )
            try:
                enrolled = self._require_bound_enrollment_cut(
                    record.enrollment.session, record.enrollment.cut
                )
                if (
                    enrolled is not record.enrollment
                    or enrolled.handle is not record.enrollment.handle
                ):
                    raise H1LiveCompletionEnrollmentUnavailable(
                        "H1 live issuance enrollment identity differs"
                    )
                self._require_live(record.enrollment.session, record.enrollment.cut)
            except BaseException:
                # A stale/revoked source is ambiguous for a one-use handoff.
                # Burn it before it can be observed by any later authority.
                record.state = "BURNED"
                raise
            # A real B producer will atomically change RESERVED to ISSUED
            # only after the exact four frames and P final fence, retaining
            # independently captured batch/root inputs here.  No such payload
            # contract exists today, so fail closed and burn before any
            # potentially reentrant root or authority operation.
            record.state = "BURNED"
            raise H1LiveCompletionEnrollmentUnavailable(
                "H1 live issuance payload contract is not mounted"
            )

    def _require_open_and_mounted(self) -> None:
        if self._closed:
            raise H1LiveCompletionEnrollmentUnavailable("H1 live enrollment is closed")
        runtime = self._runtime
        if (
            getattr(runtime, "_h1_live_completion_enrollment", None) is not self
            or getattr(runtime, "_h1_first_path_sources", None) is not self._first_path_sources
            or getattr(runtime, "_h1_native_member_sources", None) is not self._native_sources
            or getattr(runtime, "_h1_preissuance_registration_source_port", None)
            is not self._scope_port
            or getattr(runtime, "_h1_conversation_source_port", None)
            is not self._conversation_sources
            or getattr(runtime, "_h1_completion_exchange_registry", None)
            is not self._completion_registry
            or getattr(runtime, "_h1_live_publication_authority", None) is not self._authority
            or getattr(runtime, "_h1_live_completion_mount", None) is not self._publication_mount
        ):
            raise H1LiveCompletionEnrollmentUnavailable("H1 live enrollment mount differs")

    def _require_record(self, session: object) -> _EnrollmentRecord:
        self._require_open_and_mounted()
        record = self._records.get(id(session))
        if record is None or record.session is not session:
            raise H1LiveCompletionEnrollmentUnavailable("H1 completion session is not enrolled")
        return record

    def _require_bound_enrollment_cut(self, session: object, cut: object) -> _EnrollmentRecord:
        """Accept only a cut that an earlier full live replay pinned."""
        record = self._require_record(session)
        if type(session) is not H1CompletionPreparationSession:
            raise H1LiveCompletionEnrollmentUnavailable("H1 completion session is foreign")
        if type(cut) is not H1CompletionSessionCut or session._cut is not cut:
            raise H1LiveCompletionEnrollmentUnavailable("H1 live completion cut is foreign")
        if record.cut is not cut:
            raise H1LiveCompletionEnrollmentUnavailable("H1 live completion cut was not validated")
        if session._sources is not self._first_path_sources:
            raise H1LiveCompletionEnrollmentUnavailable("H1 session source owner differs")
        return record

    def _require_recovery_source_context_lease(
        self, *, source: object, context: object, lease: object
    ) -> None:
        """Revalidate the installed source and task-held lease before B use."""
        from chiplog.composition.h1_postseal_recovery_coordinator import (
            _H1PostSealRecoveryCoordinator,
        )
        from chiplog.composition.h1_recovery_execution_fence import _H1RecoveryExecutionLease
        from chiplog.composition.h1_recovery_stage_source import H1RecoveryStageSource

        runtime = self._runtime
        coordinator = getattr(runtime, "_h1_postseal_recovery_coordinator", None)
        if (
            type(source) is not H1RecoveryStageSource
            or source._runtime is not runtime
            or type(coordinator) is not _H1PostSealRecoveryCoordinator
            or coordinator._runtime is not runtime
            or getattr(runtime, "_h1_postseal_recovery_coordinator", None) is not coordinator
            or source is not getattr(coordinator, "_source", None)
            or type(lease) is not _H1RecoveryExecutionLease
            or lease._fence is not coordinator._fence
        ):
            raise H1LiveCompletionEnrollmentUnavailable(
                "H1 recovery source or lease is not installed"
            )
        try:
            source._context_state(context)
            lease.require_owned()
            source._require_current(context)
        except (RuntimeError, TypeError, ValueError) as error:
            raise H1LiveCompletionEnrollmentUnavailable(
                "H1 recovery context or lease is not current"
            ) from error

    def _require_recovery_record(self, session: object) -> _RecoveryEnrollmentRecord:
        self._require_open_and_mounted()
        record = self._recovery_records.get(id(session))
        if (
            type(session) is not H1CompletionPreparationSession
            or record is None
            or record.session is not session
            or record.state != "ACTIVE"
            or self._records.get(id(session)) is None
            or self._records[id(session)].session is not session
        ):
            raise H1LiveCompletionEnrollmentUnavailable("H1 recovery session is not registered")
        return record

    def _require_recovery_record_current(self, record: _RecoveryEnrollmentRecord) -> None:
        self._require_recovery_source_context_lease(
            source=record.source, context=record.context, lease=record.lease
        )
        if record.state != "ACTIVE":
            raise H1LiveCompletionEnrollmentUnavailable("H1 recovery session was revoked")

    @staticmethod
    def _recovery_call_identity(sent: PublicPortCall) -> tuple[str, bytes]:
        """Use the complete public-call preimage retained by terminal admission."""
        from chiplog.composition.h1_terminal_call_identity import _terminal_call_identity

        return _terminal_call_identity(sent)

    def _require_recovery_clearance(self, clearance: object) -> _RecoveryClearanceRecord:
        self._require_open_and_mounted()
        record = self._recovery_clearances.get(id(clearance))
        if (
            type(clearance) is not _H1RecoveryClearance
            or record is None
            or record.state == "BURNED"
        ):
            raise H1LiveCompletionEnrollmentUnavailable("H1 recovery clearance is not enrolled")
        return record

    def _require_clearance(self, clearance: object) -> _ClearanceRecord:
        self._require_open_and_mounted()
        record = self._clearances.get(id(clearance))
        if record is None or type(clearance) is not _H1TerminalClearance:
            raise H1LiveCompletionEnrollmentUnavailable("H1 terminal clearance is not enrolled")
        return record


__all__ = [
    "H1LiveCompletionEnrollmentUnavailable",
    "_H1LiveCompletionEnrollment",
    "_H1LiveCompletionHandle",
    "_H1LiveCompletionIssuance",
    "_H1RecoveryClearance",
    "_H1TerminalClearance",
]
