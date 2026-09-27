"""Installed H1 runtime binds the custody mutation gate to its authenticated database."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from chiplog.composition.common_cli_execution_runtime import (
    open_common_cli_execution_runtime,
    open_installed_h1_runtime,
)
from chiplog.composition.h1_delivery_evidence_journal import H1DeliveryEvidenceJournal
from chiplog.composition.h1_launch_enrollment import _open_installed_h1_launch
from chiplog.composition.h1_registration_custody import (
    H1RegistrationCustody,
    H1RegistrationCustodyError,
)
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
from tests.support.h1_installed_launch import (
    CHANNEL,
    PRINCIPAL,
    TENANT,
    installed_slot,
    prepare_installed_slot,
)


def _resources(tmp_path: Path) -> HermeticDispatchResources:
    return HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "dispatch-custody"
    )


@pytest.mark.asyncio
async def test_installed_runtime_binds_exact_gate_and_authenticated_database_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    observed: list[tuple[object, object, object, object]] = []
    original_bind = H1RegistrationCustody.bind_authority_gate

    def capture_bind(
        self: H1RegistrationCustody,
        gate: object,
        database_path: object,
        database_identity: object,
    ) -> None:
        observed.append((self, gate, database_path, database_identity))
        original_bind(self, gate, database_path, database_identity)

    monkeypatch.setattr(H1RegistrationCustody, "bind_authority_gate", capture_bind)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            assert observed == [
                (
                    launch.custody,
                    runtime._authority_gate(),
                    launch.database_path,
                    launch.database_identity,
                )
            ]
            assert launch.custody._authority_gate is runtime._authority_gate()


@pytest.mark.asyncio
async def test_installed_custody_mutation_waits_for_held_runtime_gate(tmp_path: Path) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            started = threading.Event()
            finished = threading.Event()
            failure: list[BaseException] = []

            def revoke() -> None:
                started.set()
                try:
                    launch.custody.revoke(
                        launch.custody._binding,
                        launch.custody._raw,
                        TENANT,
                        PRINCIPAL,
                        CHANNEL,
                        0,
                    )
                except BaseException as exc:
                    failure.append(exc)
                finally:
                    finished.set()

            worker = threading.Thread(target=revoke)
            with runtime._authority_gate().hold():
                worker.start()
                assert started.wait(timeout=1)
                assert not finished.wait(timeout=0.1)
            worker.join(timeout=1)

            assert not worker.is_alive()
            assert failure == []
            with pytest.raises(H1RegistrationCustodyError, match="revoked"):
                launch.custody.select(TENANT, PRINCIPAL, CHANNEL)


@pytest.mark.asyncio
async def test_uninstalled_runtime_does_not_bind_registration_custody_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[object, object, object, object]] = []
    original_bind = H1RegistrationCustody.bind_authority_gate

    def capture_bind(
        self: H1RegistrationCustody,
        gate: object,
        database_path: object,
        database_identity: object,
    ) -> None:
        observed.append((self, gate, database_path, database_identity))
        original_bind(self, gate, database_path, database_identity)

    monkeypatch.setattr(H1RegistrationCustody, "bind_authority_gate", capture_bind)

    async with open_common_cli_execution_runtime(
        tmp_path / "ordinary.sqlite3", resources=_resources(tmp_path)
    ) as runtime:
        assert runtime._appender._materializer._owner_publication_resolver is None

    assert observed == []


@pytest.mark.asyncio
async def test_runtime_teardown_releases_custody_gate_for_next_same_launch_mount(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    observed: list[tuple[object, object, object, object]] = []
    unbound: list[tuple[object, object]] = []
    original_bind = H1RegistrationCustody.bind_authority_gate
    original_unbind = H1RegistrationCustody.unbind_authority_gate

    def capture_bind(
        self: H1RegistrationCustody,
        gate: object,
        database_path: object,
        database_identity: object,
    ) -> None:
        observed.append((self, gate, database_path, database_identity))
        original_bind(self, gate, database_path, database_identity)

    def capture_unbind(self: H1RegistrationCustody, gate: object) -> None:
        unbound.append((self, gate))
        original_unbind(self, gate)

    monkeypatch.setattr(H1RegistrationCustody, "bind_authority_gate", capture_bind)
    monkeypatch.setattr(H1RegistrationCustody, "unbind_authority_gate", capture_unbind)

    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as first:
            first_gate = first._authority_gate()

        assert launch.custody._authority_gate is None
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as second:
            assert second._authority_gate() is not first_gate
            assert launch.custody._authority_gate is second._authority_gate()
            second_gate = second._authority_gate()

    assert [call[0] for call in observed] == [launch.custody, launch.custody]
    assert unbound == [(launch.custody, first_gate), (launch.custody, second_gate)]


@pytest.mark.asyncio
async def test_same_launch_rejects_a_second_live_runtime_custody_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)

    observed: list[object] = []
    original_bind = H1RegistrationCustody.bind_authority_gate

    def capture_bind(
        self: H1RegistrationCustody,
        gate: object,
        database_path: object,
        database_identity: object,
    ) -> None:
        observed.append(gate)
        original_bind(self, gate, database_path, database_identity)

    async def open_second(launch: object) -> None:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)):
            raise AssertionError("second live runtime must not yield")

    monkeypatch.setattr(H1RegistrationCustody, "bind_authority_gate", capture_bind)
    with _open_installed_h1_launch(slot) as launch:
        async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)) as runtime:
            first_gate = runtime._authority_gate()
            with pytest.raises(H1RegistrationCustodyError, match="already bound"):
                await asyncio.wait_for(open_second(launch), timeout=2)
            assert len(observed) == 2
            assert observed[0] is first_gate
            assert observed[1] is not first_gate
            assert launch.custody._authority_gate is first_gate


@pytest.mark.asyncio
async def test_post_bind_setup_failure_releases_exact_custody_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slot, expected = installed_slot(tmp_path)
    await prepare_installed_slot(slot, expected, tmp_path)
    bound: list[tuple[object, object]] = []
    unbound: list[tuple[object, object]] = []
    original_bind = H1RegistrationCustody.bind_authority_gate
    original_unbind = H1RegistrationCustody.unbind_authority_gate

    def fail_open_enrolled(cls: type[H1DeliveryEvidenceJournal], _: object) -> None:
        raise RuntimeError("injected post-bind role setup failure")

    def capture_bind(
        self: H1RegistrationCustody,
        gate: object,
        database_path: object,
        database_identity: object,
    ) -> None:
        bound.append((self, gate))
        original_bind(self, gate, database_path, database_identity)

    def capture_unbind(self: H1RegistrationCustody, gate: object) -> None:
        unbound.append((self, gate))
        original_unbind(self, gate)

    monkeypatch.setattr(H1DeliveryEvidenceJournal, "open_enrolled", classmethod(fail_open_enrolled))
    monkeypatch.setattr(H1RegistrationCustody, "bind_authority_gate", capture_bind)
    monkeypatch.setattr(H1RegistrationCustody, "unbind_authority_gate", capture_unbind)

    with _open_installed_h1_launch(slot) as launch:
        with pytest.raises(RuntimeError, match="injected post-bind role setup failure"):
            async with open_installed_h1_runtime(launch, resources=_resources(tmp_path)):
                raise AssertionError("post-bind setup failure must prevent yielding")

        assert launch.custody._authority_gate is None
        assert len(bound) == 1
        assert len(unbound) == 1
        assert bound[0][0] is unbound[0][0] is launch.custody
        assert unbound[0][1] is bound[0][1]
