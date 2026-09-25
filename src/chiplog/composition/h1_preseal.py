"""Runtime-issued, acyclic admission evidence for an H1 V2 Complete seal.

The preflight deliberately has no knowledge of a response seal or frontier
registry.  It authenticates only the already-selected Prepare, its original
workspace, and the complete negative owner inventory.  A result is useful
once: the fanout admission guard consumes it and repeats every authoritative
read immediately before SQLite starts its insert transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord

from .h1_preseal_contracts import H1V2SealPreflight

if TYPE_CHECKING:
    from .common_cli_execution_runtime import CommonCliExecutionRuntime


# This is intentionally process-local and keyed by object identity.  The
# public runtime API never accepts a preflight, and callers cannot recreate an
# issued entry by constructing an equal dataclass value.
_issued: dict[int, tuple[object, H1V2SealPreflight]] = {}


def _read(
    runtime: CommonCliExecutionRuntime,
    captured: ExecutionRunRecord,
    *,
    expected_head: str,
) -> H1V2SealPreflight:
    # Keep provider imports lazy: an unavailable complete inventory adapter is
    # an H1 eligibility failure, never an import-time change to V1 sealing.
    from .h1_owner_inventory import read_h1_scoped_owner_inventory
    from .h1_selected_prepare import (
        reopen_selected_h1_workspace,
        select_h1_v3_prepare_for_candidate,
    )

    selected = select_h1_v3_prepare_for_candidate(runtime, captured, expected_head=expected_head)
    workspace = reopen_selected_h1_workspace(runtime, selected)
    inventory = read_h1_scoped_owner_inventory(
        runtime,
        captured=captured,
        selected_prepare=selected,
        workspace=workspace,
        phase="PRE_SEAL",
    )
    return H1V2SealPreflight(
        captured_run=captured,
        prepare=selected,
        workspace=workspace,
        inventory=inventory,
        worker_session=runtime.current_worker(),
    )


def preflight_h1_v2_seal(
    runtime: CommonCliExecutionRuntime,
    captured: ExecutionRunRecord,
    *,
    expected_head: str,
) -> H1V2SealPreflight:
    """Read the pre-seal evidence under the runtime's canonical authority gate."""
    with runtime._authority_gate().hold():
        if (
            captured.head != expected_head
            or captured.worker_session != runtime.current_worker()
            or captured.tenant != runtime._tenant_id
        ):
            raise ValueError("H1 V2 candidate differs from the live runtime")
        result = _read(runtime, captured, expected_head=expected_head)
        _issued[id(result)] = (runtime, result)
        return result


def recheck_h1_v2_seal(runtime: CommonCliExecutionRuntime, preflight: H1V2SealPreflight) -> bool:
    """Consume and freshly recompute an H1 preflight for the publication guard."""
    from .h1_owner_inventory import H1OwnerInventoryFailure

    issued = _issued.pop(id(preflight), None)
    if issued != (runtime, preflight):
        return False
    with runtime._authority_gate().hold():
        try:
            fresh = _read(
                runtime,
                preflight.captured_run,
                expected_head=preflight.captured_run.head,
            )
        except H1OwnerInventoryFailure, OSError, TypeError, ValueError:
            return False
    return fresh == preflight


__all__ = ["preflight_h1_v2_seal", "recheck_h1_v2_seal"]
