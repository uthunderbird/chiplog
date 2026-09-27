"""RED contracts for the installed H1 post-seal recovery execution fence."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import importlib
import os
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.composition.h1_launch_enrollment import (
    EnrolledH1RecoveryMount,
    InstalledH1Slot,
    _open_installed_h1_launch,
    _provision_h1_enrollment,
    _provision_h1_evidence_mount,
    _provision_h1_recovery_mount,
)
from chiplog.composition.h1_postseal_recovery import (
    H1PostSealRecoveryJournal,
    H1PostSealRecoveryRootV1,
    H1PostSealRecoveryState,
    H1PostSealRecoveryTransition,
)
from chiplog.platform.authority_gate import AuthorityGate
from tests.support.h1_installed_launch import active_registration_custody, installed_slot

type MountedRecovery = tuple[EnrolledH1RecoveryMount, H1PostSealRecoveryJournal, InstalledH1Slot]


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _execution_fence_type() -> type[Any]:
    """Delay the intentionally absent private contract until test execution."""
    module = importlib.import_module("chiplog.composition.h1_recovery_execution_fence")
    return cast(
        type[Any],
        module.__dict__["_H1RecoveryExecutionFence"],
    )


@pytest.fixture
def enrolled_recovery_mount(tmp_path: Path) -> Iterator[MountedRecovery]:
    slot, expected = installed_slot(tmp_path)
    registry = active_registration_custody(expected)
    _provision_h1_enrollment(slot, expected, registry)
    _provision_h1_evidence_mount(slot, expected)
    _provision_h1_recovery_mount(slot, expected)
    gate = AuthorityGate.for_database(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        mount = launch.open_enrolled_recovery_mount(gate)
        journal = H1PostSealRecoveryJournal.open_enrolled(mount)
        try:
            yield mount, journal, slot
        finally:
            journal.close()
            mount.close()


def _root(
    mount: EnrolledH1RecoveryMount, slot: InstalledH1Slot, label: str
) -> H1PostSealRecoveryRootV1:
    metadata = os.stat(slot.database_path)
    return H1PostSealRecoveryRootV1(
        tenant_id=mount.tenant_id,
        database_id=slot.database_id,
        database_identity=(str(slot.database_path), metadata.st_dev, metadata.st_ino),
        journal_instance_id=mount.journal_instance_id,
        selected_seal_subject_id="selected-seal-" + label,
        selected_seal_head="selected-head-" + label,
        selected_seal_fingerprint=_digest("selected-fingerprint-" + label),
        selected_decision_id=_digest("selected-decision-" + label),
        selected_decision_digest=_digest("selected-decision-digest-" + label),
        selected_run_head="loop:" + _digest("selected-run-" + label),
        original_command_id="original-command-" + label,
        original_command_fingerprint=_digest("original-fingerprint-" + label),
        source_commitment=_digest("source-commitment-" + label),
        publication_command_id="publication-command-" + label,
        publication_command_fingerprint=_digest("publication-fingerprint-" + label),
    )


@pytest.mark.asyncio
async def test_execution_fence_allows_mounted_journal_scan_and_append_without_self_deadlock(
    enrolled_recovery_mount: MountedRecovery,
) -> None:
    mount, journal, slot = enrolled_recovery_mount
    fence = _execution_fence_type()(mount)

    async with await fence.acquire() as lease:
        lease.require_owned()
        before = journal.scan()
        root = _root(mount, slot, "scan-append")
        receipt = journal.append_transition(
            H1PostSealRecoveryTransition.begin(H1PostSealRecoveryState.empty(root)),
            expected_global_tip=before.tip,
        )
        assert receipt.scan.state_for_root(root.root_id()).root == root
        assert journal.scan().tip == receipt.entry_id


@pytest.mark.asyncio
async def test_execution_fence_excludes_tasks_and_rejects_a_child_borrowing_parent_lease(
    enrolled_recovery_mount: MountedRecovery,
) -> None:
    mount, _journal, _slot = enrolled_recovery_mount
    fence = _execution_fence_type()(mount)
    contender_started = asyncio.Event()
    contender_entered = asyncio.Event()
    child_attempted = asyncio.Event()

    async def contender() -> None:
        contender_started.set()
        async with await fence.acquire() as lease:
            lease.require_owned()
            contender_entered.set()

    async with await fence.acquire() as parent_lease:
        parent_lease.require_owned()
        waiting_contender = asyncio.create_task(contender())
        await contender_started.wait()
        await asyncio.sleep(0)
        assert not contender_entered.is_set()

        async def child_attempt() -> None:
            child_attempted.set()
            parent_lease.require_owned()

        child = asyncio.create_task(child_attempt())
        await child_attempted.wait()
        with pytest.raises(RuntimeError):
            await child
        assert not contender_entered.is_set()

        async def child_release_attempt() -> None:
            await parent_lease.__aexit__(None, None, None)

        with pytest.raises(RuntimeError, match="owned"):
            await asyncio.create_task(child_release_attempt())
        assert not contender_entered.is_set()

    await asyncio.wait_for(waiting_contender, timeout=1)
    assert contender_entered.is_set()


@pytest.mark.asyncio
async def test_execution_fence_rejects_same_task_reentrancy(
    enrolled_recovery_mount: MountedRecovery,
) -> None:
    mount, _journal, _slot = enrolled_recovery_mount
    fence = _execution_fence_type()(mount)

    async with await fence.acquire() as lease:
        lease.require_owned()
        with pytest.raises(RuntimeError, match="non-reentrant"):
            await fence.acquire()


@pytest.mark.asyncio
async def test_cancelled_execution_fence_waiter_never_acquires_or_leaks_its_descriptor(
    enrolled_recovery_mount: MountedRecovery,
) -> None:
    mount, _journal, _slot = enrolled_recovery_mount
    fence = _execution_fence_type()(mount)
    waiter_started = asyncio.Event()
    waiter_acquired = asyncio.Event()
    successor_acquired = asyncio.Event()

    async def waiter() -> None:
        waiter_started.set()
        async with await fence.acquire() as lease:
            lease.require_owned()
            waiter_acquired.set()

    async with await fence.acquire() as holder:
        holder.require_owned()
        waiting = asyncio.create_task(waiter())
        await waiter_started.wait()
        await asyncio.sleep(0)
        assert not waiter_acquired.is_set()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert not waiter_acquired.is_set()

    async with await fence.acquire() as successor:
        successor.require_owned()
        successor_acquired.set()
    assert successor_acquired.is_set()


@pytest.mark.asyncio
async def test_execution_fence_is_cross_process_and_fork_fail_closed(
    enrolled_recovery_mount: MountedRecovery,
) -> None:
    mount, _journal, slot = enrolled_recovery_mount
    fence = _execution_fence_type()(mount)
    read_fd, write_fd = os.pipe()
    try:
        async with await fence.acquire() as lease:
            child_pid = os.fork()
            if child_pid == 0:
                exit_code = 1
                try:
                    os.close(read_fd)
                    fd = os.open(
                        slot.root / slot.custody_name / "h1-post-seal-recovery.execution-fence",
                        os.O_RDWR | os.O_CLOEXEC,
                    )
                    try:
                        blocked = False
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            blocked = True
                        rejected = False
                        try:
                            lease.require_owned()
                        except RuntimeError:
                            rejected = True
                        if not (blocked and rejected):
                            raise AssertionError("fork child acquired or borrowed the fence")
                    finally:
                        os.close(fd)
                    os.write(write_fd, b"ok")
                    exit_code = 0
                finally:
                    os._exit(exit_code)
            os.close(write_fd)
            assert os.read(read_fd, 2) == b"ok"
            while True:
                observed_pid, status = os.waitpid(child_pid, os.WNOHANG)  # noqa: ASYNC222
                if observed_pid == child_pid:
                    break
                await asyncio.sleep(0.005)
            assert os.waitstatus_to_exitcode(status) == 0
    finally:
        for fd in (read_fd, write_fd):
            with suppress(OSError):
                os.close(fd)


@pytest.mark.asyncio
async def test_execution_fence_rejects_replaced_enrolled_inode_without_creating_a_repair(
    enrolled_recovery_mount: MountedRecovery,
) -> None:
    mount, _journal, slot = enrolled_recovery_mount
    target = slot.root / slot.custody_name / "h1-post-seal-recovery.execution-fence"
    replacement = slot.root / "replacement-fence"
    replacement.write_bytes(b"replacement")
    replacement.chmod(0o600)
    os.replace(replacement, target)
    before = target.read_bytes()

    with pytest.raises(RuntimeError, match=r"changed|poisoned|unavailable"):
        await _execution_fence_type()(mount).acquire()
    assert target.read_bytes() == before
