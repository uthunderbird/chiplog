"""RED installed contracts for historical CONVERSATION recovery after an H1 V2 seal.

The selected V2 native prefix, retained P scope wires, and the actual B
completion exchange are the only eligible inputs.  This file deliberately
does not construct a historical inventory, policy, completion receipt, ROOT,
or stage record from caller DTOs.
"""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from chiplog.capabilities.projections.conversation_preparation_contracts import (
    PrepareConversationCompletionV1,
)
from chiplog.composition.common_cli_execution_runtime import open_installed_h1_runtime
from chiplog.composition.common_execution_driver_contracts import ExecutionDriverRejectedV1
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_preissuance_registration import H1PreissuanceSourceViolation
from chiplog.composition.h1_v2_recovery_native_source import H1V2RecoveryNativeSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform.broker import PublicPortSuccess
from tests.composition.test_h1_postseal_recovery_stages import (
    _OPERATIONS,
    _install_recorders,
    _RecordingEngine,
    _seal_v3_then_v2_without_recovery,
    _stage_calls,
)
from tests.composition.test_h1_recovery_stage_source import _admit_other
from tests.support.h1_cli_execution import advance
from tests.support.h1_installed_launch import installed_slot, prepare_installed_slot

_RED_REASON = (
    "the installed recovery source has no seal-bounded historical CONVERSATION "
    "reconstruction, P replay bridge, or B-issued accepted-completion provenance"
)


def _resources(tmp_path: Path, label: str = "") -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / f"dispatch-custody{label}"
    )


def _conversation_input(runtime: object) -> PrepareConversationCompletionV1:
    states = cast(Any, runtime)._h1_postseal_recovery_journal.scan().states_by_root
    assert len(states) == 1
    raw, _command_id = states[0][1].stage_input("CONVERSATION")
    request = PrepareConversationCompletionV1.model_validate_json(raw)
    assert request.canonical_json_bytes() == raw
    return request


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_RED_REASON)
async def test_historical_conversation_input_keeps_the_original_selected_and_physical_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restart uses the sealed inventory, even after an unrelated publication.

    The source-cut commitment, tenant sequence, selected decision and physical
    source record make the full original selected/physical inventory observable
    without exposing it as a caller-shaped capability.
    """
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as sealed:
            original, initial = await _seal_v3_then_v2_without_recovery(sealed)

        first_events: list[tuple[str, object]] = []
        async with open_installed_h1_runtime(launch, resources=resources) as recovered:
            first_engine = _install_recorders(recovered, first_events, monkeypatch)
            first = await recovered.advance_execution(advance(initial, original))
            assert first.phase == "RUNNING"
            first_calls = _stage_calls(first_engine)
            first_request = _conversation_input(recovered)
            assert first_calls["CONVERSATION"].canonical_payload == (
                first_request.canonical_json_bytes()
            )
            assert first_request.original_completion_request_bytes == (
                first_calls["COMPLETION"].canonical_payload
            )
            completion_result = first_engine.results[first_calls["COMPLETION"].request_id]
            assert isinstance(completion_result, PublicPortSuccess)
            assert first_request.loop_preparation_bytes == completion_result.canonical_payload
            before = first_request.canonical_json_bytes()

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-later")
        ) as later:
            # This is a real later native publication.  It must not become the
            # historical inventory for the already selected original V2 seal.
            await later.drive_input(await _admit_other(later))
            source_owner = later._h1_conversation_source_port
            assert source_owner is not None

            def current_inventory_is_not_historical(*_args: object) -> None:
                pytest.fail("historical recovery read current conversation inventory")

            monkeypatch.setattr(
                source_owner,
                "_read_complete_current_inventory",
                current_inventory_is_not_historical,
            )
            replay = await later.advance_execution(advance(initial, original))
            assert replay.phase == "RUNNING"
            after = _conversation_input(later)
            assert after.canonical_json_bytes() == before
            assert after.source_cut == first_request.source_cut


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_RED_REASON)
async def test_historical_p_policy_replay_is_original_registration_not_a_caller_dto(
    tmp_path: Path,
) -> None:
    """P alone reopens original policy and registration from a selected seal."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            original, _initial = await _seal_v3_then_v2_without_recovery(runtime)
            selected = H1V2RecoveryNativeSource(runtime).locate_selected_seal(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
            )
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            issue = port._issue_historical_recovery_source
            replay = port._replay_historical_conversation_policy
            source_cap = issue(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected,
            )
            policy = replay(source_cap)
            registration = policy.workspace_policy.registration
            assert policy.original_issuance_ref.tenant_id == original.identity.tenant_id
            assert policy.original_issuance_bytes
            assert policy.scope_policy_bytes
            assert registration.accepted_policy == policy.scope_policy_ref
            assert policy.custody_entry_generation >= 0
            assert policy.custody_entry_digest
            with pytest.raises((H1PreissuanceSourceViolation, TypeError, ValueError)):
                replay(object())


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_RED_REASON)
async def test_historical_p_source_rejects_wrong_locator_identity_and_foreign_runtime_capability(
    tmp_path: Path,
) -> None:
    """Locators find history; they cannot select another seal or issuer table."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as issuer:
            original, _initial = await _seal_v3_then_v2_without_recovery(issuer)
            other, _other_initial = await _seal_v3_then_v2_without_recovery(issuer)
            port = issuer._h1_preissuance_registration_source_port
            assert port is not None
            issue = port._issue_historical_recovery_source
            replay = port._replay_historical_conversation_policy
            selected = H1V2RecoveryNativeSource(issuer).locate_selected_seal(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
            )
            other_seal = H1V2RecoveryNativeSource(issuer).locate_selected_seal(
                original_identity=other.identity,
                original_fingerprint=other.original_driver_command_fingerprint(),
            )
            with pytest.raises((H1PreissuanceSourceViolation, TypeError, ValueError)):
                issue(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=other_seal,
                )
            with pytest.raises((H1PreissuanceSourceViolation, TypeError, ValueError)):
                issue(
                    original_identity=dataclasses.replace(
                        original.identity, driver_command_id="driver:wrong-original"
                    ),
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=selected,
                )
            source_cap = issue(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
                selected_seal=selected,
            )
            with pytest.raises((H1PreissuanceSourceViolation, TypeError, ValueError)):
                replay(copy.copy(source_cap))
            conversation_owner = issuer._h1_conversation_source_port
            assert conversation_owner is not None
            historical_prepare = (
                conversation_owner._prepare_historical_conversation_completion_request
            )
            # P's opaque source receipt alone is insufficient.  A must ask the
            # installed B issuer to recognize the exact accepted completion;
            # a caller-shaped replacement cannot establish its provenance.
            with pytest.raises((H1PreissuanceSourceViolation, TypeError, ValueError)):
                historical_prepare(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=selected,
                    p_source_cap=source_cap,
                    accepted_completion=object(),
                )

        async with open_installed_h1_runtime(
            launch, resources=_resources(tmp_path, "-foreign")
        ) as foreign:
            foreign_port = foreign._h1_preissuance_registration_source_port
            assert foreign_port is not None
            foreign_replay = foreign_port._replay_historical_conversation_policy
            with pytest.raises((H1PreissuanceSourceViolation, TypeError, ValueError)):
                foreign_replay(source_cap)


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ("missing", "malformed"))
@pytest.mark.xfail(strict=True, reason=_RED_REASON)
async def test_historical_conversation_holds_on_missing_or_malformed_retained_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: Literal["missing", "malformed"]
) -> None:
    """The mutation happens before the real selected DECIDED append, never in a DTO."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            append = runtime._append_decision

            def append_defective(decision: dict[str, object]) -> None:
                wires = decision.get("h1_preseal_p_scope_wires_v1")
                if decision.get("kind") == "DECIDED":
                    if defect == "missing":
                        decision.pop("h1_preseal_p_scope_wires_v1", None)
                    elif isinstance(wires, dict):
                        wires["version"] = 999
                append(decision)

            monkeypatch.setattr(runtime, "_append_decision", append_defective)
            original, _initial = await _seal_v3_then_v2_without_recovery(runtime)
            selected = H1V2RecoveryNativeSource(runtime).locate_selected_seal(
                original_identity=original.identity,
                original_fingerprint=original.original_driver_command_fingerprint(),
            )
            port = runtime._h1_preissuance_registration_source_port
            assert port is not None
            issue = port._issue_historical_recovery_source
            with pytest.raises((H1PreissuanceSourceViolation, TypeError, ValueError)):
                issue(
                    original_identity=original.identity,
                    original_fingerprint=original.original_driver_command_fingerprint(),
                    selected_seal=selected,
                )


class _ConversationResultMutator(_RecordingEngine):
    """Return an authenticated-looking but semantically wrong conversation result."""

    async def call(self, sent: Any) -> Any:
        returned = await super().call(sent)
        if sent.operation_id == _OPERATIONS["CONVERSATION"]:
            assert isinstance(returned, PublicPortSuccess)
            return returned.model_copy(update={"canonical_payload": b"{}"})
        return returned


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason=_RED_REASON)
async def test_historical_conversation_result_is_validated_before_effects_can_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A durable CONVERSATION result is evidence only when it matches its exact input."""
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    resources = _resources(tmp_path)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=resources) as sealed:
            original, initial = await _seal_v3_then_v2_without_recovery(sealed)

        async with open_installed_h1_runtime(launch, resources=resources) as recovered:
            private = cast(Any, recovered)
            installed = private._supervisor.runtime()
            engine = _ConversationResultMutator(installed, [])
            monkeypatch.setattr(private._supervisor, "runtime", lambda: engine)
            result = await recovered.advance_execution(advance(initial, original))
            assert isinstance(result, ExecutionDriverRejectedV1)
            assert result.code == "HOLD"
            operations = tuple(call.operation_id for call in engine.calls)
            assert operations == (
                _OPERATIONS["COMPLETION"],
                _OPERATIONS["CONVERSATION"],
            )
            states = private._h1_postseal_recovery_journal.scan().states_by_root
            assert len(states) == 1
            state = states[0][1]
            assert "CONVERSATION" not in dict(state.results)
