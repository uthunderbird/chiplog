"""Durable, fence-owned H1 post-seal preparation recovery.

The recovery journal is the boundary around every owner preparation.  This
module never accepts a caller-shaped stage request or result: it reconstructs
the bytes from the selected native root, durably pins them, and only then lets
an enrolled fresh B session make an inert owner call.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryRecordV1,
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
)
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_recovery_execution_fence import (
    _H1RecoveryExecutionFence,
    _H1RecoveryExecutionLease,
)
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


class H1PostSealRecoveryCoordinatorError(RuntimeError):
    """The installed seal cannot safely enter the recovery journal."""


class H1PostSealRecoveryPublicationIntegrityError(H1PostSealRecoveryCoordinatorError):
    """A finalization broker result is outside its exact public contract."""


_H1RecoveryStage = Literal["COMPLETION", "CONVERSATION", "EFFECTS", "TERMINAL_WORK"]
_STAGES: Final[tuple[_H1RecoveryStage, ...]] = (
    "COMPLETION",
    "CONVERSATION",
    "EFFECTS",
    "TERMINAL_WORK",
)
_PREPARE_METHODS: Final[dict[_H1RecoveryStage, str]] = {
    "COMPLETION": "prepare_first_path_completion",
    "CONVERSATION": "prepare_conversation_completion",
    "EFFECTS": "prepare_local_commentary",
    "TERMINAL_WORK": "prepare_terminal_work",
}


@dataclass(slots=True)
class _H1CompleteChainPreflight:
    """Coordinator-owned evidence retained for one future finalizer continuation.

    This is deliberately not a DTO or authority token.  It keeps the source's
    opaque context alive only while the exact execution lease remains owned.
    The coordinator validates both identities again before a downstream owner
    may use it, then burns the context after its eventual drain.
    """

    _coordinator: _H1PostSealRecoveryCoordinator
    _source: object
    _lease: _H1RecoveryExecutionLease
    _state: H1PostSealRecoveryState
    _context: object
    _retired: bool = False


@dataclass(frozen=True, slots=True)
class _H1FinalizationOutcome:
    """Private broker result paired with its authority-owned terminal readback."""

    publication: object
    readback: object


@dataclass(frozen=True, slots=True)
class _H1FinalizationRejection:
    """A definitive broker refusal, which has no selected physical readback."""

    publication: object


class _H1PostSealRecoveryCoordinator:
    """Private authority owner for beginning or reading one recovery ROOT."""

    def __init__(self, runtime: CommonCliExecutionRuntime) -> None:
        # Avoid accepting a caller-shaped journal/mount/source graph.  The
        # canonical runtime installed all three before this object is made.
        from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
        from chiplog.composition.h1_launch_enrollment import EnrolledH1RecoveryMount

        if type(runtime) is not CommonCliExecutionRuntime:
            raise TypeError("post-seal recovery coordinator requires the installed runtime")
        mount = getattr(runtime, "_h1_recovery_mount", None)
        journal = getattr(runtime, "_h1_postseal_recovery_journal", None)
        if (
            type(mount) is not EnrolledH1RecoveryMount
            or type(journal) is not H1PostSealRecoveryJournal
        ):
            raise H1PostSealRecoveryCoordinatorError("installed recovery graph is absent")
        if journal._mount is not mount:
            raise H1PostSealRecoveryCoordinatorError("installed recovery journal mount differs")
        from chiplog.composition.h1_recovery_stage_source import H1RecoveryStageSource

        self._runtime = runtime
        self._mount = mount
        # This source is an issuer identity, not a convenient request builder.
        # Enrollment later accepts only this mounted coordinator's exact source.
        self._source = H1RecoveryStageSource(runtime)
        self._fence = _H1RecoveryExecutionFence(mount)
        self._closed = False

    async def resume_selected(
        self,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        selected_seal: CallSubjectHead | None = None,
    ) -> H1PostSealRecoveryState:
        """Independently find, derive, and durably begin one selected V2 ROOT."""
        if self._closed:
            raise H1PostSealRecoveryCoordinatorError("post-seal recovery coordinator is closed")
        if type(identity) is not DriverCommandIdentityV1:
            raise TypeError("recovery coordinator requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1PostSealRecoveryCoordinatorError("original driver fingerprint is invalid")
        if selected_seal is not None and type(selected_seal) is not CallSubjectHead:
            raise TypeError("recovery coordinator selected seal locator differs")
        try:
            state: H1PostSealRecoveryState | None = None
            async with await self._fence.acquire() as lease:
                state = self._begin_or_resume_held(
                    lease, identity, original_fingerprint, selected_seal
                )
                state = await self._resume_stages_held(lease, state, identity, original_fingerprint)
            if state is None:
                raise H1PostSealRecoveryCoordinatorError("recovery execution lease suppressed ROOT")
            return state
        except H1PostSealRecoveryCoordinatorError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1PostSealRecoveryCoordinatorError(
                "post-seal recovery ROOT cannot be safely resumed"
            ) from error

    async def finalize_selected(
        self, identity: DriverCommandIdentityV1, original_fingerprint: str
    ) -> Any:
        """Finalize one already-complete recovery cut through the installed writer.

        This is intentionally separate from ``resume_selected``.  Recovery may
        append a ROOT and its stage evidence; finalization first reconciles the
        authority's durable decision and opens B only after authenticated
        absence.  The single fence lease covers that classification, all fresh
        B/P work, the writer task, its exact selected readback, and cleanup.
        """
        if self._closed:
            raise H1PostSealRecoveryCoordinatorError("post-seal recovery coordinator is closed")
        if type(identity) is not DriverCommandIdentityV1:
            raise TypeError("finalization requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1PostSealRecoveryCoordinatorError("original driver fingerprint is invalid")

        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment
        from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority

        authority = getattr(self._runtime, "_h1_live_publication_authority", None)
        enrollment = getattr(self._runtime, "_h1_live_completion_enrollment", None)
        if type(authority) is not H1LivePublicationAuthority:
            raise H1PostSealRecoveryCoordinatorError(
                "installed final publication authority is absent"
            )
        if type(enrollment) is not _H1LiveCompletionEnrollment:
            raise H1PostSealRecoveryCoordinatorError("installed finalization enrollment is absent")

        worker = asyncio.create_task(
            self._finalize_selected_worker(
                identity,
                original_fingerprint,
                authority=authority,
                enrollment=enrollment,
            )
        )
        deferred_cancellation: asyncio.CancelledError | None = None
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError as error:
                if deferred_cancellation is None:
                    deferred_cancellation = error
        # Consume the worker result before honouring caller cancellation: an
        # authority or cleanup failure is the integrity outcome, not a reason
        # to hide it behind cancellation.
        result = worker.result()
        if deferred_cancellation is not None:
            raise deferred_cancellation
        return result

    async def _finalize_selected_worker(
        self,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        *,
        authority: Any,
        enrollment: Any,
    ) -> _H1FinalizationOutcome | _H1FinalizationRejection:
        """Run all task-affine finalization work in the lease-owning task."""
        result: _H1FinalizationOutcome | _H1FinalizationRejection | None = None
        async with await self._fence.acquire() as lease:
            # The finalization authority is downstream of the producer choice.
            # Do this read-only durable check before asking it to classify or
            # resume anything: SCOPED_V3 has no local mounted implementation.
            self._require_selected_local_producer_held(
                lease, identity, original_fingerprint
            )
            # This call is the sole selected/pending/absence oracle.  In
            # particular, None means B proved authenticated absence; HOLD,
            # malformed history, and uncertainty leave through an exception.
            classified = await authority._recover_finalization_held(
                identity=identity,
                original_fingerprint=original_fingerprint,
                lease=lease,
            )
            if classified is not None:
                result = await self._route_finalization_publication_held(
                    authority=authority,
                    identity=identity,
                    original_fingerprint=original_fingerprint,
                    publication=classified,
                )
            elif classified is None:
                preflight: _H1CompleteChainPreflight | None = None
                session: Any | None = None
                try:
                    preflight = self._preflight_complete_chain_held(
                        lease, identity, original_fingerprint
                    )
                    # B owns the exact enrollment graph and rejects all foreign
                    # continuation/capture/lease objects before making an owner call.
                    session = enrollment._open_finalization_session(preflight=preflight)
                    for stage in _STAGES:
                        lease.require_owned()
                        semantic_input, effects_command_id = preflight._state.stage_input(stage)
                        predecessor_effects_input = self._terminal_effects_input(
                            preflight._state, stage
                        )
                        self._require_effects_command_id(stage, semantic_input, effects_command_id)
                        session._bind_recovery_stage(
                            stage=stage,
                            semantic_input=semantic_input,
                            predecessor_effects_input=predecessor_effects_input,
                        )
                        exchange = await getattr(session, _PREPARE_METHODS[stage])()
                        returned = getattr(exchange, "returned", None)
                        result_bytes = getattr(returned, "canonical_payload", None)
                        expected = dict(preflight._state.results).get(stage)
                        if (
                            not isinstance(result_bytes, bytes)
                            or expected is None
                            or result_bytes != expected
                        ):
                            raise H1PostSealRecoveryCoordinatorError(
                                "finalization replay result differs from the durable pin"
                            )

                    issuance = await session._issue_finalization()
                    publication = await authority._commit_finalization_held(source=issuance)
                    result = await self._route_finalization_publication_held(
                        authority=authority,
                        identity=identity,
                        original_fingerprint=original_fingerprint,
                        publication=publication,
                    )
                finally:
                    # A B/P task may have accepted work even when the caller
                    # has cancelled repeatedly.  Drain it before burning its
                    # enrolled context, then retire the opaque preflight while
                    # the same cross-process lease remains owned.  A drain
                    # failure remains an integrity outcome, but cannot skip
                    # the retirement required for every captured continuation.
                    try:
                        if session is not None:
                            await self._drain_session_held(lease, session)
                    finally:
                        if preflight is not None and not preflight._retired:
                            self._retire_complete_chain_preflight_held(lease, preflight)

        if result is None:
            raise H1PostSealRecoveryCoordinatorError("finalization lease exited without a result")
        return result

    async def _route_finalization_publication_held(
        self,
        *,
        authority: Any,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        publication: object,
    ) -> _H1FinalizationOutcome | _H1FinalizationRejection:
        """Require an exact broker result before attempting selected readback."""
        from chiplog.platform._owner_publication_contracts import (
            JournalSelectedPublication,
            PublicationRejected,
        )

        if type(publication) is JournalSelectedPublication:
            readback = await authority._read_finalization_receipt_held(
                identity=identity,
                original_fingerprint=original_fingerprint,
                publication=publication,
            )
            return _H1FinalizationOutcome(publication, readback)
        if type(publication) is PublicationRejected:
            return _H1FinalizationRejection(publication)
        raise H1PostSealRecoveryPublicationIntegrityError(
            "H1 finalization publication result differs"
        )

    def _preflight_complete_chain_held(
        self,
        lease: _H1RecoveryExecutionLease,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
    ) -> _H1CompleteChainPreflight:
        """Read and validate an already-complete recovery chain without changing it.

        A finalizer calls this only after authenticating its current caller and
        original H0 admission.  This private seam deliberately does not do
        either: its arguments locate the frozen V2 source, and it must never
        turn a missing ROOT or stage into a new recovery operation.
        """
        if type(lease) is not _H1RecoveryExecutionLease:
            raise TypeError("complete-chain preflight requires the exact recovery lease")
        lease.require_owned()
        if type(identity) is not DriverCommandIdentityV1:
            raise TypeError("complete-chain preflight requires the exact original driver identity")
        if not isinstance(original_fingerprint, str) or len(original_fingerprint) != 64:
            raise H1PostSealRecoveryCoordinatorError("original driver fingerprint is invalid")
        if self._closed:
            raise H1PostSealRecoveryCoordinatorError("post-seal recovery coordinator is closed")

        context: object | None = None
        try:
            self._mount.assert_current()
            # Keep source location, root derivation and the journal observation
            # in one mounted authority interval.  Unlike _begin_or_resume_held,
            # this branch has no append or reconciliation path.
            with self._runtime._authority_gate().hold():
                locator = H1V2RecoveryNativeSource(self._runtime).locate_selected_seal(
                    original_identity=identity, original_fingerprint=original_fingerprint
                )
                derived_root = H1PostSealRecoveryRootSource(self._runtime).derive_on_restart(
                    identity, original_fingerprint, locator
                )
                scan = self._journal().scan()
                root_id = dict(scan.root_id_by_selected_seal).get(
                    (
                        derived_root.tenant_id,
                        derived_root.database_id,
                        derived_root.selected_seal_subject_id,
                        derived_root.selected_seal_head,
                        derived_root.selected_seal_fingerprint,
                    )
                )
                if root_id is None:
                    raise H1PostSealRecoveryCoordinatorError("durable recovery ROOT is absent")
                state = scan.state_for_root(root_id)
                if (
                    state.root.root_id() != derived_root.root_id()
                    or state.root.as_dict() != derived_root.as_dict()
                ):
                    raise H1PostSealRecoveryCoordinatorError("durable recovery ROOT differs")
                self._require_local_producer(state)
                self._require_complete_chain_shape(state)
                context = self._source._capture_recovery(
                    original_identity=identity,
                    original_fingerprint=original_fingerprint,
                    selected_seal=locator,
                    root=state.root,
                )
                self._validate_complete_chain_held(context, state)
                return _H1CompleteChainPreflight(self, self._source, lease, state, context)
        except BaseException:
            if context is not None:
                # Capture allocates issuer-owned capability state even when a
                # later durable link fails.  Retire it under the still-owned
                # lease before reporting the hold.
                lease.require_owned()
                self._source._retire_recovery_context(context)
            raise

    def _require_complete_chain_preflight_held(
        self, lease: _H1RecoveryExecutionLease, capture: _H1CompleteChainPreflight
    ) -> H1PostSealRecoveryState:
        """Recheck exact lease/context ownership immediately before continuation."""
        if type(lease) is not _H1RecoveryExecutionLease:
            raise TypeError("complete-chain preflight requires the exact recovery lease")
        lease.require_owned()
        if (
            type(capture) is not _H1CompleteChainPreflight
            or capture._coordinator is not self
            or capture._source is not self._source
            or capture._lease is not lease
            or capture._retired
        ):
            raise H1PostSealRecoveryCoordinatorError("complete-chain preflight differs")
        self._require_local_producer(capture._state)
        self._source._require_current(capture._context)
        return capture._state

    def _retire_complete_chain_preflight_held(
        self, lease: _H1RecoveryExecutionLease, capture: _H1CompleteChainPreflight
    ) -> None:
        """Burn a completed preflight only under its original held lease."""
        if type(lease) is not _H1RecoveryExecutionLease:
            raise TypeError("complete-chain preflight requires the exact recovery lease")
        lease.require_owned()
        if (
            type(capture) is not _H1CompleteChainPreflight
            or capture._coordinator is not self
            or capture._source is not self._source
            or capture._lease is not lease
            or capture._retired
        ):
            raise H1PostSealRecoveryCoordinatorError("complete-chain preflight differs")
        self._source._retire_recovery_context(capture._context)
        capture._retired = True

    @staticmethod
    def _require_complete_chain_shape(state: H1PostSealRecoveryState) -> None:
        if (
            tuple(stage for stage, _semantic_input, _command_id in state.inputs) != _STAGES
            or tuple(stage for stage, _result in state.results) != _STAGES
            or state.next_stage != "COMPLETE"
        ):
            raise H1PostSealRecoveryCoordinatorError("durable recovery chain is incomplete")

    def _validate_complete_chain_held(
        self, context: object, state: H1PostSealRecoveryState
    ) -> None:
        """Rebuild all semantic inputs and validate all persisted results in order."""
        durable_results: dict[_H1RecoveryStage, bytes] = {}
        for stage in _STAGES:
            self._source._require_current(context)
            semantic_input, effects_command_id = state.stage_input(stage)
            expected = self._source._reconstruct_input(
                context,
                stage,
                durable_results,
                effects_command_id,
                predecessor_effects_input=self._terminal_effects_input(state, stage),
            )
            if semantic_input != expected:
                raise H1PostSealRecoveryCoordinatorError("durable stage input differs")
            self._require_effects_command_id(stage, semantic_input, effects_command_id)
            result = dict(state.results)[stage]
            self._source._validate_result(context, stage, semantic_input, result)
            durable_results[stage] = result

    async def _resume_stages_held(
        self,
        lease: Any,
        state: H1PostSealRecoveryState,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
    ) -> H1PostSealRecoveryState:
        """Run one source context and retire it before the lease can be released."""
        lease.require_owned()
        self._require_local_producer(state)
        contexts: list[object] = []
        try:
            return await self._resume_stages_inner_held(
                lease, state, identity, original_fingerprint, contexts
            )
        finally:
            if contexts:
                lease.require_owned()
                self._source._retire_recovery_context(contexts[0])

    async def _resume_stages_inner_held(
        self,
        lease: Any,
        state: H1PostSealRecoveryState,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        contexts: list[object],
    ) -> H1PostSealRecoveryState:
        """Reconstruct, pin, replay, and commit all four stages under one lease.

        This private entry point deliberately takes the lease rather than
        acquiring one.  All source reconstruction, journal reconciliation,
        session lifetime, and cancellation drain therefore share exactly one
        cross-process owner.
        """
        lease.require_owned()
        self._require_local_producer(state)
        source = self._source
        locator = CallSubjectHead(
            subject_id=state.root.selected_seal_subject_id,
            revision=Present(
                head=state.root.selected_seal_head,
                fingerprint=state.root.selected_seal_fingerprint,
            ),
        )
        context = source._capture_recovery(
            original_identity=identity,
            original_fingerprint=original_fingerprint,
            selected_seal=locator,
            root=state.root,
        )
        contexts.append(context)

        # Validate every already durable link before constructing B.  This is
        # also the no-IPC fast path for an entirely durable chain.
        durable_results: dict[_H1RecoveryStage, bytes] = {}
        first_incomplete: _H1RecoveryStage | None = None
        for stage in _STAGES:
            source._require_current(context)
            pinned = self._existing_input(state, stage)
            predecessor_effects_input = self._terminal_effects_input(state, stage)
            expected = source._reconstruct_input(
                context,
                stage,
                durable_results,
                pinned[1] if pinned is not None else None,
                predecessor_effects_input=predecessor_effects_input,
            )
            if pinned is None:
                first_incomplete = stage
                break
            if pinned[0] != expected:
                raise H1PostSealRecoveryCoordinatorError("durable stage input differs")
            self._require_effects_command_id(stage, expected, pinned[1])
            result = self._existing_result(state, stage)
            if result is None:
                first_incomplete = stage
                break
            source._validate_result(context, stage, expected, result)
            durable_results[stage] = result
        if first_incomplete is None:
            return state

        session = self._open_recovery_session(source=source, context=context, lease=lease)
        try:
            session._bind_recovery(source=source, context=context, lease=lease)
            for stage in _STAGES:
                lease.require_owned()
                source._require_current(context)
                pinned = self._existing_input(state, stage)
                predecessor_effects_input = self._terminal_effects_input(state, stage)
                semantic_input = source._reconstruct_input(
                    context,
                    stage,
                    durable_results,
                    pinned[1] if pinned is not None else None,
                    predecessor_effects_input=predecessor_effects_input,
                )
                if pinned is None:
                    effects_command_id = self._effects_command_id(stage, semantic_input)
                    record = H1PostSealRecoveryTransition.pin_input(
                        state,
                        stage=stage,
                        semantic_input=semantic_input,
                        effects_command_id=effects_command_id,
                    )
                    state = self._append_stage_held(lease, state, record)
                    pinned = self._existing_input(state, stage)
                    if pinned is None or pinned[0] != semantic_input:
                        raise H1PostSealRecoveryCoordinatorError("stage input readback differs")
                else:
                    if pinned[0] != semantic_input:
                        raise H1PostSealRecoveryCoordinatorError("durable stage input differs")
                self._require_effects_command_id(stage, semantic_input, pinned[1])

                # A new B session must replay every earlier durable stage.  A
                # result remains evidence only after its fresh exchange agrees
                # byte-for-byte; a fully durable chain returned above opens no
                # session and sends no IPC.
                session._bind_recovery_stage(
                    stage=stage,
                    semantic_input=semantic_input,
                    predecessor_effects_input=predecessor_effects_input,
                )
                exchange = await getattr(session, _PREPARE_METHODS[stage])()
                returned = getattr(exchange, "returned", None)
                result_bytes = getattr(returned, "canonical_payload", None)
                if not isinstance(result_bytes, bytes):
                    raise H1PostSealRecoveryCoordinatorError("owner result bytes are unavailable")
                source._validate_result(context, stage, semantic_input, result_bytes)
                durable = self._existing_result(state, stage)
                if durable is not None:
                    if durable != result_bytes:
                        raise H1PostSealRecoveryCoordinatorError("durable stage result differs")
                else:
                    record = H1PostSealRecoveryTransition.commit_result(
                        state, stage=stage, result_bytes=result_bytes
                    )
                    state = self._append_stage_held(lease, state, record)
                    durable = self._existing_result(state, stage)
                    if durable != result_bytes:
                        raise H1PostSealRecoveryCoordinatorError("stage result readback differs")
                durable_results[stage] = result_bytes
            return state
        finally:
            # Session cleanup owns any broker task that crossed IPC.  Keep the
            # execution lease until it settles even if this coordinator is
            # cancelled; releasing first would admit a competing B issuer.
            await self._drain_session_held(lease, session)

    def _append_stage_held(
        self,
        lease: Any,
        state: H1PostSealRecoveryState,
        record: H1PostSealRecoveryRecordV1,
    ) -> H1PostSealRecoveryState:
        """CAS append/readback one stage record, reopening on every uncertainty."""
        lease.require_owned()
        if type(record) is not H1PostSealRecoveryRecordV1 or record.kind == "ROOT":
            raise TypeError("stage append requires one canonical stage record")
        if record.root_id != state.root.root_id() or record.predecessor_entry_id != state.head:
            raise H1PostSealRecoveryCoordinatorError("stage record predecessor differs")
        try:
            receipt = self._journal().append_transition(record, expected_global_tip=state.head)
        except BaseException as error:
            reconciled = self._reopen_and_reconcile_stage(state.root, record, error)
            if not isinstance(error, Exception):
                raise
            return reconciled
        returned = receipt.scan.state_for_root(state.root.root_id())
        self._require_record_readback(returned, record)
        return returned

    @staticmethod
    def _existing_input(
        state: H1PostSealRecoveryState, stage: _H1RecoveryStage
    ) -> tuple[bytes, str | None] | None:
        try:
            return state.stage_input(stage)
        except Exception:
            return None

    @staticmethod
    def _existing_result(state: H1PostSealRecoveryState, stage: _H1RecoveryStage) -> bytes | None:
        return dict(state.results).get(stage)

    def _terminal_effects_input(
        self, state: H1PostSealRecoveryState, stage: _H1RecoveryStage
    ) -> tuple[bytes, str] | None:
        if stage != "TERMINAL_WORK":
            return None
        effects = self._existing_input(state, "EFFECTS")
        if effects is None or effects[1] is None:
            raise H1PostSealRecoveryCoordinatorError("terminal Effects predecessor is absent")
        return effects[0], effects[1]

    @staticmethod
    def _effects_command_id(stage: _H1RecoveryStage, semantic_input: bytes) -> str | None:
        if stage != "EFFECTS":
            return None
        try:
            decoded = json.loads(semantic_input)
            command_id = decoded["identity"]["command_id"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise H1PostSealRecoveryCoordinatorError("effects semantic input is invalid") from error
        if not isinstance(command_id, str) or not command_id:
            raise H1PostSealRecoveryCoordinatorError("effects command ID is invalid")
        return command_id

    def _require_effects_command_id(
        self, stage: _H1RecoveryStage, semantic_input: bytes, pinned_command_id: str | None
    ) -> None:
        if self._effects_command_id(stage, semantic_input) != pinned_command_id:
            raise H1PostSealRecoveryCoordinatorError("effects command ID differs")

    @staticmethod
    def _require_record_readback(
        state: H1PostSealRecoveryState, record: H1PostSealRecoveryRecordV1
    ) -> None:
        assert record.stage is not None
        if record.kind == "STAGE_INPUT":
            if state.stage_input(record.stage) != (
                record.semantic_input,
                record.effects_command_id,
            ):
                raise H1PostSealRecoveryCoordinatorError("stage input readback differs")
        elif dict(state.results).get(record.stage) != record.result_bytes:
            raise H1PostSealRecoveryCoordinatorError("stage result readback differs")

    def _reopen_and_reconcile_stage(
        self,
        root: Any,
        record: H1PostSealRecoveryRecordV1,
        append_error: BaseException,
    ) -> H1PostSealRecoveryState:
        state = self._reopen_and_reconcile(root, append_error)
        try:
            self._require_record_readback(state, record)
        except Exception as error:
            raise H1PostSealRecoveryCoordinatorError("stage append outcome is uncertain") from error
        return state

    def _open_recovery_session(self, *, source: Any, context: object, lease: Any) -> Any:
        """Open and revalidate a session only through installed enrollment.

        A callable attribute is not enrollment.  The recovery B session must
        be an exact membership of the installed table before it can receive a
        source context or a durable stage pin.
        """
        from chiplog.composition.h1_completion_preparation_session import (
            H1CompletionPreparationSession,
        )
        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment

        enrollment = getattr(self._runtime, "_h1_live_completion_enrollment", None)
        if type(enrollment) is not _H1LiveCompletionEnrollment:
            raise H1PostSealRecoveryCoordinatorError("installed recovery B enrollment is absent")
        session = enrollment._open_recovery_session(source=source, context=context, lease=lease)
        if type(session) is not H1CompletionPreparationSession:
            raise H1PostSealRecoveryCoordinatorError("installed recovery B session differs")
        enrollment._require_recovery_session(
            session=session,
            source=source,
            context=context,
            lease=lease,
        )
        return session

    async def _drain_session_held(self, lease: Any, session: Any) -> None:
        drain = getattr(session, "_drain_recovery", None)
        if not callable(drain):
            raise H1PostSealRecoveryCoordinatorError("recovery B session lacks a drain")
        task = asyncio.create_task(drain())
        cancelled: asyncio.CancelledError | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                cancelled = error
        # Propagate a drain failure before re-raising cancellation: a failed
        # drain is an integrity hold, not permission to free the lease early.
        task.result()
        # This coordinator task still owns the exact task-affine lease after
        # the shielded broker task has settled.  Only then may the installed
        # enrollment retire the ordinary/finalization B session.
        lease.require_owned()
        from chiplog.composition.h1_live_completion_enrollment import _H1LiveCompletionEnrollment

        enrollment = getattr(self._runtime, "_h1_live_completion_enrollment", None)
        if type(enrollment) is not _H1LiveCompletionEnrollment:
            raise H1PostSealRecoveryCoordinatorError("installed recovery B enrollment is absent")
        enrollment._revoke_recovery_session(session)
        if cancelled is not None:
            raise cancelled

    def _begin_or_resume_held(
        self,
        lease: Any,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
        supplied_locator: CallSubjectHead | None,
    ) -> H1PostSealRecoveryState:
        """Fence-held synchronous ROOT transaction; it performs no owner IPC."""
        lease.require_owned()
        self._mount.assert_current()
        # This is synchronous: retain the mounted authority gate for the whole
        # source proof and global journal transaction.  The future B call is
        # deliberately outside this block because AuthorityGate cannot span an
        # await.
        with self._runtime._authority_gate().hold():
            locator = H1V2RecoveryNativeSource(self._runtime).locate_selected_seal(
                original_identity=identity, original_fingerprint=original_fingerprint
            )
            if supplied_locator is not None and locator != supplied_locator:
                raise H1PostSealRecoveryCoordinatorError("selected seal locator differs")
            root = H1PostSealRecoveryRootSource(self._runtime).derive_on_restart(
                identity, original_fingerprint, locator
            )
            journal = self._journal()
            scan = journal.scan()
            root_id = dict(scan.root_id_by_selected_seal).get(
                (
                    root.tenant_id,
                    root.database_id,
                    root.selected_seal_subject_id,
                    root.selected_seal_head,
                    root.selected_seal_fingerprint,
                )
            )
            if root_id is not None:
                state = scan.state_for_root(root_id)
                if state.root.as_dict() != root.as_dict():
                    raise H1PostSealRecoveryCoordinatorError("durable recovery ROOT differs")
                return state
            # A same-ID ROOT under another selected locator is also integrity loss:
            # no root ID is allowed to become an alternative source of authority.
            existing = dict(scan.states_by_root).get(root.root_id())
            if existing is not None:
                if existing.root.as_dict() != root.as_dict():
                    raise H1PostSealRecoveryCoordinatorError("durable recovery ROOT differs")
                return existing
            record = H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(root))
            try:
                receipt = journal.append_transition(record, expected_global_tip=scan.tip)
            except BaseException as error:
                # An append failure can mean that bytes reached durable storage.  Do
                # not retry the old wrapper or record; reopen once and reconcile.
                # Cancellation and process-control exceptions must retain their
                # control-flow meaning even if that reconciliation finds ROOT.
                if not isinstance(error, Exception):
                    self._reopen_and_reconcile(root, error)
                    raise
                return self._reopen_and_reconcile(root, error)
            state = receipt.scan.state_for_root(root.root_id())
            if state.root.as_dict() != root.as_dict():
                raise H1PostSealRecoveryCoordinatorError("recovery ROOT readback differs")
            return state

    def _reopen_and_reconcile(
        self, root: Any, append_error: BaseException
    ) -> H1PostSealRecoveryState:
        journal = self._journal()
        journal.close()
        try:
            reopened = H1PostSealRecoveryJournal.open_enrolled(self._mount)
            self._runtime._h1_postseal_recovery_journal = reopened
            scan = reopened.scan()
            root_id = dict(scan.root_id_by_selected_seal).get(
                (
                    root.tenant_id,
                    root.database_id,
                    root.selected_seal_subject_id,
                    root.selected_seal_head,
                    root.selected_seal_fingerprint,
                )
            )
            if root_id is None:
                raise H1PostSealRecoveryCoordinatorError(
                    "recovery ROOT append outcome is uncertain"
                ) from append_error
            state = scan.state_for_root(root_id)
            if state.root.as_dict() != root.as_dict():
                raise H1PostSealRecoveryCoordinatorError("durable recovery ROOT differs")
            self._require_local_producer(state)
            return state
        except H1PostSealRecoveryCoordinatorError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1PostSealRecoveryCoordinatorError(
                "recovery ROOT append outcome cannot be reconciled"
            ) from error

    def _journal(self) -> H1PostSealRecoveryJournal:
        journal = getattr(self._runtime, "_h1_postseal_recovery_journal", None)
        if type(journal) is not H1PostSealRecoveryJournal or journal._mount is not self._mount:
            raise H1PostSealRecoveryCoordinatorError("installed recovery journal differs")
        return journal

    @staticmethod
    def _require_local_producer(state: H1PostSealRecoveryState) -> None:
        """Reject a durable producer choice that this mounted coordinator cannot run."""
        if state.selected_producer != "LOCAL_V2":
            raise H1PostSealRecoveryCoordinatorError(
                "selected recovery producer is unavailable to the local coordinator"
            )

    def _require_selected_local_producer_held(
        self,
        lease: _H1RecoveryExecutionLease,
        identity: DriverCommandIdentityV1,
        original_fingerprint: str,
    ) -> None:
        """Fail closed on a selected scoped ROOT before finalization authority work.

        Absence remains the finalization authority's concern, so this helper
        only rejects a matching durable ROOT whose producer is unavailable.
        """
        if type(lease) is not _H1RecoveryExecutionLease:
            raise TypeError("producer check requires the exact recovery lease")
        lease.require_owned()
        self._mount.assert_current()
        with self._runtime._authority_gate().hold():
            locator = H1V2RecoveryNativeSource(self._runtime).locate_selected_seal(
                original_identity=identity, original_fingerprint=original_fingerprint
            )
            derived_root = H1PostSealRecoveryRootSource(self._runtime).derive_on_restart(
                identity, original_fingerprint, locator
            )
            scan = self._journal().scan()
            root_id = dict(scan.root_id_by_selected_seal).get(
                (
                    derived_root.tenant_id,
                    derived_root.database_id,
                    derived_root.selected_seal_subject_id,
                    derived_root.selected_seal_head,
                    derived_root.selected_seal_fingerprint,
                )
            )
            if root_id is None:
                return
            state = scan.state_for_root(root_id)
            if (
                state.root.root_id() != derived_root.root_id()
                or state.root.as_dict() != derived_root.as_dict()
            ):
                raise H1PostSealRecoveryCoordinatorError("durable recovery ROOT differs")
            self._require_local_producer(state)

    def close(self) -> None:
        if self._closed:
            return
        self._fence.close()
        # _fence.close proves there is no live lease.  Burn source-owned
        # contexts while the mounted graph is still present, before journal
        # teardown can make their provenance ambiguous.
        for state in tuple(self._source._recovery_contexts.values()):
            self._source._retire_recovery_context(state.context)
        # Reconciliation replaces the runtime-bound wrapper.  The opener's
        # original wrapper is still closed by its owner; this closes a reopened
        # descriptor exactly once before the mount is released.
        self._journal().close()
        self._closed = True
