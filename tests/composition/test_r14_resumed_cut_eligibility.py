"""Collector control seam; authenticated source and resume ports are isolated.

Reaching inventory is required, but does not establish a complete mounted cut.
"""

from types import SimpleNamespace as NS
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.composition import r14_native_recovery_cut as collector
from chiplog.composition import r17_native_one_call_scope as scopes
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _cut
from tests.capabilities.agent_loop.test_original_call_resolution_preparation import (
    request as resolution_request,
)


class InventoryReached(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("selected,no_retry", [(True, False), (False, False), (True, True)])
async def test_resumed_active_run_requires_exact_resume_before_inventory(
    monkeypatch, selected, no_retry
):
    original = cast(ExecutionRunRecord, (await _cut()).complete_ordered_run_lineage[-1])
    ref = collector._run_ref(original)
    run = original.model_copy(
        update={
            "suspension_baseline": ref,
            "original_obligations": (resolution_request().cut.original,),
            "no_retry_references": (ref,) if no_retry else (),
        }
    )
    binding = NS(scope_bytes=b"isolated scope", anchor="anchor")
    scope = NS(
        planned_run_id=run.run_id,
        database_id="database",
        scope_id="scope",
        worker_session_id=run.worker_session,
        authenticated_cli_ref="cli",
    )
    scoped = NS(anchor=binding.anchor, scope=scope)
    facts = NS(
        source=NS(
            original_prepare=NS(original_native_scope_binding=binding),
            target_run_lineage=(collector._run_ref(run),),
        )
    )
    inputs = (object(), object(), object(), ())
    subset = NS(
        selected_facts=facts,
        accepted_closed_outcome=object(),
        accepted_open_outcome=None,
        authenticated_execution_snapshot=NS(records=(run,)),
        authenticated_seals=(object(),),
        authenticated_cancellations=(),
        authenticated_acceptances=(object(),),
    )
    runtime = NS(
        _check_database_identity=lambda: None,
        _pending=lambda: (),
        _pending_gate_publications=lambda: (),
    )
    source = collector.NativeRecoveryCutSource(
        lambda _: NS(
            schema_id=collector.SCHEMA_V5,
            canonical_record_bytes=b"isolated profile",
        )
    )
    monkeypatch.setattr(collector, "decode_native_one_call_recovery_profile_v5", lambda _: object())
    monkeypatch.setattr(
        collector,
        "R14NativeRecoverySourceSubsetReader",
        lambda _: NS(
            collect=lambda: subset,
        ),
    )
    monkeypatch.setattr(
        collector, "NativeOneCallContextScopeV1", NS(model_validate_json=lambda _: scope)
    )
    monkeypatch.setattr(collector, "bind_native_prepare_scope", lambda _: binding)
    monkeypatch.setattr(
        scopes,
        "NativeOneCallScopeService",
        lambda _: NS(
            _lineage=lambda: (scoped,),
            _inventory=lambda: None,
            _check_cli_current=lambda _: None,
        ),
    )
    monkeypatch.setattr(
        source, "_selected_accepted_direct_terminal_inputs", lambda *_: (False, None)
    )
    monkeypatch.setattr(source, "_selected_accepted_recovery_inputs", lambda *_: inputs)
    calls = []

    def selected_resume(actual_runtime, actual_run, actual_inputs):
        assert actual_runtime is runtime and actual_run is run and actual_inputs is inputs
        calls.append(run)
        return object() if selected else None

    monkeypatch.setattr(source, "_selected_resume_for_run", selected_resume, raising=False)

    def inventory(_):
        raise InventoryReached

    monkeypatch.setattr(collector, "collect_r16_native_inventory", inventory)
    if selected and not no_retry:
        with pytest.raises(InventoryReached):
            source._collect_held(runtime)
    else:
        assert source._collect_held(runtime) is None
    assert calls == [run]
