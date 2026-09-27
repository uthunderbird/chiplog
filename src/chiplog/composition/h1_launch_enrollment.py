"""One protected, explicitly enrolled H1 launcher slot.

The installation root is an OS-provisioned trust boundary: its configured
directory and all paths below it must be owned by this launcher identity and
not group/world writable.  This module deliberately has no public CLI entry
point and does not open a runtime; the latter needs the separately-owned
strict read-only SQLite seam.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Never, Protocol

from pydantic import Field, field_validator, model_validator

from chiplog.capabilities.deployment_trust._model import DatabaseGenesis
from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyDTO,
    Digest,
    Identity,
    UInt64,
)
from chiplog.composition.h1_registration_custody import (
    H1RegistrationCustody,
    H1RegistrationCustodyError,
    H1RegistrationCustodyV1,
    _trusted_launcher_binding_for_canonical_runtime,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r7_trust_durability import TrustDurabilityObservation

_MARKER = "h1-launch-enrollment.json"
_INSTALL_LOCK = ".h1-installation.lock"
_PAIR_LOCK_PREFIX = ".h1-registration-custody-"
_MAX_BYTES = 16_384
_EVIDENCE_ROLE = "h1-delivery-evidence"
_EVIDENCE_MARKER = "h1-delivery-evidence-enrollment.json"
_EVIDENCE_BODY = "h1-delivery-evidence"
_RECOVERY_ROLE = "h1-post-seal-recovery"
_RECOVERY_MARKER = "h1-post-seal-recovery-enrollment.json"
_RECOVERY_BODY = "h1-post-seal-recovery"
_RECOVERY_EXECUTION_FENCE = _RECOVERY_BODY + ".execution-fence"
_RECOVERY_MOUNT_ISSUER = object()


class H1LaunchEnrollmentError(RuntimeError):
    """Installed H1 identity or its persistence is unavailable or uncertain."""


class H1EvidenceMountError(H1LaunchEnrollmentError):
    """An enrolled evidence role is absent, foreign, corrupt, or unavailable."""


class H1RecoveryMountError(H1LaunchEnrollmentError):
    """An enrolled post-seal recovery role is absent, foreign, or unavailable."""


class _TrustVerifier(Protocol):
    def verify(self) -> TrustDurabilityObservation | None: ...


def _genesis_digest(tenant_id: str, database_id: str) -> str:
    # Exact byte-equivalent of DeploymentTrustService.initialize(), deliberately
    # excluding deployment_id because existing DatabaseGenesis has no such field.
    raw = json.dumps(
        asdict(DatabaseGenesis(tenant_id, database_id, 1)), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(raw).hexdigest()


class ExpectedH1Genesis:
    """Installer-supplied intent, never inferred from SQLite or a registry."""

    __slots__ = ("database_id", "deployment_id", "digest", "genesis_version", "tenant_id")

    def __init__(
        self, deployment_id: str, tenant_id: str, database_id: str, genesis_version: int = 1
    ) -> None:
        if not all(
            type(value) is str and value for value in (deployment_id, tenant_id, database_id)
        ):
            raise TypeError("expected H1 genesis identities must be nonempty strings")
        if type(genesis_version) is not int or genesis_version != 1:
            raise ValueError("only genesis version 1 is installed")
        self.deployment_id = deployment_id
        self.tenant_id = tenant_id
        self.database_id = database_id
        self.genesis_version = genesis_version
        self.digest = _genesis_digest(tenant_id, database_id)


class InstalledH1Slot:
    """Trusted launcher wiring for one fixed deployment/database installation."""

    __slots__ = (
        "_trust",
        "custody_name",
        "database_id",
        "database_path",
        "deployment_id",
        "root",
        "tenant_id",
    )

    def __init__(
        self,
        root: Path,
        custody_name: str,
        database_path: Path,
        deployment_id: str,
        tenant_id: str,
        database_id: str,
        trust: _TrustVerifier,
    ) -> None:
        root = Path(root)
        database_path = Path(database_path)
        if not root.is_absolute() or not database_path.is_absolute():
            raise ValueError("installed root and database path must be absolute")
        if root != Path(os.path.normpath(root)) or database_path != Path(
            os.path.normpath(database_path)
        ):
            raise ValueError("installed paths must be normalized")
        if not custody_name or "/" in custody_name or custody_name in {".", ".."}:
            raise ValueError("custody directory must be one fixed relative component")
        if not all(
            type(value) is str and value for value in (deployment_id, tenant_id, database_id)
        ):
            raise TypeError("slot identities must be nonempty strings")
        if not callable(getattr(trust, "verify", None)):
            raise TypeError("slot requires existing verified trust")
        self.root, self.custody_name, self.database_path = root, custody_name, database_path
        self.deployment_id, self.tenant_id, self.database_id, self._trust = (
            deployment_id,
            tenant_id,
            database_id,
            trust,
        )


class H1LaunchEnrollmentV1(CliCustodyDTO):
    schema_id: str = "chiplog.execution.h1-launch-enrollment.v1"
    deployment_id: Identity
    database_id: Identity
    tenant_id: Identity
    genesis_version: int = Field(default=1)
    database_genesis_digest: Digest
    database_path: Identity
    database_dev: UInt64
    database_ino: UInt64

    @field_validator("genesis_version")
    @classmethod
    def _version_one(cls, value: int) -> int:
        if type(value) is not int or value != 1:
            raise ValueError("genesis version must be exactly 1")
        return value

    @field_validator("database_ino")
    @classmethod
    def _positive_inode(cls, value: int) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("database inode must be positive")
        return value

    @field_validator("database_path")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if len(value.encode()) > 4096 or "\x00" in value or not os.path.isabs(value):
            raise ValueError("database path is not a bounded absolute path")
        path = Path(value)
        if str(path) != os.path.normpath(value) or any(part in {".", ".."} for part in path.parts):
            raise ValueError("database path is not normalized")
        return value

    @model_validator(mode="after")
    def _fixed_schema(self) -> H1LaunchEnrollmentV1:
        if self.schema_id != "chiplog.execution.h1-launch-enrollment.v1":
            raise ValueError("unknown enrollment schema")
        return self


class H1EvidenceMountEnrollmentV1(CliCustodyDTO):
    """Immutable protected binding for the auxiliary H1 evidence role."""

    schema_id: str = "chiplog.execution.h1-delivery-evidence-enrollment.v1"
    deployment_id: Identity
    database_id: Identity
    tenant_id: Identity
    database_genesis_digest: Digest
    role: Identity
    journal_instance_id: Identity
    body_name: Identity
    body_dev: UInt64
    body_ino: UInt64
    key_dev: UInt64
    key_ino: UInt64
    key_digest: Digest
    lock_dev: UInt64
    lock_ino: UInt64

    @model_validator(mode="after")
    def _fixed_schema(self) -> H1EvidenceMountEnrollmentV1:
        if (
            self.schema_id != "chiplog.execution.h1-delivery-evidence-enrollment.v1"
            or self.role != _EVIDENCE_ROLE
            or self.body_name != _EVIDENCE_BODY
        ):
            raise ValueError("unknown evidence role enrollment")
        return self


class H1RecoveryMountEnrollmentV1(CliCustodyDTO):
    """Immutable protected binding for the post-seal recovery journal role."""

    schema_id: str = "chiplog.execution.h1-post-seal-recovery-enrollment.v1"
    deployment_id: Identity
    database_id: Identity
    tenant_id: Identity
    database_genesis_digest: Digest
    role: Identity
    journal_instance_id: Identity
    body_name: Identity
    body_dev: UInt64
    body_ino: UInt64
    key_dev: UInt64
    key_ino: UInt64
    key_digest: Digest
    lock_dev: UInt64
    lock_ino: UInt64

    @model_validator(mode="after")
    def _fixed_schema(self) -> H1RecoveryMountEnrollmentV1:
        if (
            self.schema_id != "chiplog.execution.h1-post-seal-recovery-enrollment.v1"
            or self.role != _RECOVERY_ROLE
            or self.body_name != _RECOVERY_BODY
        ):
            raise ValueError("unknown recovery role enrollment")
        return self


class H1RecoveryMountEnrollmentV2(CliCustodyDTO):
    """V2 binds the separately provisioned recovery execution-fence inode.

    V1 markers deliberately remain readable only as a migration diagnostic:
    their immutable bytes cannot safely be augmented at runtime.
    """

    schema_id: str = "chiplog.execution.h1-post-seal-recovery-enrollment.v2"
    deployment_id: Identity
    database_id: Identity
    tenant_id: Identity
    database_genesis_digest: Digest
    role: Identity
    journal_instance_id: Identity
    body_name: Identity
    body_dev: UInt64
    body_ino: UInt64
    key_dev: UInt64
    key_ino: UInt64
    key_digest: Digest
    lock_dev: UInt64
    lock_ino: UInt64
    execution_fence_dev: UInt64
    execution_fence_ino: UInt64

    @model_validator(mode="after")
    def _fixed_schema(self) -> H1RecoveryMountEnrollmentV2:
        if (
            self.schema_id != "chiplog.execution.h1-post-seal-recovery-enrollment.v2"
            or self.role != _RECOVERY_ROLE
            or self.body_name != _RECOVERY_BODY
            or (self.execution_fence_dev, self.execution_fence_ino)
            == (self.lock_dev, self.lock_ino)
        ):
            raise ValueError("unknown recovery role enrollment")
        return self


def _private_directory(fd: int) -> os.stat_result:
    item = os.fstat(fd)
    if not stat.S_ISDIR(item.st_mode) or item.st_uid != os.getuid() or item.st_mode & 0o077:
        raise H1LaunchEnrollmentError("installed directory is not protected")
    return item


def _private_regular(fd: int) -> os.stat_result:
    item = os.fstat(fd)
    if (
        not stat.S_ISREG(item.st_mode)
        or item.st_uid != os.getuid()
        or item.st_mode & 0o077
        or item.st_nlink != 1
    ):
        raise H1LaunchEnrollmentError("installed file is not private regular storage")
    return item


def _open_directory(path: Path) -> int:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise H1LaunchEnrollmentError("installed directory cannot be opened safely") from exc
    try:
        _private_directory(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_database(slot: InstalledH1Slot) -> tuple[int, tuple[int, int]]:
    try:
        fd = os.open(slot.database_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise H1LaunchEnrollmentError("installed database cannot be opened safely") from exc
    try:
        item = _private_regular(fd)
        return fd, (item.st_dev, item.st_ino)
    except BaseException:
        os.close(fd)
        raise


def _same_path_identity(path: Path, identity: tuple[int, int]) -> bool:
    try:
        item = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(item.st_mode) and (item.st_dev, item.st_ino) == identity


def _same_directory_identity(path: Path, fd: int) -> bool:
    try:
        observed = os.stat(path, follow_symlinks=False)
        pinned = os.fstat(fd)
    except OSError:
        return False
    return stat.S_ISDIR(observed.st_mode) and (observed.st_dev, observed.st_ino) == (
        pinned.st_dev,
        pinned.st_ino,
    )


def _reject_duplicates(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise H1LaunchEnrollmentError("enrollment has duplicate JSON keys")
        result[key] = value
    return result


def _decode_marker(raw: bytes) -> H1LaunchEnrollmentV1:
    if not raw or len(raw) > _MAX_BYTES:
        raise H1LaunchEnrollmentError("enrollment exceeds bounded schema")
    try:
        value = json.loads(raw.decode(), object_pairs_hook=_reject_duplicates)
        marker = H1LaunchEnrollmentV1.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, H1LaunchEnrollmentError, ValueError) as exc:
        if isinstance(exc, H1LaunchEnrollmentError):
            raise
        raise H1LaunchEnrollmentError("enrollment is not canonical JSON") from exc
    if marker.canonical_bytes() != raw:
        raise H1LaunchEnrollmentError("enrollment is not canonical JSON")
    return marker


def _read_marker(custody_fd: int) -> tuple[bytes, H1LaunchEnrollmentV1]:
    try:
        fd = os.open(_MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=custody_fd)
    except FileNotFoundError:
        raise H1LaunchEnrollmentError("enrollment marker is absent") from None
    except OSError as exc:
        raise H1LaunchEnrollmentError("enrollment marker cannot be opened safely") from exc
    try:
        _private_regular(fd)
        raw = os.read(fd, _MAX_BYTES + 1)
        _private_regular(fd)
    finally:
        os.close(fd)
    return raw, _decode_marker(raw)


def _evidence_instance_id(slot: InstalledH1Slot) -> str:
    raw = json.dumps(
        [
            slot.deployment_id,
            slot.database_id,
            _genesis_digest(slot.tenant_id, slot.database_id),
            slot.tenant_id,
            _EVIDENCE_ROLE,
        ],
        separators=(",", ":"),
    ).encode()
    return _EVIDENCE_ROLE + ":" + hashlib.sha256(raw).hexdigest()


def _recovery_instance_id(slot: InstalledH1Slot) -> str:
    raw = json.dumps(
        [
            slot.deployment_id,
            slot.database_id,
            _genesis_digest(slot.tenant_id, slot.database_id),
            slot.tenant_id,
            _RECOVERY_ROLE,
        ],
        separators=(",", ":"),
    ).encode()
    return _RECOVERY_ROLE + ":" + hashlib.sha256(raw).hexdigest()


def _read_evidence_marker(custody_fd: int) -> tuple[bytes, H1EvidenceMountEnrollmentV1]:
    try:
        fd = os.open(
            _EVIDENCE_MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=custody_fd
        )
    except FileNotFoundError:
        raise H1EvidenceMountError("enrolled evidence marker is absent") from None
    except OSError as exc:
        raise H1EvidenceMountError("enrolled evidence marker is unavailable") from exc
    try:
        _private_regular(fd)
        raw = os.read(fd, _MAX_BYTES + 1)
        _private_regular(fd)
    finally:
        os.close(fd)
    if not raw or len(raw) > _MAX_BYTES:
        raise H1EvidenceMountError("enrolled evidence marker is corrupt")
    try:
        value = json.loads(raw.decode(), object_pairs_hook=_reject_duplicates)
        marker = H1EvidenceMountEnrollmentV1.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, H1LaunchEnrollmentError, ValueError) as exc:
        raise H1EvidenceMountError("enrolled evidence marker is corrupt") from exc
    if marker.canonical_bytes() != raw:
        raise H1EvidenceMountError("enrolled evidence marker is noncanonical")
    return raw, marker


def _read_recovery_marker(custody_fd: int) -> tuple[bytes, H1RecoveryMountEnrollmentV2]:
    try:
        fd = os.open(
            _RECOVERY_MARKER, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=custody_fd
        )
    except FileNotFoundError:
        raise H1RecoveryMountError("enrolled recovery marker is absent") from None
    except OSError as exc:
        raise H1RecoveryMountError("enrolled recovery marker is unavailable") from exc
    try:
        _private_regular(fd)
        raw = os.read(fd, _MAX_BYTES + 1)
        _private_regular(fd)
    finally:
        os.close(fd)
    if not raw or len(raw) > _MAX_BYTES:
        raise H1RecoveryMountError("enrolled recovery marker is corrupt")
    try:
        value = json.loads(raw.decode(), object_pairs_hook=_reject_duplicates)
        if not isinstance(value, dict) or value.get("schema_id") != (
            "chiplog.execution.h1-post-seal-recovery-enrollment.v2"
        ):
            raise H1RecoveryMountError(
                "recovery enrollment requires explicit V2 administrative migration"
            )
        marker = H1RecoveryMountEnrollmentV2.model_validate(value)
    except (UnicodeDecodeError, json.JSONDecodeError, H1LaunchEnrollmentError, ValueError) as exc:
        raise H1RecoveryMountError("enrolled recovery marker is corrupt") from exc
    if marker.canonical_bytes() != raw:
        raise H1RecoveryMountError("enrolled recovery marker is noncanonical")
    return raw, marker


def _open_private_role_file(
    custody_fd: int, name: str, expected: tuple[int, int] | None = None
) -> tuple[int, tuple[int, int]]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=custody_fd)
    except FileNotFoundError:
        raise H1EvidenceMountError("enrolled evidence storage is absent") from None
    except OSError as exc:
        raise H1EvidenceMountError("enrolled evidence storage is unavailable") from exc
    try:
        item = _private_regular(fd)
        identity = (item.st_dev, item.st_ino)
        if expected is not None and identity != expected:
            raise H1EvidenceMountError("enrolled evidence storage changed")
        return fd, identity
    except BaseException:
        os.close(fd)
        raise


def _check_evidence_marker(
    slot: InstalledH1Slot,
    marker: H1EvidenceMountEnrollmentV1,
    *,
    body_identity: tuple[int, int],
    key_identity: tuple[int, int],
    key_bytes: bytes,
    lock_identity: tuple[int, int],
) -> None:
    if (
        marker.deployment_id,
        marker.database_id,
        marker.tenant_id,
        marker.database_genesis_digest,
        marker.role,
        marker.journal_instance_id,
        marker.body_name,
        marker.body_dev,
        marker.body_ino,
        marker.key_dev,
        marker.key_ino,
        marker.key_digest,
        marker.lock_dev,
        marker.lock_ino,
    ) != (
        slot.deployment_id,
        slot.database_id,
        slot.tenant_id,
        _genesis_digest(slot.tenant_id, slot.database_id),
        _EVIDENCE_ROLE,
        _evidence_instance_id(slot),
        _EVIDENCE_BODY,
        body_identity[0],
        body_identity[1],
        key_identity[0],
        key_identity[1],
        hashlib.sha256(key_bytes).hexdigest(),
        lock_identity[0],
        lock_identity[1],
    ):
        raise H1EvidenceMountError("enrolled evidence marker is foreign")


def _check_recovery_marker(
    slot: InstalledH1Slot,
    marker: H1RecoveryMountEnrollmentV2,
    *,
    body_identity: tuple[int, int],
    key_identity: tuple[int, int],
    key_bytes: bytes,
    lock_identity: tuple[int, int],
    execution_fence_identity: tuple[int, int],
) -> None:
    if (
        marker.deployment_id,
        marker.database_id,
        marker.tenant_id,
        marker.database_genesis_digest,
        marker.role,
        marker.journal_instance_id,
        marker.body_name,
        marker.body_dev,
        marker.body_ino,
        marker.key_dev,
        marker.key_ino,
        marker.key_digest,
        marker.lock_dev,
        marker.lock_ino,
        marker.execution_fence_dev,
        marker.execution_fence_ino,
    ) != (
        slot.deployment_id,
        slot.database_id,
        slot.tenant_id,
        _genesis_digest(slot.tenant_id, slot.database_id),
        _RECOVERY_ROLE,
        _recovery_instance_id(slot),
        _RECOVERY_BODY,
        body_identity[0],
        body_identity[1],
        key_identity[0],
        key_identity[1],
        hashlib.sha256(key_bytes).hexdigest(),
        lock_identity[0],
        lock_identity[1],
        execution_fence_identity[0],
        execution_fence_identity[1],
    ):
        raise H1RecoveryMountError("enrolled recovery marker is foreign")


def _pair_lock_name(database_id: str, digest: str) -> str:
    return (
        _PAIR_LOCK_PREFIX
        + hashlib.sha256(f"{database_id}\0{digest}".encode()).hexdigest()
        + ".lock"
    )


def _verify_pair_lock(custody_fd: int, database_id: str, digest: str) -> None:
    try:
        fd = os.open(
            _pair_lock_name(database_id, digest),
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=custody_fd,
        )
    except OSError as exc:
        raise H1LaunchEnrollmentError(
            "P0 pair lock is absent; read-only launch will not create it"
        ) from exc
    try:
        _private_regular(fd)
    finally:
        os.close(fd)


def _verify_active(slot: InstalledH1Slot, expected: ExpectedH1Genesis) -> None:
    observed = slot._trust.verify()
    if observed is None or observed.phase != "ACTIVE":
        raise H1LaunchEnrollmentError("installed trust is not ACTIVE")
    if (observed.tenant_id, observed.database_instance_id, observed.genesis_head) != (
        expected.tenant_id,
        expected.database_id,
        expected.digest,
    ):
        raise H1LaunchEnrollmentError("verified trust differs from explicit expected genesis")


def _check_slot_expected(slot: InstalledH1Slot, expected: ExpectedH1Genesis) -> None:
    if (slot.deployment_id, slot.tenant_id, slot.database_id) != (
        expected.deployment_id,
        expected.tenant_id,
        expected.database_id,
    ):
        raise H1LaunchEnrollmentError("explicit expected genesis differs from installed slot")


def _check_marker(
    slot: InstalledH1Slot, marker: H1LaunchEnrollmentV1, identity: tuple[int, int]
) -> None:
    if (
        marker.deployment_id,
        marker.tenant_id,
        marker.database_id,
        marker.genesis_version,
        marker.database_genesis_digest,
        marker.database_path,
        marker.database_dev,
        marker.database_ino,
    ) != (
        slot.deployment_id,
        slot.tenant_id,
        slot.database_id,
        1,
        _genesis_digest(slot.tenant_id, slot.database_id),
        str(slot.database_path),
        identity[0],
        identity[1],
    ):
        raise H1LaunchEnrollmentError("enrollment differs from installed slot or database locator")


def _acquire_install_lock(root_fd: int, *, create: bool) -> int:
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    try:
        fd = os.open(_INSTALL_LOCK, flags, 0o600, dir_fd=root_fd)
        _private_regular(fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        if "fd" in locals():
            os.close(fd)
        raise H1LaunchEnrollmentError("installed slot is locked") from None
    except OSError as exc:
        if "fd" in locals():
            os.close(fd)
        raise H1LaunchEnrollmentError("installed lock unavailable") from exc


def _write_named_marker(custody_fd: int, name: str, raw: bytes) -> None:
    temporary = "." + name + "-" + secrets.token_hex(16) + ".tmp"
    fd = -1
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=custody_fd,
        )
        _private_regular(fd)
        offset = 0
        while offset < len(raw):
            written = os.write(fd, raw[offset:])
            if written <= 0:
                raise OSError("incomplete enrollment write")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.link(
            temporary, name, src_dir_fd=custody_fd, dst_dir_fd=custody_fd, follow_symlinks=False
        )
        os.unlink(temporary, dir_fd=custody_fd)
        os.fsync(custody_fd)
    except FileExistsError:
        raise H1LaunchEnrollmentError("enrollment marker already exists") from None
    except OSError as exc:
        raise H1LaunchEnrollmentError("enrollment persistence uncertain") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=custody_fd)


def _write_marker(custody_fd: int, raw: bytes) -> None:
    _write_named_marker(custody_fd, _MARKER, raw)


def _provision_h1_enrollment(
    slot: InstalledH1Slot, expected: ExpectedH1Genesis, registry: H1RegistrationCustodyV1
) -> None:
    """Durably publish P0 registry first, then the immutable enrollment marker."""
    if (
        type(slot) is not InstalledH1Slot
        or type(expected) is not ExpectedH1Genesis
        or type(registry) is not H1RegistrationCustodyV1
    ):
        raise TypeError(
            "installed provisioning requires trusted slot, explicit expected genesis, "
            "and P0 registry"
        )
    _check_slot_expected(slot, expected)
    _verify_active(slot, expected)
    if (registry.deployment_id, registry.database_id, registry.database_genesis_digest) != (
        slot.deployment_id,
        slot.database_id,
        expected.digest,
    ):
        raise H1LaunchEnrollmentError("P0 registry differs from explicit installed identity")
    root_fd = custody_fd = db_fd = lock_fd = -1
    try:
        root_fd = _open_directory(slot.root)
        lock_fd = _acquire_install_lock(root_fd, create=True)
        os.fsync(root_fd)
        custody_fd = _open_directory(slot.root / slot.custody_name)
        db_fd, identity = _open_database(slot)
        if not _same_path_identity(slot.database_path, identity):
            raise H1LaunchEnrollmentError("database changed during enrollment")
        try:
            _read_marker(custody_fd)
        except H1LaunchEnrollmentError as exc:
            if "absent" not in str(exc):
                raise
        else:
            raise H1LaunchEnrollmentError("enrollment marker already exists")
        binding = _trusted_launcher_binding_for_canonical_runtime(
            custody_fd, slot.deployment_id, slot.database_id, expected.digest
        )
        H1RegistrationCustody.install(binding, registry)
        _verify_pair_lock(custody_fd, slot.database_id, expected.digest)
        os.fsync(custody_fd)
        marker = H1LaunchEnrollmentV1(
            deployment_id=slot.deployment_id,
            database_id=slot.database_id,
            tenant_id=slot.tenant_id,
            database_genesis_digest=expected.digest,
            database_path=str(slot.database_path),
            database_dev=identity[0],
            database_ino=identity[1],
        )
        _write_marker(custody_fd, marker.canonical_bytes())
        raw, observed = _read_marker(custody_fd)
        _check_marker(slot, observed, identity)
        if raw != marker.canonical_bytes():
            raise H1LaunchEnrollmentError("enrollment marker bytes differ")
    except H1RegistrationCustodyError as exc:
        raise H1LaunchEnrollmentError("P0 registry installation failed") from exc
    finally:
        for fd in (db_fd, custody_fd, lock_fd, root_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)


def _provision_h1_evidence_mount(slot: InstalledH1Slot, expected: ExpectedH1Genesis) -> None:
    """Explicitly create and enroll the auxiliary role; normal launch never calls this."""
    if type(slot) is not InstalledH1Slot or type(expected) is not ExpectedH1Genesis:
        raise TypeError("evidence provisioning requires trusted slot and explicit expected genesis")
    _check_slot_expected(slot, expected)
    _verify_active(slot, expected)
    root_fd = custody_fd = db_fd = lock_fd = body_fd = key_fd = head_fd = role_lock_fd = -1
    try:
        root_fd = _open_directory(slot.root)
        lock_fd = _acquire_install_lock(root_fd, create=True)
        custody_fd = _open_directory(slot.root / slot.custody_name)
        db_fd, database_identity = _open_database(slot)
        if not _same_path_identity(slot.database_path, database_identity):
            raise H1EvidenceMountError("database changed during evidence provisioning")
        raw, launch_marker = _read_marker(custody_fd)
        if raw != launch_marker.canonical_bytes():
            raise H1EvidenceMountError("launch enrollment bytes differ")
        _check_marker(slot, launch_marker, database_identity)
        _verify_pair_lock(custody_fd, slot.database_id, expected.digest)
        try:
            existing_raw, existing = _read_evidence_marker(custody_fd)
        except H1EvidenceMountError as exc:
            if "absent" not in str(exc):
                raise
        else:
            body_fd, body_identity = _open_private_role_file(custody_fd, _EVIDENCE_BODY)
            key_fd, key_identity = _open_private_role_file(custody_fd, _EVIDENCE_BODY + ".key")
            head_fd, _ = _open_private_role_file(custody_fd, _EVIDENCE_BODY + ".head")
            role_lock_fd, role_lock_identity = _open_private_role_file(
                custody_fd, _EVIDENCE_BODY + ".lock"
            )
            key_bytes = os.read(key_fd, 33)
            _check_evidence_marker(
                slot,
                existing,
                body_identity=body_identity,
                key_identity=key_identity,
                key_bytes=key_bytes,
                lock_identity=role_lock_identity,
            )
            if existing_raw != existing.canonical_bytes():
                raise H1EvidenceMountError("enrolled evidence marker bytes differ")
            return
        # This constructor is permitted only in this explicit administrative
        # provisioning path.  Recovery uses EnrolledH1EvidenceMount below.
        from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
        from chiplog.platform.authority_gate import AuthorityGate

        gate = AuthorityGate.for_database(slot.database_path)
        with gate.hold():
            journal = IndependentTenantDecisionJournal.for_authority_bundle(
                slot.root / slot.custody_name / _EVIDENCE_BODY, authority_gate=gate
            )
            body, key, _head = journal.physical_sources()
        # The legacy primitive's head/lock helpers inherit process defaults.
        # Provisioning fixes them before the immutable role marker is published.
        for name in (
            _EVIDENCE_BODY,
            _EVIDENCE_BODY + ".key",
            _EVIDENCE_BODY + ".head",
            _EVIDENCE_BODY + ".lock",
        ):
            os.chmod(name, 0o600, dir_fd=custody_fd, follow_symlinks=False)
        body_identity = (body[1], body[2])
        key_identity = (key[1], key[2])
        key_fd, observed_key_identity = _open_private_role_file(
            custody_fd, _EVIDENCE_BODY + ".key", key_identity
        )
        key_bytes = os.read(key_fd, 33)
        if len(key_bytes) != 32:
            raise H1EvidenceMountError("provisioned evidence key is corrupt")
        role_lock_fd, role_lock_identity = _open_private_role_file(
            custody_fd, _EVIDENCE_BODY + ".lock"
        )
        marker = H1EvidenceMountEnrollmentV1(
            deployment_id=slot.deployment_id,
            database_id=slot.database_id,
            tenant_id=slot.tenant_id,
            database_genesis_digest=expected.digest,
            role=_EVIDENCE_ROLE,
            journal_instance_id=_evidence_instance_id(slot),
            body_name=_EVIDENCE_BODY,
            body_dev=body_identity[0],
            body_ino=body_identity[1],
            key_dev=observed_key_identity[0],
            key_ino=observed_key_identity[1],
            key_digest=hashlib.sha256(key_bytes).hexdigest(),
            lock_dev=role_lock_identity[0],
            lock_ino=role_lock_identity[1],
        )
        _write_named_marker(custody_fd, _EVIDENCE_MARKER, marker.canonical_bytes())
        written, observed = _read_evidence_marker(custody_fd)
        _check_evidence_marker(
            slot,
            observed,
            body_identity=body_identity,
            key_identity=observed_key_identity,
            key_bytes=key_bytes,
            lock_identity=role_lock_identity,
        )
        if written != marker.canonical_bytes():
            raise H1EvidenceMountError("enrolled evidence marker bytes differ")
    finally:
        for fd in (role_lock_fd, head_fd, key_fd, body_fd, db_fd, custody_fd, lock_fd, root_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)


def _require_absent_recovery_sidecars(custody_fd: int) -> None:
    """Reject credential seeding before the immutable recovery marker exists."""
    for name in (
        _RECOVERY_BODY,
        _RECOVERY_BODY + ".key",
        _RECOVERY_BODY + ".head",
        _RECOVERY_BODY + ".lock",
        _RECOVERY_EXECUTION_FENCE,
        _RECOVERY_BODY + ".head.new",
    ):
        try:
            os.stat(name, dir_fd=custody_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise H1RecoveryMountError("recovery role storage is unavailable") from exc
        raise H1RecoveryMountError("un-enrolled recovery role storage already exists")


def _open_recovery_role_file(
    custody_fd: int, name: str, expected: tuple[int, int] | None = None
) -> tuple[int, tuple[int, int]]:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=custody_fd)
    except FileNotFoundError:
        raise H1RecoveryMountError("enrolled recovery storage is absent") from None
    except OSError as exc:
        raise H1RecoveryMountError("enrolled recovery storage is unavailable") from exc
    try:
        item = _private_regular(fd)
        identity = (item.st_dev, item.st_ino)
        if expected is not None and identity != expected:
            raise H1RecoveryMountError("enrolled recovery storage changed")
        return fd, identity
    except BaseException:
        os.close(fd)
        raise


def _provision_h1_recovery_mount(slot: InstalledH1Slot, expected: ExpectedH1Genesis) -> None:
    """Administrative-only fresh enrollment; runtime opening never invokes this."""
    if type(slot) is not InstalledH1Slot or type(expected) is not ExpectedH1Genesis:
        raise TypeError("recovery provisioning requires trusted slot and explicit expected genesis")
    _check_slot_expected(slot, expected)
    _verify_active(slot, expected)
    root_fd = custody_fd = db_fd = lock_fd = body_fd = key_fd = head_fd = role_lock_fd = -1
    fence_fd = -1
    try:
        root_fd = _open_directory(slot.root)
        lock_fd = _acquire_install_lock(root_fd, create=True)
        custody_fd = _open_directory(slot.root / slot.custody_name)
        db_fd, database_identity = _open_database(slot)
        if not _same_path_identity(slot.database_path, database_identity):
            raise H1RecoveryMountError("database changed during recovery provisioning")
        launch_raw, launch_marker = _read_marker(custody_fd)
        _check_marker(slot, launch_marker, database_identity)
        if launch_raw != launch_marker.canonical_bytes():
            raise H1RecoveryMountError("launch enrollment bytes differ")
        _verify_pair_lock(custody_fd, slot.database_id, expected.digest)
        try:
            existing_raw, existing = _read_recovery_marker(custody_fd)
        except H1RecoveryMountError as exc:
            if "absent" not in str(exc):
                raise
        else:
            body_fd, body_identity = _open_recovery_role_file(custody_fd, _RECOVERY_BODY)
            key_fd, key_identity = _open_recovery_role_file(custody_fd, _RECOVERY_BODY + ".key")
            head_fd, _ = _open_recovery_role_file(custody_fd, _RECOVERY_BODY + ".head")
            role_lock_fd, lock_identity = _open_recovery_role_file(
                custody_fd, _RECOVERY_BODY + ".lock"
            )
            fence_fd, fence_identity = _open_recovery_role_file(
                custody_fd, _RECOVERY_EXECUTION_FENCE
            )
            key_bytes = os.read(key_fd, 33)
            if len(key_bytes) != 32:
                raise H1RecoveryMountError("enrolled recovery key is corrupt")
            _check_recovery_marker(
                slot,
                existing,
                body_identity=body_identity,
                key_identity=key_identity,
                key_bytes=key_bytes,
                lock_identity=lock_identity,
                execution_fence_identity=fence_identity,
            )
            if existing_raw != existing.canonical_bytes():
                raise H1RecoveryMountError("enrolled recovery marker bytes differ")
            return
        _require_absent_recovery_sidecars(custody_fd)
        from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal

        gate = AuthorityGate.for_database(slot.database_path)
        with gate.hold():
            journal = IndependentTenantDecisionJournal.for_authority_bundle(
                slot.root / slot.custody_name / _RECOVERY_BODY, authority_gate=gate
            )
            body, key, _head = journal.physical_sources()
        for name in (
            _RECOVERY_BODY,
            _RECOVERY_BODY + ".key",
            _RECOVERY_BODY + ".head",
            _RECOVERY_BODY + ".lock",
        ):
            os.chmod(name, 0o600, dir_fd=custody_fd, follow_symlinks=False)
        body_identity = (body[1], body[2])
        key_fd, key_identity = _open_recovery_role_file(
            custody_fd, _RECOVERY_BODY + ".key", (key[1], key[2])
        )
        key_bytes = os.read(key_fd, 33)
        if len(key_bytes) != 32:
            raise H1RecoveryMountError("provisioned recovery key is corrupt")
        role_lock_fd, lock_identity = _open_recovery_role_file(custody_fd, _RECOVERY_BODY + ".lock")
        try:
            fence_fd = os.open(
                _RECOVERY_EXECUTION_FENCE,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=custody_fd,
            )
            os.fsync(fence_fd)
            os.fsync(custody_fd)
        except OSError as exc:
            raise H1RecoveryMountError("recovery execution fence cannot be provisioned") from exc
        fence_identity = (os.fstat(fence_fd).st_dev, os.fstat(fence_fd).st_ino)
        _private_regular(fence_fd)
        marker = H1RecoveryMountEnrollmentV2(
            deployment_id=slot.deployment_id,
            database_id=slot.database_id,
            tenant_id=slot.tenant_id,
            database_genesis_digest=expected.digest,
            role=_RECOVERY_ROLE,
            journal_instance_id=_recovery_instance_id(slot),
            body_name=_RECOVERY_BODY,
            body_dev=body_identity[0],
            body_ino=body_identity[1],
            key_dev=key_identity[0],
            key_ino=key_identity[1],
            key_digest=hashlib.sha256(key_bytes).hexdigest(),
            lock_dev=lock_identity[0],
            lock_ino=lock_identity[1],
            execution_fence_dev=fence_identity[0],
            execution_fence_ino=fence_identity[1],
        )
        _write_named_marker(custody_fd, _RECOVERY_MARKER, marker.canonical_bytes())
        written, observed = _read_recovery_marker(custody_fd)
        _check_recovery_marker(
            slot,
            observed,
            body_identity=body_identity,
            key_identity=key_identity,
            key_bytes=key_bytes,
            lock_identity=lock_identity,
            execution_fence_identity=fence_identity,
        )
        if written != marker.canonical_bytes():
            raise H1RecoveryMountError("enrolled recovery marker bytes differ")
    finally:
        for fd in (
            fence_fd,
            role_lock_fd,
            head_fd,
            key_fd,
            body_fd,
            db_fd,
            custody_fd,
            lock_fd,
            root_fd,
        ):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)


class InstalledH1Launch:
    __slots__ = (
        "_custody_fd",
        "_db_fd",
        "_lock_fd",
        "_poisoned",
        "_raw",
        "_root_fd",
        "_slot",
        "custody",
        "database_identity",
        "database_path",
    )

    def __init__(
        self,
        slot: InstalledH1Slot,
        root_fd: int,
        custody_fd: int,
        db_fd: int,
        lock_fd: int,
        raw: bytes,
        identity: tuple[int, int],
        custody: H1RegistrationCustody,
    ) -> None:
        self.database_path, self.database_identity, self.custody = (
            slot.database_path,
            identity,
            custody,
        )
        (
            self._slot,
            self._root_fd,
            self._custody_fd,
            self._db_fd,
            self._lock_fd,
            self._raw,
            self._poisoned,
        ) = slot, root_fd, custody_fd, db_fd, lock_fd, raw, False

    def __reduce__(self) -> Never:
        raise TypeError("installed launch is not serializable")

    def assert_current(self) -> None:
        if self._poisoned:
            raise H1LaunchEnrollmentError("installed launch is poisoned")
        try:
            _private_directory(self._root_fd)
            _private_directory(self._custody_fd)
            if not _same_directory_identity(self._slot.root, self._root_fd):
                raise H1LaunchEnrollmentError("installed root changed")
            if not _same_directory_identity(
                self._slot.root / self._slot.custody_name, self._custody_fd
            ):
                raise H1LaunchEnrollmentError("installed custody directory changed")
            if (
                not _same_path_identity(self._slot.database_path, self.database_identity)
                or (os.fstat(self._db_fd).st_dev, os.fstat(self._db_fd).st_ino)
                != self.database_identity
            ):
                raise H1LaunchEnrollmentError("installed database changed")
            raw, marker = _read_marker(self._custody_fd)
            if raw != self._raw:
                raise H1LaunchEnrollmentError("enrollment marker changed")
            _check_marker(self._slot, marker, self.database_identity)
            self.custody._ensure_current()
        except BaseException as exc:
            self._poisoned = True
            if isinstance(exc, H1LaunchEnrollmentError):
                raise
            raise H1LaunchEnrollmentError("installed launch currentness is uncertain") from exc

    def close(self) -> None:
        self.custody.close()
        for name in ("_db_fd", "_custody_fd", "_lock_fd", "_root_fd"):
            fd = getattr(self, name)
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
                setattr(self, name, -1)

    def open_enrolled_evidence_mount(self, authority_gate: object) -> EnrolledH1EvidenceMount:
        """Return the installed role binding; it never provisions or repairs storage."""
        from chiplog.platform.authority_gate import AuthorityGate

        if type(authority_gate) is not AuthorityGate:
            raise TypeError("enrolled evidence mount requires the runtime authority gate")
        if authority_gate.database != self.database_path.resolve(strict=False):
            raise H1EvidenceMountError("enrolled evidence gate is foreign")
        self.assert_current()
        return EnrolledH1EvidenceMount(self, authority_gate)

    def open_enrolled_recovery_mount(self, authority_gate: object) -> EnrolledH1RecoveryMount:
        """Return the existing post-seal role; this never provisions or repairs it."""
        if type(authority_gate) is not AuthorityGate:
            raise TypeError("enrolled recovery mount requires the runtime authority gate")
        if authority_gate.database != self.database_path.resolve(strict=False):
            raise H1RecoveryMountError("enrolled recovery gate is foreign")
        self.assert_current()
        return EnrolledH1RecoveryMount(self, authority_gate, _RECOVERY_MOUNT_ISSUER)


class EnrolledH1EvidenceMount:
    """Private, descriptor-bound installed evidence role for E's reader seam."""

    __slots__ = (
        "_body_identity",
        "_head_identity",
        "_key_identity",
        "_launch",
        "_lock_identity",
        "_marker_raw",
        "_poisoned",
        "authority_gate",
        "journal_instance_id",
        "tenant_id",
    )
    authority_gate: AuthorityGate

    def __init__(self, launch: InstalledH1Launch, authority_gate: object) -> None:
        if type(launch) is not InstalledH1Launch or type(authority_gate) is not AuthorityGate:
            raise TypeError("enrolled evidence mount is issued only by installed launch")
        self._launch = launch
        self.authority_gate = authority_gate
        self.tenant_id = launch._slot.tenant_id
        self.journal_instance_id = _evidence_instance_id(launch._slot)
        self._marker_raw = b""
        self._poisoned = False
        self._body_identity = self._key_identity = (0, 0)
        self._head_identity = self._lock_identity = (0, 0)
        self._refresh_current()

    def __reduce__(self) -> Never:
        raise TypeError("enrolled evidence mount is not serializable")

    @property
    def body_identity(self) -> tuple[int, int]:
        return self._body_identity

    @property
    def key_identity(self) -> tuple[int, int]:
        return self._key_identity

    def _refresh_current(self) -> None:
        if self._poisoned:
            raise H1EvidenceMountError("enrolled evidence mount is poisoned")
        launch = self._launch
        launch.assert_current()
        body_fd = key_fd = head_fd = role_lock_fd = -1
        try:
            raw, marker = _read_evidence_marker(launch._custody_fd)
            body_fd, body_identity = _open_private_role_file(
                launch._custody_fd,
                _EVIDENCE_BODY,
                None if self._body_identity == (0, 0) else self._body_identity,
            )
            key_fd, key_identity = _open_private_role_file(
                launch._custody_fd,
                _EVIDENCE_BODY + ".key",
                None if self._key_identity == (0, 0) else self._key_identity,
            )
            head_fd, head_identity = _open_private_role_file(
                launch._custody_fd, _EVIDENCE_BODY + ".head"
            )
            role_lock_fd, lock_identity = _open_private_role_file(
                launch._custody_fd,
                _EVIDENCE_BODY + ".lock",
                None if self._lock_identity == (0, 0) else self._lock_identity,
            )
            key_bytes = os.read(key_fd, 33)
            if len(key_bytes) != 32:
                raise H1EvidenceMountError("enrolled evidence key is corrupt")
            _check_evidence_marker(
                launch._slot,
                marker,
                body_identity=body_identity,
                key_identity=key_identity,
                key_bytes=key_bytes,
                lock_identity=lock_identity,
            )
            if self._marker_raw and raw != self._marker_raw:
                raise H1EvidenceMountError("enrolled evidence marker changed")
            self._marker_raw = raw
            self._body_identity, self._key_identity = body_identity, key_identity
            self._head_identity, self._lock_identity = head_identity, lock_identity
        except BaseException as exc:
            self._poisoned = True
            if isinstance(exc, H1EvidenceMountError):
                raise
            raise H1EvidenceMountError("enrolled evidence validation is unavailable") from exc
        finally:
            for fd in (role_lock_fd, head_fd, key_fd, body_fd):
                if fd >= 0:
                    with suppress(OSError):
                        os.close(fd)

    def assert_current(self) -> None:
        self._refresh_current()

    def _open_existing_evidence_journal(self) -> object:
        """Open only the pinned role through the primitive's non-creating API."""
        self.assert_current()
        from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal

        opener = getattr(IndependentTenantDecisionJournal, "for_existing_authority_bundle", None)
        if opener is None:
            raise H1EvidenceMountError("existing-only evidence journal opener is unavailable")
        try:
            journal = opener(
                self._launch._slot.root / self._launch._slot.custody_name / _EVIDENCE_BODY,
                authority_gate=self.authority_gate,
                expected_body_identity=(
                    str(self._launch._slot.root / self._launch._slot.custody_name / _EVIDENCE_BODY),
                    *self._body_identity,
                ),
                expected_key_identity=(
                    str(
                        self._launch._slot.root
                        / self._launch._slot.custody_name
                        / (_EVIDENCE_BODY + ".key")
                    ),
                    *self._key_identity,
                ),
                expected_lock_identity=(
                    str(
                        self._launch._slot.root
                        / self._launch._slot.custody_name
                        / (_EVIDENCE_BODY + ".lock")
                    ),
                    *self._lock_identity,
                ),
            )
            sources = journal.physical_sources()
            if (sources[0][1], sources[0][2]) != self._body_identity or (
                sources[1][1],
                sources[1][2],
            ) != self._key_identity:
                raise H1EvidenceMountError("existing evidence journal differs from enrollment")
            self.assert_current()
            return journal
        except H1EvidenceMountError:
            raise
        except BaseException as exc:
            raise H1EvidenceMountError("existing evidence journal is unavailable") from exc


class EnrolledH1RecoveryMount:
    """Issuer-private, descriptor-bound role for the post-seal recovery journal."""

    __slots__ = (
        "_body_identity",
        "_execution_fence_identity",
        "_key_identity",
        "_launch",
        "_lock_identity",
        "_marker_raw",
        "_poisoned",
        "authority_gate",
        "journal_instance_id",
        "tenant_id",
    )
    authority_gate: AuthorityGate

    def __init__(self, launch: InstalledH1Launch, authority_gate: object, issuer: object) -> None:
        if (
            type(launch) is not InstalledH1Launch
            or type(authority_gate) is not AuthorityGate
            or issuer is not _RECOVERY_MOUNT_ISSUER
        ):
            raise TypeError("enrolled recovery mount is issued only by installed launch")
        self._launch = launch
        self.authority_gate = authority_gate
        self.tenant_id = launch._slot.tenant_id
        self.journal_instance_id = _recovery_instance_id(launch._slot)
        self._marker_raw = b""
        self._poisoned = False
        self._body_identity = self._key_identity = self._lock_identity = (0, 0)
        self._execution_fence_identity = (0, 0)
        self._refresh_current()

    def __reduce__(self) -> Never:
        raise TypeError("enrolled recovery mount is not serializable")

    @property
    def body_identity(self) -> tuple[int, int]:
        return self._body_identity

    @property
    def key_identity(self) -> tuple[int, int]:
        return self._key_identity

    @property
    def execution_fence_identity(self) -> tuple[int, int]:
        return self._execution_fence_identity

    def close(self) -> None:
        self._poisoned = True

    def _refresh_current(self) -> None:
        if self._poisoned:
            raise H1RecoveryMountError("enrolled recovery mount is closed or poisoned")
        launch = self._launch
        body_fd = key_fd = head_fd = lock_fd = fence_fd = -1
        try:
            launch.assert_current()
            raw, marker = _read_recovery_marker(launch._custody_fd)
            body_fd, body_identity = _open_recovery_role_file(
                launch._custody_fd,
                _RECOVERY_BODY,
                None if self._body_identity == (0, 0) else self._body_identity,
            )
            key_fd, key_identity = _open_recovery_role_file(
                launch._custody_fd,
                _RECOVERY_BODY + ".key",
                None if self._key_identity == (0, 0) else self._key_identity,
            )
            # The head is deliberately live: append replaces it atomically.
            head_fd, _ = _open_recovery_role_file(launch._custody_fd, _RECOVERY_BODY + ".head")
            lock_fd, lock_identity = _open_recovery_role_file(
                launch._custody_fd,
                _RECOVERY_BODY + ".lock",
                None if self._lock_identity == (0, 0) else self._lock_identity,
            )
            fence_fd, fence_identity = _open_recovery_role_file(
                launch._custody_fd,
                _RECOVERY_EXECUTION_FENCE,
                None
                if self._execution_fence_identity == (0, 0)
                else self._execution_fence_identity,
            )
            key_bytes = os.read(key_fd, 33)
            if len(key_bytes) != 32:
                raise H1RecoveryMountError("enrolled recovery key is corrupt")
            _check_recovery_marker(
                launch._slot,
                marker,
                body_identity=body_identity,
                key_identity=key_identity,
                key_bytes=key_bytes,
                lock_identity=lock_identity,
                execution_fence_identity=fence_identity,
            )
            if self._marker_raw and raw != self._marker_raw:
                raise H1RecoveryMountError("enrolled recovery marker changed")
            self._marker_raw = raw
            (
                self._body_identity,
                self._key_identity,
                self._lock_identity,
                self._execution_fence_identity,
            ) = (
                body_identity,
                key_identity,
                lock_identity,
                fence_identity,
            )
        except BaseException as exc:
            self._poisoned = True
            if isinstance(exc, H1RecoveryMountError):
                raise
            raise H1RecoveryMountError("enrolled recovery validation is unavailable") from exc
        finally:
            for fd in (fence_fd, lock_fd, head_fd, key_fd, body_fd):
                if fd >= 0:
                    with suppress(OSError):
                        os.close(fd)

    def assert_current(self) -> None:
        self._refresh_current()

    def _open_existing_execution_fence(self) -> int:
        """Return one fresh descriptor for the enrolled execution inode only."""
        self.assert_current()
        try:
            fd = os.open(
                _RECOVERY_EXECUTION_FENCE,
                os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=self._launch._custody_fd,
            )
        except OSError as exc:
            raise H1RecoveryMountError("enrolled recovery execution fence is unavailable") from exc
        try:
            item = _private_regular(fd)
            if (item.st_dev, item.st_ino) != self._execution_fence_identity:
                raise H1RecoveryMountError("enrolled recovery execution fence changed")
            self.assert_current()
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _open_existing_recovery_journal(self) -> object:
        """Open the authenticated role through the primitive's non-creating API only."""
        self.assert_current()
        from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal

        journal: IndependentTenantDecisionJournal | None = None
        try:
            journal = IndependentTenantDecisionJournal.for_existing_authority_bundle(
                self._launch._slot.root / self._launch._slot.custody_name / _RECOVERY_BODY,
                authority_gate=self.authority_gate,
                expected_body_identity=(
                    str(self._launch._slot.root / self._launch._slot.custody_name / _RECOVERY_BODY),
                    *self._body_identity,
                ),
                expected_key_identity=(
                    str(
                        self._launch._slot.root
                        / self._launch._slot.custody_name
                        / (_RECOVERY_BODY + ".key")
                    ),
                    *self._key_identity,
                ),
                expected_lock_identity=(
                    str(
                        self._launch._slot.root
                        / self._launch._slot.custody_name
                        / (_RECOVERY_BODY + ".lock")
                    ),
                    *self._lock_identity,
                ),
            )
            sources = journal.physical_sources()
            if (sources[0][1], sources[0][2]) != self._body_identity or (
                sources[1][1],
                sources[1][2],
            ) != self._key_identity:
                raise H1RecoveryMountError("existing recovery journal differs from enrollment")
            self.assert_current()
            return journal
        except H1RecoveryMountError:
            if journal is not None:
                journal.close()
            raise
        except BaseException as exc:
            if journal is not None:
                journal.close()
            raise H1RecoveryMountError("existing recovery journal is unavailable") from exc


@contextmanager
def _open_installed_h1_launch(slot: InstalledH1Slot) -> Iterator[InstalledH1Launch]:
    if type(slot) is not InstalledH1Slot:
        raise TypeError("installed launch requires trusted slot")
    root_fd = custody_fd = db_fd = lock_fd = -1
    mounted: H1RegistrationCustody | None = None
    try:
        root_fd = _open_directory(slot.root)
        lock_fd = _acquire_install_lock(root_fd, create=False)
        custody_fd = _open_directory(slot.root / slot.custody_name)
        db_fd, identity = _open_database(slot)
        raw, marker = _read_marker(custody_fd)
        _check_marker(slot, marker, identity)
        _verify_active(
            slot,
            ExpectedH1Genesis(marker.deployment_id, marker.tenant_id, marker.database_id),
        )
        _verify_pair_lock(custody_fd, slot.database_id, marker.database_genesis_digest)
        binding = _trusted_launcher_binding_for_canonical_runtime(
            custody_fd, slot.deployment_id, slot.database_id, marker.database_genesis_digest
        )
        mounted = H1RegistrationCustody.mount(binding)
        launch = InstalledH1Launch(
            slot, root_fd, custody_fd, db_fd, lock_fd, raw, identity, mounted
        )
        root_fd = custody_fd = db_fd = lock_fd = -1
        mounted = None
        launch.assert_current()
        try:
            yield launch
        finally:
            launch.close()
    except H1RegistrationCustodyError as exc:
        raise H1LaunchEnrollmentError("P0 registry cannot be mounted") from exc
    finally:
        if mounted is not None:
            mounted.close()
        for fd in (db_fd, custody_fd, lock_fd, root_fd):
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
