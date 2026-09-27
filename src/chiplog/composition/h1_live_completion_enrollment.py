"""Private, runtime-owned enrollment for future live H1 completion minting.

This owner establishes provenance only.  It does not issue a publication
capability or make the mounted writer authority permissive.
"""

from __future__ import annotations

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
            for issuance in self._issuances.values():
                issuance.state = "BURNED"
            self._clearances.clear()
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
    "_H1TerminalClearance",
]
