"""Private, installed H1 completion invocation source.

Only the mounted runtime may authenticate a completed B invocation.  This
source accepts no completion request, ``InvocationProofRef``, or reconstructed
principal/trust bytes: caller evidence cannot become authority.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from chiplog.composition.h1_completion_issuance import (
    H1CompletionCaptureV1,
    H1CompletionOwnerExchangeV1,
)
from chiplog.composition.r7_planning import ObservedTrustCall
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    InvocationProofRef,
    PublicationIdentity,
)
from chiplog.platform.broker import BrokerSession, PublicPortSuccess

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


class H1LiveInvocationSourceUnavailable(RuntimeError):
    """The installed runtime cannot authenticate a live H1 completion invocation."""


@dataclass(frozen=True, slots=True)
class _H1InvocationCapture:
    """One source-retained AUTHENTICATE observation, never caller evidence."""

    session: object
    recovery: object
    identity: object
    observed: ObservedTrustCall
    principal_bytes: bytes
    issued_at_ns: int
    issuance_id: str
    consumed: bool = False


@dataclass(frozen=True)
class H1InvocationOwnerExtension:
    """The smallest missing owner contract; this is documentation, never a grant."""

    operation: str
    reason: str
    required_observations: tuple[str, ...]
    requires_shared_gate_currentness: bool
    requires_one_use_unpredictable_issuer: bool


class H1LiveInvocationSource:
    """Capture an installed actor observation for one enrolled finalization.

    A future installed owner must retain the actual enrolled B session and
    completed completion exchange, authenticate its broker route, supply the
    canonical principal and immutable trust observation, then recheck all of
    them at the shared authority-gate cut.  This object intentionally accepts
    no owner callback or evidence constructor, so a DTO cannot supply any of
    those facts.
    """

    _REQUIRED_OWNER_EXTENSION = H1InvocationOwnerExtension(
        operation="agent_loop.complete_acceptance.v2",
        reason="no installed H1 completion broker/trust proof owner",
        required_observations=(
            "exact enrolled B session and completion exchange",
            "canonical authenticated principal bytes",
            "immutable observed trust evidence",
            "broker epoch/session/runtime generation",
        ),
        requires_shared_gate_currentness=True,
        requires_one_use_unpredictable_issuer=True,
    )

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("H1 live invocation source requires the installed runtime")
        self._runtime = runtime
        self._issued_count = 0
        self._captures: dict[int, _H1InvocationCapture] = {}
        self._revoked = False

    @property
    def required_owner_extension(self) -> H1InvocationOwnerExtension:
        return self._REQUIRED_OWNER_EXTENSION

    @property
    def issued_count(self) -> int:
        """No issuer identity exists before the broker/trust owner is installed."""
        return self._issued_count

    async def capture_invocation(
        self, *, session: object, identity: object
    ) -> _H1InvocationCapture:
        """Capture the actual installed AUTHENTICATE exchange for one B session.

        ``identity`` is retained as the exact source-owned driver target.  The
        principal and trust result come from the runtime's installed trust
        owner, after the exact recovery session has been authenticated under
        the gate.
        """
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
        from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment

        if self._revoked or getattr(self._runtime, "_h1_live_invocation_source", None) is not self:
            raise H1LiveInvocationSourceUnavailable(
                "H1 completion invocation source is not installed"
            )
        runtime = self._runtime
        enrollment = getattr(runtime, "_h1_live_completion_enrollment", None)
        if (
            type(runtime) is not CommonCliExecutionRuntime
            or type(identity) is not DriverCommandIdentityV1
            or type(enrollment) is not _H1LiveCompletionEnrollment
        ):
            raise H1LiveInvocationSourceUnavailable("H1 invocation inputs are not installed")
        with runtime._authority_gate().hold():
            recovery = enrollment._require_recovery_record(session)
            if recovery.preflight is None:
                raise H1LiveInvocationSourceUnavailable(
                    "H1 invocation is not a finalization session"
                )
            enrollment._require_recovery_record_current(recovery)
        try:
            observed = await runtime._execution_actor("hermetic-ingress")
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1LiveInvocationSourceUnavailable(
                "H1 invocation authentication is unavailable"
            ) from error
        with runtime._authority_gate().hold():
            if self._revoked or getattr(runtime, "_h1_live_invocation_source", None) is not self:
                raise H1LiveInvocationSourceUnavailable("H1 invocation source was revoked")
            current = enrollment._require_recovery_record(session)
            if current is not recovery or current.preflight is None:
                raise H1LiveInvocationSourceUnavailable("H1 invocation recovery enrollment changed")
            enrollment._require_recovery_record_bound_for_invocation(current)
            runtime._check_execution_actor(observed)
            principal = observed.result.reference_bytes
            if type(principal) is not bytes or not principal:
                raise H1LiveInvocationSourceUnavailable("H1 invocation principal is unavailable")
            capture = _H1InvocationCapture(
                session=session,
                recovery=recovery,
                identity=identity,
                observed=observed,
                principal_bytes=principal,
                issued_at_ns=time.monotonic_ns(),
                issuance_id="h1-invocation:" + secrets.token_hex(32),
            )
            self._captures[id(capture)] = capture
            self._issued_count += 1
            return capture

    def _materialize_capture(
        self,
        *,
        capture: object,
        session: object,
        identity: PublicationIdentity,
        expected: AuthoritativeReadManifest,
        replay_exchanges: tuple[
            H1CompletionOwnerExchangeV1,
            H1CompletionOwnerExchangeV1,
            H1CompletionOwnerExchangeV1,
            H1CompletionOwnerExchangeV1,
        ],
    ) -> H1CompletionCaptureV1:
        """Consume a retained observation into V2 evidence after all joins exist."""
        from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
        from chiplog.composition.h1_completion_preparation_session import (
            H1CompletionPreparationSession,
        )
        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment

        runtime = self._runtime
        with runtime._authority_gate().hold():
            stored = self._captures.get(id(capture))
            enrollment = getattr(runtime, "_h1_live_completion_enrollment", None)
            if (
                getattr(runtime, "_h1_live_invocation_source", None) is not self
                or type(capture) is not _H1InvocationCapture
                or stored is not capture
                or capture.consumed
                or capture.session is not session
                or self._revoked
                or type(enrollment) is not _H1LiveCompletionEnrollment
            ):
                raise H1LiveInvocationSourceUnavailable("H1 invocation capture is unavailable")
            # Materialization is an attempt, not a retryable read.  Remove
            # the sole source-held reference before any replay, trust check,
            # or evidence construction can fail.
            self._captures.pop(id(capture))
            recovery = enrollment._require_recovery_record(session)
            if recovery is not capture.recovery or recovery.preflight is None:
                raise H1LiveInvocationSourceUnavailable("H1 invocation enrollment differs")
            enrollment._require_recovery_record_current(recovery)
            state = cast(Any, recovery.source)._context_state(recovery.context)
            if (
                type(capture.identity) is not DriverCommandIdentityV1
                or state.native.original_identity is not capture.identity
            ):
                raise H1LiveInvocationSourceUnavailable("H1 invocation driver identity differs")
            if runtime._trust_observation_guard(capture.observed) is not None:
                raise H1LiveInvocationSourceUnavailable("H1 invocation trust is stale")
            path = Path(runtime._database).resolve()
            stat = path.stat()
            caller = capture.observed.request.caller
            if (
                type(session) is not H1CompletionPreparationSession
                or type(caller) is not BrokerSession
                or type(replay_exchanges) is not tuple
                or len(replay_exchanges) != 4
                or any(
                    type(exchange) is not H1CompletionOwnerExchangeV1
                    for exchange in replay_exchanges
                )
            ):
                raise H1LiveInvocationSourceUnavailable("H1 invocation replay exchanges differ")
            completion, conversation, effects, terminal = replay_exchanges
            finalization = session._recovery
            if (
                finalization is None
                or finalization.poisoned
                or finalization.dispatched_stages
                != ("COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK")
                or finalization.pending_stage is not None
                or completion is not session._completion_exchange
                or conversation is not session._conversation_exchange
                or effects is not session._effects_exchange
                or terminal is not session._terminal_work_exchange
            ):
                raise H1LiveInvocationSourceUnavailable("H1 invocation replay enrollment differs")
            engine = runtime._supervisor.runtime()
            for exchange, role, owner in (
                (completion, "completion", "agent_loop"),
                (conversation, "conversation", "projections"),
                (effects, "effects", "effects"),
                (terminal, "terminal_work", "agent_loop"),
            ):
                sent, returned = exchange.sent, exchange.returned
                if (
                    exchange.role != role
                    or type(returned) is not PublicPortSuccess
                    or returned.request_id != sent.request_id
                    or returned.responder != sent.callee
                    or sent.caller != caller
                    or sent.callee != engine.session(owner)
                ):
                    raise H1LiveInvocationSourceUnavailable("H1 invocation replay route differs")
            sessions = (
                caller,
                capture.observed.request.callee,
                *(exchange.sent.callee for exchange in replay_exchanges),
            )
            issued = capture.issuance_id
            observed_json = H1CompletionCaptureV1.model_construct(
                principal_bytes=capture.principal_bytes,
                invocation=InvocationProofRef(
                    issuance_id=issued,
                    issuance_fingerprint="0" * 64,
                    broker_epoch=str(caller.broker_epoch),
                    broker_session=caller.session_id,
                    runtime_generation=caller.generation_id,
                    operation_subject=identity.command_id,
                ),
                observed=capture.observed,
                database_id="hermetic-database",
                physical_path=str(path),
                physical_device=stat.st_dev,
                physical_inode=stat.st_ino,
                broker_epoch=caller.broker_epoch,
                runtime_generation=caller.generation_id,
                broker_session_id=caller.session_id,
                worker_session_id=runtime.current_worker(),
                sessions=sessions,
                observed_time_ns=capture.issued_at_ns,
                clock_epoch="monotonic",
                valid_until_ns=capture.observed.request.budget.absolute_deadline_ns,
                expected=expected,
            )
            observed_json = observed_json.model_dump(mode="json")["observed"]
            fingerprint = hashlib.sha256(
                json.dumps(
                    {
                        "domain": "chiplog.composition.h1-completion-invocation.v1",
                        "issuance_id": issued,
                        "identity": identity.model_dump(mode="json"),
                        "principal_fingerprint": hashlib.sha256(
                            capture.principal_bytes
                        ).hexdigest(),
                        "observed_fingerprint": hashlib.sha256(
                            json.dumps(
                                observed_json,
                                sort_keys=True,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ).encode()
                        ).hexdigest(),
                    },
                    sort_keys=True,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            return H1CompletionCaptureV1(
                principal_bytes=capture.principal_bytes,
                invocation=InvocationProofRef(
                    issuance_id=issued,
                    issuance_fingerprint=fingerprint,
                    broker_epoch=str(caller.broker_epoch),
                    broker_session=caller.session_id,
                    runtime_generation=caller.generation_id,
                    operation_subject=identity.command_id,
                ),
                observed=capture.observed,
                database_id="hermetic-database",
                physical_path=str(path),
                physical_device=stat.st_dev,
                physical_inode=stat.st_ino,
                broker_epoch=caller.broker_epoch,
                runtime_generation=caller.generation_id,
                broker_session_id=caller.session_id,
                worker_session_id=runtime.current_worker(),
                sessions=sessions,
                observed_time_ns=capture.issued_at_ns,
                clock_epoch="monotonic",
                valid_until_ns=capture.observed.request.budget.absolute_deadline_ns,
                expected=expected,
            )

    def _revoke_all(self) -> None:
        self._revoked = True
        self._captures.clear()


__all__ = [
    "H1InvocationOwnerExtension",
    "H1LiveInvocationSource",
    "H1LiveInvocationSourceUnavailable",
]
