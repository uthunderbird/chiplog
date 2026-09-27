"""Installed ROOT-only handoff after an authenticated H1 V2 seal.

The coordinator deliberately stops after a durable recovery ROOT.  B
continuation belongs to the next recovery slice; keeping the execution fence
around this transaction gives that slice one place to extend safely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
)
from chiplog.composition.h1_postseal_recovery_source import H1PostSealRecoveryRootSource
from chiplog.composition.h1_recovery_execution_fence import _H1RecoveryExecutionFence
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource

if TYPE_CHECKING:
    from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime


class H1PostSealRecoveryCoordinatorError(RuntimeError):
    """The installed seal cannot safely enter the recovery journal."""


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
        self._runtime = runtime
        self._mount = mount
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
            if state is None:
                raise H1PostSealRecoveryCoordinatorError("recovery execution lease suppressed ROOT")
            return state
        except H1PostSealRecoveryCoordinatorError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            raise H1PostSealRecoveryCoordinatorError(
                "post-seal recovery ROOT cannot be safely resumed"
            ) from error

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

    def close(self) -> None:
        if self._closed:
            return
        self._fence.close()
        # Reconciliation replaces the runtime-bound wrapper.  The opener's
        # original wrapper is still closed by its owner; this closes a reopened
        # descriptor exactly once before the mount is released.
        self._journal().close()
        self._closed = True
