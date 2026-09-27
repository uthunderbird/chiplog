"""Independent, selected-seal-bounded semantic recovery inputs.

The source owns opaque recovery contexts.  A ROOT is only an equality target:
it never becomes authority to construct a request.  Each stage is rebuilt
from the selected V2 source and issuer-owned predecessor evidence; missing
private owner seams hold recovery instead of admitting a current-path fallback.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Never, cast

from chiplog.capabilities.agent_loop.call_acceptance_contracts import (
    CallSubjectHead,
    SealedResponseRecord,
)
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_completion_preparation import (
    prepare_first_path_execution_completion,
)
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    PrepareExecutionCompletionFirstPathV2,
    first_path_completion_request_fingerprint,
)
from chiplog.capabilities.agent_loop.post_terminal_contracts import (
    PreparedPostTerminalWork,
    PrepareTerminalWork,
)
from chiplog.capabilities.agent_loop.post_terminal_record_contracts import (
    validate_prepared_post_terminal_work,
)
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.terminal_work_preparation import prepare_h1_terminal_work
from chiplog.capabilities.effects.h1_local_preparation import prepare_h1_local_commentary
from chiplog.capabilities.effects.h1_local_preparation_contracts import (
    H1LocalCommentaryOwnerCallV1,
    H1LocalCommentaryRouteV1,
    PreparedH1LocalCommentaryV1,
    PrepareH1LocalCommentaryV1,
)
from chiplog.capabilities.projections.conversation_completion_owner import (
    ConversationCompletionOwner,
)
from chiplog.capabilities.projections.conversation_preparation_contracts import (
    PrepareConversationCompletionV1,
    PreparedConversationCompletionV1,
    conversation_source_request_fingerprint,
)
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_launch_enrollment import EnrolledH1RecoveryMount
from chiplog.composition.h1_postseal_recovery import H1PostSealRecoveryRootV1
from chiplog.composition.h1_recovery_historical_pe_source import (
    H1RecoveryHistoricalPESource,
    H1RecoveryHistoricalPESourceError,
)
from chiplog.composition.h1_v2_recovery_native_source import (
    H1V2RecoveryNativeCut,
    H1V2RecoveryNativeSource,
    H1V2RecoveryNativeSourceError,
)
from chiplog.composition.r14_fanout_contracts import SEAL_SCHEMA


class H1RecoveryStageSourceError(ValueError):
    """The installed native source cannot prove COMPLETION-stage facts."""


class H1RecoveryHistoricalPSourceUnavailable(H1RecoveryStageSourceError):
    """P has not supplied an authenticated historical delivery/fence bridge."""


H1RecoveryStage = Literal["COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK"]

_STAGES: frozenset[str] = frozenset({"COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK"})


@dataclass(frozen=True, slots=True)
class H1RecoveryCompletionNativeFacts:
    """Read-only native facts needed before P may build the completion request."""

    selected_response_seal: CallSubjectHead
    run: ExecutionRunRecord
    selected_attempt: CallSubjectHead
    selector_generation: int
    visibility_manifest: CallSubjectHead
    exact_captured_response: bytes


@dataclass(frozen=True, slots=True)
class _IssuedRecoveryCompletionNative:
    """Identity-held replay token; fields are evidence, never a request authority."""

    original_identity: DriverCommandIdentityV1
    original_fingerprint: str
    selected_seal: CallSubjectHead
    native_cut: H1V2RecoveryNativeCut
    facts: H1RecoveryCompletionNativeFacts


class _IssuedRecoveryStages:
    """Uncopyable issuer-held handle; all state is private to its source."""

    __slots__ = ()

    def __init__(self) -> None:
        raise TypeError("recovery stage contexts are issued only by their source")

    def __copy__(self) -> Never:
        raise H1RecoveryStageSourceError("recovery stage context is not issuer-owned")

    def __deepcopy__(self, memo: object) -> Never:
        del memo
        raise H1RecoveryStageSourceError("recovery stage context is not issuer-owned")

    def __reduce__(self) -> Never:
        raise H1RecoveryStageSourceError("recovery stage context is not issuer-owned")


@dataclass(frozen=True, slots=True)
class _AuthenticatedHistoricalCompletion:
    """B-owned inert evidence for one validated historical completion result.

    Its fields are deliberately not a capability.  The issuer table below is
    the authority boundary; A must replay this exact identity through the
    mounted coordinator source before using it.
    """

    request_bytes: bytes
    result_bytes: bytes
    native: H1V2RecoveryNativeCut


@dataclass(frozen=True, slots=True)
class _AuthenticatedHistoricalConversation:
    """B-owned inert evidence for one validated historical conversation result."""

    request_bytes: bytes
    result_bytes: bytes
    completion: _AuthenticatedHistoricalCompletion


@dataclass(frozen=True, slots=True)
class _AuthenticatedHistoricalEffects:
    """B-owned inert evidence for one validated historical Effects result."""

    request_bytes: bytes
    result_bytes: bytes
    completion: _AuthenticatedHistoricalCompletion
    conversation: _AuthenticatedHistoricalConversation


@dataclass(slots=True)
class _RecoveryStageState:
    context: _IssuedRecoveryStages
    root: H1PostSealRecoveryRootV1
    native: _IssuedRecoveryCompletionNative
    completion_input: bytes
    p_source_cap: object | None = None
    accepted_completion: _AuthenticatedHistoricalCompletion | None = None
    accepted_conversation: _AuthenticatedHistoricalConversation | None = None
    accepted_effects: _AuthenticatedHistoricalEffects | None = None


class H1RecoveryStageSource:
    """Issue and replay native, selected-seal-bounded COMPLETION facts."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("recovery stage source requires the installed runtime")
        mount = getattr(runtime, "_h1_recovery_mount", None)
        if type(mount) is not EnrolledH1RecoveryMount:
            raise H1RecoveryStageSourceError("recovery stage source requires an enrolled mount")
        gate = runtime._authority_gate()
        if mount.authority_gate is not gate:
            raise H1RecoveryStageSourceError("recovery stage source mount gate differs")
        self._runtime = runtime
        self._native = H1V2RecoveryNativeSource(runtime)
        self._issued: dict[int, _IssuedRecoveryCompletionNative] = {}
        self._recovery_contexts: dict[int, _RecoveryStageState] = {}
        self._accepted_completions: dict[int, _AuthenticatedHistoricalCompletion] = {}
        self._accepted_conversations: dict[int, _AuthenticatedHistoricalConversation] = {}
        self._accepted_effects: dict[int, _AuthenticatedHistoricalEffects] = {}

    def _capture_recovery(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
        root: H1PostSealRecoveryRootV1,
    ) -> object:
        """Capture one source-owned semantic reconstruction context.

        The complete historical request is rebuilt at capture, then rebuilt
        again before every use.  ``root`` is compared as a complete immutable
        target; no field of it is treated as a source of request facts.
        """
        self._validate_inputs(original_identity, original_fingerprint, selected_seal)
        self._require_root_target(root, original_identity, original_fingerprint, selected_seal)
        native = self.issue_completion_native(
            original_identity=original_identity,
            original_fingerprint=original_fingerprint,
            selected_seal=selected_seal,
        )
        completion_input = self._historical_completion_bytes(
            original_identity, original_fingerprint, selected_seal
        )
        context = object.__new__(_IssuedRecoveryStages)
        self._recovery_contexts[id(context)] = _RecoveryStageState(
            context, root, native, completion_input
        )
        return context

    def _reconstruct_input(
        self,
        context: object,
        stage: H1RecoveryStage,
        predecessor_results: Mapping[H1RecoveryStage, bytes],
        effects_command_id: str | None,
        *,
        predecessor_effects_input: tuple[bytes, str] | None = None,
    ) -> bytes:
        """Return canonical semantic bytes or fail closed without owner IPC."""
        state = self._context_state(context)
        self._require_current(context)
        self._require_stage(stage)
        self._require_predecessor_chain(stage, predecessor_results)
        if stage != "TERMINAL_WORK" and predecessor_effects_input is not None:
            raise H1RecoveryStageSourceError("unexpected historical effects predecessor input")
        if stage == "COMPLETION":
            if predecessor_results or effects_command_id is not None:
                raise H1RecoveryStageSourceError(
                    "completion stage has unexpected predecessor evidence"
                )
            return state.completion_input
        completion = self._accepted_completion_from_predecessor(state, predecessor_results)
        if stage == "CONVERSATION":
            return self._historical_conversation_bytes(state, completion)
        conversation = self._accepted_conversation_from_predecessor(
            state, predecessor_results, completion
        )
        if stage == "EFFECTS":
            derived_command_id = self._historical_effects_command_id(
                state, completion, conversation
            )
            if effects_command_id is not None and effects_command_id != derived_command_id:
                raise H1RecoveryStageSourceError("historical effects command ID differs")
            return self._historical_effects_bytes(
                state, completion, conversation, derived_command_id
            )
        self._validate_effects_predecessor(
            state, completion, conversation, predecessor_results, predecessor_effects_input
        )
        effects = self._accepted_effects_from_predecessor(
            state, predecessor_results, completion, conversation
        )
        return self._historical_terminal_work_bytes(state, completion, conversation, effects)

    @staticmethod
    def _frame_historical_effects_seed(value: bytes) -> bytes:
        return len(value).to_bytes(8, "big") + value

    def _historical_effects_command_id(
        self,
        state: _RecoveryStageState,
        completion: _AuthenticatedHistoricalCompletion,
        conversation: _AuthenticatedHistoricalConversation,
    ) -> str:
        seed = b"chiplog.h1.postseal-recovery.effects-command.v1\0"
        for value in (
            state.root.root_id().encode("ascii"),
            completion.request_bytes,
            completion.result_bytes,
            conversation.request_bytes,
            conversation.result_bytes,
        ):
            seed += self._frame_historical_effects_seed(value)
        return "h1-effects:" + hashlib.sha256(seed).hexdigest()[:48]

    def _validate_effects_predecessor(
        self,
        state: _RecoveryStageState,
        completion: _AuthenticatedHistoricalCompletion,
        conversation: _AuthenticatedHistoricalConversation,
        predecessor_results: Mapping[H1RecoveryStage, bytes],
        predecessor_effects_input: tuple[bytes, str] | None,
    ) -> None:
        if not isinstance(predecessor_effects_input, tuple) or len(predecessor_effects_input) != 2:
            raise H1RecoveryStageSourceError("historical effects predecessor input is unavailable")
        semantic_input, effects_command_id = predecessor_effects_input
        if not isinstance(semantic_input, bytes) or not isinstance(effects_command_id, str):
            raise H1RecoveryStageSourceError("historical effects predecessor input is invalid")
        try:
            request = PrepareH1LocalCommentaryV1.model_validate_json(semantic_input)
        except ValueError as error:
            raise H1RecoveryStageSourceError(
                "historical effects predecessor input is invalid"
            ) from error
        if (
            request.canonical_bytes() != semantic_input
            or request.identity.command_id != effects_command_id
            or effects_command_id
            != self._historical_effects_command_id(state, completion, conversation)
            or self._historical_effects_bytes(state, completion, conversation, effects_command_id)
            != semantic_input
        ):
            raise H1RecoveryStageSourceError("historical effects predecessor input differs")
        result = predecessor_results["EFFECTS"]
        self._validate_effects_result(state, completion, conversation, semantic_input, result)

    def _validate_result(
        self,
        context: object,
        stage: H1RecoveryStage,
        semantic_input: bytes,
        result_bytes: bytes,
    ) -> None:
        """Purely verify a returned canonical result against one pinned input."""
        state = self._context_state(context)
        self._require_stage(stage)
        if not isinstance(semantic_input, bytes) or not isinstance(result_bytes, bytes):
            raise TypeError("recovery stage semantic input and result must be bytes")
        if stage == "COMPLETION":
            if semantic_input != state.completion_input:
                raise H1RecoveryStageSourceError(
                    "completion semantic input differs from selected source"
                )
            completion = self._validate_completion_result(state, semantic_input, result_bytes)
            if state.accepted_completion is not None:
                self._accepted_completions.pop(id(state.accepted_completion), None)
            state.accepted_completion = completion
            return
        completion = self._accepted_completion_from_predecessor(state, {})
        if stage == "CONVERSATION":
            receipt = self._validate_conversation_result(
                state, completion, semantic_input, result_bytes
            )
            if state.accepted_conversation is not None:
                self._accepted_conversations.pop(id(state.accepted_conversation), None)
            state.accepted_conversation = receipt
            return
        conversation = self._accepted_conversation_from_predecessor(state, {}, completion)
        if stage == "EFFECTS":
            self._validate_effects_result(
                state, completion, conversation, semantic_input, result_bytes
            )
            return
        effects = self._accepted_effects_from_predecessor(state, {}, completion, conversation)
        self._validate_terminal_work_result(
            state, completion, conversation, effects, semantic_input, result_bytes
        )

    def _validate_completion_result(
        self,
        state: _RecoveryStageState,
        semantic_input: bytes,
        result_bytes: bytes,
    ) -> _AuthenticatedHistoricalCompletion:
        try:
            request = PrepareExecutionCompletionFirstPathV2.model_validate_json(semantic_input)
            result = PreparedExecutionCompletion.model_validate_json(result_bytes)
            expected = prepare_first_path_execution_completion(request)
        except ValueError as error:
            raise H1RecoveryStageSourceError("completion result is invalid") from error
        if (
            request.canonical_bytes() != semantic_input
            or result.canonical_bytes() != result_bytes
            or not isinstance(expected, PreparedExecutionCompletion)
            or expected.canonical_bytes() != result_bytes
            or result.source_request_fingerprint
            != first_path_completion_request_fingerprint(request)
        ):
            raise H1RecoveryStageSourceError("completion result differs from semantic input")
        issued = _AuthenticatedHistoricalCompletion(
            request_bytes=semantic_input,
            result_bytes=result_bytes,
            native=state.native.native_cut,
        )
        self._accepted_completions[id(issued)] = issued
        return issued

    def _replay_accepted_completion(
        self,
        accepted_completion: object,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> _AuthenticatedHistoricalCompletion:
        """Return only an exact, still-valid B historical-completion receipt."""
        if (
            type(accepted_completion) is not _AuthenticatedHistoricalCompletion
            or self._accepted_completions.get(id(accepted_completion)) is not accepted_completion
        ):
            raise H1RecoveryStageSourceError("historical completion receipt is not issuer-owned")
        self._validate_inputs(original_identity, original_fingerprint, selected_seal)
        receipt = cast(_AuthenticatedHistoricalCompletion, accepted_completion)
        try:
            fresh = self._native.select(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
            )
        except H1V2RecoveryNativeSourceError as error:
            raise H1RecoveryStageSourceError("historical completion source differs") from error
        if fresh != receipt.native:
            raise H1RecoveryStageSourceError("historical completion native source differs")
        try:
            request = PrepareExecutionCompletionFirstPathV2.model_validate_json(
                receipt.request_bytes
            )
            result = PreparedExecutionCompletion.model_validate_json(receipt.result_bytes)
            expected = prepare_first_path_execution_completion(request)
        except ValueError as error:
            raise H1RecoveryStageSourceError("historical completion receipt is invalid") from error
        if (
            request.canonical_bytes() != receipt.request_bytes
            or result.canonical_bytes() != receipt.result_bytes
            or not isinstance(expected, PreparedExecutionCompletion)
            or expected.canonical_bytes() != receipt.result_bytes
            or result.source_request_fingerprint
            != first_path_completion_request_fingerprint(request)
        ):
            raise H1RecoveryStageSourceError("historical completion receipt differs on replay")
        return receipt

    def _accepted_completion_from_predecessor(
        self,
        state: _RecoveryStageState,
        predecessor_results: Mapping[H1RecoveryStage, bytes],
    ) -> _AuthenticatedHistoricalCompletion:
        raw = predecessor_results.get("COMPLETION")
        if raw is not None:
            if not isinstance(raw, bytes):
                raise TypeError("completion predecessor result must be bytes")
            if state.accepted_completion is None or state.accepted_completion.result_bytes != raw:
                receipt = self._validate_completion_result(state, state.completion_input, raw)
                if state.accepted_completion is not None:
                    self._accepted_completions.pop(id(state.accepted_completion), None)
                state.accepted_completion = receipt
        if state.accepted_completion is None:
            raise H1RecoveryStageSourceError("validated completion predecessor is unavailable")
        return self._replay_accepted_completion(
            state.accepted_completion,
            original_identity=state.native.original_identity,
            original_fingerprint=state.native.original_fingerprint,
            selected_seal=state.native.selected_seal,
        )

    def _issue_historical_p_source(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> object:
        port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
        issue = getattr(port, "_issue_historical_recovery_source", None)
        if not callable(issue):
            raise H1RecoveryHistoricalPSourceUnavailable("historical P source is unavailable")
        try:
            return issue(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
            )
        except (RuntimeError, TypeError, ValueError) as error:
            raise H1RecoveryHistoricalPSourceUnavailable(
                "historical P source is unavailable"
            ) from error

    def _historical_p_cap(self, state: _RecoveryStageState) -> object:
        if state.p_source_cap is None:
            state.p_source_cap = self._issue_historical_p_source(
                original_identity=state.native.original_identity,
                original_fingerprint=state.native.original_fingerprint,
                selected_seal=state.native.selected_seal,
            )
        return state.p_source_cap

    def _historical_conversation_bytes(
        self, state: _RecoveryStageState, completion: _AuthenticatedHistoricalCompletion
    ) -> bytes:
        """Delegate historical conversation construction to installed A only."""
        cap = self._historical_p_cap(state)
        port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
        replay = getattr(port, "_replay_historical_conversation_policy", None)
        owner = getattr(self._runtime, "_h1_conversation_source_port", None)
        build = getattr(owner, "_prepare_historical_conversation_completion_request", None)
        if not callable(replay) or not callable(build):
            raise H1RecoveryHistoricalPSourceUnavailable(
                "historical conversation source is unavailable"
            )
        try:
            replay(cap)
            request = build(
                original_identity=state.native.original_identity,
                original_fingerprint=state.native.original_fingerprint,
                selected_seal=state.native.selected_seal,
                p_source_cap=cap,
                accepted_completion=completion,
            )
            if type(request) is not PrepareConversationCompletionV1:
                raise TypeError("historical conversation request differs")
            raw = request.canonical_json_bytes()
            if (
                PrepareConversationCompletionV1.model_validate_json(raw).canonical_json_bytes()
                != raw
            ):
                raise ValueError("historical conversation request is noncanonical")
            return raw
        except H1RecoveryStageSourceError:
            raise
        except (RuntimeError, TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("historical conversation source differs") from error

    def _accepted_conversation_from_predecessor(
        self,
        state: _RecoveryStageState,
        predecessor_results: Mapping[H1RecoveryStage, bytes],
        completion: _AuthenticatedHistoricalCompletion,
    ) -> _AuthenticatedHistoricalConversation:
        raw = predecessor_results.get("CONVERSATION")
        expected = self._historical_conversation_bytes(state, completion)
        if raw is not None:
            if not isinstance(raw, bytes):
                raise TypeError("conversation predecessor result must be bytes")
            if (
                state.accepted_conversation is None
                or state.accepted_conversation.result_bytes != raw
                or state.accepted_conversation.completion is not completion
            ):
                receipt = self._validate_conversation_result(state, completion, expected, raw)
                if state.accepted_conversation is not None:
                    self._accepted_conversations.pop(id(state.accepted_conversation), None)
                state.accepted_conversation = receipt
        if state.accepted_conversation is None:
            raise H1RecoveryStageSourceError("validated conversation predecessor is unavailable")
        return self._replay_accepted_conversation(
            state.accepted_conversation, state, completion, expected
        )

    def _historical_effects_bytes(
        self,
        state: _RecoveryStageState,
        completion: _AuthenticatedHistoricalCompletion,
        conversation: _AuthenticatedHistoricalConversation,
        effects_command_id: str | None,
    ) -> bytes:
        if not isinstance(effects_command_id, str) or not effects_command_id:
            raise H1RecoveryStageSourceError("historical effects command ID is unavailable")
        cap = self._historical_p_cap(state)
        port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
        replay = getattr(port, "_replay_historical_effects_source", None)
        if not callable(replay):
            raise H1RecoveryHistoricalPSourceUnavailable("historical effects source is unavailable")
        try:
            p_effects_source = replay(cap)
            completion_request = PrepareExecutionCompletionFirstPathV2.model_validate_json(
                completion.request_bytes
            )
            completion_result = PreparedExecutionCompletion.model_validate_json(
                completion.result_bytes
            )
            conversation_request = PrepareConversationCompletionV1.model_validate_json(
                conversation.request_bytes
            )
            conversation_result = PreparedConversationCompletionV1.model_validate_json(
                conversation.result_bytes
            )
            from chiplog.composition.h1_completion_preparation_session import (
                H1CompletionPreparationSession,
            )

            build = getattr(
                H1CompletionPreparationSession, "_build_historical_effects_request", None
            )
            if not callable(build):
                raise TypeError("historical effects request builder is unavailable")
            request = build(
                completion_request=completion_request,
                completion_result=completion_result,
                conversation_request=conversation_request,
                conversation_result=conversation_result,
                p_effects_source=p_effects_source,
                fence=completion_request.fence,
                effects_command_id=effects_command_id,
            )
            if type(request) is not PrepareH1LocalCommentaryV1:
                raise TypeError("historical effects request differs")
            raw = request.canonical_bytes()
            if (
                PrepareH1LocalCommentaryV1.model_validate_json(raw).canonical_bytes() != raw
                or request.identity.command_id != effects_command_id
            ):
                raise ValueError("historical effects request is noncanonical")
            return raw
        except H1RecoveryStageSourceError:
            raise
        except (RuntimeError, TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("historical effects source differs") from error

    def _accepted_effects_from_predecessor(
        self,
        state: _RecoveryStageState,
        predecessor_results: Mapping[H1RecoveryStage, bytes],
        completion: _AuthenticatedHistoricalCompletion,
        conversation: _AuthenticatedHistoricalConversation,
    ) -> _AuthenticatedHistoricalEffects:
        raw = predecessor_results.get("EFFECTS")
        if raw is not None and not isinstance(raw, bytes):
            raise TypeError("effects predecessor result must be bytes")
        if (
            state.accepted_effects is not None
            and (raw is None or state.accepted_effects.result_bytes == raw)
            and state.accepted_effects.completion is completion
            and state.accepted_effects.conversation is conversation
        ):
            return state.accepted_effects
        raise H1RecoveryHistoricalPSourceUnavailable(
            "historical effects input must retain its command ID"
        )

    def _historical_terminal_work_bytes(
        self,
        state: _RecoveryStageState,
        completion: _AuthenticatedHistoricalCompletion,
        conversation: _AuthenticatedHistoricalConversation,
        effects: _AuthenticatedHistoricalEffects,
    ) -> bytes:
        del state, conversation
        try:
            completion_request = PrepareExecutionCompletionFirstPathV2.model_validate_json(
                completion.request_bytes
            )
            completion_result = PreparedExecutionCompletion.model_validate_json(
                completion.result_bytes
            )
            effects_request = PrepareH1LocalCommentaryV1.model_validate_json(effects.request_bytes)
            effects_result = PreparedH1LocalCommentaryV1.model_validate_json(effects.result_bytes)
            from chiplog.composition.h1_completion_preparation_session import (
                H1CompletionPreparationSession,
            )

            request = H1CompletionPreparationSession._build_historical_terminal_work_request(
                completion_request=completion_request,
                completion_result=completion_result,
                effects_request=effects_request,
                effects_result=effects_result,
            )
            if type(request) is not PrepareTerminalWork:
                raise TypeError("historical terminal-work request differs")
            raw = request.canonical_bytes()
            if PrepareTerminalWork.model_validate_json(raw).canonical_bytes() != raw:
                raise ValueError("historical terminal-work request is noncanonical")
            return raw
        except (RuntimeError, TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("historical terminal-work source differs") from error

    def _validate_conversation_result(
        self,
        state: _RecoveryStageState,
        completion: _AuthenticatedHistoricalCompletion,
        semantic_input: bytes,
        result_bytes: bytes,
    ) -> _AuthenticatedHistoricalConversation:
        expected_input = self._historical_conversation_bytes(state, completion)
        if semantic_input != expected_input:
            raise H1RecoveryStageSourceError(
                "conversation semantic input differs from selected source"
            )
        try:
            request = PrepareConversationCompletionV1.model_validate_json(semantic_input)
            result = PreparedConversationCompletionV1.model_validate_json(result_bytes)
            expected = ConversationCompletionOwner().prepare_completion(request)
        except ValueError as error:
            raise H1RecoveryStageSourceError("conversation result is invalid") from error
        if (
            request.canonical_json_bytes() != semantic_input
            or result.canonical_json_bytes() != result_bytes
            or not isinstance(expected, PreparedConversationCompletionV1)
            or expected.canonical_json_bytes() != result_bytes
            or result.source_request_fingerprint != conversation_source_request_fingerprint(request)
        ):
            raise H1RecoveryStageSourceError("conversation result differs from semantic input")
        issued = _AuthenticatedHistoricalConversation(semantic_input, result_bytes, completion)
        self._accepted_conversations[id(issued)] = issued
        return issued

    def _replay_accepted_conversation(
        self,
        accepted_conversation: object,
        state: _RecoveryStageState,
        completion: _AuthenticatedHistoricalCompletion,
        expected_input: bytes,
    ) -> _AuthenticatedHistoricalConversation:
        if (
            type(accepted_conversation) is not _AuthenticatedHistoricalConversation
            or self._accepted_conversations.get(id(accepted_conversation))
            is not accepted_conversation
        ):
            raise H1RecoveryStageSourceError("historical conversation receipt is not issuer-owned")
        receipt = cast(_AuthenticatedHistoricalConversation, accepted_conversation)
        if receipt.completion is not completion or receipt.request_bytes != expected_input:
            raise H1RecoveryStageSourceError("historical conversation receipt differs")
        replayed = self._validate_conversation_result(
            state, completion, receipt.request_bytes, receipt.result_bytes
        )
        self._accepted_conversations.pop(id(replayed), None)
        if replayed != receipt:
            raise H1RecoveryStageSourceError("historical conversation receipt differs on replay")
        return receipt

    def _validate_effects_result(
        self,
        state: _RecoveryStageState,
        completion: _AuthenticatedHistoricalCompletion,
        conversation: _AuthenticatedHistoricalConversation,
        semantic_input: bytes,
        result_bytes: bytes,
    ) -> None:
        try:
            request = PrepareH1LocalCommentaryV1.model_validate_json(semantic_input)
            expected_input = self._historical_effects_bytes(
                state,
                completion,
                conversation,
                request.identity.command_id,
            )
            route = H1LocalCommentaryRouteV1(
                tenant_id=request.original_completion_request.run.tenant,
                database_id=request.original_completion_request.source.database_id,
                worker_session_id=request.original_completion_request.run.worker_session,
                broker_epoch=0,
                runtime_generation="historical-recovery",
                broker_session_id="historical-recovery-broker",
                owner_session_id="historical-recovery-effects",
                request_id=request.identity.command_id,
            )
            call = H1LocalCommentaryOwnerCallV1(
                route=route,
                request=request,
                request_digest=hashlib.sha256(semantic_input).hexdigest(),
            )
            result = PreparedH1LocalCommentaryV1.model_validate_json(result_bytes)
            expected = prepare_h1_local_commentary(call)
        except ValueError as error:
            raise H1RecoveryStageSourceError("effects result is invalid") from error
        if (
            request.canonical_bytes() != semantic_input
            or request.identity.command_id
            != self._historical_effects_command_id(state, completion, conversation)
            or expected_input != semantic_input
            or result.canonical_bytes() != result_bytes
            or not isinstance(expected, PreparedH1LocalCommentaryV1)
            or expected.canonical_bytes() != result_bytes
            or result.source_request_fingerprint != hashlib.sha256(semantic_input).hexdigest()
        ):
            raise H1RecoveryStageSourceError("effects result differs from semantic input")
        issued = _AuthenticatedHistoricalEffects(
            semantic_input, result_bytes, completion, conversation
        )
        self._accepted_effects[id(issued)] = issued
        if state.accepted_effects is not None:
            self._accepted_effects.pop(id(state.accepted_effects), None)
        state.accepted_effects = issued

    def _validate_terminal_work_result(
        self,
        state: _RecoveryStageState,
        completion: _AuthenticatedHistoricalCompletion,
        conversation: _AuthenticatedHistoricalConversation,
        effects: _AuthenticatedHistoricalEffects,
        semantic_input: bytes,
        result_bytes: bytes,
    ) -> None:
        expected_input = self._historical_terminal_work_bytes(
            state, completion, conversation, effects
        )
        if semantic_input != expected_input:
            raise H1RecoveryStageSourceError(
                "terminal-work semantic input differs from selected source"
            )
        try:
            request = PrepareTerminalWork.model_validate_json(semantic_input)
            result = PreparedPostTerminalWork.model_validate_json(result_bytes)
            expected = prepare_h1_terminal_work(request)
            validate_prepared_post_terminal_work(request, result)
        except ValueError as error:
            raise H1RecoveryStageSourceError("terminal-work result is invalid") from error
        if (
            request.canonical_bytes() != semantic_input
            or result.canonical_bytes() != result_bytes
            or not isinstance(expected, PreparedPostTerminalWork)
            or expected.canonical_bytes() != result_bytes
        ):
            raise H1RecoveryStageSourceError("terminal-work result differs from semantic input")

    def _require_current(self, context: object) -> None:
        """Re-select the full V2 cut; ROOT itself is never currentness evidence."""
        state = self._context_state(context)
        self.replay_completion_native(state.native)
        rebuilt = self._historical_completion_bytes(
            state.native.original_identity,
            state.native.original_fingerprint,
            state.native.selected_seal,
        )
        if rebuilt != state.completion_input:
            raise H1RecoveryStageSourceError("selected historical completion input differs")
        if state.p_source_cap is not None:
            port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
            replay_conversation = getattr(port, "_replay_historical_conversation_policy", None)
            replay_effects = getattr(port, "_replay_historical_effects_source", None)
            if not callable(replay_conversation) or not callable(replay_effects):
                raise H1RecoveryHistoricalPSourceUnavailable(
                    "historical P source is no longer available"
                )
            try:
                replay_conversation(state.p_source_cap)
                replay_effects(state.p_source_cap)
            except (RuntimeError, TypeError, ValueError) as error:
                raise H1RecoveryHistoricalPSourceUnavailable(
                    "historical P source differs on replay"
                ) from error

    def _context_state(self, context: object) -> _RecoveryStageState:
        state = self._recovery_contexts.get(id(context))
        if (
            type(context) is not _IssuedRecoveryStages
            or state is None
            or state.context is not context
        ):
            raise H1RecoveryStageSourceError("recovery stage context is not issuer-owned")
        return state

    def _retire_recovery_context(self, context: object) -> None:
        """Burn one context and every opaque receipt it made reachable.

        The coordinator calls this only after its B session has drained.  A
        receipt is runtime-local evidence, so it must not outlive the recovery
        context that bound its selected native cut and lease.
        """
        state = self._context_state(context)
        accepted = state.accepted_completion
        if accepted is not None:
            self._accepted_completions.pop(id(accepted), None)
        conversation = state.accepted_conversation
        if conversation is not None:
            self._accepted_conversations.pop(id(conversation), None)
        effects = state.accepted_effects
        if effects is not None:
            self._accepted_effects.pop(id(effects), None)
        if state.p_source_cap is not None:
            port = getattr(self._runtime, "_h1_preissuance_registration_source_port", None)
            revoke = getattr(port, "_revoke_historical_recovery_source", None)
            if not callable(revoke):
                raise H1RecoveryHistoricalPSourceUnavailable(
                    "historical P source cannot be retired"
                )
            try:
                revoke(state.p_source_cap)
            except (RuntimeError, TypeError, ValueError) as error:
                raise H1RecoveryHistoricalPSourceUnavailable(
                    "historical P source cannot be retired"
                ) from error
        self._issued.pop(id(state.native), None)
        self._recovery_contexts.pop(id(context), None)

    @staticmethod
    def _require_stage(stage: object) -> None:
        if not isinstance(stage, str) or stage not in _STAGES:
            raise H1RecoveryStageSourceError("recovery stage differs from the fixed order")

    @staticmethod
    def _require_predecessor_chain(
        stage: H1RecoveryStage, predecessor_results: Mapping[H1RecoveryStage, bytes]
    ) -> None:
        expected: dict[H1RecoveryStage, frozenset[H1RecoveryStage]] = {
            "COMPLETION": frozenset(),
            "CONVERSATION": frozenset({"COMPLETION"}),
            "EFFECTS": frozenset({"COMPLETION", "CONVERSATION"}),
            "TERMINAL_WORK": frozenset({"COMPLETION", "CONVERSATION", "EFFECTS"}),
        }
        if set(predecessor_results) != expected[stage] or not all(
            isinstance(value, bytes) for value in predecessor_results.values()
        ):
            raise H1RecoveryStageSourceError("recovery predecessor chain differs")

    def _historical_completion_bytes(
        self,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> bytes:
        try:
            reader = H1RecoveryHistoricalPESource(self._runtime)
            receipt = reader.issue_completion_projection(
                original_identity=original_identity,
                original_fingerprint=original_fingerprint,
                selected_seal=selected_seal,
            )
            request = reader.reconstruct_completion_input(receipt)
            raw = request.canonical_bytes()
            if (
                PrepareExecutionCompletionFirstPathV2.model_validate_json(raw).canonical_bytes()
                != raw
            ):
                raise H1RecoveryStageSourceError("historical completion input is noncanonical")
            return raw
        except H1RecoveryStageSourceError:
            raise
        except H1RecoveryHistoricalPESourceError as error:
            raise H1RecoveryHistoricalPSourceUnavailable(
                "historical P/E completion source is unavailable or invalid"
            ) from error

    @staticmethod
    def _require_root_target(
        root: object,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> None:
        if type(root) is not H1PostSealRecoveryRootV1:
            raise TypeError("recovery stage source requires the exact selected ROOT")
        if (
            root.tenant_id != original_identity.tenant_id
            or root.database_id != original_identity.database_id
            or root.original_command_id != original_identity.driver_command_id
            or root.original_command_fingerprint != original_fingerprint
            or root.selected_seal_subject_id != selected_seal.subject_id
            or root.selected_seal_head != selected_seal.revision.head
            or root.selected_seal_fingerprint != selected_seal.revision.fingerprint
        ):
            raise H1RecoveryStageSourceError("recovery ROOT differs from selected native source")

    def issue_completion_native(
        self,
        *,
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> _IssuedRecoveryCompletionNative:
        """Issue facts after independently selecting the installed V2 native cut."""
        self._validate_inputs(original_identity, original_fingerprint, selected_seal)
        try:
            with self._runtime._authority_gate().hold():
                native = self._native.select(
                    original_identity=original_identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=selected_seal,
                )
                facts = self._facts(native, selected_seal)
                issued = _IssuedRecoveryCompletionNative(
                    original_identity, original_fingerprint, selected_seal, native, facts
                )
                self._issued[id(issued)] = issued
                return issued
        except H1RecoveryStageSourceError:
            raise
        except H1V2RecoveryNativeSourceError as error:
            raise H1RecoveryStageSourceError("selected native source differs") from error
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("selected native source differs") from error

    def replay_completion_native(
        self, issued: _IssuedRecoveryCompletionNative
    ) -> H1RecoveryCompletionNativeFacts:
        """Re-select all native evidence; copied tokens and changed cuts fail closed."""
        if (
            type(issued) is not _IssuedRecoveryCompletionNative
            or self._issued.get(id(issued)) is not issued
        ):
            raise H1RecoveryStageSourceError("completion native token is not issuer-owned")
        try:
            with self._runtime._authority_gate().hold():
                fresh = self._native.select(
                    original_identity=issued.original_identity,
                    original_fingerprint=issued.original_fingerprint,
                    selected_seal=issued.selected_seal,
                )
                facts = self._facts(fresh, issued.selected_seal)
                if fresh != issued.native_cut or facts != issued.facts:
                    raise H1RecoveryStageSourceError("selected native source differs on replay")
                return facts
        except H1RecoveryStageSourceError:
            raise
        except H1V2RecoveryNativeSourceError as error:
            raise H1RecoveryStageSourceError("selected native source differs on replay") from error
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("selected native source differs on replay") from error

    def reconstruct_completion_input(
        self, issued: _IssuedRecoveryCompletionNative
    ) -> PrepareExecutionCompletionFirstPathV2:
        """Re-open the full selected historical P/E cut, never a live P receipt."""
        self.replay_completion_native(issued)
        raw = self._historical_completion_bytes(
            issued.original_identity, issued.original_fingerprint, issued.selected_seal
        )
        try:
            return PrepareExecutionCompletionFirstPathV2.model_validate_json(raw)
        except ValueError as error:
            raise H1RecoveryStageSourceError("historical completion input is invalid") from error

    @staticmethod
    def _validate_inputs(
        original_identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead,
    ) -> None:
        if type(original_identity) is not DriverCommandIdentityV1:
            raise TypeError("recovery stage source requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1RecoveryStageSourceError("original driver fingerprint is invalid")
        if type(selected_seal) is not CallSubjectHead:
            raise TypeError("recovery stage source requires an exact selected seal")

    @staticmethod
    def _facts(
        native: H1V2RecoveryNativeCut, selected_seal: CallSubjectHead
    ) -> H1RecoveryCompletionNativeFacts:
        if type(native) is not H1V2RecoveryNativeCut:
            raise H1RecoveryStageSourceError("selected native cut differs")
        source = native.source
        lineage = source.complete_ordered_run_lineage
        if len(lineage) < 3:
            raise H1RecoveryStageSourceError("selected native Run lineage is incomplete")
        captured, run = lineage[-2:]
        if (
            source.selected_response_seal != selected_seal
            or run.event != "ModelCompletionPrepared"
            or captured.event != "ModelResponseCaptured"
            or len(run.turns) != 1
            or len(captured.turns) != 1
        ):
            raise H1RecoveryStageSourceError("selected native Run or capture differs")
        turn, captured_turn = run.turns[0], captured.turns[0]
        if (
            turn.turn_id != captured_turn.turn_id
            or turn.attempts != captured_turn.attempts
            or turn.selector != captured_turn.selector
            or len(turn.attempts) != 1
            or turn.response_seal != selected_seal
        ):
            raise H1RecoveryStageSourceError("selected native attempt or seal differs")
        attempt = turn.attempts[0]
        if attempt.response_base64 is None:
            raise H1RecoveryStageSourceError("selected native response is absent")
        try:
            response = base64.b64decode(attempt.response_base64, validate=True)
        except (TypeError, ValueError) as error:
            raise H1RecoveryStageSourceError("selected native response is invalid") from error
        manifest = attempt.manifest
        selected_attempt = CallSubjectHead(
            subject_id=attempt.attempt_id,
            revision=Present(head=attempt.head, fingerprint=attempt.digest()),
        )
        visibility_manifest = CallSubjectHead(
            subject_id="visibility",
            revision=Present(head="record:" + manifest.digest(), fingerprint=manifest.digest()),
        )
        H1RecoveryStageSource._require_physical_seal(native, selected_seal)
        return H1RecoveryCompletionNativeFacts(
            selected_seal,
            run,
            selected_attempt,
            attempt.generation,
            visibility_manifest,
            response,
        )

    @staticmethod
    def _require_physical_seal(
        native: H1V2RecoveryNativeCut, selected_seal: CallSubjectHead
    ) -> None:
        matches = [
            member
            for member in native.physical_members
            if member.schema_id == SEAL_SCHEMA
            and member.record_id == selected_seal.revision.head
            and hashlib.sha256(member.canonical_bytes).hexdigest()
            == selected_seal.revision.fingerprint
        ]
        if len(matches) != 1 or selected_seal.revision.head != (
            "record:" + selected_seal.revision.fingerprint
        ):
            raise H1RecoveryStageSourceError("selected native physical seal differs")
        try:
            seal = SealedResponseRecord.model_validate_json(matches[0].canonical_bytes)
        except ValueError as error:
            raise H1RecoveryStageSourceError("selected native physical seal is invalid") from error
        if (
            seal.canonical_bytes() != matches[0].canonical_bytes
            or seal.response_seal_id != selected_seal.subject_id
        ):
            raise H1RecoveryStageSourceError("selected native physical seal differs")


__all__ = [
    "H1RecoveryCompletionNativeFacts",
    "H1RecoveryHistoricalPSourceUnavailable",
    "H1RecoveryStage",
    "H1RecoveryStageSource",
    "H1RecoveryStageSourceError",
]
