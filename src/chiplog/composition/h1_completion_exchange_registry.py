"""Private retained evidence for one completed B first-path owner exchange.

This is deliberately a past-stage registry.  It does not issue a request,
perform owner IPC, or establish a current policy decision.  Its only writer is
the B session after that session has retained a canonical successful completion
reply and its preflight has retained the native and P scope capabilities.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
    first_path_completion_request_fingerprint,
)
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_first_path_sources import H1FirstPathCapture
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


@dataclass(frozen=True, slots=True)
class _CompletionExchangeRecord:
    """Strong identity-held record; its fields are not a public authority DTO."""

    session: object
    first_path: H1FirstPathCapture
    exchange: H1CompletionOwnerExchangeV1
    native_cap: H1CurrentNativeMemberSourceCut
    scope_cap: object


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

    __slots__ = ("_closed", "_native_sources", "_records", "_runtime", "_scope_port")

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
        self._closed = False

    def _revoke_all(self) -> None:
        """Make this runtime-bound past-stage table permanently unusable."""
        self._records.clear()
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
        request = getattr(preflight, "request", None)
        if (
            type(first_path) is not H1FirstPathCapture
            or getattr(preflight, "first_path", None) is not first_path
            or type(native_cap) is not H1CurrentNativeMemberSourceCut
            or type(request) is not PrepareExecutionCompletionFirstPathV2
            or exchange is None
        ):
            raise H1CompletionExchangeRegistryViolation("H1 completion registry binding is absent")
        if native_cap._capture is not first_path or request.source != first_path.source:
            raise H1CompletionExchangeRegistryViolation("H1 completion registry source differs")
        self._require_success(exchange, request)
        # Both concrete owners retain their own private identity tables.  The
        # return values are intentionally discarded: replay is the authority
        # check, while this registry only retains the exact capabilities.
        self._native_sources.replay_current(native_cap)
        self._scope_port._replay_completion_scope(scope_cap, native_cap)
        if any(record.session is session for record in self._records):
            raise H1CompletionExchangeRegistryViolation(
                "H1 completion registry session is duplicate"
            )
        self._records.append(
            _CompletionExchangeRecord(session, first_path, exchange, native_cap, scope_cap)
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
]
