"""Post-owner admission retains fresh authority while reusing verified semantics."""

import sqlite3
from contextlib import closing, nullcontext
from dataclasses import replace
from types import SimpleNamespace

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
)
from chiplog.composition import r14_call_authority as call_authority
from chiplog.composition import r14_call_history_witness as history_witness
from chiplog.composition import r14_call_preview as call_preview
from chiplog.composition import r14_loop_history
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_history_witness import (
    capture_call_history_inputs,
    read_cached_execution_call_history,
    seed_call_history_cache,
)
from chiplog.composition.r14_call_preview import execution_run_head
from chiplog.composition.r14_loop_history import read_execution_call_history
from chiplog.composition.r14_native_recovery_cut import NativeRecoveryCutSource
from chiplog.composition.r16_dispatch_publication import authenticate
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from chiplog.platform._owner_publication_contracts import (
    JournalSelectedPublication,
    PublicationRejected,
)
from chiplog.platform.authority_reads import (
    capture_authority_storage_state,
)
from chiplog.platform.owner_publications import BrokerPublicationCoordinator
from chiplog.platform.workspace_snapshot import workspace_snapshot
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    (
        "unchanged",
        "loop",
        "owner",
        "preview",
        "h1-workspace",
        "dashboard",
        "custody",
        "historical-digest",
        "revoke",
        "source",
        "snapshot",
        "deadline",
        "missing-key",
        "missing-witness",
        "copied-preparation",
        "physical",
    ),
)
async def test_native_post_owner_admission_uses_same_verified_history_with_fresh_capture(
    tmp_path, monkeypatch, change
):
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "witness.sqlite",
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v5")
        wire = await admit_runtime_slot(runtime, "call-history-witness")
        receipt = await runtime.drive_input(wire)
        started = await runtime.begin_execution(
            "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", started.run_id, started.head, native_one_call_context=True
        )
        sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
        history = read_execution_call_history(runtime)
        (row,) = history[1].ordered_calls
        before = history
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=row.original_call_id,
                initialized=row.initialized,
                current_run=execution_run_head(sealed),
            ),
        )
        authority = call_authority.CallPublicationAuthority(
            runtime, await authenticate(runtime, "hermetic-ingress")
        )
        batch = await authority.prepare_fresh(
            "hermetic-ingress",
            CallAcceptanceAdoption(
                act_id="witness-adopt",
                preview_bytes=preview.canonical_bytes(),
            ),
        )
        prepared, value, witness = authority._issued[-1]
        assert witness is not None
        if change != "unchanged":
            if change in ("loop", "owner", "preview", "h1-workspace", "dashboard"):
                with runtime._authority_gate().hold():
                    if change == "loop":
                        journal = runtime._loop_decisions()
                    elif change == "owner":
                        journal = runtime._owner_decisions()._raw
                    elif change == "preview":
                        journal = runtime._require_call_preview_journal()
                    else:
                        suffix = (
                            ".h1-workspace-issuance"
                            if change == "h1-workspace"
                            else ".workspace-issuance"
                        )
                        journal = IndependentTenantDecisionJournal.for_existing_authority_bundle(
                            runtime._database.with_suffix(suffix),
                            authority_gate=runtime._authority_gate(),
                        )
                    with (
                        closing(journal)
                        if change in ("h1-workspace", "dashboard")
                        else nullcontext(journal)
                    ):
                        entries = journal.entries()
                        journal.append(
                            b'{"unregistered":true}', entries[-1][0] if entries else None
                        )
            elif change == "custody":
                resources._credential_id += "/changed"
            elif change == "historical-digest":
                monkeypatch.setattr(resources, "observation_digest", lambda observation: "0" * 64)
            elif change == "revoke":
                resources.revoke()
            elif change == "source":
                monkeypatch.setattr(
                    runtime._full_recovery_cut_source,
                    "authenticate_selected_held",
                    lambda *a: False,
                )
                with runtime._authority_gate().hold():
                    assert capture_call_history_inputs(runtime) is None
            elif change == "deadline":
                expiry = min(
                    s.valid_until_ns for s in value.retained.loop_request.binding.cut.sources
                )
                monkeypatch.setattr(
                    call_authority, "time", SimpleNamespace(monotonic_ns=lambda: expiry + 1)
                )
            elif change == "missing-key":
                key = runtime._database.with_suffix(".h1-workspace-issuance.key")
                key.unlink()
            elif change == "missing-witness":
                authority._issued[-1] = prepared, value, None
                monkeypatch.setattr(call_authority, "read_execution_call_history", no_replay)
            elif change == "physical":
                with sqlite3.connect(runtime._database) as connection:
                    changed = connection.execute(
                        "UPDATE records SET canonical_bytes=? WHERE record_id=?",
                        (b"{}", before[0].records[-1].head),
                    )
                    assert changed.rowcount == 1
                runtime._commitment_journal.commit(
                    runtime._tenant_id, capture_authority_storage_state(runtime._database)[0]
                )
            if change == "snapshot":
                with workspace_snapshot(runtime._database):
                    result = authority.check_prepared(prepared)
                assert result == "INDETERMINATE"
            elif change == "missing-witness":
                with pytest.raises(AssertionError, match="full semantic history"):
                    authority.check_prepared(prepared)
            elif change == "copied-preparation":
                assert authority.check_prepared(replace(prepared)) == "DENIED"
            elif change == "physical":
                coordinator = BrokerPublicationCoordinator(
                    runtime._appender, authority, runtime._owner_decisions()
                )
                assert isinstance(await coordinator.commit(batch), PublicationRejected)
            else:
                disposition = authority.check_prepared(prepared)
                if change == "historical-digest":
                    with pytest.raises(LoopIntegrityError):
                        read_execution_call_history(runtime)
                assert disposition in ("STALE", "INDETERMINATE")
            if change == "missing-key":
                assert not key.exists()
            with sqlite3.connect(runtime._database) as connection:
                assert connection.execute(
                    "SELECT count(*) FROM publications WHERE operation_kind='effects.accept_call'"
                ).fetchone() == (0,)
                assert connection.execute("SELECT head FROM tenant_heads").fetchone() == (
                    before[0].tenant_head,
                )
            assert resources.require_original_provider().transfers == ()
            return
        captures = []
        original_capture = call_authority.capture_dispatch

        def fresh_capture(*args, **kwargs):
            captures.append(kwargs.get("execution_call_history"))
            return original_capture(*args, **kwargs)

        def no_semantic_replay(*args, **kwargs):
            raise AssertionError("post-owner writer repeated full semantic history")

        monkeypatch.setattr(call_authority, "capture_dispatch", fresh_capture)
        monkeypatch.setattr(call_authority, "read_execution_call_history", no_semantic_replay)
        coordinator = BrokerPublicationCoordinator(
            runtime._appender, authority, runtime._owner_decisions()
        )
        result = await coordinator.commit(batch)
        assert isinstance(result, JournalSelectedPublication), result
        assert len(captures) == 1 and captures[0] is not None
        assert captures[0] == before
        after = read_execution_call_history(runtime)
        assert after[0].tenant_head == before[0].tenant_head + 1
        assert after[1].ordered_calls[0].acceptance.kind == "CONSEQUENTIAL_ACCEPTED"
        selected = runtime._owner_decisions().lookup(runtime._tenant_id, batch.identity.command_id)
        assert selected is not None and authority.materialization_state(selected) == "COMPLETE"
        assert resources.require_original_provider().transfers == ()


@pytest.mark.asyncio
async def test_public_accept_seeds_exact_history_cache_and_main_commitment_change_misses(
    tmp_path, monkeypatch
):
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "history-cache.sqlite",
        resources=resources,
        responses=(native_response(),),
        manifest=R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
        recovery_cut_source=NativeRecoveryCutSource(),
    ) as runtime:
        await runtime.publish_native_recovery_profile(version="v5")
        wire = await admit_runtime_slot(runtime, "call-history-cache")
        receipt = await runtime.drive_input(wire)
        started = await runtime.begin_execution(
            "hermetic-ingress", receipt.stable_run_lineage_id, receipt.selected_run_head.head
        )
        captured = await runtime.capture_execution(
            "hermetic-ingress", started.run_id, started.head, native_one_call_context=True
        )
        sealed = await runtime.seal_execution("hermetic-ingress", captured.run_id, captured.head)
        before = read_execution_call_history(runtime)
        (row,) = before[1].ordered_calls
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=row.original_call_id,
                initialized=row.initialized,
                current_run=execution_run_head(sealed),
            ),
        )
        adoption = CallAcceptanceAdoption(
            act_id="history-cache-adoption", preview_bytes=preview.canonical_bytes()
        )
        await runtime.accept_call("hermetic-ingress", adoption)

        assert runtime._call_history_cache is not None
        inputs, cached_history = runtime._call_history_cache
        assert inputs.runtime is runtime
        assert cached_history[0].tenant_head == before[0].tenant_head + 1
        assert inputs.authority_commitment == capture_authority_storage_state(runtime._database)[0]

        def unexpected_full_history_read(*args, **kwargs):
            raise AssertionError("unchanged post-accept preview join missed verified history cache")

        original_preview_reader = call_preview.read_execution_call_history
        monkeypatch.setattr(
            call_preview, "read_execution_call_history", unexpected_full_history_read
        )
        with runtime._authority_gate().hold():
            assert read_cached_execution_call_history(runtime) == cached_history
        preview_values, joined = call_preview._read_call_previews_and_history(
            runtime, require_history=True
        )
        assert joined == cached_history
        assert len(preview_values) == 1
        assert preview_values[0].preview.canonical_bytes() == adoption.preview_bytes

        actual_anchor = runtime._commitment_journal.load(runtime._tenant_id)
        assert actual_anchor == inputs.authority_commitment
        runtime._commitment_journal.commit(runtime._tenant_id, "0" * 64)
        fallback_reads = []

        def observed_full_reader(current_runtime):
            fallback_reads.append(current_runtime)
            return original_preview_reader(current_runtime)

        with monkeypatch.context() as patch:
            patch.setattr(call_preview, "read_execution_call_history", observed_full_reader)
            with pytest.raises(EffectsIntegrityError):
                call_preview._read_call_previews_and_history(runtime, require_history=True)
        assert fallback_reads == [runtime]
        runtime._commitment_journal.commit(runtime._tenant_id, actual_anchor)
        with runtime._authority_gate().hold():
            seed_call_history_cache(runtime)
            assert runtime._call_history_cache is not None

        with sqlite3.connect(runtime._database) as connection:
            connection.execute("CREATE TABLE unrelated_unregistered_table (value TEXT)")
        fallback_reads.clear()
        with monkeypatch.context() as patch:
            patch.setattr(call_preview, "read_execution_call_history", observed_full_reader)
            with pytest.raises(EffectsIntegrityError):
                call_preview._read_call_previews_and_history(runtime, require_history=True)
        assert fallback_reads == [runtime]
        with sqlite3.connect(runtime._database) as connection:
            connection.execute("DROP TABLE unrelated_unregistered_table")

        full_reads = []
        original_reader = r14_loop_history.read_execution_call_history

        def count_full_reader(current_runtime):
            full_reads.append(current_runtime)
            return original_reader(current_runtime)

        with monkeypatch.context() as patch:
            patch.setattr(history_witness, "capture_call_history_inputs", lambda _: None)
            patch.setattr(r14_loop_history, "read_execution_call_history", count_full_reader)
            with runtime._authority_gate().hold():
                seed_call_history_cache(runtime)
            assert full_reads == [runtime]
            assert runtime._call_history_cache is None

        with runtime._authority_gate().hold():
            seed_call_history_cache(runtime)
        with monkeypatch.context() as patch:
            patch.setattr(
                runtime,
                "_require_no_pending",
                lambda: (_ for _ in ()).throw(RuntimeError("pending owner publication")),
            )
            with (
                runtime._authority_gate().hold(),
                pytest.raises(RuntimeError, match="pending owner publication"),
            ):
                read_cached_execution_call_history(runtime)

        with sqlite3.connect(runtime._database) as connection:
            row_id = connection.execute(
                "SELECT record_id FROM records WHERE tenant_id=? ORDER BY record_id LIMIT 1",
                (runtime._tenant_id,),
            ).fetchone()[0]
            changed = connection.execute(
                "UPDATE records SET canonical_bytes=canonical_bytes || X'20' "
                "WHERE tenant_id=? AND record_id=?",
                (runtime._tenant_id, row_id),
            )
            assert changed.rowcount == 1
        with runtime._authority_gate().hold():
            assert read_cached_execution_call_history(runtime) is None
            assert runtime._call_history_cache is None
        assert resources.require_original_provider().transfers == ()


def no_replay(*args, **kwargs):
    raise AssertionError("post-owner writer repeated full semantic history")
