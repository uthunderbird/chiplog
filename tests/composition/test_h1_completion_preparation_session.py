"""Diagnostic guards for the H1 live completion session boundary."""

from __future__ import annotations

import asyncio
import base64
import secrets
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chiplog.capabilities.agent_loop import _execution_completion_process as completion_process
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import WorkPreparationRejected
from chiplog.capabilities.agent_loop.terminal_work_preparation import prepare_h1_terminal_work
from chiplog.capabilities.projections import (
    _conversation_completion_process as conversation_process,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    PrepareConversationCompletionV1,
    PreparedConversationCompletionV1,
)
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_completion_preparation_session import (
    H1CompletionPreparationSession,
    H1CompletionPreparationUnavailable,
    H1CompletionSessionCut,
    _H1CompletionPreflight,
)
from chiplog.composition.h1_conversation_sources import H1ConversationSources
from chiplog.composition.h1_first_path_sources import H1FirstPathCapture, H1FirstPathSources
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortFailure,
    PublicPortRejected,
    PublicPortResult,
    PublicPortSuccess,
)
from tests.capabilities.agent_loop.test_execution_first_path_completion_contracts import (
    request as real_first_path_request,
)
from tests.capabilities.projections.test_conversation_completion_owner import (
    _request as real_conversation_request,
)
from tests.support.completion_assembly import accepted_completion_fixture


@dataclass
class _Conversation:
    current: bool = True

    def capture_current(self, *, first_path: object, completion_exchange: object) -> object:
        assert first_path is _FIRST_PATH and completion_exchange is _EXCHANGE
        return self

    def check_current(self, capture: object) -> bool:
        return capture is self and self.current


class _Gate:
    def hold(self) -> _Gate:
        return self

    def __enter__(self) -> _Gate:
        return self

    def __exit__(self, *args: object) -> None:
        return None


_FIRST_PATH = object()
_EXCHANGE = object()


def _sources(*, current: bool = True) -> H1FirstPathSources:
    source: Any = object.__new__(H1FirstPathSources)
    source._gate = _Gate()
    runtime = SimpleNamespace()
    conversation = _Conversation(current=current)
    owner: Any = object.__new__(H1ConversationSources)
    owner._runtime = runtime
    owner._gate = source._gate
    owner.capture_current = lambda **_: conversation
    owner.check_current = lambda capture: capture is conversation and conversation.current
    runtime._h1_conversation_source_port = owner
    source._runtime = runtime
    source.capture_current = lambda **_: _FIRST_PATH
    source.check_current = lambda captured: captured is _FIRST_PATH and current
    return cast(H1FirstPathSources, source)


@pytest.mark.parametrize("defect", ("absent", "runtime", "gate"))
def test_session_refuses_an_absent_or_foreign_mounted_a_owner(defect: str) -> None:
    source = _sources()
    runtime = source._runtime
    owner = runtime._h1_conversation_source_port
    if defect == "absent":
        del runtime._h1_conversation_source_port
    elif defect == "runtime":
        owner._runtime = object()
    else:
        owner._gate = _Gate()

    with pytest.raises(H1CompletionPreparationUnavailable, match="exact mounted A"):
        H1CompletionPreparationSession(first_path_sources=source)


class _CompletionEngine:
    def __init__(
        self, *, callee: BrokerSession, returned: PublicPortSuccess, block_call: bool = False
    ) -> None:
        self._callee = callee
        self._returned = returned
        self._block_call = block_call
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.calls: list[PublicPortCall] = []

    def session(self, owner: str) -> BrokerSession:
        assert owner == "agent_loop"
        return self._callee

    async def call(self, sent: PublicPortCall) -> PublicPortSuccess:
        self.calls.append(sent)
        self.entered.set()
        if self._block_call and len(self.calls) == 1:
            await self.release.wait()
        return self._returned


class _ConversationEngine:
    """Fake transport which dispatches the installed projections process."""

    def __init__(self, callee: BrokerSession) -> None:
        self._callee = callee
        self.calls: list[PublicPortCall] = []
        self.returned: PublicPortResult | None = None

    def session(self, owner: str) -> BrokerSession:
        assert owner == "projections"
        return self._callee

    async def call(self, sent: PublicPortCall) -> PublicPortResult:
        self.calls.append(sent)
        reply = conversation_process.dispatch(sent.operation_id, sent.canonical_payload)
        assert "payload" in reply
        self.returned = PublicPortSuccess(
            request_id=sent.request_id,
            responder=self._callee,
            schema_id=cast(str, reply["schema_id"]),
            canonical_payload=base64.b64decode(cast(str, reply["payload"])),
        )
        return self.returned


async def _captured_conversation_session() -> tuple[
    H1CompletionPreparationSession,
    _ConversationEngine,
    H1CompletionSessionCut,
    PrepareConversationCompletionV1,
]:
    """Create an A-issued capture and run the installed projections owner behind a broker."""
    request = await real_conversation_request()
    source = _sources()
    runtime = source._runtime
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="projections",
        session_id="projections-session",
    )
    engine = _ConversationEngine(callee)
    cast(Any, runtime)._supervisor = SimpleNamespace(runtime=lambda: engine)
    cast(Any, runtime)._tenant_id = "tenant"
    owner = cast(Any, runtime._h1_conversation_source_port)
    owner._prepare_conversation_completion_request = lambda capture: request
    session = H1CompletionPreparationSession(first_path_sources=source)
    initial = H1CompletionSessionCut(
        original_identity=cast("DriverCommandIdentityV1", object()),
        original_fingerprint="0" * 64,
        selected_seal=cast("CallSubjectHead", object()),
        first_path=cast("H1FirstPathCapture", _FIRST_PATH),
        conversation=None,
    )
    session._cut = initial
    session._completion_exchange = cast("H1CompletionOwnerExchangeV1", _EXCHANGE)
    cut = session.capture_current(
        original_identity=initial.original_identity,
        original_fingerprint=initial.original_fingerprint,
        selected_seal=initial.selected_seal,
        completion_exchange=cast("H1CompletionOwnerExchangeV1", _EXCHANGE),
    )
    return session, engine, cut, request


@pytest.mark.asyncio
async def test_session_calls_installed_projections_route_from_its_mounted_a_capture() -> None:
    session, engine, cut, request = await _captured_conversation_session()

    exchange = await session.prepare_conversation_completion()

    assert len(engine.calls) == 1
    assert exchange.role == "conversation"
    assert exchange.sent is engine.calls[0]
    assert exchange.returned is engine.returned
    assert exchange.returned.request_id == exchange.sent.request_id
    assert exchange.sent.operation_id == "projections.prepare_conversation_completion"
    assert exchange.sent.schema_id == request.schema_id
    assert exchange.sent.canonical_payload == request.canonical_json_bytes()
    assert exchange.sent.callee.owner_id == "projections"
    assert (
        exchange.sent_at_ns <= exchange.returned_at_ns < exchange.sent.budget.absolute_deadline_ns
    )
    assert session._cut is cut


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ("request_id", "responder", "schema", "rejected"))
async def test_session_rejects_same_byte_substituted_or_rejected_conversation_reply(
    defect: str,
) -> None:
    session, engine, _cut, _request = await _captured_conversation_session()
    call = engine.call

    async def substituted(sent: PublicPortCall) -> PublicPortResult:
        returned = await call(sent)
        assert isinstance(returned, PublicPortSuccess)
        if defect == "request_id":
            return returned.model_copy(update={"request_id": "concurrent-same-bytes"})
        if defect == "responder":
            return returned.model_copy(
                update={
                    "responder": returned.responder.model_copy(
                        update={"session_id": "other-projections-session"}
                    )
                }
            )
        if defect == "schema":
            return returned.model_copy(update={"schema_id": "chiplog.conversation.old-result.v1"})
        return PublicPortRejected(
            request_id=sent.request_id,
            responder=sent.callee,
            failure=PublicPortFailure(kind="PROTOCOL_REJECTED", reason="denied"),
        )

    engine.call = substituted  # type: ignore[method-assign]
    with pytest.raises(H1CompletionPreparationUnavailable, match="exact success"):
        await session.prepare_conversation_completion()
    assert len(engine.calls) == 1
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_conversation_completion()


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ("noncanonical", "fingerprint"))
async def test_session_rejects_noncanonical_or_wrong_fingerprint_conversation_result(
    defect: str,
) -> None:
    session, engine, _cut, _request = await _captured_conversation_session()
    call = engine.call

    async def substituted(sent: PublicPortCall) -> PublicPortResult:
        returned = await call(sent)
        assert isinstance(returned, PublicPortSuccess)
        if defect == "noncanonical":
            return returned.model_copy(
                update={"canonical_payload": b"\n" + returned.canonical_payload}
            )
        result = PreparedConversationCompletionV1.model_validate_json(returned.canonical_payload)
        return returned.model_copy(
            update={
                "canonical_payload": result.model_copy(
                    update={"source_request_fingerprint": "0" * 64}
                ).canonical_json_bytes()
            }
        )

    engine.call = substituted  # type: ignore[method-assign]
    with pytest.raises(H1CompletionPreparationUnavailable):
        await session.prepare_conversation_completion()
    assert len(engine.calls) == 1
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_conversation_completion()


@pytest.mark.asyncio
async def test_session_consumes_stale_or_cancelled_conversation_attempt_without_retry() -> None:
    session, engine, cut, _request = await _captured_conversation_session()
    assert isinstance(cut.conversation, _Conversation)
    cut.conversation.current = False
    with pytest.raises(H1CompletionPreparationUnavailable, match="stale"):
        await session.prepare_conversation_completion()
    assert engine.calls == []
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_conversation_completion()


@pytest.mark.asyncio
async def test_session_consumes_effects_attempt_when_the_installed_p_source_is_absent() -> None:
    """Effects has the same pre-replay one-use boundary as conversation."""
    session, engine, cut, _conversation_request = await _captured_conversation_session()
    await session.prepare_conversation_completion()
    request = await real_first_path_request(canonical_response=True)
    reply = completion_process.dispatch(
        completion_process.FIRST_PATH_OPERATION, request.canonical_bytes()
    )
    raw = base64.b64decode(cast(str, reply["payload"]))
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="agent_loop",
        session_id="agent-loop-session",
    )
    sent = PublicPortCall(
        operation_id="agent_loop.prepare_first_path_completion",
        request_id="h1-completion:test",
        caller=BrokerSession(
            tenant_id="tenant",
            broker_epoch=1,
            generation_id="generation",
            owner_id="broker",
            session_id="broker:generation",
        ),
        callee=callee,
        schema_id=request.schema_id,
        canonical_payload=request.canonical_bytes(),
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=1,
            absolute_deadline_ns=5_000_000_000,
        ),
    )
    session._completion_exchange = H1CompletionOwnerExchangeV1(
        role="completion",
        sent=sent,
        returned=PublicPortSuccess(
            request_id=sent.request_id,
            responder=callee,
            schema_id="chiplog.agent-loop.prepared-execution-completion-result.v1",
            canonical_payload=raw,
        ),
        sent_at_ns=1,
        returned_at_ns=2,
    )
    session._preflight = _H1CompletionPreflight(
        first_path=cut.first_path,
        native_cap=object(),
        verified_original=object(),
        scope_cap=object(),
        delivery_receipt=object(),
        request=request,
    )

    with pytest.raises(H1CompletionPreparationUnavailable, match="installed P/native"):
        await session.prepare_local_commentary()
    assert len(engine.calls) == 1
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_local_commentary()


@pytest.mark.asyncio
async def test_session_terminal_result_requires_prepared_commitment_not_protocol_success() -> None:
    fixture = await accepted_completion_fixture("v3", "empty")
    request = fixture.assembly.terminal_work_request
    prepared = prepare_h1_terminal_work(request)
    assert not isinstance(prepared, WorkPreparationRejected)

    assert H1CompletionPreparationSession._validate_terminal_work_result(
        request, prepared.canonical_bytes()
    ) == prepared

    rejected = WorkPreparationRejected(code="DENIED", reason="h1-nonempty-obligations")
    with pytest.raises(H1CompletionPreparationUnavailable, match="result differs"):
        H1CompletionPreparationSession._validate_terminal_work_result(
            request, rejected.canonical_bytes()
        )
    with pytest.raises(H1CompletionPreparationUnavailable, match="result differs"):
        H1CompletionPreparationSession._validate_terminal_work_result(
            request, prepared.model_copy(update={"complete_commitment": "0" * 64}).canonical_bytes()
        )

    session, engine, _cut, _request = await _captured_conversation_session()
    call = engine.call
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked(sent: PublicPortCall) -> PublicPortResult:
        engine.calls.append(sent)
        entered.set()
        await release.wait()
        return await call(sent)

    engine.call = blocked  # type: ignore[method-assign]
    task = asyncio.create_task(session.prepare_conversation_completion())
    await entered.wait()
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_conversation_completion()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(engine.calls) == 1
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_conversation_completion()


@pytest.mark.asyncio
async def test_session_consumes_terminal_attempt_before_p_proof_or_terminal_dispatch() -> None:
    """A missing P proof owner burns this terminal leg before any broker call."""
    source = _sources()
    session = H1CompletionPreparationSession(first_path_sources=source)
    conversation = source._runtime._h1_conversation_source_port.capture_current()
    cut = H1CompletionSessionCut(
        original_identity=cast("DriverCommandIdentityV1", object()),
        original_fingerprint="0" * 64,
        selected_seal=cast("CallSubjectHead", object()),
        first_path=cast("H1FirstPathCapture", _FIRST_PATH),
        conversation=conversation,
    )
    session._cut = cut
    session._preflight = cast("_H1CompletionPreflight", object())
    session._completion_exchange = cast("H1CompletionOwnerExchangeV1", _EXCHANGE)
    session._conversation_exchange = cast("H1CompletionOwnerExchangeV1", _EXCHANGE)
    session._effects_exchange = cast("H1CompletionOwnerExchangeV1", _EXCHANGE)

    with pytest.raises(H1CompletionPreparationUnavailable, match="mounted P clearance owner"):
        await session.prepare_terminal_work()
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_terminal_work()
    assert session._terminal_work_exchange is None


@pytest.mark.asyncio
async def test_terminal_budget_is_exactly_bound_once_and_expiry_still_blocks_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The terminal frame gets one finite pre-bind budget and is never renewed."""
    from chiplog.composition.h1_live_completion_enrollment import (
        _H1LiveCompletionEnrollment,
        _H1TerminalClearance,
    )
    from chiplog.composition.h1_runtime_preissuance_port import _H1RuntimePreissuancePort

    fixture = await accepted_completion_fixture("v3", "empty")
    source = _sources()
    runtime = source._runtime
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="agent_loop",
        session_id="agent-loop-session",
    )
    broker_calls: list[PublicPortCall] = []

    class _TerminalEngine:
        def session(self, owner: str) -> BrokerSession:
            assert owner == "agent_loop"
            return callee

        async def _call_with_admission_guard(
            self, sent: PublicPortCall, **_: object
        ) -> PublicPortResult:
            broker_calls.append(sent)
            raise RuntimeError("stop after guarded dispatch")

    runtime._supervisor = SimpleNamespace(runtime=lambda: _TerminalEngine())
    runtime._tenant_id = "tenant"
    runtime._authority_gate = lambda: source._gate
    port: Any = object.__new__(_H1RuntimePreissuancePort)
    port._runtime = runtime
    enrollment: Any = object.__new__(_H1LiveCompletionEnrollment)
    runtime._h1_preissuance_registration_source_port = port
    runtime._h1_live_completion_enrollment = enrollment
    session = H1CompletionPreparationSession(first_path_sources=source)
    session._cut = H1CompletionSessionCut(
        original_identity=cast("DriverCommandIdentityV1", object()),
        original_fingerprint="0" * 64,
        selected_seal=cast("CallSubjectHead", object()),
        first_path=cast("H1FirstPathCapture", _FIRST_PATH),
        conversation=cast("_Conversation", object()),
    )
    session._preflight = cast("_H1CompletionPreflight", object())
    session._completion_exchange = cast("H1CompletionOwnerExchangeV1", _EXCHANGE)
    session._conversation_exchange = cast("H1CompletionOwnerExchangeV1", _EXCHANGE)
    session._effects_exchange = cast("H1CompletionOwnerExchangeV1", _EXCHANGE)
    session._prepare_terminal_work_request = lambda **_: fixture.assembly.terminal_work_request

    proof = object()
    clearance = object.__new__(_H1TerminalClearance)
    bound: list[PublicPortCall] = []
    burned: list[object] = []
    proof_sessions: list[H1CompletionPreparationSession] = [session]
    monkeypatch.setattr(
        _H1LiveCompletionEnrollment, "_require_live", lambda *_: None
    )
    monkeypatch.setattr(
        _H1LiveCompletionEnrollment,
        "_terminal_admission_guard",
        lambda *_: cast(Any, lambda *_: None),
    )
    monkeypatch.setattr(
        _H1LiveCompletionEnrollment,
        "_burn_terminal_clearance",
        lambda _, value: burned.append(value),
    )

    async def prepare_proof(_: object, actual_session: object) -> object:
        assert actual_session in proof_sessions
        return proof

    def bind(
        _: object, actual_session: object, actual_proof: object, sent: PublicPortCall
    ) -> _H1TerminalClearance:
        assert actual_session is session
        assert actual_proof is proof
        bound.append(sent)
        return clearance

    monkeypatch.setattr(_H1RuntimePreissuancePort, "_prepare_terminal_scope_proof", prepare_proof)
    monkeypatch.setattr(_H1RuntimePreissuancePort, "_bind_terminal_clearance", bind)
    clock = iter((1_000, 1_001))
    monkeypatch.setattr(
        "chiplog.composition.h1_completion_preparation_session.time.monotonic_ns",
        lambda: next(clock),
    )

    with pytest.raises(RuntimeError, match="stop after guarded dispatch"):
        await session.prepare_terminal_work()

    assert bound == broker_calls
    assert len(bound) == 1
    assert bound[0].budget.absolute_deadline_ns == 1_000 + 90_000_000_000
    assert bound[0].budget.absolute_deadline_ns - 1_000 == 90_000_000_000
    assert burned == [clearance]

    # A clearance never renews this exact frame: an already elapsed deadline
    # denies before the guarded broker route can send it.
    second = H1CompletionPreparationSession(first_path_sources=source)
    proof_sessions.append(second)
    second._cut = session._cut
    second._preflight = session._preflight
    second._completion_exchange = session._completion_exchange
    second._conversation_exchange = session._conversation_exchange
    second._effects_exchange = session._effects_exchange
    second._prepare_terminal_work_request = session._prepare_terminal_work_request
    expired_bound: list[PublicPortCall] = []

    def bind_expired(
        _: object, actual_session: object, actual_proof: object, sent: PublicPortCall
    ) -> _H1TerminalClearance:
        assert actual_session is second
        assert actual_proof is proof
        expired_bound.append(sent)
        return clearance

    monkeypatch.setattr(_H1RuntimePreissuancePort, "_bind_terminal_clearance", bind_expired)
    clock = iter((2_000, 2_000 + 90_000_000_000))
    monkeypatch.setattr(
        "chiplog.composition.h1_completion_preparation_session.time.monotonic_ns",
        lambda: next(clock),
    )
    broker_calls.clear()

    with pytest.raises(H1CompletionPreparationUnavailable, match="expired before dispatch"):
        await second.prepare_terminal_work()
    assert len(expired_bound) == 1
    assert broker_calls == []
    assert burned == [clearance, clearance]

async def _live_session(
    *,
    monkeypatch: pytest.MonkeyPatch,
    response_schema: str = "chiplog.agent-loop.prepared-execution-completion-result.v1",
    response_id: str = "h1-completion:" + "a" * 48,
    responder: BrokerSession | None = None,
    noncanonical_payload: bool = False,
    response_payload: bytes | None = None,
    block_call: bool = False,
) -> tuple[
    H1CompletionPreparationSession,
    _CompletionEngine,
    PrepareExecutionCompletionFirstPathV2,
    PublicPortSuccess,
]:
    """Bind the issuer-held builder seam to a real owner-process request."""
    request = await real_first_path_request(canonical_response=True)
    reply = completion_process.dispatch(
        completion_process.FIRST_PATH_OPERATION, request.canonical_bytes()
    )
    raw = base64.b64decode(cast(str, reply["payload"]))
    decoded = PreparedExecutionCompletion.model_validate_json(raw)
    assert decoded.canonical_bytes() == raw
    callee = BrokerSession(
        tenant_id="tenant",
        broker_epoch=1,
        generation_id="generation",
        owner_id="agent_loop",
        session_id="agent-loop-session",
    )
    returned = PublicPortSuccess(
        request_id=response_id,
        responder=callee if responder is None else responder,
        schema_id=response_schema,
        canonical_payload=(
            b"\n" + raw
            if noncanonical_payload
            else raw
            if response_payload is None
            else response_payload
        ),
    )
    engine = _CompletionEngine(callee=callee, returned=returned, block_call=block_call)
    source = _sources()
    source._runtime._supervisor = SimpleNamespace(runtime=lambda: engine)
    source._runtime._tenant_id = "tenant"
    # This private source seam stands in for the still-unmounted authenticated
    # delivery/fence builder.  It supplies a genuine, typed V2 request and is
    # never a caller argument to the session API.
    cast(Any, source)._prepare_first_path_completion_request = lambda _: request
    session = H1CompletionPreparationSession(first_path_sources=source)
    session._cut = H1CompletionSessionCut(
        original_identity=cast("DriverCommandIdentityV1", object()),
        original_fingerprint="0" * 64,
        selected_seal=cast("CallSubjectHead", object()),
        first_path=cast("H1FirstPathCapture", SimpleNamespace(source=request.source)),
        conversation=None,
    )
    return session, engine, request, returned


@pytest.mark.asyncio
async def test_session_refuses_fake_engine_wires_without_the_installed_preflight_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets, "token_hex", lambda _: "a" * 48)
    session, engine, _request, _returned = await _live_session(monkeypatch=monkeypatch)

    with pytest.raises(H1CompletionPreparationUnavailable, match="installed B/P/E"):
        await session.prepare_first_path_completion()
    assert engine.calls == []
    assert session._completion_exchange is None


@pytest.mark.asyncio
async def test_session_denies_obsolete_completion_owner_response_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets, "token_hex", lambda _: "a" * 48)
    session, _, _, _ = await _live_session(
        monkeypatch=monkeypatch,
        response_schema="chiplog.agent-loop.prepared-execution-completion.v1",
    )

    with pytest.raises(H1CompletionPreparationUnavailable, match="installed B/P/E"):
        await session.prepare_first_path_completion()
    assert session._completion_exchange is None
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_first_path_completion()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "defect", ("request_id", "responder", "noncanonical", "substituted_result")
)
async def test_session_denies_uncorrelated_or_noncanonical_completion_result(
    monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    monkeypatch.setattr(secrets, "token_hex", lambda _: "a" * 48)
    if defect == "request_id":
        session, _, _, _ = await _live_session(monkeypatch=monkeypatch, response_id="wrong-request")
    elif defect == "responder":
        session, _, _, _ = await _live_session(
            monkeypatch=monkeypatch,
            responder=BrokerSession(
                tenant_id="tenant",
                broker_epoch=1,
                generation_id="generation",
                owner_id="agent_loop",
                session_id="substituted-agent-loop-session",
            ),
        )
    else:
        if defect == "noncanonical":
            session, _, _, _ = await _live_session(
                monkeypatch=monkeypatch, noncanonical_payload=True
            )
        else:
            _, _, _, genuine = await _live_session(monkeypatch=monkeypatch)
            substituted = PreparedExecutionCompletion.model_validate_json(
                genuine.canonical_payload
            ).model_copy(update={"source_request_fingerprint": "0" * 64})
            session, _, _, _ = await _live_session(
                monkeypatch=monkeypatch, response_payload=substituted.canonical_bytes()
            )

    with pytest.raises(H1CompletionPreparationUnavailable, match="installed B/P/E"):
        await session.prepare_first_path_completion()

    assert session._completion_exchange is None


@pytest.mark.asyncio
async def test_session_is_one_use_across_sequential_and_concurrent_completion_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets, "token_hex", lambda _: "a" * 48)
    session, engine, _, _ = await _live_session(monkeypatch=monkeypatch, block_call=True)

    first = asyncio.create_task(session.prepare_first_path_completion())
    with pytest.raises(H1CompletionPreparationUnavailable, match="installed B/P/E"):
        await first
    error: H1CompletionPreparationUnavailable | None = None
    try:
        await session.prepare_first_path_completion()
    except H1CompletionPreparationUnavailable as caught:
        error = caught
    assert error is not None
    assert "already started" in str(error)
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_first_path_completion()
    assert engine.calls == []


@pytest.mark.asyncio
async def test_session_remains_consumed_when_its_owner_call_is_cancelled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(secrets, "token_hex", lambda _: "a" * 48)
    session, engine, _, _ = await _live_session(monkeypatch=monkeypatch, block_call=True)

    with pytest.raises(H1CompletionPreparationUnavailable, match="installed B/P/E"):
        await session.prepare_first_path_completion()
    with pytest.raises(H1CompletionPreparationUnavailable, match="already started"):
        await session.prepare_first_path_completion()
    assert engine.calls == []


def test_session_binds_the_exact_mounted_a_reader_and_calls_its_keyword_only_pair_api() -> None:
    source = _sources()
    session = H1CompletionPreparationSession(first_path_sources=source)
    cut = H1CompletionSessionCut(
        original_identity=cast("DriverCommandIdentityV1", object()),
        original_fingerprint="0" * 64,
        selected_seal=cast("CallSubjectHead", object()),
        first_path=cast("H1FirstPathCapture", _FIRST_PATH),
        conversation=None,
    )
    session._cut = cut
    session._completion_exchange = cast("H1CompletionOwnerExchangeV1", _EXCHANGE)

    captured = session.capture_current(
        original_identity=cut.original_identity,
        original_fingerprint=cut.original_fingerprint,
        selected_seal=cut.selected_seal,
        completion_exchange=cast("H1CompletionOwnerExchangeV1", _EXCHANGE),
    )

    assert session._conversation_sources is source._runtime._h1_conversation_source_port
    assert captured.conversation is not None


async def test_session_refuses_to_send_a_caller_owned_completion_dto_without_a_bound_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _sources()
    cast(Any, source)._prepare_first_path_completion_request = None
    session = H1CompletionPreparationSession(first_path_sources=source)
    session._cut = H1CompletionSessionCut(
        original_identity=cast("DriverCommandIdentityV1", object()),
        original_fingerprint="0" * 64,
        selected_seal=cast("CallSubjectHead", object()),
        first_path=cast("H1FirstPathCapture", _FIRST_PATH),
        conversation=None,
    )

    with pytest.raises(H1CompletionPreparationUnavailable, match="installed B/P/E"):
        await session.prepare_first_path_completion()


def test_session_rejects_copied_or_stale_source_before_terminal_work() -> None:
    source = _sources()
    session = H1CompletionPreparationSession(first_path_sources=source)
    conversation = source._runtime._h1_conversation_source_port.capture_current()
    # Deliberately bypass value construction: the diagnostic exercises identity,
    # not a fixture-shaped source as authority.
    cut = H1CompletionSessionCut(
        original_identity=cast("DriverCommandIdentityV1", object()),
        original_fingerprint="0" * 64,
        selected_seal=cast("CallSubjectHead", object()),
        first_path=cast("H1FirstPathCapture", _FIRST_PATH),
        conversation=conversation,
    )
    session._cut = cut
    assert session.check_current(cut)
    conversation.current = False
    with pytest.raises(H1CompletionPreparationUnavailable, match="stale"):
        session.require_current_before_terminal_work(cut)
    assert not session.check_current(object())
