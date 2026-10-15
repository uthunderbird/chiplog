"""A native validation reuses its derived command, never authentication success."""

import json
from dataclasses import replace

import pytest

from chiplog.architecture.r7_runtime import R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST
from chiplog.composition import r14_recovery_publication as publication
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.native_cancelled import native_response, selected_native_cancelled


@pytest.mark.asyncio
async def test_native_selected_validation_constructs_once_and_rejects_damaged_exchange(
    tmp_path, monkeypatch
):
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "command-reuse.sqlite", resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_NEXT_TURN_V27_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as runtime:
        await runtime.publish_native_recovery_profile()
        await selected_native_cancelled(runtime, bound=True)
        await runtime.publish_native_one_call_cancelled_accounting()
        await runtime.publish_execution_accounting_readiness()
        with runtime._authority_gate().hold():
            entries = tuple(json.loads(raw) for _, _, raw in runtime._loop_decisions().entries())
            entry, = (item for item in entries if item.get("kind") == "DECIDED"
                      and item.get("operation_kind") == publication.OPERATION)
            physical = runtime._publication(entry)
            original = publication.command
            calls = []

            def counted(evidence):
                calls.append(evidence)
                return original(evidence)

            monkeypatch.setattr(publication, "command", counted)
            retained = publication.validate_selected(runtime, entry, physical)
            assert len(calls) == 1, "one validation must derive the recovery command only once"
            assert original(retained) == physical
            calls.clear()
            assert runtime._full_recovery_cut_source.authenticate_selected_held(
                runtime, retained, entry
            ) is True
            assert len(calls) == 1

            class DenyingNativeSource(NativeRecoveryCutSource):
                def authenticate_selected_held(self, runtime, evidence, selected_entry):
                    assert evidence == retained and selected_entry == entry
                    denied.append(True)
                    return False

            denied = []
            with monkeypatch.context() as scoped:
                scoped.setattr(runtime, "_full_recovery_cut_source", DenyingNativeSource())
                with pytest.raises(ValueError, match="unauthenticated"):
                    publication.validate_selected(runtime, entry, physical)
            assert denied == [True]
            calls.clear()
            with pytest.raises(ValueError, match="unauthenticated"):
                publication.validate_selected(
                    runtime, entry, replace(physical, request_fingerprint="0" * 64)
                )
            assert len(calls) == 1
            bad_result = retained.accounting_result.model_copy(
                update={"source_request_fingerprint": "0" * 64}
            )
            bad_reply = retained.accounting_reply.model_copy(update={"canonical_payload": b"{}"})
            assert retained.actor_budget_v1 is not None
            bad_budget = retained.actor_budget_v1.model_copy(
                update={"issued_monotonic_ns": retained.actor_budget_v1.issued_monotonic_ns + 1}
            )
            for damaged in (
                retained.model_copy(update={"accounting_result": bad_result}),
                retained.model_copy(update={"accounting_reply": bad_reply}),
                retained.model_copy(update={"actor_budget_v1": bad_budget}),
            ):
                bad_entry = dict(
                    entry, execution_recovery_evidence=damaged.canonical_bytes().decode()
                )
                with pytest.raises(ValueError):
                    publication.validate_selected(runtime, bad_entry, physical)
                assert runtime._full_recovery_cut_source.authenticate_selected_held(
                    runtime, damaged, bad_entry
                ) is False
            assert tuple(
                json.loads(raw) for _, _, raw in runtime._loop_decisions().entries()
            ) == entries
            assert len(resources.require_original_provider().transfers) == 0
