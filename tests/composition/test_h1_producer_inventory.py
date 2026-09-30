"""Mounted contract tests for the complete H1 producer inventory."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import open_installed_h1_scoped_runtime
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_producer_inventory import (
    H1ProducerInventoryReader,
    H1ProducerInventoryViolation,
)
from chiplog.composition.h1_producer_sources import H1ProducerSourceReader
from chiplog.composition.h1_scoped_delivery_authority import H1ScopedDeliveryAuthorityReader
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot
from tests.support.h1_scoped_first_b import (
    issue_real_h1_scoped_authority,
    scoped_resources,
    write_scoped_pins,
)


@pytest.mark.asyncio
async def test_mounted_inventory_has_all_eleven_roles_and_rejects_real_resource_revoke(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            authority_reader, authority_capture = await issue_real_h1_scoped_authority(runtime)
            physical_reader = H1ProducerSourceReader(runtime, authority_reader)
            reader = H1ProducerInventoryReader(runtime, authority_reader, physical_reader)
            capture = await reader.read(authority_capture=authority_capture)

            assert tuple(type(capture.original_sources).model_fields) == (
                "schema_id",
                "trust",
                "planning",
                "semantic_registry",
                "original_adoption",
                "runtime_and_fence",
                "endpoint",
                "credential_lifecycle",
                "deployment_entitlement",
                "clock",
                "effects_history",
                "normative_conflict_generation",
            )
            assert capture.current.sources == capture.original_sources
            assert capture.current.command_fingerprint == capture.command_fingerprint
            assert (
                capture.expected_tenant_head
                == capture.physical_capture._record.physical_cut.tenant_frontier
            )
            assert capture.current_worker == capture.physical_capture.current_worker
            assert tuple(session.owner_id for session in capture.runtime_sessions) == (
                "effects",
                "agent_loop",
                "deployment_trust",
            )
            with runtime._authority_gate().hold():
                assert reader.recheck_held(capture) is capture

            runtime._require_dispatch_resources().revoke()
            with runtime._authority_gate().hold(), pytest.raises(H1ProducerInventoryViolation):
                reader.recheck_held(capture)


@pytest.mark.asyncio
async def test_inventory_rejects_wrong_issuer_and_released_or_closed_capture(
    tmp_path: Path,
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    write_scoped_pins(slot.database_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_scoped_runtime(
            launch, resources=scoped_resources(tmp_path)
        ) as runtime:
            authority_reader, authority_capture = await issue_real_h1_scoped_authority(runtime)
            other_authority_reader = H1ScopedDeliveryAuthorityReader(runtime)
            with pytest.raises(TypeError, match="physical reader"):
                H1ProducerInventoryReader(
                    runtime,
                    authority_reader,
                    H1ProducerSourceReader(runtime, other_authority_reader),
                )
            reader = H1ProducerInventoryReader(
                runtime, authority_reader, H1ProducerSourceReader(runtime, authority_reader)
            )
            capture = await reader.read(authority_capture=authority_capture)
            reader.release(capture)
            with runtime._authority_gate().hold(), pytest.raises(H1ProducerInventoryViolation):
                reader.recheck_held(capture)
            with pytest.raises(TypeError, match="cannot be copied"):
                copy.copy(capture)
            reader.close()
            with pytest.raises(H1ProducerInventoryViolation, match="closed"):
                await reader.read(authority_capture=authority_capture)
