"""Authority-gate continuity for installed H1 registration custody mutations."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from chiplog.composition.h1_registration_custody import (
    H1RegistrationCustody,
    H1RegistrationCustodyError,
    H1RegistrationCustodyV1,
    _trusted_launcher_binding_for_canonical_runtime,
)
from chiplog.platform.authority_gate import AuthorityGate

_DEPLOYMENT = "deployment-a"
_DATABASE = "database-a"
_GENESIS = "a" * 64
_FILENAME = "h1-registration-custody.json"


def _registry(*, generation: int = 0) -> H1RegistrationCustodyV1:
    return H1RegistrationCustodyV1.model_validate(
        {
            "schema_id": "chiplog.execution.h1-registration-custody.v1",
            "deployment_id": _DEPLOYMENT,
            "database_id": _DATABASE,
            "database_genesis_digest": _GENESIS,
            "entries": [
                {
                    "tenant_id": "tenant-a",
                    "principal_id": "principal-a",
                    "channel_id": "channel-a",
                    "registration_id": "registration-a",
                    "generation": generation,
                    "status": "ACTIVE",
                    "origin_recipient_id": "recipient-a",
                    "conversation_id": "conversation-a",
                    "visible_channels": ["channel-a"],
                    "accepted_policy_selector": "H1_OWNER_ISSUED_ORIGIN_EXACT_V1",
                }
            ],
        }
    )


def _mounted(tmp_path: Path) -> tuple[H1RegistrationCustody, object, bytes]:
    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    binding = _trusted_launcher_binding_for_canonical_runtime(
        directory_fd, _DEPLOYMENT, _DATABASE, _GENESIS
    )
    registry = _registry()
    raw = registry.canonical_bytes()
    (tmp_path / _FILENAME).write_bytes(raw)
    (tmp_path / _FILENAME).chmod(0o600)
    return H1RegistrationCustody.mount(binding), binding, raw


def _bind(custody: H1RegistrationCustody, tmp_path: Path) -> AuthorityGate:
    database = tmp_path / "installed.sqlite3"
    database.touch()
    gate = AuthorityGate.for_database(database)
    stat = database.stat()
    custody.bind_authority_gate(gate, database, (stat.st_dev, stat.st_ino))
    return gate


def _replace(custody: H1RegistrationCustody, binding: object, raw: bytes) -> None:
    custody.replace(
        binding,
        raw,
        "tenant-a",
        "principal-a",
        "channel-a",
        0,
        _registry(generation=1),
    )


def _revoke(custody: H1RegistrationCustody, binding: object, raw: bytes) -> None:
    custody.revoke(binding, raw, "tenant-a", "principal-a", "channel-a", 0)


@pytest.mark.parametrize("mutation", [_replace, _revoke])
def test_trusted_installer_mutation_remains_available_before_runtime_gate_binding(
    tmp_path: Path, mutation: Callable[[H1RegistrationCustody, object, bytes], None]
) -> None:
    custody, binding, raw = _mounted(tmp_path)
    try:
        mutation(custody, binding, raw)
        if mutation is _replace:
            assert custody.select("tenant-a", "principal-a", "channel-a").generation == 1
        else:
            with pytest.raises(H1RegistrationCustodyError, match="revoked"):
                custody.select("tenant-a", "principal-a", "channel-a")
    finally:
        custody.close()


@pytest.mark.parametrize("mutation", [_replace, _revoke])
def test_installed_custody_mutation_waits_for_canonical_authority_gate(
    tmp_path: Path, mutation: Callable[[H1RegistrationCustody, object, bytes], None]
) -> None:
    custody, binding, raw = _mounted(tmp_path)
    gate = _bind(custody, tmp_path)
    entered = threading.Event()
    completed = threading.Event()
    errors: list[BaseException] = []

    def mutate() -> None:
        entered.set()
        try:
            mutation(custody, binding, raw)
        except BaseException as error:
            errors.append(error)
        finally:
            completed.set()

    worker = threading.Thread(target=mutate)
    try:
        with gate.hold():
            worker.start()
            assert entered.wait(timeout=1)
            assert not completed.wait(timeout=0.1)
            assert custody.select("tenant-a", "principal-a", "channel-a").generation == 0
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert errors == []
        if mutation is _replace:
            assert custody.select("tenant-a", "principal-a", "channel-a").generation == 1
        else:
            with pytest.raises(H1RegistrationCustodyError, match="revoked"):
                custody.select("tenant-a", "principal-a", "channel-a")
    finally:
        worker.join(timeout=1)
        custody.unbind_authority_gate(gate)
        custody.close()


@pytest.mark.parametrize("mutation", [_replace, _revoke])
def test_installed_custody_mutation_reenters_its_gate_without_pair_lock_deadlock(
    tmp_path: Path, mutation: Callable[[H1RegistrationCustody, object, bytes], None]
) -> None:
    custody, binding, raw = _mounted(tmp_path)
    gate = _bind(custody, tmp_path)
    try:
        with gate.hold():
            mutation(custody, binding, raw)
        if mutation is _replace:
            assert custody.select("tenant-a", "principal-a", "channel-a").generation == 1
        else:
            with pytest.raises(H1RegistrationCustodyError, match="revoked"):
                custody.select("tenant-a", "principal-a", "channel-a")
    finally:
        custody.unbind_authority_gate(gate)
        custody.close()


def test_installed_custody_rejects_foreign_authority_gate(tmp_path: Path) -> None:
    custody, _, _ = _mounted(tmp_path)
    database = tmp_path / "installed.sqlite3"
    database.touch()
    stat = database.stat()
    try:
        with pytest.raises(H1RegistrationCustodyError, match="authority gate differs"):
            custody.bind_authority_gate(
                AuthorityGate.for_database(tmp_path / "foreign.sqlite3"),
                database,
                (stat.st_dev, stat.st_ino),
            )
    finally:
        custody.close()


def test_installed_custody_unbind_requires_the_exact_bound_gate_and_allows_restart(
    tmp_path: Path,
) -> None:
    custody, binding, raw = _mounted(tmp_path)
    first_gate = _bind(custody, tmp_path)
    second_gate = AuthorityGate.for_database(first_gate.database)
    try:
        with pytest.raises(H1RegistrationCustodyError, match="authority gate differs"):
            custody.unbind_authority_gate(second_gate)
        custody.unbind_authority_gate(first_gate)
        with pytest.raises(H1RegistrationCustodyError, match="authority gate differs"):
            custody.unbind_authority_gate(first_gate)
        database_stat = first_gate.database.stat()
        custody.bind_authority_gate(
            second_gate,
            second_gate.database,
            (database_stat.st_dev, database_stat.st_ino),
        )
        _replace(custody, binding, raw)
        assert custody.select("tenant-a", "principal-a", "channel-a").generation == 1
    finally:
        custody.unbind_authority_gate(second_gate)
        custody.close()


def test_unbind_waits_for_leased_mutation_before_allowing_rebind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custody, binding, raw = _mounted(tmp_path)
    first_gate = _bind(custody, tmp_path)
    mutation_entered = threading.Event()
    release_mutation = threading.Event()
    mutation_done = threading.Event()
    unbind_done = threading.Event()
    errors: list[BaseException] = []
    second_gate: AuthorityGate | None = None
    original = H1RegistrationCustody._durable_replace

    def pause_replace(self: H1RegistrationCustody, replacement: bytes) -> None:
        mutation_entered.set()
        assert release_mutation.wait(timeout=1)
        original(self, replacement)

    def mutate() -> None:
        try:
            _replace(custody, binding, raw)
        except BaseException as error:
            errors.append(error)
        finally:
            mutation_done.set()

    def unbind() -> None:
        try:
            custody.unbind_authority_gate(first_gate)
        except BaseException as error:
            errors.append(error)
        finally:
            unbind_done.set()

    monkeypatch.setattr(H1RegistrationCustody, "_durable_replace", pause_replace)
    mutation = threading.Thread(target=mutate)
    unbinder = threading.Thread(target=unbind)
    try:
        mutation.start()
        assert mutation_entered.wait(timeout=1)
        unbinder.start()
        assert not unbind_done.wait(timeout=0.1)
        with pytest.raises(H1RegistrationCustodyError, match=r"already bound|transition"):
            custody.bind_authority_gate(
                AuthorityGate.for_database(first_gate.database),
                first_gate.database,
                (first_gate.database.stat().st_dev, first_gate.database.stat().st_ino),
            )
        release_mutation.set()
        mutation.join(timeout=1)
        unbinder.join(timeout=1)
        assert not mutation.is_alive()
        assert not unbinder.is_alive()
        assert errors == []
        second_gate = AuthorityGate.for_database(first_gate.database)
        database_stat = first_gate.database.stat()
        custody.bind_authority_gate(
            second_gate,
            second_gate.database,
            (database_stat.st_dev, database_stat.st_ino),
        )
        assert custody.select("tenant-a", "principal-a", "channel-a").generation == 1
    finally:
        release_mutation.set()
        mutation.join(timeout=1)
        unbinder.join(timeout=1)
        if second_gate is not None:
            custody.unbind_authority_gate(second_gate)
        custody.close()


def test_bind_waits_for_unbound_mutation_lease_before_installing_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custody, binding, raw = _mounted(tmp_path)
    database = tmp_path / "installed.sqlite3"
    database.touch()
    database_stat = database.stat()
    gate = AuthorityGate.for_database(database)
    mutation_entered = threading.Event()
    release_mutation = threading.Event()
    bind_done = threading.Event()
    bound = threading.Event()
    errors: list[BaseException] = []
    original = H1RegistrationCustody._durable_replace

    def pause_replace(self: H1RegistrationCustody, replacement: bytes) -> None:
        mutation_entered.set()
        assert release_mutation.wait(timeout=1)
        original(self, replacement)

    def mutate() -> None:
        try:
            _replace(custody, binding, raw)
        except BaseException as error:
            errors.append(error)

    def bind() -> None:
        try:
            custody.bind_authority_gate(
                gate, database, (database_stat.st_dev, database_stat.st_ino)
            )
            bound.set()
        except BaseException as error:
            errors.append(error)
        finally:
            bind_done.set()

    monkeypatch.setattr(H1RegistrationCustody, "_durable_replace", pause_replace)
    mutation = threading.Thread(target=mutate)
    binder = threading.Thread(target=bind)
    try:
        mutation.start()
        assert mutation_entered.wait(timeout=1)
        binder.start()
        assert not bind_done.wait(timeout=0.1)
        with pytest.raises(H1RegistrationCustodyError, match="transition"):
            _replace(custody, binding, raw)
        with pytest.raises(H1RegistrationCustodyError, match="transition"):
            custody.close()
        release_mutation.set()
        mutation.join(timeout=1)
        binder.join(timeout=1)
        assert not mutation.is_alive()
        assert not binder.is_alive()
        assert errors == []
        assert custody.select("tenant-a", "principal-a", "channel-a").generation == 1
        with gate.hold():
            assert custody.select("tenant-a", "principal-a", "channel-a").generation == 1
    finally:
        release_mutation.set()
        mutation.join(timeout=1)
        binder.join(timeout=1)
        if bound.is_set():
            custody.unbind_authority_gate(gate)
        custody.close()


def test_close_denies_bound_gate_and_preserves_pair_lock_until_unbound(tmp_path: Path) -> None:
    custody, _, _ = _mounted(tmp_path)
    gate = _bind(custody, tmp_path)
    try:
        with pytest.raises(H1RegistrationCustodyError, match="authority gate is bound"):
            custody.close()
        with pytest.raises(H1RegistrationCustodyError, match="locked"):
            H1RegistrationCustody.mount(_trusted_launcher_binding_for_canonical_runtime(
                os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY), _DEPLOYMENT, _DATABASE, _GENESIS
            ))
        custody.unbind_authority_gate(gate)
        custody.close()
        reopened = H1RegistrationCustody.mount(_trusted_launcher_binding_for_canonical_runtime(
            os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY), _DEPLOYMENT, _DATABASE, _GENESIS
        ))
        reopened.close()
    finally:
        if custody._lock_fd >= 0:
            custody.unbind_authority_gate(gate)
            custody.close()
