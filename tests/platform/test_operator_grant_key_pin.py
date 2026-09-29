"""Acceptance checks for the protected V2 operator-grant key pin."""

import hashlib
import os
from pathlib import Path

import pytest

from chiplog.platform.authority_gate import AuthorityGate, AuthorityGateError
from chiplog.platform.operator_grant_key_pin import (
    OperatorGrantKeyPinFileV2,
    OperatorGrantPinError,
    load_operator_grant_key_pin,
)

_DOMAIN = b"chiplog.operator-grant-key-pin.v2\x00"


def _path(database: Path) -> Path:
    return database.with_suffix(database.suffix + ".operator-grant-key.json")


def _record(**changes: object) -> OperatorGrantKeyPinFileV2:
    record = OperatorGrantKeyPinFileV2(
        tenant_id="tenant",
        database_id="database",
        operator_key_id="operator-key",
        policy_id="policy",
        allowed_operations=(
            "ISSUE_PREPARED_EXTERNAL_DELIVERY_GRANT",
            "REVOKE_PREPARED_EXTERNAL_DELIVERY_GRANT",
        ),
        status="ACTIVE",
        public_key=bytes(range(32)),
    )
    return record.model_copy(update=changes)


def _provision(tmp_path: Path) -> tuple[AuthorityGate, Path, OperatorGrantKeyPinFileV2]:
    database = tmp_path / "authority.sqlite3"
    database.touch()
    gate = AuthorityGate.for_database(database)
    path = _path(database)
    record = _record()
    path.write_bytes(record.canonical_bytes())
    path.chmod(0o600)
    return gate, path, record


def test_loads_valid_v2_pin_and_rejects_outside_gate_or_missing_pin(tmp_path: Path) -> None:
    gate, path, record = _provision(tmp_path)
    with pytest.raises(AuthorityGateError):
        load_operator_grant_key_pin(gate, tenant_id="tenant", database_id="database")
    with gate.hold():
        pin = load_operator_grant_key_pin(gate, tenant_id="tenant", database_id="database")
        assert pin.binding.policy_id == record.policy_id
        assert pin.binding.ref.head == hashlib.sha256(_DOMAIN + path.read_bytes()).hexdigest()
        pin.assert_current()
    path.unlink()
    with gate.hold(), pytest.raises(OperatorGrantPinError):
        load_operator_grant_key_pin(gate, tenant_id="tenant", database_id="database")


def test_currentness_ignores_sibling_but_rejects_identical_atomic_replacement(
    tmp_path: Path,
) -> None:
    gate, path, _ = _provision(tmp_path)
    with gate.hold():
        pin = load_operator_grant_key_pin(gate, tenant_id="tenant", database_id="database")
        path.with_name("harmless-sibling").write_text("harmless")
        pin.assert_current()
        before_inode = path.stat().st_ino
        replacement = path.with_name("replacement.operator-grant-key.json")
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, path)
        assert path.stat().st_ino != before_inode
        with pytest.raises(OperatorGrantPinError):
            pin.assert_current()


@pytest.mark.parametrize("unsafe", ("symlink", "fifo", "v1-schema", "v1-operation"))
def test_rejects_unsafe_or_cross_v1_pin(tmp_path: Path, unsafe: str) -> None:
    gate, path, _ = _provision(tmp_path)
    if unsafe == "symlink":
        target = path.with_name("target")
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)
        path.unlink()
        path.symlink_to(target)
    elif unsafe == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    else:
        raw = _record().model_dump(mode="json")
        if unsafe == "v1-schema":
            raw["schema_id"] = "chiplog.operator-policy-key-pin.v1"
        else:
            raw["allowed_operations"] = ["ISSUE_PREPARED_EXTERNAL_SELF_DELIVERY_POLICY"]
        import json

        path.write_text(json.dumps(raw, sort_keys=True, separators=(",", ":")))
        path.chmod(0o600)
    with gate.hold(), pytest.raises(OperatorGrantPinError):
        load_operator_grant_key_pin(gate, tenant_id="tenant", database_id="database")
