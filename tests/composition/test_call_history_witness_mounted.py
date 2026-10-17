"""Post-owner admission retains fresh authority while reusing verified semantics."""

import json
import math
import sqlite3
from contextlib import closing, nullcontext
from dataclasses import fields, replace
from types import SimpleNamespace

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.effects_broker import EffectsIntegrityError
from chiplog.adapters.driven.loop_sqlite import LoopIntegrityError
from chiplog.architecture.r7_runtime import (
    R14_EXECUTION_NATIVE_SEALED_ACCOUNTING_V39_PRODUCTION_MANIFEST,
)
from chiplog.composition import r13_runtime, r14_loop_history
from chiplog.composition import r14_call_authority as call_authority
from chiplog.composition import r14_call_history_witness as history_witness
from chiplog.composition import r14_call_preview as call_preview
from chiplog.composition import r16_dispatch_inputs as dispatch_inputs
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_call_acceptance_port import (
    CallAcceptanceAdoption,
    CallAcceptanceTarget,
)
from chiplog.composition.r14_call_history_witness import (
    _supported_prefix,
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
from chiplog.verification.r8_surface import R8SurfaceViolation, verify_offline_import_boundary
from tests.support.h1_cli_execution import admit_runtime_slot
from tests.support.native_cancelled import native_response


def test_call_history_cache_recognizes_authenticated_open_resolution_prefix() -> None:
    """The exact mounted OPEN/resolution loop families remain eligible for reuse."""
    expected = {
        "agent_loop.native-one-call-recovery-profile.v5": {
            "chiplog.execution.native-one-call-recovery-profile.v5",
        },
        "agent_loop.accepted-open-recovery.v1": {
            "chiplog.execution.accepted-open-accounting.v1",
            "chiplog.execution.accepted-open-recovery-opening.v1",
            "chiplog.execution.accepted-open-terminal-disposition.v1",
        },
        "agent_loop.execution-suspension.v1": {
            "chiplog.execution.suspension-baseline.v2",
            "chiplog.agent-loop.execution-record.v2",
            "chiplog.execution.suspension-pair.v2",
        },
        "agent_loop.original-resolver-authority-policy.v1": {
            "chiplog.loop.original-resolver-authority-policy.v1",
            "chiplog.loop.original-effect-evidence-reducer-policy.v1",
        },
        "agent_loop.original-resolver-invocation.v1": {
            "chiplog.loop.original-resolver-invocation.v1",
        },
        "agent_loop.original-call-resolution.v1": {
            "chiplog.loop.original-resolution-basis.v1",
            "chiplog.loop.original-obligation-closure.v1",
            "chiplog.loop.recovered-call-outcome.v1",
            "chiplog.loop.original-resolver-batch.v1",
        },
        "agent_loop.loop-semantic-reduction.v1": {
            "chiplog.loop.semantic-reduction.v1",
        },
        "agent_loop.execution-resume.v1": {
            "chiplog.agent-loop.execution-record.v2",
        },
        "agent_loop.execution-next-turn.v1": {
            "chiplog.agent-loop.execution-record.v2",
        },
    }
    entries = [
        (
            str(index),
            None,
            json.dumps(
                {
                    "kind": "DECIDED",
                    "operation_kind": operation,
                    "records": [{"schema": schema} for schema in sorted(schemas)],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode(),
        )
        for index, (operation, schemas) in enumerate(expected.items())
    ]
    entries.insert(
        0,
        (
            "profile",
            None,
            b'{"kind":"DECIDED","operation_kind":"agent_loop.native-one-call-recovery-profile.v5",'
            b'"records":[{"schema":"chiplog.execution.native-one-call-recovery-profile.v5"}]}',
        ),
    )
    assert _supported_prefix(tuple(entries))

    unknown = (
        "unknown",
        None,
        b'{"kind":"DECIDED","operation_kind":"agent_loop.unregistered.v1",'
        b'"records":[{"schema":"chiplog.unknown.v1"}]}',
    )
    assert not _supported_prefix((*entries, unknown))

    wrong_schema = (
        "wrong-schema",
        None,
        b'{"kind":"DECIDED","operation_kind":"agent_loop.execution-resume.v1",'
        b'"records":[{"schema":"chiplog.execution.resume.v1"}]}',
    )
    assert not _supported_prefix((*entries, wrong_schema))


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

        def no_semantic_replay(*args, **kwargs) -> None:
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

        expected_entries = runtime._loop_decisions().entries()
        assert len(expected_entries) == 20
        decoded_rows: list[bytes | str] = []
        native_loads = r13_runtime._orjson_loads

        def count_native_loads(raw: bytes | str) -> object:
            decoded_rows.append(raw)
            return native_loads(raw)

        with monkeypatch.context() as patch:
            patch.setattr(r13_runtime, "_orjson_loads", count_native_loads)
            assert runtime._pending() == ()
        assert decoded_rows == [payload for _, _, payload in expected_entries]
        assert len(decoded_rows) == 20

        before = read_execution_call_history(runtime)
        (row,) = before[1].ordered_calls
        full_history_reads = []
        original_preview_history_reader = call_preview.read_execution_call_history

        def counted_preview_history_reader(current_runtime):
            full_history_reads.append(current_runtime)
            return original_preview_history_reader(current_runtime)

        monkeypatch.setattr(
            call_preview, "read_execution_call_history", counted_preview_history_reader
        )
        preview = await runtime.preview_call_acceptance(
            "hermetic-ingress",
            CallAcceptanceTarget(
                original_call_id=row.original_call_id,
                initialized=row.initialized,
                current_run=execution_run_head(sealed),
            ),
        )
        assert full_history_reads == [runtime]
        adoption = CallAcceptanceAdoption(
            act_id="history-cache-adoption", preview_bytes=preview.canonical_bytes()
        )
        cache_lookup_observations = []
        original_cached_history_reader = history_witness.read_cached_execution_call_history

        def observed_cached_history_reader(current_runtime):
            entry = current_runtime._call_history_cache
            current_inputs = history_witness.capture_call_history_inputs(current_runtime)
            changed_fields = (
                tuple(
                    item.name
                    for item in fields(entry[0])
                    if getattr(entry[0], item.name) != getattr(current_inputs, item.name)
                )
                if entry is not None and current_inputs is not None
                else ()
            )
            result = original_cached_history_reader(current_runtime)
            cache_lookup_observations.append(
                (entry is not None, current_inputs is not None, result is not None, changed_fields)
            )
            return result

        monkeypatch.setattr(
            history_witness,
            "read_cached_execution_call_history",
            observed_cached_history_reader,
        )
        await runtime.accept_call("hermetic-ingress", adoption)
        assert cache_lookup_observations == [(True, True, True, ())]
        assert runtime._call_history_cache is not None
        inputs, cached_history = runtime._call_history_cache

        dispatch_cache_observations = []
        original_dispatch_cache_reader = history_witness.read_cached_execution_call_history

        def observed_dispatch_cache_reader(current_runtime):
            result = original_dispatch_cache_reader(current_runtime)
            dispatch_cache_observations.append(result)
            return result

        monkeypatch.setattr(
            history_witness,
            "read_cached_execution_call_history",
            observed_dispatch_cache_reader,
        )
        with runtime._authority_gate().hold():
            dispatch_history = dispatch_inputs._verified_call_history_for_dispatch(runtime)
        assert dispatch_cache_observations == [cached_history]
        assert dispatch_history == cached_history

        fallback_cache_results = []
        fallback_history_reads = []

        def unsupported_dispatch_cache_inputs(_runtime) -> None:
            return None

        def observed_fallback_cache_reader(current_runtime):
            result = original_dispatch_cache_reader(current_runtime)
            fallback_cache_results.append(result)
            return result

        def counted_fallback_history_reader(current_runtime):
            fallback_history_reads.append(current_runtime)
            return read_execution_call_history(current_runtime)

        with monkeypatch.context() as patch:
            patch.setattr(
                history_witness,
                "read_cached_execution_call_history",
                observed_fallback_cache_reader,
            )
            patch.setattr(
                history_witness,
                "capture_call_history_inputs",
                unsupported_dispatch_cache_inputs,
            )
            patch.setattr(
                r14_loop_history,
                "read_execution_call_history",
                counted_fallback_history_reader,
            )
            with runtime._authority_gate().hold():
                fallback_history = dispatch_inputs._verified_call_history_for_dispatch(runtime)
        assert fallback_cache_results == [None]
        assert fallback_history_reads == [runtime]
        assert fallback_history == cached_history
        with runtime._authority_gate().hold():
            runtime._call_history_cache = None

        refreshed_cache_results = []
        refreshed_history_reads = []

        def observed_refreshed_cache_reader(current_runtime):
            result = original_dispatch_cache_reader(current_runtime)
            refreshed_cache_results.append(result)
            return result

        def counted_refreshed_history_reader(current_runtime):
            refreshed_history_reads.append(current_runtime)
            return read_execution_call_history(current_runtime)

        with monkeypatch.context() as patch:
            patch.setattr(
                history_witness,
                "read_cached_execution_call_history",
                observed_refreshed_cache_reader,
            )
            patch.setattr(
                r14_loop_history,
                "read_execution_call_history",
                counted_refreshed_history_reader,
            )
            with runtime._authority_gate().hold():
                refreshed_history = dispatch_inputs._verified_call_history_for_dispatch(runtime)
                repeated_history = dispatch_inputs._verified_call_history_for_dispatch(runtime)
        assert refreshed_history_reads == [runtime]
        assert refreshed_cache_results == [None, cached_history]
        assert refreshed_history == repeated_history == cached_history

        assert inputs.runtime is runtime
        assert cached_history[0].tenant_head == before[0].tenant_head + 1
        assert inputs.authority_commitment == capture_authority_storage_state(runtime._database)[0]

        def unexpected_full_history_read(*args, **kwargs) -> None:
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


def test_pending_json_decoder_preserves_stdlib_duplicate_nan_and_large_integer_values() -> None:
    duplicate_key = b'{"value":1,"value":2}'
    assert r13_runtime._decode_pending_json(duplicate_key) == json.loads(duplicate_key)

    nan_value = r13_runtime._decode_pending_json(b'{"value":NaN}')
    assert isinstance(nan_value, dict) and math.isnan(nan_value["value"])

    large_integer = b'{"value":9223372036854775808}'
    assert r13_runtime._decode_pending_json(large_integer) == json.loads(large_integer)


@pytest.mark.parametrize("number", (-(2**63) - 1, -(2**63), 2**64 - 1, 2**64, 2**80 + 1, 1.25))
@pytest.mark.parametrize("nested", (False, True))
@pytest.mark.parametrize("byte_input", (False, True))
def test_pending_json_decoder_preserves_numeric_value_and_type(number, nested, byte_input) -> None:
    value = {"items": [number]} if nested else number
    raw = json.dumps(value)
    if byte_input:
        raw = raw.encode()
    actual = r13_runtime._decode_pending_json(raw)
    expected = json.loads(raw)
    actual_number = actual["items"][0] if nested else actual
    assert actual == expected
    assert type(actual_number) is type(number)


@pytest.mark.parametrize(
    "relative",
    ["chiplog/composition/r13_runtime.py", "chiplog/adapters/driven/deployment_trust/_journal.py"],
)
def test_r8_registers_native_json_dependency_only_at_reviewed_readers(tmp_path, relative) -> None:
    pending_reader = tmp_path / "src" / relative
    composition = pending_reader.parent
    composition.mkdir(parents=True)
    pending_reader.write_text("import orjson\n", encoding="utf-8")
    verify_offline_import_boundary(tmp_path)

    unrelated = composition / "unrelated.py"
    unrelated.write_text("import orjson\n", encoding="utf-8")
    with pytest.raises(R8SurfaceViolation, match="unregistered dependency"):
        verify_offline_import_boundary(tmp_path)
    unrelated.unlink()

    pending_reader.write_text("from orjson import loads\n", encoding="utf-8")
    with pytest.raises(R8SurfaceViolation, match="unregistered dependency"):
        verify_offline_import_boundary(tmp_path)


def no_replay(*args, **kwargs) -> None:
    raise AssertionError("post-owner writer repeated full semantic history")
