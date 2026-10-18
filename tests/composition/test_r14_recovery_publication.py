"""Mounted fail-closed adapter and real isolated owner IPC.

Synthetic wire cuts are not selected authority and cannot publish via the default
source. These tests intentionally make no full-cut or continuation claim.
"""

import json
from collections import Counter
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from chiplog.architecture.r7_runtime import R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.execution_recovery_contracts import (
    PreparedExecutionAccounting,
    PrepareExecutionAccounting,
)
from chiplog.composition import r14_recovery_publication as publication
from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.r14_recovery_publication import (
    _exchange,
    publish_execution_accounting_readiness,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.capabilities.agent_loop.execution_recovery_preparation import _cut


async def test_mounted_source_hold_and_owner_ipc_do_not_publish(tmp_path: Path) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "runtime.sqlite",
        resources=resources,
        manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
    ) as runtime:
        before = runtime._commitment_journal.load(runtime._tenant_id)
        with pytest.raises(LoopRejected, match="complete authenticated recovery cut"):
            await publish_execution_accounting_readiness(runtime)
        # A canonical synthetic owner request proves IPC dispatch only. The Run,
        # selected references, and missing inventory are not runtime authority.
        request = PrepareExecutionAccounting(command_id="synthetic", cut=await _cut())
        result, sent, reply = await _exchange(
            runtime,
            request,
            "agent_loop.prepare_execution_accounting.v1",
            PreparedExecutionAccounting,
        )
        assert result.source_request_fingerprint == request.digest()
        assert reply.responder == sent.callee
        assert runtime._commitment_journal.load(runtime._tenant_id) == before
        with pytest.raises(LoopRejected, match="complete authenticated recovery cut"):
            await publish_execution_accounting_readiness(runtime)


async def test_selected_profile_exact_restart_and_hold(tmp_path: Path) -> None:
    from chiplog.composition.r14_loop_history import read_execution_history
    from chiplog.composition.r14_recovery_profile_publication import (
        publish_native_recovery_profile,
        read_native_recovery_profile_held,
    )

    database = tmp_path / "profile.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database, resources=resources, manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
    ) as runtime:
        before = read_execution_history(runtime).records
        selected = await publish_native_recovery_profile(runtime)
        assert selected is not None
        assert await publish_native_recovery_profile(runtime) == selected
        assert read_execution_history(runtime).records == before
        with pytest.raises(LoopRejected, match="complete authenticated recovery cut"):
            await publish_execution_accounting_readiness(runtime)
    async with open_common_cli_execution_runtime(
        database, resources=resources, manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
    ) as runtime:
        with runtime._authority_gate().hold():
            assert read_native_recovery_profile_held(runtime) == selected
        assert await publish_native_recovery_profile(runtime) == selected


async def test_v3_profile_selection_reopens_with_exact_version(tmp_path: Path) -> None:
    from chiplog.composition.r14_recovery_profile_publication import (
        publish_native_recovery_profile,
        read_native_recovery_profile_held,
    )

    database = tmp_path / "profile-v3.sqlite"
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        database, resources=resources, manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
    ) as runtime:
        selected = await publish_native_recovery_profile(runtime, version="v3")
        assert selected is not None
        assert selected.schema_id.endswith(".v3")
        with runtime._authority_gate().hold():
            assert read_native_recovery_profile_held(runtime) == selected
    async with open_common_cli_execution_runtime(
        database, resources=resources, manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
    ) as runtime:
        with runtime._authority_gate().hold():
            assert read_native_recovery_profile_held(runtime) == selected
        assert await publish_native_recovery_profile(runtime, version="v3") == selected


async def test_profile_lost_ack_recovers_exact_selection(tmp_path: Path) -> None:
    from unittest.mock import patch

    from chiplog.composition.r14_recovery_profile_publication import publish_native_recovery_profile
    from chiplog.platform._sqlite import LostCommitAcknowledgement

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    database = tmp_path / "crash.sqlite"
    async with open_common_cli_execution_runtime(
        database, resources=resources, manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
    ) as runtime:
        original = runtime._appender.submit

        async def lose_ack(batch) -> None:
            await original(batch)
            raise LostCommitAcknowledgement("profile ack lost")

        with (
            patch.object(runtime._appender, "submit", side_effect=lose_ack),
            pytest.raises(LostCommitAcknowledgement),
        ):
            await publish_native_recovery_profile(runtime)
    async with open_common_cli_execution_runtime(
        database, resources=resources, manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
    ) as runtime:
        assert await publish_native_recovery_profile(runtime) is not None


async def test_profile_physical_tamper_rejects_read_and_restart(tmp_path: Path) -> None:
    import sqlite3

    from chiplog.composition.r14_recovery_profile_publication import (
        publish_native_recovery_profile,
        read_native_recovery_profile_held,
    )

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    database = tmp_path / "tamper.sqlite"
    async with open_common_cli_execution_runtime(
        database, resources=resources, manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
    ) as runtime:
        selected = await publish_native_recovery_profile(runtime)
        assert selected is not None
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE records SET canonical_bytes=? WHERE record_id=?",
                (b"{}", selected.physical_record.subject_id),
            )
        with (
            runtime._authority_gate().hold(),
            pytest.raises(ValueError, match="selected/physical mismatch"),
        ):
            read_native_recovery_profile_held(runtime)
    with pytest.raises((ValueError, RuntimeError)):
        async with open_common_cli_execution_runtime(
            database, resources=resources, manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST
        ):
            pytest.fail("tampered profile reopened")


async def test_v26_actor_budget_is_issued_before_ipc_and_expires(tmp_path: Path) -> None:
    import time
    from unittest.mock import patch

    from chiplog.composition.r14_recovery_publication import (
        RECOVERY_ACTOR_BUDGET_NS,
        issue_execution_recovery_actor_v1,
        validate_recovery_actor_budget,
    )

    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "custody"
    )
    async with open_common_cli_execution_runtime(
        tmp_path / "budget.sqlite",
        resources=resources,
        manifest=R14_EXECUTION_RECOVERY_V26_PRODUCTION_MANIFEST,
    ) as runtime:
        engine = runtime._supervisor.runtime()
        sent = []
        original = engine.call

        async def capture(request):
            sent.append(request)
            return await original(request)

        with patch.object(engine, "call", side_effect=capture):
            observed, receipt = await issue_execution_recovery_actor_v1(runtime, "hermetic-ingress")
        assert sent == [receipt.request]
        assert receipt.response == observed.response
        assert (
            observed.request.budget.absolute_deadline_ns
            == receipt.issued_monotonic_ns + RECOVERY_ACTOR_BUDGET_NS
        )
        assert observed.request.budget.policy_version == 26
        with patch("chiplog.composition.r7_planning.time.monotonic_ns", return_value=100):
            generic = runtime._trust_call_request(
                "AUTHENTICATE", {}, snapshot_bytes=receipt.trust_snapshot_bytes
            )
        assert generic.budget.absolute_deadline_ns == 100 + 5_000_000_000
        assert generic.budget.policy_version == 1
        validate_recovery_actor_budget(receipt)
        altered = receipt.model_copy(
            update={"issued_monotonic_ns": receipt.issued_monotonic_ns + 1}
        )
        with pytest.raises(ValueError, match="original issuance"):
            validate_recovery_actor_budget(altered)
        with (
            runtime._authority_gate().hold(),
            patch(
                "chiplog.composition.r7_planning.time.monotonic_ns",
                return_value=observed.request.budget.absolute_deadline_ns,
            ),
            pytest.raises(LoopRejected, match="authentication unavailable or stale"),
        ):
            runtime._check_execution_actor(observed)
        assert time.monotonic_ns() < observed.request.budget.absolute_deadline_ns


class _ReachedCutCollector(Exception):
    pass


async def test_readiness_scans_each_retained_entry_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # A unit scan probe stops before cut validation; these entries confer no authority.
    raws = (
        json.dumps({"kind": "DECIDED", "operation_kind": publication.OPERATION}).encode(),
        b'{"kind":"DECIDED","operation_kind":"other"}',
        b'{"kind":"MATERIALIZED"}',
    )
    entries = tuple((str(index), None, raw) for index, raw in enumerate(raws))
    observed: Counter[bytes] = Counter()
    original_loads = json.loads

    def counted_loads(raw: bytes) -> dict[str, object]:
        observed[raw] += 1
        value = original_loads(raw)
        assert isinstance(value, dict)
        return value

    runtime = SimpleNamespace(
        _execution_actor=AsyncMock(return_value=object()),
        _authority_gate=Mock(return_value=SimpleNamespace(hold=nullcontext)),
        _check_execution_actor=Mock(),
        _pending=Mock(),
        _loop_decisions=Mock(return_value=SimpleNamespace(entries=lambda: entries)),
    )
    collector = Mock()
    collector.collect_held.side_effect = _ReachedCutCollector
    monkeypatch.setattr(publication, "json", SimpleNamespace(loads=counted_loads))
    monkeypatch.setattr(publication, "_capture_readiness_inputs", lambda _: None)
    monkeypatch.setattr(publication, "source", lambda _: collector)
    with pytest.raises(_ReachedCutCollector):
        await publication.publish_execution_accounting_readiness(runtime)
    collector.collect_held.assert_called_once_with(runtime)
    assert observed == Counter(dict.fromkeys(raws, 1))
