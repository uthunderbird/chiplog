"""Negative and durability vectors for the deployment-owned H1 registry."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from chiplog.composition.h1_registration_custody import (
    H1RegistrationCustody,
    H1RegistrationCustodyError,
    H1RegistrationCustodyV1,
    _trusted_launcher_binding_for_canonical_runtime,
)

DEPLOYMENT = "deployment-a"
DATABASE = "database-a"
GENESIS = "a" * 64
FILENAME = "h1-registration-custody.json"


def _entry(*, generation: int = 0, status: str = "ACTIVE") -> dict[str, object]:
    return {
        "tenant_id": "tenant-a",
        "principal_id": "principal-a",
        "channel_id": "channel-a",
        "registration_id": "registration-a",
        "generation": generation,
        "status": status,
        "origin_recipient_id": "recipient-a",
        "conversation_id": "conversation-a",
        "visible_channels": ["channel-a", "channel-b"],
        "accepted_policy_selector": "H1_OWNER_ISSUED_ORIGIN_EXACT_V1",
    }


def _registry(*, entries: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "schema_id": "chiplog.execution.h1-registration-custody.v1",
        "deployment_id": DEPLOYMENT,
        "database_id": DATABASE,
        "database_genesis_digest": GENESIS,
        "entries": entries if entries is not None else [_entry()],
    }


def _raw(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _binding(directory: Path) -> object:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    return _trusted_launcher_binding_for_canonical_runtime(
        descriptor, DEPLOYMENT, DATABASE, GENESIS
    )


def _install(directory: Path, value: dict[str, object] | None = None) -> bytes:
    raw = _raw(value or _registry())
    path = directory / FILENAME
    path.write_bytes(raw)
    path.chmod(0o600)
    return raw


def _fail_fsync(_: int) -> None:
    raise OSError("no")


def _fail_replace(*_: object) -> None:
    raise OSError("no")


def test_mount_refuses_absent_registry(tmp_path: Path) -> None:
    with pytest.raises(H1RegistrationCustodyError, match="absent"):
        H1RegistrationCustody.mount(_binding(tmp_path))


def test_trusted_installer_creates_once_and_mount_selects(tmp_path: Path) -> None:
    binding = _binding(tmp_path)
    registry = H1RegistrationCustodyV1.model_validate(_registry())
    H1RegistrationCustody.install(binding, registry)
    with pytest.raises(H1RegistrationCustodyError, match="already exists"):
        H1RegistrationCustody.install(binding, registry)
    custody = H1RegistrationCustody.mount(binding)
    assert (
        custody.select("tenant-a", "principal-a", "channel-a").registration_id == "registration-a"
    )


def test_mount_refuses_symlink_and_nonprivate_file(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.write_bytes(_raw(_registry()))
    (tmp_path / FILENAME).symlink_to(target)
    with pytest.raises(H1RegistrationCustodyError):
        H1RegistrationCustody.mount(_binding(tmp_path))
    (tmp_path / FILENAME).unlink()
    _install(tmp_path)
    (tmp_path / FILENAME).chmod(0o644)
    with pytest.raises(H1RegistrationCustodyError, match="private"):
        H1RegistrationCustody.mount(_binding(tmp_path))


def test_mount_refuses_duplicate_key_and_wrong_binding(tmp_path: Path) -> None:
    duplicate = (
        b'{"database_genesis_digest":"' + GENESIS.encode() + b'","database_id":"database-a",'
        b'"deployment_id":"deployment-a","entries":[],"entries":[],"schema_id":'
        b'"chiplog.execution.h1-registration-custody.v1"}'
    )
    path = tmp_path / FILENAME
    path.write_bytes(duplicate)
    path.chmod(0o600)
    with pytest.raises(H1RegistrationCustodyError, match="duplicate"):
        H1RegistrationCustody.mount(_binding(tmp_path))
    _install(tmp_path)
    with pytest.raises(H1RegistrationCustodyError, match="binding"):
        H1RegistrationCustody.mount(
            _trusted_launcher_binding_for_canonical_runtime(
                os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY), "wrong", DATABASE, GENESIS
            )
        )


def test_mount_refuses_ambiguous_and_revoked_selection(tmp_path: Path) -> None:
    duplicate = _entry()
    _install(tmp_path, _registry(entries=[_entry(), duplicate]))
    with pytest.raises(H1RegistrationCustodyError, match="schema"):
        H1RegistrationCustody.mount(_binding(tmp_path))
    _install(tmp_path, _registry(entries=[_entry(status="REVOKED")]))
    custody = H1RegistrationCustody.mount(_binding(tmp_path))
    with pytest.raises(H1RegistrationCustodyError, match="revoked"):
        custody.select("tenant-a", "principal-a", "channel-a")


def test_installer_cas_blocks_equivocation_and_generation_jump(tmp_path: Path) -> None:
    original = _install(tmp_path)
    binding = _binding(tmp_path)
    custody = H1RegistrationCustody.mount(binding)
    replacement = H1RegistrationCustodyV1.model_validate(_registry(entries=[_entry(generation=1)]))
    replacement_raw = replacement.canonical_bytes()
    custody.replace(binding, original, "tenant-a", "principal-a", "channel-a", 0, replacement)
    with pytest.raises(H1RegistrationCustodyError, match="CAS"):
        custody.replace(binding, original, "tenant-a", "principal-a", "channel-a", 0, replacement)
    jumped = H1RegistrationCustodyV1.model_validate(_registry(entries=[_entry(generation=3)]))
    with pytest.raises(H1RegistrationCustodyError, match="generation"):
        custody.replace(binding, replacement_raw, "tenant-a", "principal-a", "channel-a", 1, jumped)


def test_targeted_cas_and_revoke_allow_divergent_entry_generations(tmp_path: Path) -> None:
    second = {
        **_entry(generation=4),
        "channel_id": "channel-b",
        "registration_id": "registration-b",
        "visible_channels": ["channel-b"],
    }
    original_registry = _registry(entries=[_entry(generation=1), second])
    original = _install(tmp_path, original_registry)
    binding = _binding(tmp_path)
    custody = H1RegistrationCustody.mount(binding)
    replacement = H1RegistrationCustodyV1.model_validate(
        _registry(entries=[_entry(generation=2), second])
    )
    replacement_raw = replacement.canonical_bytes()
    custody.replace(binding, original, "tenant-a", "principal-a", "channel-a", 1, replacement)
    custody.revoke(binding, replacement_raw, "tenant-a", "principal-a", "channel-b", 4)
    assert custody.select("tenant-a", "principal-a", "channel-a").generation == 2
    with pytest.raises(H1RegistrationCustodyError, match="revoked"):
        custody.select("tenant-a", "principal-a", "channel-b")


def test_second_live_mount_refuses_same_registry_database_pair(tmp_path: Path) -> None:
    _install(tmp_path)
    first = H1RegistrationCustody.mount(_binding(tmp_path))
    with pytest.raises(H1RegistrationCustodyError, match="locked"):
        H1RegistrationCustody.mount(_binding(tmp_path))
    first.close()


def test_installer_creation_refuses_while_pair_is_live(tmp_path: Path) -> None:
    _install(tmp_path)
    mounted = H1RegistrationCustody.mount(_binding(tmp_path))
    with pytest.raises(H1RegistrationCustodyError, match="locked"):
        H1RegistrationCustody.install(
            _binding(tmp_path), H1RegistrationCustodyV1.model_validate(_registry())
        )
    mounted.close()


def test_live_out_of_process_registry_edit_poison_denies_selection(tmp_path: Path) -> None:
    _install(tmp_path)
    custody = H1RegistrationCustody.mount(_binding(tmp_path))
    _install(tmp_path, _registry(entries=[_entry(generation=1)]))
    with pytest.raises(H1RegistrationCustodyError, match="poisoned"):
        custody.select("tenant-a", "principal-a", "channel-a")


def test_restart_rereads_durable_replacement(tmp_path: Path) -> None:
    original = _install(tmp_path)
    binding = _binding(tmp_path)
    custody = H1RegistrationCustody.mount(binding)
    replacement = H1RegistrationCustodyV1.model_validate(_registry(entries=[_entry(generation=1)]))
    custody.replace(binding, original, "tenant-a", "principal-a", "channel-a", 0, replacement)
    custody.close()
    restarted = H1RegistrationCustody.mount(_binding(tmp_path))
    assert restarted.select("tenant-a", "principal-a", "channel-a").generation == 1


@pytest.mark.parametrize("failure", ["fsync", "replace"])
def test_uncertain_persistence_poison_denies_future_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    original = _install(tmp_path)
    binding = _binding(tmp_path)
    custody = H1RegistrationCustody.mount(binding)
    replacement = H1RegistrationCustodyV1.model_validate(_registry(entries=[_entry(generation=1)]))
    if failure == "fsync":
        monkeypatch.setattr("chiplog.composition.h1_registration_custody.os.fsync", _fail_fsync)
    else:
        monkeypatch.setattr("chiplog.composition.h1_registration_custody.os.replace", _fail_replace)
    with pytest.raises(H1RegistrationCustodyError, match="poisoned"):
        custody.replace(binding, original, "tenant-a", "principal-a", "channel-a", 0, replacement)
    with pytest.raises(H1RegistrationCustodyError, match="poisoned"):
        custody.select("tenant-a", "principal-a", "channel-a")


def test_valid_installed_selection(tmp_path: Path) -> None:
    _install(tmp_path)
    custody = H1RegistrationCustody.mount(_binding(tmp_path))
    selected = custody.select("tenant-a", "principal-a", "channel-a")
    assert selected.origin_recipient_id == "recipient-a"


def test_selected_entry_and_registry_collections_are_immutable(tmp_path: Path) -> None:
    mutation_method = "append"
    registry = H1RegistrationCustodyV1.model_validate(_registry())
    with pytest.raises(AttributeError):
        getattr(registry.entries, mutation_method)(registry.entries[0])
    _install(tmp_path)
    custody = H1RegistrationCustody.mount(_binding(tmp_path))
    selected = custody.select("tenant-a", "principal-a", "channel-a")
    with pytest.raises(AttributeError):
        getattr(selected.visible_channels, mutation_method)("attacker-channel")
    assert custody.select("tenant-a", "principal-a", "channel-a").visible_channels == (
        "channel-a",
        "channel-b",
    )


def test_trusted_installer_revokes_active_registration(tmp_path: Path) -> None:
    original = _install(tmp_path)
    binding = _binding(tmp_path)
    custody = H1RegistrationCustody.mount(binding)
    custody.revoke(binding, original, "tenant-a", "principal-a", "channel-a", 0)
    with pytest.raises(H1RegistrationCustodyError, match="revoked"):
        custody.select("tenant-a", "principal-a", "channel-a")
