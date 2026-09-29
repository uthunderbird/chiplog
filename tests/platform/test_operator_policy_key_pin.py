"""Acceptance tests for the authority-gated operator policy key pin."""

import base64
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust.operator_policy_command_verifier import (
    OperatorPolicyKeyBindingV1,
)
from chiplog.platform.authority_gate import AuthorityGate, AuthorityGateError
from chiplog.platform.operator_policy_key_pin import (
    OperatorPolicyKeyPinFileV1,
    OperatorPolicyPinError,
    PinnedOperatorPolicyKey,
    load_operator_policy_key_pin,
)

_DOMAIN = b"chiplog.operator-policy-key-pin.v1\x00"


def _path(database: Path) -> Path:
    return database.with_suffix(database.suffix + ".operator-policy-key.json")


def _record(**changes: object) -> OperatorPolicyKeyPinFileV1:
    record = OperatorPolicyKeyPinFileV1(
        tenant_id="tenant",
        database_id="database",
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=(
            "ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
            "REVOKE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY",
        ),
        status="ACTIVE",
        public_key=bytes(range(32)),
    )
    return record.model_copy(update=changes)


def _provision(tmp_path: Path) -> tuple[Path, AuthorityGate, Path, OperatorPolicyKeyPinFileV1]:
    database = tmp_path / "authority.sqlite3"
    database.touch()
    gate = AuthorityGate.for_database(database)
    record = _record()
    path = _path(database)
    path.write_bytes(record.canonical_bytes())
    path.chmod(0o600)
    return database, gate, path, record


def _load(
    gate: AuthorityGate, *, tenant_id: str = "tenant", database_id: str = "database"
) -> PinnedOperatorPolicyKey:
    return load_operator_policy_key_pin(gate, tenant_id=tenant_id, database_id=database_id)


def test_loads_exact_canonical_pin_and_exposes_content_addressed_binding(tmp_path: Path) -> None:
    database, gate, path, record = _provision(tmp_path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    with gate.hold():
        pin = _load(gate)
        assert pin.binding == OperatorPolicyKeyBindingV1(
            tenant_id=record.tenant_id,
            database_id=record.database_id,
            operator_key_id=record.operator_key_id,
            policy_id=record.policy_id,
            allowed_operations=record.allowed_operations,
            status=record.status,
            public_key=record.public_key,
            ref=ExactHead(
                identity=record.operator_key_id,
                head=hashlib.sha256(_DOMAIN + raw).hexdigest(),
                fingerprint=digest,
            ),
        )
        pin.assert_current()

    equivalent_gate = AuthorityGate.for_database(database)
    with equivalent_gate.hold():
        assert _load(equivalent_gate).binding == pin.binding


def test_missing_pin_and_calls_outside_its_held_gate_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    database.touch()
    gate = AuthorityGate.for_database(database)

    with pytest.raises(AuthorityGateError):
        _load(gate)
    with gate.hold(), pytest.raises(OperatorPolicyPinError):
        _load(gate)

    expected_root = tmp_path / "expected"
    expected_root.mkdir()
    _, expected_gate, _, _ = _provision(expected_root)
    foreign_database = tmp_path / "foreign.sqlite3"
    foreign_database.touch()
    foreign_gate = AuthorityGate.for_database(foreign_database)
    with foreign_gate.hold(), pytest.raises(AuthorityGateError):
        _load(expected_gate)


@pytest.mark.parametrize("field", ("tenant_id", "database_id"))
def test_load_rejects_requested_scope_that_differs_from_the_pin(tmp_path: Path, field: str) -> None:
    _, gate, _, _ = _provision(tmp_path)
    requested = {"tenant_id": "tenant", "database_id": "database"}
    requested[field] = "other"

    with gate.hold(), pytest.raises(OperatorPolicyPinError):
        _load(gate, **requested)


@pytest.mark.parametrize(
    "raw",
    (
        b"",
        b"{}",
        b'{"allowed_operations":[]}',
        _record().canonical_bytes() + b"\n",
        json.dumps(
            {**json.loads(_record().canonical_bytes()), "unexpected": "field"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {**json.loads(_record().canonical_bytes()), "public_key": "AA=="},
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                **json.loads(_record().canonical_bytes()),
                "public_key": base64.b64encode(b"x" * 31).decode(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                **json.loads(_record().canonical_bytes()),
                "public_key": base64.b64encode(b"x" * 33).decode(),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            json.loads(_record().canonical_bytes()), sort_keys=True, separators=(",", ": ")
        ).encode(),
    ),
)
def test_load_rejects_malformed_or_noncanonical_pin_bytes(tmp_path: Path, raw: bytes) -> None:
    _, gate, path, _ = _provision(tmp_path)
    path.write_bytes(raw)
    path.chmod(0o600)

    with gate.hold(), pytest.raises(OperatorPolicyPinError):
        _load(gate)


def test_load_rejects_pin_larger_than_the_fixed_read_bound(tmp_path: Path) -> None:
    _, gate, path, _ = _provision(tmp_path)
    path.write_bytes(b"x" * 65_537)
    path.chmod(0o600)

    with gate.hold(), pytest.raises(OperatorPolicyPinError):
        _load(gate)


@pytest.mark.parametrize("kind", ("mode_0640", "mode_0700", "symlink", "hardlink", "fifo"))
def test_load_rejects_unsafe_pin_file_types_permissions_and_aliases(
    tmp_path: Path, kind: str
) -> None:
    _, gate, path, _ = _provision(tmp_path)
    if kind == "mode_0640":
        path.chmod(0o640)
    elif kind == "mode_0700":
        path.chmod(0o700)
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)
        path.unlink()
        path.symlink_to(target)
    elif kind == "hardlink":
        alias = tmp_path / "alias"
        os.link(path, alias)
    else:
        path.unlink()
        os.mkfifo(path, 0o600)

    with gate.hold(), pytest.raises(OperatorPolicyPinError):
        _load(gate)


def test_load_rejects_pin_with_foreign_descriptor_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, gate, path, _ = _provision(tmp_path)
    original_fstat = os.fstat
    pin_inode = path.stat().st_ino

    def foreign_owner(descriptor: int) -> os.stat_result:
        metadata = original_fstat(descriptor)
        if metadata.st_ino != pin_inode:
            return metadata
        fields = list(metadata)
        fields[4] = os.geteuid() + 1
        return os.stat_result(fields)

    monkeypatch.setattr(os, "fstat", foreign_owner)
    with gate.hold(), pytest.raises(OperatorPolicyPinError):
        _load(gate)


def test_load_rejects_group_or_world_writable_pin_parent(tmp_path: Path) -> None:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    _, gate, _, _ = _provision(parent)
    with gate.hold():
        pass
    parent.chmod(0o722)

    with gate.hold(), pytest.raises(OperatorPolicyPinError):
        _load(gate)


def test_load_rejects_pin_parent_with_foreign_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, gate, path, _ = _provision(tmp_path)
    original_lstat = Path.lstat
    original_fstat = os.fstat

    def foreign_owner(metadata: os.stat_result) -> os.stat_result:
        fields = list(metadata)
        fields[4] = os.geteuid() + 1
        return os.stat_result(fields)

    def parent_lstat(target: Path) -> os.stat_result:
        metadata = original_lstat(target)
        return foreign_owner(metadata) if target == path.parent else metadata

    def parent_fstat(descriptor: int) -> os.stat_result:
        metadata = original_fstat(descriptor)
        return foreign_owner(metadata) if stat.S_ISDIR(metadata.st_mode) else metadata

    monkeypatch.setattr(Path, "lstat", parent_lstat)
    monkeypatch.setattr(os, "fstat", parent_fstat)
    with gate.hold(), pytest.raises(OperatorPolicyPinError):
        _load(gate)


def test_assert_current_requires_its_gate_to_be_held(tmp_path: Path) -> None:
    _, gate, _, _ = _provision(tmp_path)
    with gate.hold():
        pin = _load(gate)

    with pytest.raises(AuthorityGateError):
        pin.assert_current()

    foreign_database = tmp_path / "foreign.sqlite3"
    foreign_database.touch()
    with AuthorityGate.for_database(foreign_database).hold(), pytest.raises(AuthorityGateError):
        pin.assert_current()


def test_assert_current_ignores_benign_sibling_but_rejects_identical_pin_replacement(
    tmp_path: Path,
) -> None:
    _, gate, path, _ = _provision(tmp_path)

    with gate.hold():
        pin = _load(gate)
        before_parent_ctime = path.parent.lstat().st_ctime_ns
        sidecar = path.with_name("authority.sqlite3-sidecar")
        sidecar.write_text("benign")
        assert path.parent.lstat().st_ctime_ns != before_parent_ctime
        pin.assert_current()

        before_inode = path.stat().st_ino
        replacement = path.with_name("replacement.operator-policy-key.json")
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, path)
        assert path.stat().st_ino != before_inode

        with pytest.raises(OperatorPolicyPinError):
            pin.assert_current()


@pytest.mark.parametrize(
    "mutation",
    ("changed_bytes_rewrite", "identical_bytes_rewrite", "identical_bytes_inode_replacement"),
)
def test_assert_current_rejects_pin_changed_after_load_while_gate_is_held(
    tmp_path: Path, mutation: str
) -> None:
    _, gate, path, _ = _provision(tmp_path)

    with gate.hold():
        pin = _load(gate)
        before_inode = path.stat().st_ino
        original = path.read_bytes()
        if mutation == "changed_bytes_rewrite":
            path.write_bytes(_record(policy_id="new-policy").canonical_bytes())
            assert path.stat().st_ino == before_inode
        elif mutation == "identical_bytes_rewrite":
            path.write_bytes(original)
            assert path.stat().st_ino == before_inode
        else:
            replacement = path.with_name("replacement.operator-policy-key.json")
            replacement.write_bytes(original)
            replacement.chmod(0o600)
            os.replace(replacement, path)
            assert path.stat().st_ino != before_inode

        with pytest.raises(OperatorPolicyPinError):
            pin.assert_current()
