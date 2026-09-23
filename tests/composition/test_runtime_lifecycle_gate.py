"""Canonical lifecycle races after real trust-owner evaluation."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from chiplog.composition.r7_planning import R7PlanningRuntime, open_r7_runtime
from chiplog.composition.r7_supervisor import R4RuntimeAdmission
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_ledger import BrokerAuthorityLedger
from chiplog.platform.r7_runtime import AuthorityBrokerRuntime


async def _bootstrap(runtime: R7PlanningRuntime) -> None:
    await runtime.bootstrap(
        database_instance_id="database-1",
        principal_id="principal-1",
        credential_id="credential-1",
        session_id="session-1",
        token="token-1",
    )


def _rival_epoch(database: Path) -> None:
    ledger = BrokerAuthorityLedger(
        database.with_suffix(database.suffix + ".broker.sqlite3"),
        authority_gate=AuthorityGate.for_database(database),
    )
    ledger.allocate_epoch("tenant-1", "rival-endpoint", "rival-key")


def test_canonical_restart_and_close_publish_fresh_epoch_then_drain(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with open_r7_runtime(
            tmp_path / "runtime.sqlite", tenant_id="tenant-1", operator_secret=b"secret"
        ) as runtime:
            await _bootstrap(runtime)
            first = runtime._read_ledger.current_state("tenant-1")
            assert not first.owner_draining
            runtime.restart_generation()
            second = runtime._read_ledger.current_state("tenant-1")
            assert second.broker_epoch > first.broker_epoch
            assert second.owner_generation != first.owner_generation
            assert not second.owner_draining
            runtime.close()
            assert runtime._read_ledger.current_state("tenant-1").owner_draining

    asyncio.run(exercise())


@pytest.mark.parametrize("mutation", ["epoch", "head-copy"])
def test_real_admission_cannot_publish_after_source_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    database = tmp_path / "runtime.sqlite"
    original = R4RuntimeAdmission.authenticate_runtime_admission
    candidates: list[int] = []

    def mutate(self: R4RuntimeAdmission, tenant: str, owner: AuthorityBrokerRuntime) -> Any:
        evidence = original(self, tenant, owner)
        assert evidence.accepted
        candidates.extend(int(item.process_identity) for item in owner.graph_generation().owners)
        if mutation == "epoch":
            _rival_epoch(database)
        else:
            head = database.with_suffix(database.suffix + ".trust-journal.head")
            replacement = head.with_name(head.name + ".replacement")
            replacement.write_bytes(head.read_bytes())
            replacement.chmod(head.stat().st_mode & 0o777)
            replacement.replace(head)
        return evidence

    async def exercise() -> None:
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"secret"
        ) as runtime:
            await _bootstrap(runtime)
            with monkeypatch.context() as patch:
                patch.setattr(R4RuntimeAdmission, "authenticate_runtime_admission", mutate)
                with pytest.raises((RuntimeError, PermissionError)):
                    runtime.restart_generation()
            assert candidates
            for pid in candidates:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
            with pytest.raises(RuntimeError):
                runtime._supervisor.runtime()
            assert runtime._read_ledger.current_state("tenant-1").owner_draining

    asyncio.run(exercise())


def test_superseded_close_does_not_drain_newer_runtime(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "runtime.sqlite"
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"secret"
        ) as old:
            await _bootstrap(old)
            async with open_r7_runtime(
                database, tenant_id="tenant-1", operator_secret=b"secret"
            ) as new:
                before = new._read_ledger.current_state("tenant-1")
                assert not before.owner_draining
                old.close()
                assert new._read_ledger.current_state("tenant-1") == before
                assert (
                    new._supervisor.runtime().session("deployment_trust").broker_epoch
                    == before.broker_epoch
                )

    asyncio.run(exercise())


def test_superseded_real_bootstrap_response_publishes_no_trust_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "runtime.sqlite"
    original = R7PlanningRuntime._observed_trust_call
    reached: list[str] = []

    async def mutate(self: R7PlanningRuntime, mode: Any, value: object) -> Any:
        observed = await original(self, mode, value)
        if mode == "BOOTSTRAP":
            assert observed.result.disposition == "VALID"
            assert self._trust.owner_snapshot_entries() == ()
            reached.append(mode)
            _rival_epoch(database)
        return observed

    async def exercise() -> None:
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"secret"
        ) as runtime:
            monkeypatch.setattr(R7PlanningRuntime, "_observed_trust_call", mutate)
            with pytest.raises((RuntimeError, PermissionError)):
                await _bootstrap(runtime)
            assert reached == ["BOOTSTRAP"]
            assert runtime._trust.owner_snapshot_entries() == ()

    asyncio.run(exercise())


def test_process_start_close_and_sync_ipc_allow_independent_gate_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "runtime.sqlite"
    reached: set[str] = set()
    code = (
        "from pathlib import Path\n"
        "from chiplog.platform.authority_gate import AuthorityGate\n"
        "import sys\n"
        "with AuthorityGate.for_database(Path(sys.argv[1])).hold():\n"
        "    print('entered', flush=True)\n"
    )

    def wrap(name: str) -> None:
        original = getattr(AuthorityBrokerRuntime, name)

        def checked(self: AuthorityBrokerRuntime, *args: Any, **kwargs: Any) -> Any:
            result = subprocess.run(
                [sys.executable, "-c", code, str(database)],
                capture_output=True,
                timeout=5,
                check=True,
            )
            assert result.stdout == b"entered\n"
            reached.add(name)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(AuthorityBrokerRuntime, name, checked)

    for name in ("start", "close", "call_sync"):
        wrap(name)

    async def exercise() -> None:
        async with open_r7_runtime(
            database, tenant_id="tenant-1", operator_secret=b"secret"
        ) as runtime:
            await _bootstrap(runtime)
            runtime.restart_generation()

    asyncio.run(exercise())
    assert reached == {"start", "close", "call_sync"}
