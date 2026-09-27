"""Mounted P source checks for A's private conversation-policy prerequisite."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, cast

import pytest

from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.h1_workspace_policy_v2 import H1OriginalWorkspaceIssuanceV2
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.composition.test_h1_completion_scope_source import _original_and_native
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot


@pytest.mark.asyncio
async def test_installed_p_replays_authenticated_conversation_policy_only_for_held_scope_and_native(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as runtime:
            original, native = await _original_and_native(runtime)
            port = runtime._h1_preissuance_registration_source_port
            native_sources = runtime._h1_native_member_sources
            assert port is not None
            assert native_sources is not None

            scope = await port._capture_completion_scope(native._capture, native, original)
            projected = port._replay_conversation_policy(scope, native)
            original_cut = port._original_cut(original)
            original_issuance = H1OriginalWorkspaceIssuanceV2.model_validate_json(
                projected.original_issuance_bytes
            )
            entry = launch.custody.select(
                projected.workspace_policy.tenant,
                projected.workspace_policy.principal,
                "hermetic-local",
            )

            assert projected.workspace_policy is original_cut.cut.workspace_policy
            assert projected.recipient == (
                native._native.source.complete_ordered_run_lineage[-1].origin.recipient
            )
            assert projected.scope_policy_ref == (
                projected.workspace_policy.registration.accepted_policy
            )
            assert projected.scope_policy_bytes == (
                original_cut.cut.scope.disclosure_policy.canonical_source_bytes
            )
            assert projected.custody_entry_generation == entry.generation
            assert (
                projected.custody_entry_digest
                == hashlib.sha256(entry.canonical_bytes()).hexdigest()
            )
            assert projected.original_issuance_ref == original_cut.selected.issuance_ref
            assert original_issuance.canonical_bytes() == projected.original_issuance_bytes
            assert (
                projected.original_tenant,
                projected.original_run_id,
                projected.original_started_run_head,
                projected.original_turn_id,
                projected.original_worker_session,
            ) == (
                original_issuance.tenant,
                original_issuance.run_id,
                original_issuance.started_run_head,
                original_issuance.turn_id,
                original_issuance.worker_session,
            )

            # A separately issued, valid native cut cannot be paired with the
            # first capability.  Both tokens are real mounted-owner outputs.
            sibling_native = native_sources.capture_current(native._capture)
            sibling_scope = await port._capture_completion_scope(
                sibling_native._capture, sibling_native, original
            )
            assert (
                port._replay_conversation_policy(sibling_scope, sibling_native).recipient
                == projected.recipient
            )
            with pytest.raises(H1PreissuanceSourceViolation, match="native"):
                port._replay_conversation_policy(scope, sibling_native)

            # A subsequent, genuine deployment-trust append changes a replayed
            # constituent.  The old scope must not be projected from retained
            # DTOs after that durable transition.
            with runtime._authority_gate().hold():
                runtime._trust.append_hermetic_output_scope(
                    original_cut.cut.scope.model_copy(
                        update={"scope_id": "unrelated-conversation-policy-scope"}
                    ).canonical_bytes()
                )
            with pytest.raises((H1PreissuanceSourceViolation, ValueError)):
                port._replay_conversation_policy(scope, native)
            with pytest.raises(H1PreissuanceSourceViolation, match="capability"):
                port._replay_conversation_policy(cast(Any, object()), native)
