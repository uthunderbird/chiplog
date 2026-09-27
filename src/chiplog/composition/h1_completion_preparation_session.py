"""Private, fail-closed hand-off for an H1 live completion preparation.

The session owns the only first-path reader and the opaque conversation cut.
It deliberately does *not* accept a caller supplied completion/conversation/
effects/work result bundle: those public DTOs are evidence, never authority.
The owner-call router is mounted by the CLI leaf; until it supplies the live
routes this seam refuses to mint a publication capability.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Protocol

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.completion_terminal_work_sources import (
    AcceptedCompletionWorkSourceV1,
    completion_terminal_work_source_bytes,
)
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
    first_path_completion_request_fingerprint,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PreparedPostTerminalWork,
    PrepareTerminalWork,
    WorkCommandIdentity,
)
from chiplog.capabilities.agent_loop.post_terminal_record_contracts import (
    validate_prepared_post_terminal_work,
)
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    manifest_ref,
    run_ref,
)
from chiplog.capabilities.agent_loop.terminal_work_preparation import (
    prepared_post_terminal_work_commitment,
)
from chiplog.capabilities.effects.contracts import CommandIdentity
from chiplog.capabilities.effects.fences import Absent
from chiplog.capabilities.effects.h1_local_preparation import _intent_id
from chiplog.capabilities.effects.h1_local_preparation_contracts import (
    H1LocalCommentaryOwnerCallV1,
    H1LocalCommentaryRouteV1,
    PreparedH1LocalCommentaryV1,
    PrepareH1LocalCommentaryV1,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    PrepareConversationCompletionV1,
    PreparedConversationCompletionV1,
    conversation_source_request_fingerprint,
)
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_completion_issuance import H1CompletionOwnerExchangeV1
from chiplog.composition.h1_conversation_sources import (
    H1ConversationSources as _ConversationSources,
)
from chiplog.composition.h1_first_path_sources import H1FirstPathCapture, H1FirstPathSources
from chiplog.composition.h1_selected_prepare import select_h1_v3_prepare_for_seal
from chiplog.platform.broker import (
    BrokerSession,
    CallBudget,
    PublicPortCall,
    PublicPortRejected,
    PublicPortSuccess,
)


class H1CompletionPreparationUnavailable(ValueError):
    """Raised before a mounted live owner chain can issue H1 authority."""


class H1ConversationCapture(Protocol):
    """Issuer-owned A capability.  It is intentionally structurally opaque."""


@dataclass(frozen=True, slots=True)
class H1CompletionSessionCut:
    """Private in-memory source binding; not a serializable publication token."""

    original_identity: DriverCommandIdentityV1
    original_fingerprint: str
    selected_seal: CallSubjectHead
    first_path: H1FirstPathCapture
    conversation: H1ConversationCapture | None


@dataclass(frozen=True, slots=True)
class _H1CompletionPreflight:
    """B-owned identity joins retained before the first completion IPC."""

    first_path: H1FirstPathCapture
    native_cap: object
    verified_original: object
    scope_cap: object
    delivery_receipt: object
    request: PrepareExecutionCompletionFirstPathV2


@dataclass(frozen=True, slots=True)
class _H1FinalFenceInputs:
    """Private B-held identity joins released only to the mounted P owner."""

    first_path: H1FirstPathCapture
    native_cap: object
    scope_cap: object
    delivery_receipt: object
    request: PrepareExecutionCompletionFirstPathV2
    completion_exchange: H1CompletionOwnerExchangeV1
    conversation_exchange: H1CompletionOwnerExchangeV1
    effects_exchange: H1CompletionOwnerExchangeV1
    terminal_work_exchange: H1CompletionOwnerExchangeV1 | None


class H1CompletionPreparationSession:
    """One-use live H1 source session.

    This is the only B object allowed to keep the A capture.  A later writer
    receives a capability minted by the completed owner chain, never this cut.
    The owner-chain mint is intentionally absent until the runtime integration
    can supply actual public routes for all four roles.
    """

    def __init__(
        self,
        *,
        first_path_sources: H1FirstPathSources,
    ) -> None:
        if not isinstance(first_path_sources, H1FirstPathSources):
            raise TypeError("H1 completion session requires one H1FirstPathSources instance")
        self._sources = first_path_sources
        runtime = first_path_sources._runtime
        conversation_sources = getattr(runtime, "_h1_conversation_source_port", None)
        if (
            type(conversation_sources) is not _ConversationSources
            or conversation_sources._runtime is not runtime
            or conversation_sources._gate is not first_path_sources._gate
        ):
            raise H1CompletionPreparationUnavailable(
                "H1 completion requires the exact mounted A conversation source owner"
            )
        # B retains the runtime-mounted A owner.  Constructing a fresh reader
        # would create a separate identity table and permit no authenticated
        # capture to cross this hand-off.
        self._conversation_sources = conversation_sources
        self._cut: H1CompletionSessionCut | None = None
        self._preflight: _H1CompletionPreflight | None = None
        self._completion_exchange: H1CompletionOwnerExchangeV1 | None = None
        self._completion_started = False
        self._completion_dispatch_started = False
        self._conversation_exchange: H1CompletionOwnerExchangeV1 | None = None
        self._conversation_started = False
        self._effects_exchange: H1CompletionOwnerExchangeV1 | None = None
        self._effects_started = False
        self._terminal_work_exchange: H1CompletionOwnerExchangeV1 | None = None
        self._terminal_work_started = False
        self._terminal_clearance: object | None = None

    def capture_first_path(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> H1FirstPathCapture:
        """Take this session's sole pre-IPC H1 source capture."""
        if self._cut is not None:
            raise H1CompletionPreparationUnavailable("H1 completion session is already started")
        gate = self._sources._gate
        with gate.hold():
            first_path = self._sources.capture_current(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
            )
            self._cut = H1CompletionSessionCut(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
                first_path=first_path,
                conversation=None,
            )
            return first_path

    async def prepare_first_path_completion(self) -> H1CompletionOwnerExchangeV1:
        """Make and retain the genuine completion exchange outside the gate.

        Every input is issued by the installed A/P/E owners.  In particular,
        no public request or delivery/fence value crosses this boundary.
        """
        cut = self._cut
        if cut is None or cut.conversation is not None:
            raise H1CompletionPreparationUnavailable("H1 completion session source is unavailable")
        if self._completion_exchange is not None or self._completion_started:
            raise H1CompletionPreparationUnavailable("H1 completion session is already started")
        self._completion_started = True
        try:
            await self._build_first_path_completion_preflight()
            return await self._dispatch_first_path_completion()
        except BaseException:
            # The owner call may already have crossed the process boundary;
            # cancellation and failure must not permit a second use of this cut.
            raise

    async def _build_first_path_completion_preflight(self) -> _H1CompletionPreflight:
        """Retain the canonical completion request before opening B's route.

        This private seam has no caller-supplied request or pin.  It retains
        the installed A/P/E joins and complete canonical request so a future
        recovery coordinator can persist the semantic bytes before dispatch.
        It deliberately establishes no historical authority.
        """
        cut = self._cut
        if cut is None or cut.conversation is not None:
            raise H1CompletionPreparationUnavailable("H1 completion session source is unavailable")
        if self._completion_exchange is not None or self._preflight is not None:
            raise H1CompletionPreparationUnavailable("H1 completion session is already started")
        try:
            from chiplog.composition.h1_completion_exchange_registry import (
                H1CompletionExchangeRegistry,
            )
            from chiplog.composition.h1_native_member_sources import H1NativeMemberSources
            from chiplog.composition.h1_pre_request_member_evidence import (
                H1PreRequestMemberEvidence,
            )
            from chiplog.composition.h1_pre_request_worker_evidence import (
                H1PreRequestWorkerEvidence,
            )
            from chiplog.composition.h1_runtime_preissuance_port import (
                _H1RuntimePreissuancePort,
            )
            from chiplog.composition.h1_worker_evidence import _H1InstalledWorkerEvidenceOwner

            runtime = self._sources._runtime
            native_sources = getattr(runtime, "_h1_native_member_sources", None)
            port = getattr(runtime, "_h1_preissuance_registration_source_port", None)
            member_issuer = getattr(runtime, "_h1_pre_request_member_evidence", None)
            worker_issuer = getattr(runtime, "_h1_pre_request_worker_evidence", None)
            worker_owner = getattr(runtime, "_h1_installed_worker_evidence_owner", None)
            registry = getattr(runtime, "_h1_completion_exchange_registry", None)
            if (
                type(native_sources) is not H1NativeMemberSources
                or native_sources._runtime is not runtime
                or type(port) is not _H1RuntimePreissuancePort
                or type(member_issuer) is not H1PreRequestMemberEvidence
                or type(worker_issuer) is not H1PreRequestWorkerEvidence
                or type(worker_owner) is not _H1InstalledWorkerEvidenceOwner
                or type(registry) is not H1CompletionExchangeRegistry
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 completion requires one installed B/P/E completion mount"
                )
            request_builder = self._sources._prepare_first_path_completion_request
            if not callable(request_builder):
                raise H1CompletionPreparationUnavailable(
                    "H1 first-path source lacks a session-owned request builder"
                )
            native_cap = native_sources.capture_current(cut.first_path)
            selected = select_h1_v3_prepare_for_seal(
                runtime, selected_seal=cut.first_path.source.selected_response_seal
            )
            # This awaits the installed current-scope owner.  The one-use flag
            # was deliberately set before it, and no authority gate is held.
            verified_original = await port.verify_selected_original(selected.prepare)
            scope_cap = await port._capture_completion_scope(
                cut.first_path, native_cap, verified_original
            )
            member_receipt = member_issuer._issue_members(native_cap, scope_cap)
            worker_cap = worker_owner._capture_for_pre_request(native_cap)
            worker_receipt = worker_issuer._issue_worker(native_cap, worker_cap)
            delivery_receipt = port._prepare_delivery_inputs(
                cut.first_path,
                native_cap,
                scope_cap,
                member_receipt,
                worker_receipt,
            )
            request = request_builder(cut.first_path, delivery_receipt)
            if type(request) is not PrepareExecutionCompletionFirstPathV2:
                raise H1CompletionPreparationUnavailable(
                    "H1 first-path source returned an invalid session-owned completion request"
                )
            if request.source != cut.first_path.source:
                raise H1CompletionPreparationUnavailable(
                    "H1 session-owned completion request differs from session source"
                )
            self._preflight = _H1CompletionPreflight(
                first_path=cut.first_path,
                native_cap=native_cap,
                verified_original=verified_original,
                scope_cap=scope_cap,
                delivery_receipt=delivery_receipt,
                request=request,
            )
            return self._preflight
        except (TypeError, ValueError) as error:
            raise H1CompletionPreparationUnavailable(
                "H1 completion source returned invalid canonical evidence"
            ) from error

    async def _dispatch_first_path_completion(self) -> H1CompletionOwnerExchangeV1:
        """Dispatch the exact semantic request retained by the private builder."""
        cut = self._cut
        preflight = self._preflight
        if (
            cut is None
            or cut.conversation is not None
            or preflight is None
            or preflight.first_path is not cut.first_path
            or self._completion_exchange is not None
            or self._completion_dispatch_started
        ):
            raise H1CompletionPreparationUnavailable("H1 completion session is already started")
        # A direct private dispatch is still one-use.  An interrupted owner
        # exchange has an ambiguous outcome and cannot consume this preflight
        # a second time.
        self._completion_dispatch_started = True
        try:
            from chiplog.composition.h1_completion_exchange_registry import (
                H1CompletionExchangeRegistry,
            )
            from chiplog.composition.h1_runtime_preissuance_port import (
                _H1RuntimePreissuancePort,
            )

            runtime = self._sources._runtime
            port = getattr(runtime, "_h1_preissuance_registration_source_port", None)
            registry = getattr(runtime, "_h1_completion_exchange_registry", None)
            if (
                type(port) is not _H1RuntimePreissuancePort
                or type(registry) is not H1CompletionExchangeRegistry
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 completion requires one installed B/P/E completion mount"
                )
            request_builder = self._sources._prepare_first_path_completion_request
            if not callable(request_builder):
                raise H1CompletionPreparationUnavailable(
                    "H1 first-path source lacks a session-owned request builder"
                )
            request = preflight.request
            # Immediately before the irreversible call, rederive the request
            # and atomically consume P's one-use receipt.  A changed source,
            # receipt, or command identity leaves the session consumed.
            with self._sources._gate.hold():
                replayed = request_builder(cut.first_path, preflight.delivery_receipt)
                if replayed.canonical_bytes() != request.canonical_bytes():
                    raise H1CompletionPreparationUnavailable(
                        "H1 completion preflight request is no longer current"
                    )
                delivery, fence = port._consume_delivery_inputs(
                    preflight.delivery_receipt, cut.first_path
                )
                if (
                    delivery != request.delivery
                    or fence.canonical_bytes() != request.fence.canonical_bytes()
                ):
                    raise H1CompletionPreparationUnavailable(
                        "H1 completion consumed delivery inputs differ from preflight"
                    )
            engine = runtime._supervisor.runtime()
            callee = engine.session("agent_loop")
            caller = BrokerSession(
                tenant_id=runtime._tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id="broker:" + callee.generation_id,
            )
            sent_at_ns = time.monotonic_ns()
            sent = PublicPortCall(
                operation_id="agent_loop.prepare_first_path_completion",
                request_id="h1-completion:" + secrets.token_hex(24),
                caller=caller,
                callee=callee,
                schema_id=request.schema_id,
                canonical_payload=request.canonical_bytes(),
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    policy_version=1,
                    absolute_deadline_ns=sent_at_ns + 5_000_000_000,
                ),
            )
            returned = await engine.call(sent)
            returned_at_ns = time.monotonic_ns()
            if (
                not isinstance(returned, PublicPortSuccess)
                or returned.request_id != sent.request_id
                or returned.responder != callee
                or returned.schema_id
                != "chiplog.agent-loop.prepared-execution-completion-result.v1"
                or returned_at_ns >= sent.budget.absolute_deadline_ns
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 completion owner did not return its exact success"
                )
            result = PreparedExecutionCompletion.model_validate_json(returned.canonical_payload)
            if result.canonical_bytes() != returned.canonical_payload:
                raise H1CompletionPreparationUnavailable(
                    "H1 completion owner result is noncanonical"
                )
            if result.source_request_fingerprint != first_path_completion_request_fingerprint(
                request
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 completion owner result differs from session request"
                )
            exchange = H1CompletionOwnerExchangeV1(
                role="completion",
                sent=sent,
                returned=returned,
                sent_at_ns=sent_at_ns,
                returned_at_ns=returned_at_ns,
            )
            self._completion_exchange = exchange
            registry._register_actual_success(self)
            return exchange
        except H1CompletionPreparationUnavailable:
            raise
        except (TypeError, ValueError) as error:
            raise H1CompletionPreparationUnavailable(
                "H1 completion owner returned invalid canonical evidence"
            ) from error

    def capture_current(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
        completion_exchange: H1CompletionOwnerExchangeV1,
    ) -> H1CompletionSessionCut:
        """Capture source and A policy atomically after a genuine completion reply.

        The completion exchange is required to be actual success evidence by A.
        This method performs no IPC and therefore never awaits under the gate.
        """
        if self._completion_exchange is not completion_exchange:
            raise H1CompletionPreparationUnavailable("H1 completion exchange is not session-issued")
        if self._cut is None or self._cut.conversation is not None:
            raise H1CompletionPreparationUnavailable("H1 completion session source is unavailable")
        gate = self._sources._gate
        with gate.hold():
            first_path = self._cut.first_path
            if (
                self._cut.original_identity != original_identity
                or self._cut.original_fingerprint != original_fingerprint
                or self._cut.selected_seal != selected_seal
            ):
                raise H1CompletionPreparationUnavailable("H1 completion source identity differs")
            conversation = self._conversation_sources.capture_current(
                first_path=first_path, completion_exchange=completion_exchange
            )
            cut = H1CompletionSessionCut(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
                first_path=first_path,
                conversation=conversation,
            )
            self._cut = cut
            return cut

    async def prepare_conversation_completion(self) -> H1CompletionOwnerExchangeV1:
        """Send the exact mounted-A request to the installed projections owner.

        The conversation capture and request remain owned by A.  This B
        session only holds their opaque identity and records the actual broker
        frames once the owner has accepted that request.
        """
        cut = self._cut
        if cut is None or cut.conversation is None:
            raise H1CompletionPreparationUnavailable("H1 conversation source is unavailable")
        if self._conversation_exchange is not None or self._conversation_started:
            raise H1CompletionPreparationUnavailable("H1 conversation session is already started")
        # This is set before the currentness check because a stale source or an
        # interrupted call has an ambiguous outcome and must never be retried.
        self._conversation_started = True
        try:
            runtime = self._sources._runtime
            gate = self._sources._gate
            with gate.hold():
                if (
                    self._cut is not cut
                    or self._sources.check_current(cut.first_path) is not True
                    or self._conversation_sources.check_current(cut.conversation) is not True
                ):
                    raise H1CompletionPreparationUnavailable(
                        "H1 completion source or conversation cut is stale"
                    )
                request = self._conversation_sources._prepare_conversation_completion_request(
                    cut.conversation
                )
                if type(request) is not PrepareConversationCompletionV1:
                    raise H1CompletionPreparationUnavailable(
                        "H1 conversation source returned an invalid session-owned request"
                    )
                request_bytes = request.canonical_json_bytes()
                if (
                    PrepareConversationCompletionV1.model_validate_json(
                        request_bytes
                    ).canonical_json_bytes()
                    != request_bytes
                ):
                    raise H1CompletionPreparationUnavailable(
                        "H1 conversation source returned a noncanonical request"
                    )
            engine = runtime._supervisor.runtime()
            callee = engine.session("projections")
            caller = BrokerSession(
                tenant_id=runtime._tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id="broker:" + callee.generation_id,
            )
            sent_at_ns = time.monotonic_ns()
            sent = PublicPortCall(
                operation_id="projections.prepare_conversation_completion",
                request_id="h1-conversation:" + secrets.token_hex(24),
                caller=caller,
                callee=callee,
                schema_id=request.schema_id,
                canonical_payload=request_bytes,
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    policy_version=1,
                    absolute_deadline_ns=sent_at_ns + 5_000_000_000,
                ),
            )
            returned = await engine.call(sent)
            returned_at_ns = time.monotonic_ns()
            if (
                not isinstance(returned, PublicPortSuccess)
                or returned.request_id != sent.request_id
                or returned.responder != callee
                or returned.schema_id != "chiplog.conversation.prepared-completion-result.v1"
                or returned_at_ns < sent_at_ns
                or returned_at_ns >= sent.budget.absolute_deadline_ns
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 conversation owner did not return its exact success"
                )
            result = PreparedConversationCompletionV1.model_validate_json(
                returned.canonical_payload
            )
            if result.canonical_json_bytes() != returned.canonical_payload:
                raise H1CompletionPreparationUnavailable(
                    "H1 conversation owner result is noncanonical"
                )
            if result.source_request_fingerprint != conversation_source_request_fingerprint(
                request
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 conversation owner result differs from session request"
                )
            exchange = H1CompletionOwnerExchangeV1(
                role="conversation",
                sent=sent,
                returned=returned,
                sent_at_ns=sent_at_ns,
                returned_at_ns=returned_at_ns,
            )
            self._conversation_exchange = exchange
            return exchange
        except H1CompletionPreparationUnavailable:
            raise
        except (TypeError, ValueError) as error:
            raise H1CompletionPreparationUnavailable(
                "H1 conversation owner returned invalid canonical evidence"
            ) from error

    async def prepare_local_commentary(self) -> H1CompletionOwnerExchangeV1:
        """Make the one genuine H1 effects-owner preparation call.

        P owns the selected scope, retained origin, and worker fence.  B only
        combines that private replay with the actual preceding owner exchanges
        and retains the real effects broker frames.
        """
        cut = self._cut
        preflight = self._preflight
        completion_exchange = self._completion_exchange
        conversation_exchange = self._conversation_exchange
        if (
            cut is None
            or cut.conversation is None
            or preflight is None
            or completion_exchange is None
            or conversation_exchange is None
        ):
            raise H1CompletionPreparationUnavailable("H1 effects source is unavailable")
        if self._effects_exchange is not None or self._effects_started:
            raise H1CompletionPreparationUnavailable("H1 effects session is already started")
        # Set this before any source replay: an interrupted or stale attempt
        # has an ambiguous boundary outcome and cannot be retried.
        self._effects_started = True
        try:
            from chiplog.composition.h1_native_member_sources import H1NativeMemberSources
            from chiplog.composition.h1_runtime_preissuance_port import (
                _AuthenticatedCompletionEffectsSource,
                _H1RuntimePreissuancePort,
            )

            runtime = self._sources._runtime
            port = getattr(runtime, "_h1_preissuance_registration_source_port", None)
            native_sources = getattr(runtime, "_h1_native_member_sources", None)
            if (
                type(port) is not _H1RuntimePreissuancePort
                or port._runtime is not runtime
                or type(native_sources) is not H1NativeMemberSources
                or native_sources._runtime is not runtime
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 effects requires the exact installed P/native source owners"
                )
            if (
                completion_exchange.role != "completion"
                or conversation_exchange.role != "conversation"
                or not isinstance(completion_exchange.returned, PublicPortSuccess)
                or not isinstance(conversation_exchange.returned, PublicPortSuccess)
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 preceding owner exchange is unavailable"
                )
            try:
                completion_request = PrepareExecutionCompletionFirstPathV2.model_validate_json(
                    completion_exchange.sent.canonical_payload
                )
                prepared_completion = PreparedExecutionCompletion.model_validate_json(
                    completion_exchange.returned.canonical_payload
                )
                conversation_request = PrepareConversationCompletionV1.model_validate_json(
                    conversation_exchange.sent.canonical_payload
                )
                conversation_result = PreparedConversationCompletionV1.model_validate_json(
                    conversation_exchange.returned.canonical_payload
                )
            except ValueError as error:
                raise H1CompletionPreparationUnavailable(
                    "H1 preceding owner exchange has invalid canonical evidence"
                ) from error
            if (
                completion_request.canonical_bytes() != completion_exchange.sent.canonical_payload
                or prepared_completion.canonical_bytes()
                != completion_exchange.returned.canonical_payload
                or completion_request.canonical_bytes() != preflight.request.canonical_bytes()
                or prepared_completion.source_request_fingerprint
                != first_path_completion_request_fingerprint(preflight.request)
                or conversation_request.canonical_json_bytes()
                != conversation_exchange.sent.canonical_payload
                or conversation_result.canonical_json_bytes()
                != conversation_exchange.returned.canonical_payload
                or conversation_result.source_request_fingerprint
                != conversation_source_request_fingerprint(conversation_request)
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 preceding owner exchange differs from retained source"
                )
            gate = self._sources._gate
            with gate.hold():
                if (
                    self._cut is not cut
                    or self._sources.check_current(cut.first_path) is not True
                    or self._conversation_sources.check_current(cut.conversation) is not True
                ):
                    raise H1CompletionPreparationUnavailable(
                        "H1 effects source or conversation cut is stale"
                    )
                effects_source = port._replay_completion_effects_source(
                    preflight.scope_cap,
                    preflight.native_cap,
                    preflight.delivery_receipt,
                    cut.first_path,
                )
                if type(effects_source) is not _AuthenticatedCompletionEffectsSource:
                    raise H1CompletionPreparationUnavailable("H1 effects source replay is invalid")
            engine = runtime._supervisor.runtime()
            callee = engine.session("effects")
            request_id = "h1-effects:" + secrets.token_hex(24)
            caller = BrokerSession(
                tenant_id=runtime._tenant_id,
                broker_epoch=callee.broker_epoch,
                generation_id=callee.generation_id,
                owner_id="broker",
                session_id="broker:" + callee.generation_id,
            )
            route = H1LocalCommentaryRouteV1(
                tenant_id=preflight.request.run.tenant,
                database_id=preflight.request.source.database_id,
                worker_session_id=preflight.request.run.worker_session,
                broker_epoch=caller.broker_epoch,
                runtime_generation=caller.generation_id,
                broker_session_id=caller.session_id,
                owner_session_id=callee.session_id,
                request_id=request_id,
            )
            identity = CommandIdentity(
                command_id=request_id,
                fingerprint=hashlib.sha256(request_id.encode()).hexdigest(),
                expected_tenant_head=preflight.request.source.tenant_commit_sequence,
            )
            provisional_request = PrepareH1LocalCommentaryV1(
                identity=identity,
                intent_id="pending",
                expected_intent=Absent(),
                original_completion_request=preflight.request,
                prepared_completion=prepared_completion,
                selected_scope=effects_source.selected_scope,
                retained_origin=effects_source.retained_origin,
                fence=effects_source.fence,
            )
            provisional_call = H1LocalCommentaryOwnerCallV1(
                route=route,
                request=provisional_request,
                request_digest=hashlib.sha256(provisional_request.canonical_bytes()).hexdigest(),
            )
            delivery_id = prepared_completion.delivery.manifest.ordered_deliveries[0].delivery_id
            request = provisional_request.model_copy(
                update={"intent_id": _intent_id(provisional_call, delivery_id)}
            )
            owner_call = H1LocalCommentaryOwnerCallV1(
                route=route,
                request=request,
                request_digest=hashlib.sha256(request.canonical_bytes()).hexdigest(),
            )
            sent_at_ns = time.monotonic_ns()
            sent = PublicPortCall(
                operation_id="effects.prepare_h1_local_commentary",
                request_id=request_id,
                caller=caller,
                callee=callee,
                schema_id=owner_call.schema_id,
                canonical_payload=owner_call.canonical_bytes(),
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    policy_version=1,
                    absolute_deadline_ns=sent_at_ns + 5_000_000_000,
                ),
            )
            if (
                owner_call.route.tenant_id != sent.caller.tenant_id
                or owner_call.route.tenant_id != sent.callee.tenant_id
                or owner_call.route.broker_epoch != sent.caller.broker_epoch
                or owner_call.route.runtime_generation != sent.caller.generation_id
                or owner_call.route.broker_session_id != sent.caller.session_id
                or owner_call.route.owner_session_id != sent.callee.session_id
                or owner_call.route.request_id != sent.request_id
                or owner_call.canonical_bytes() != sent.canonical_payload
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 effects route differs from broker call"
                )
            returned = await engine.call(sent)
            returned_at_ns = time.monotonic_ns()
            if (
                not isinstance(returned, PublicPortSuccess)
                or returned.request_id != sent.request_id
                or returned.responder != callee
                or returned.schema_id != "chiplog.effects.prepared-h1-local-commentary.v1"
                or returned_at_ns < sent_at_ns
                or returned_at_ns >= sent.budget.absolute_deadline_ns
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 effects owner did not return its exact success"
                )
            result = PreparedH1LocalCommentaryV1.model_validate_json(returned.canonical_payload)
            if (
                result.canonical_bytes() != returned.canonical_payload
                or result.source_request_fingerprint
                != hashlib.sha256(request.canonical_bytes()).hexdigest()
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 effects owner result differs from session request"
                )
            with gate.hold():
                if (
                    self._cut is not cut
                    or self._sources.check_current(cut.first_path) is not True
                    or self._conversation_sources.check_current(cut.conversation) is not True
                ):
                    raise H1CompletionPreparationUnavailable(
                        "H1 effects source or conversation cut is stale"
                    )
                replayed_source = port._replay_completion_effects_source(
                    preflight.scope_cap,
                    preflight.native_cap,
                    preflight.delivery_receipt,
                    cut.first_path,
                )
                if (
                    type(replayed_source) is not _AuthenticatedCompletionEffectsSource
                    or replayed_source != effects_source
                ):
                    raise H1CompletionPreparationUnavailable(
                        "H1 effects source changed during owner call"
                    )
            exchange = H1CompletionOwnerExchangeV1(
                role="effects",
                sent=sent,
                returned=returned,
                sent_at_ns=sent_at_ns,
                returned_at_ns=returned_at_ns,
            )
            self._effects_exchange = exchange
            return exchange
        except H1CompletionPreparationUnavailable:
            raise
        except (TypeError, ValueError, IndexError) as error:
            raise H1CompletionPreparationUnavailable(
                "H1 effects owner returned invalid canonical evidence"
            ) from error

    def _prepare_terminal_work_request(
        self,
        *,
        preflight: _H1CompletionPreflight,
        completion_exchange: H1CompletionOwnerExchangeV1,
        effects_exchange: H1CompletionOwnerExchangeV1,
    ) -> PrepareTerminalWork:
        """Derive terminal-work bytes from this session's exact retained exchanges."""
        if (
            completion_exchange.role != "completion"
            or effects_exchange.role != "effects"
            or not isinstance(completion_exchange.returned, PublicPortSuccess)
            or not isinstance(effects_exchange.returned, PublicPortSuccess)
        ):
            raise H1CompletionPreparationUnavailable("H1 terminal work exchange is unavailable")
        try:
            completion_request = PrepareExecutionCompletionFirstPathV2.model_validate_json(
                completion_exchange.sent.canonical_payload
            )
            prepared = PreparedExecutionCompletion.model_validate_json(
                completion_exchange.returned.canonical_payload
            )
            effects_call = H1LocalCommentaryOwnerCallV1.model_validate_json(
                effects_exchange.sent.canonical_payload
            )
            effects_result = PreparedH1LocalCommentaryV1.model_validate_json(
                effects_exchange.returned.canonical_payload
            )
        except ValueError as error:
            raise H1CompletionPreparationUnavailable(
                "H1 terminal work exchange has invalid canonical evidence"
            ) from error

        if (
            completion_request.canonical_bytes() != completion_exchange.sent.canonical_payload
            or completion_request.canonical_bytes() != preflight.request.canonical_bytes()
            or prepared.canonical_bytes() != completion_exchange.returned.canonical_payload
            or prepared.source_request_fingerprint
            != first_path_completion_request_fingerprint(preflight.request)
            or effects_call.canonical_bytes() != effects_exchange.sent.canonical_payload
            or effects_result.canonical_bytes() != effects_exchange.returned.canonical_payload
            or effects_result.source_request_fingerprint
            != hashlib.sha256(effects_call.request.canonical_bytes()).hexdigest()
            or effects_call.request.original_completion_request.canonical_bytes()
            != preflight.request.canonical_bytes()
            or effects_call.request.prepared_completion != prepared
        ):
            raise H1CompletionPreparationUnavailable(
                "H1 effects exchange differs from retained completion source"
            )
        source = AcceptedCompletionWorkSourceV1(
            original_completion_request_bytes=completion_exchange.sent.canonical_payload,
            prepared_completion_bytes=completion_exchange.returned.canonical_payload,
            terminal_run=prepared.run,
            terminal_run_head=run_ref(prepared.run),
            terminal_manifest=prepared.terminal_manifest,
            terminal_manifest_head=manifest_ref(prepared.terminal_manifest),
            ordered_open_obligations=prepared.terminal_manifest.complete_open_original_obligations,
        )
        if source.ordered_open_obligations:
            raise H1CompletionPreparationUnavailable(
                "H1 terminal work requires zero open obligations"
            )
        return PrepareTerminalWork(
            identity=WorkCommandIdentity(
                tenant_id=source.terminal_run.tenant,
                command_id=source.terminal_manifest.command_id,
            ),
            terminal_run=source.terminal_run,
            original_terminalization_request=completion_terminal_work_source_bytes(source),
            terminal_manifest=source.terminal_manifest_head,
            ordered_open_obligations=source.ordered_open_obligations,
        )

    def _replay_final_fence_inputs(self, port: object) -> _H1FinalFenceInputs:
        """Release this session's retained inputs only to its mounted P owner."""
        from chiplog.composition.h1_completion_exchange_registry import (
            H1CompletionExchangeRegistry,
        )
        from chiplog.composition.h1_native_member_sources import H1NativeMemberSources
        from chiplog.composition.h1_runtime_preissuance_port import _H1RuntimePreissuancePort

        runtime = self._sources._runtime
        registry = getattr(runtime, "_h1_completion_exchange_registry", None)
        native_sources = getattr(runtime, "_h1_native_member_sources", None)
        cut = self._cut
        preflight = self._preflight
        completion_exchange = self._completion_exchange
        conversation_exchange = self._conversation_exchange
        effects_exchange = self._effects_exchange
        if type(port) is not _H1RuntimePreissuancePort:
            raise H1CompletionPreparationUnavailable("H1 final fence inputs are unavailable")
        port_value = port
        if (
            port is not getattr(runtime, "_h1_preissuance_registration_source_port", None)
            or port_value._runtime is not runtime
            or type(registry) is not H1CompletionExchangeRegistry
            or type(native_sources) is not H1NativeMemberSources
            or type(cut) is not H1CompletionSessionCut
            or cut.conversation is None
            or preflight is None
            or completion_exchange is None
            or conversation_exchange is None
            or effects_exchange is None
        ):
            raise H1CompletionPreparationUnavailable("H1 final fence inputs are unavailable")
        registry_inputs = registry._replay_completion_exchange(cut.first_path, completion_exchange)
        if (
            registry_inputs.session_identity is not self
            or registry_inputs.first_path is not cut.first_path
            or registry_inputs.native_cap is not preflight.native_cap
            or registry_inputs.scope_cap is not preflight.scope_cap
            or preflight.first_path is not cut.first_path
            or completion_exchange.role != "completion"
            or conversation_exchange.role != "conversation"
            or effects_exchange.role != "effects"
            or (
                self._terminal_work_exchange is not None
                and self._terminal_work_exchange.role != "terminal_work"
            )
        ):
            raise H1CompletionPreparationUnavailable("H1 final fence inputs differ from session")
        return _H1FinalFenceInputs(
            first_path=cut.first_path,
            native_cap=preflight.native_cap,
            scope_cap=preflight.scope_cap,
            delivery_receipt=preflight.delivery_receipt,
            request=preflight.request,
            completion_exchange=completion_exchange,
            conversation_exchange=conversation_exchange,
            effects_exchange=effects_exchange,
            terminal_work_exchange=self._terminal_work_exchange,
        )

    async def prepare_terminal_work(self) -> H1CompletionOwnerExchangeV1:
        """Prepare zero-obligation terminal work through the guarded live route."""
        cut = self._cut
        preflight = self._preflight
        completion_exchange = self._completion_exchange
        conversation_exchange = self._conversation_exchange
        effects_exchange = self._effects_exchange
        if (
            cut is None
            or cut.conversation is None
            or preflight is None
            or completion_exchange is None
            or conversation_exchange is None
            or effects_exchange is None
        ):
            raise H1CompletionPreparationUnavailable("H1 terminal work source is unavailable")
        if self._terminal_work_exchange is not None or self._terminal_work_started:
            raise H1CompletionPreparationUnavailable("H1 terminal work is already started")
        # The terminal request may reach the broker worker even if this task is
        # cancelled.  Consume the session leg before any replay or await.
        self._terminal_work_started = True
        clearance: object | None = None
        enrollment: object | None = None
        try:
            from chiplog.composition.h1_live_completion_enrollment import (
                _H1LiveCompletionEnrollment,
                _H1TerminalClearance,
            )
            from chiplog.composition.h1_runtime_preissuance_port import _H1RuntimePreissuancePort

            runtime = self._sources._runtime
            port = getattr(runtime, "_h1_preissuance_registration_source_port", None)
            enrollment = getattr(runtime, "_h1_live_completion_enrollment", None)
            if (
                type(port) is not _H1RuntimePreissuancePort
                or port._runtime is not runtime
                or type(enrollment) is not _H1LiveCompletionEnrollment
            ):
                raise H1CompletionPreparationUnavailable(
                    "H1 terminal work requires the mounted P clearance owner"
                )
            enrollment_owner = enrollment
            enrollment_owner._require_live(self, cut)
            request = self._prepare_terminal_work_request(
                preflight=preflight,
                completion_exchange=completion_exchange,
                effects_exchange=effects_exchange,
            )
            engine = runtime._supervisor.runtime()
            guarded_call = getattr(engine, "_call_with_admission_guard", None)
            if not callable(guarded_call):
                raise H1CompletionPreparationUnavailable(
                    "H1 terminal work requires the guarded broker transport"
                )
            callee = engine.session("agent_loop")
            # The P owner read is intentionally call-independent.  Building
            # the terminal frame before that await would spend its ninety-second
            # owner budget while P establishes currentness.
            proof = await port._prepare_terminal_scope_proof(self)
            created_at_ns = time.monotonic_ns()
            sent = PublicPortCall(
                operation_id="agent_loop.prepare_terminal_work",
                request_id="h1-terminal-work:" + secrets.token_hex(24),
                caller=BrokerSession(
                    tenant_id=runtime._tenant_id,
                    broker_epoch=callee.broker_epoch,
                    generation_id=callee.generation_id,
                    owner_id="broker",
                    session_id="broker:" + callee.generation_id,
                ),
                callee=callee,
                schema_id="chiplog.agent-loop.prepare-terminal-work.v1",
                canonical_payload=request.canonical_bytes(),
                budget=CallBudget(
                    remaining_calls=1,
                    remaining_depth=1,
                    policy_version=1,
                    absolute_deadline_ns=created_at_ns + 90_000_000_000,
                ),
            )
            # Binding is synchronous and consumes P's opaque proof before any
            # re-entrant replay.  This final, unmodified object is the one the
            # enrollment guard will admit to the broker.
            clearance = port._bind_terminal_clearance(self, proof, sent)
            if type(clearance) is not _H1TerminalClearance:
                raise H1CompletionPreparationUnavailable("H1 terminal clearance is invalid")
            self._terminal_clearance = clearance
            guard = enrollment_owner._terminal_admission_guard(clearance)
            sent_at_ns = time.monotonic_ns()
            if sent_at_ns >= sent.budget.absolute_deadline_ns:
                raise H1CompletionPreparationUnavailable(
                    "H1 terminal clearance expired before dispatch"
                )
            returned = await guarded_call(
                sent,
                admission_guard=guard,
                authority_gate=runtime._authority_gate(),
            )
            returned_at_ns = time.monotonic_ns()
            if (
                not isinstance(returned, PublicPortSuccess)
                or returned.request_id != sent.request_id
                or returned.responder != callee
                or returned.schema_id != "chiplog.agent-loop.prepared-post-terminal-work-result.v1"
                or returned_at_ns < sent_at_ns
                or returned_at_ns >= sent.budget.absolute_deadline_ns
            ):
                outcome = (
                    returned.failure.kind
                    if isinstance(returned, PublicPortRejected)
                    else type(returned).__name__
                )
                raise H1CompletionPreparationUnavailable(
                    "H1 terminal owner did not return its exact success: "
                    f"{outcome}, elapsed_ns={returned_at_ns - sent_at_ns}, "
                    f"deadline_remaining_ns={sent.budget.absolute_deadline_ns - returned_at_ns}"
                )
            self._validate_terminal_work_result(request, returned.canonical_payload)
            exchange = H1CompletionOwnerExchangeV1(
                role="terminal_work",
                sent=sent,
                returned=returned,
                sent_at_ns=sent_at_ns,
                returned_at_ns=returned_at_ns,
            )
            self._terminal_work_exchange = exchange
            return exchange
        except BaseException:
            if clearance is not None and type(enrollment) is _H1LiveCompletionEnrollment:
                enrollment._burn_terminal_clearance(clearance)
            raise

    @staticmethod
    def _validate_terminal_work_result(
        request: PrepareTerminalWork, returned_payload: bytes
    ) -> PreparedPostTerminalWork:
        """Accept only the fully joined prepared terminal-work result."""
        try:
            result = PreparedPostTerminalWork.model_validate_json(returned_payload)
            if result.canonical_bytes() != returned_payload:
                raise ValueError("noncanonical prepared terminal-work result")
            validate_prepared_post_terminal_work(request, result)
            if result.complete_commitment != prepared_post_terminal_work_commitment(
                request, result.ordered_work, result.complete_records
            ):
                raise ValueError("prepared terminal-work commitment differs")
            return result
        except ValueError as error:
            raise H1CompletionPreparationUnavailable(
                "H1 terminal owner result differs from exact request"
            ) from error

    def check_current(self, cut: object) -> bool:
        """Synchronously revalidate exactly this session's two issuer cuts."""
        if not isinstance(cut, H1CompletionSessionCut) or self._cut is not cut:
            return False
        gate = self._sources._gate
        with gate.hold():
            return (
                self._sources.check_current(cut.first_path) is True
                and cut.conversation is not None
                and self._conversation_sources.check_current(cut.conversation) is True
            )

    def require_current_before_terminal_work(self, cut: object) -> H1CompletionSessionCut:
        """The owner chain must call this before its terminal-work IPC."""
        if not self.check_current(cut):
            raise H1CompletionPreparationUnavailable(
                "H1 completion source or conversation cut is stale"
            )
        assert isinstance(cut, H1CompletionSessionCut)
        return cut
