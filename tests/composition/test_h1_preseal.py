"""Local issuance and re-read behavior of the H1 V2 preflight carrier."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from types import SimpleNamespace
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition import h1_preseal
from chiplog.composition.common_cli_execution_runtime import CommonCliExecutionRuntime
from chiplog.composition.h1_preseal_contracts import (
    H1InventoryReceipt,
    H1SelectedPrepare,
    H1V2SealPreflight,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1VerifiedWorkspaceClosure


class _Gate:
    def hold(self) -> AbstractContextManager[None]:
        return nullcontext()


class _Runtime:
    _tenant_id = "tenant"

    def _authority_gate(self) -> _Gate:
        return _Gate()

    def current_worker(self) -> str:
        return "worker"


def _captured() -> ExecutionRunRecord:
    return cast(
        ExecutionRunRecord,
        SimpleNamespace(head="captured", worker_session="worker", tenant="tenant"),
    )


def _proof(captured: ExecutionRunRecord) -> H1V2SealPreflight:
    return H1V2SealPreflight(
        captured_run=captured,
        prepare=cast(H1SelectedPrepare, "prepare"),
        workspace=cast(H1VerifiedWorkspaceClosure, "workspace"),
        inventory=cast(H1InventoryReceipt, "inventory"),
        worker_session="worker",
    )


def test_preflight_is_runtime_issued_and_recheck_consumes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    captured = _captured()
    proof = _proof(captured)
    monkeypatch.setattr(h1_preseal, "_read", lambda *_args, **_kwargs: proof)

    issued = h1_preseal.preflight_h1_v2_seal(
        cast(CommonCliExecutionRuntime, runtime), captured, expected_head="captured"
    )

    assert h1_preseal.recheck_h1_v2_seal(cast(CommonCliExecutionRuntime, runtime), issued)
    assert not h1_preseal.recheck_h1_v2_seal(cast(CommonCliExecutionRuntime, runtime), issued)
    assert not h1_preseal.recheck_h1_v2_seal(
        cast(CommonCliExecutionRuntime, runtime), _proof(captured)
    )


def test_recheck_rejects_source_change_after_owner_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _Runtime()
    captured = _captured()
    original = _proof(captured)
    changed = H1V2SealPreflight(
        captured_run=original.captured_run,
        prepare=original.prepare,
        workspace=original.workspace,
        inventory=cast(H1InventoryReceipt, "owner-journal-appended"),
        worker_session=original.worker_session,
    )
    reads = iter((original, changed))
    monkeypatch.setattr(h1_preseal, "_read", lambda *_args, **_kwargs: next(reads))

    issued = h1_preseal.preflight_h1_v2_seal(
        cast(CommonCliExecutionRuntime, runtime), captured, expected_head="captured"
    )

    assert not h1_preseal.recheck_h1_v2_seal(cast(CommonCliExecutionRuntime, runtime), issued)
