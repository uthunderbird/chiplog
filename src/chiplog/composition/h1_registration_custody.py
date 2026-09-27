"""Deployment-owned, fail-closed custody for H1 conversation registrations.

This is deliberately not a configuration reader.  A canonical launcher must
provide an already-open protected directory and independently authenticated
deployment/database/genesis facts.  No operation supplies a path or filename.
The temporary launcher factory below is private: canonical launch wiring is not
present yet, so ordinary callers cannot turn a :class:`~pathlib.Path` into H1
authority.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Annotated, Literal, Never

from pydantic import Field, field_validator, model_validator

from chiplog.capabilities.deployment_trust.cli_custody_contracts import (
    CliCustodyDTO,
    Digest,
    Identity,
    UInt64,
)
from chiplog.platform.authority_gate import AuthorityGate, AuthorityGateError, checked_file_identity

_FILENAME = "h1-registration-custody.json"
_LOCK_PREFIX = ".h1-registration-custody-"
_MAX_BYTES = 65_536
_MAX_ENTRIES = 256
_BINDING_TOKEN = object()


class H1RegistrationCustodyError(RuntimeError):
    """The protected registry is absent, invalid, unavailable, or uncertain."""


class H1RegistrationEntryV1(CliCustodyDTO):
    tenant_id: Identity
    principal_id: Identity
    channel_id: Identity
    registration_id: Identity
    generation: UInt64
    status: Literal["ACTIVE", "REVOKED"]
    origin_recipient_id: Identity
    conversation_id: Identity
    visible_channels: Annotated[tuple[Identity, ...], Field(min_length=1, max_length=256)]
    accepted_policy_selector: Literal["H1_OWNER_ISSUED_ORIGIN_EXACT_V1"] = (
        "H1_OWNER_ISSUED_ORIGIN_EXACT_V1"
    )

    @field_validator("visible_channels", mode="before")
    @classmethod
    def _freeze_visible_channels(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def _validate_visible_channels(self) -> H1RegistrationEntryV1:
        if self.visible_channels != tuple(sorted(set(self.visible_channels))):
            raise ValueError("visible channels must be sorted and unique")
        if self.channel_id not in self.visible_channels:
            raise ValueError("visible channels must contain origin channel")
        return self

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.tenant_id, self.principal_id, self.channel_id)


class H1RegistrationCustodyV1(CliCustodyDTO):
    schema_id: Literal["chiplog.execution.h1-registration-custody.v1"] = (
        "chiplog.execution.h1-registration-custody.v1"
    )
    deployment_id: Identity
    database_id: Identity
    database_genesis_digest: Digest
    entries: Annotated[tuple[H1RegistrationEntryV1, ...], Field(max_length=_MAX_ENTRIES)]

    @field_validator("entries", mode="before")
    @classmethod
    def _freeze_entries(cls, value: object) -> object:
        return tuple(value) if type(value) is list else value

    @model_validator(mode="after")
    def _validate_entries(self) -> H1RegistrationCustodyV1:
        keys = [entry.key for entry in self.entries]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("entries must be sorted and unique by tenant/principal/channel")
        registrations = [entry.registration_id for entry in self.entries]
        if len(registrations) != len(set(registrations)):
            raise ValueError("registration IDs must be unique")
        return self


class TrustedH1RegistrationLauncherBinding:
    """Private launch capability; callers cannot mount from a path or DTO."""

    __slots__ = ("_database_id", "_deployment_id", "_directory_fd", "_genesis", "_installer")

    def __init__(
        self,
        token: object,
        directory_fd: int,
        deployment_id: str,
        database_id: str,
        genesis: str,
    ) -> None:
        if token is not _BINDING_TOKEN:
            raise TypeError("trusted launcher binding is created only by canonical launch")
        self._directory_fd = directory_fd
        self._deployment_id = deployment_id
        self._database_id = database_id
        self._genesis = genesis
        self._installer = object()

    def __reduce__(self) -> Never:
        raise TypeError("trusted launcher binding is not serializable")


def _trusted_launcher_binding_for_canonical_runtime(
    directory_fd: int, deployment_id: str, database_id: str, database_genesis_digest: str
) -> TrustedH1RegistrationLauncherBinding:
    """Temporary canonical-launch seam; integration must retain the opened FD."""
    if type(directory_fd) is not int:
        raise TypeError("canonical launch must provide an open directory descriptor")
    return TrustedH1RegistrationLauncherBinding(
        _BINDING_TOKEN, directory_fd, deployment_id, database_id, database_genesis_digest
    )


def _private_directory(fd: int) -> None:
    metadata = os.fstat(fd)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise H1RegistrationCustodyError("trusted launcher directory is not private")


def _private_regular(fd: int) -> None:
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
        or metadata.st_nlink != 1
    ):
        raise H1RegistrationCustodyError("registry is not private regular storage")


def _reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise H1RegistrationCustodyError("registry has duplicate JSON keys")
        value[key] = item
    return value


def _decode(raw: bytes) -> H1RegistrationCustodyV1:
    if not raw or len(raw) > _MAX_BYTES:
        raise H1RegistrationCustodyError("registry exceeds bounded schema")
    try:
        decoded = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, H1RegistrationCustodyError) as exc:
        if isinstance(exc, H1RegistrationCustodyError):
            raise
        raise H1RegistrationCustodyError("registry is not canonical JSON") from None
    try:
        registry = H1RegistrationCustodyV1.model_validate(decoded)
    except Exception as exc:
        raise H1RegistrationCustodyError("registry has invalid schema") from exc
    if registry.canonical_bytes() != raw:
        raise H1RegistrationCustodyError("registry is not canonical JSON")
    return registry


def _read_registry(directory_fd: int) -> tuple[bytes, H1RegistrationCustodyV1]:
    try:
        fd = os.open(_FILENAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory_fd)
    except FileNotFoundError:
        raise H1RegistrationCustodyError("registry is absent") from None
    except OSError as exc:
        raise H1RegistrationCustodyError("registry cannot be opened safely") from exc
    try:
        _private_regular(fd)
        chunks: list[bytes] = []
        remaining = _MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        _private_regular(fd)
    finally:
        os.close(fd)
    return raw, _decode(raw)


def _pair_lock_name(binding: TrustedH1RegistrationLauncherBinding) -> str:
    return (
        _LOCK_PREFIX
        + hashlib.sha256(f"{binding._database_id}\0{binding._genesis}".encode()).hexdigest()
        + ".lock"
    )


def _acquire_pair_lock(binding: TrustedH1RegistrationLauncherBinding) -> int:
    """Serialize every mount and installer mutation for this registry/database pair."""
    _private_directory(binding._directory_fd)
    lock_fd = -1
    acquired = False
    try:
        lock_fd = os.open(
            _pair_lock_name(binding),
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=binding._directory_fd,
        )
        _private_regular(lock_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        return lock_fd
    except BlockingIOError:
        raise H1RegistrationCustodyError("registry/database pair is already locked") from None
    except OSError as exc:
        raise H1RegistrationCustodyError("registry/database pair lock unavailable") from exc
    finally:
        if lock_fd >= 0 and not acquired:
            with suppress(OSError):
                fcntl.flock(lock_fd, fcntl.LOCK_UN | fcntl.LOCK_NB)
            os.close(lock_fd)


class H1RegistrationCustody:
    """Live mounted registry; close it to release the database-pair lifetime lock."""

    __slots__ = (
        "_authority_condition",
        "_authority_gate",
        "_authority_inflight",
        "_authority_transition",
        "_binding",
        "_lock_fd",
        "_poisoned",
        "_raw",
        "_registry",
    )

    def __init__(
        self,
        binding: TrustedH1RegistrationLauncherBinding,
        lock_fd: int,
        raw: bytes,
        registry: H1RegistrationCustodyV1,
    ) -> None:
        self._authority_condition = threading.Condition(threading.RLock())
        self._authority_gate: AuthorityGate | None = None
        self._authority_inflight = 0
        self._authority_transition = False
        self._binding = binding
        self._lock_fd = lock_fd
        self._raw = raw
        self._registry = registry
        self._poisoned = False

    @staticmethod
    def install(installer: object, registry: H1RegistrationCustodyV1) -> None:
        """Exclusive trusted-installer provisioning; ordinary runtime mount never creates."""
        if type(installer) is not TrustedH1RegistrationLauncherBinding:
            raise H1RegistrationCustodyError("installation requires trusted installer binding")
        if type(registry) is not H1RegistrationCustodyV1:
            raise H1RegistrationCustodyError("installation requires canonical registry")
        trusted = installer
        _private_directory(trusted._directory_fd)
        if (
            registry.deployment_id != trusted._deployment_id
            or registry.database_id != trusted._database_id
            or registry.database_genesis_digest != trusted._genesis
        ):
            raise H1RegistrationCustodyError("installation binding differs from trusted launch")
        raw = registry.canonical_bytes()
        lock_fd = _acquire_pair_lock(trusted)
        fd = -1
        temporary = ".h1-registration-custody-install-" + secrets.token_hex(16) + ".tmp"
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=trusted._directory_fd,
            )
            _private_regular(fd)
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                if written <= 0:
                    raise OSError("incomplete registry installation write")
                offset += written
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.link(
                temporary,
                _FILENAME,
                src_dir_fd=trusted._directory_fd,
                dst_dir_fd=trusted._directory_fd,
                follow_symlinks=False,
            )
            os.fsync(trusted._directory_fd)
        except FileExistsError:
            raise H1RegistrationCustodyError(
                "registry already exists; installation is exclusive"
            ) from None
        except OSError as exc:
            raise H1RegistrationCustodyError(
                "registry installation persistence uncertain; reconcile before mount"
            ) from exc
        finally:
            if fd >= 0:
                os.close(fd)
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=trusted._directory_fd)
            os.close(lock_fd)

    @classmethod
    def mount(cls, binding: object) -> H1RegistrationCustody:
        if type(binding) is not TrustedH1RegistrationLauncherBinding:
            raise H1RegistrationCustodyError("mount requires canonical trusted launcher binding")
        trusted = binding
        lock_fd = _acquire_pair_lock(trusted)
        try:
            raw, registry = _read_registry(trusted._directory_fd)
            if (
                registry.deployment_id != trusted._deployment_id
                or registry.database_id != trusted._database_id
                or registry.database_genesis_digest != trusted._genesis
            ):
                raise H1RegistrationCustodyError("registry binding differs from trusted launch")
        except BaseException:
            os.close(lock_fd)
            raise
        return cls(trusted, lock_fd, raw, registry)

    def close(self) -> None:
        if self._lock_fd >= 0:
            os.close(self._lock_fd)
            self._lock_fd = -1

    def bind_authority_gate(
        self,
        authority_gate: object,
        database_path: object,
        database_identity: object,
    ) -> None:
        """Bind the one runtime authority gate after installed launch authenticates its database."""
        self._available()
        transition_started = False
        try:
            with self._authority_condition:
                if self._authority_gate is not None:
                    raise H1RegistrationCustodyError("authority gate is already bound")
                if self._authority_transition:
                    raise H1RegistrationCustodyError("authority gate transition is in progress")
                self._authority_transition = True
                transition_started = True
            if type(authority_gate) is not AuthorityGate or not isinstance(database_path, Path):
                raise H1RegistrationCustodyError("authority gate binding is invalid")
            if (
                type(database_identity) is not tuple
                or len(database_identity) != 2
                or any(type(value) is not int or value <= 0 for value in database_identity)
            ):
                raise H1RegistrationCustodyError("authority database identity is invalid")
            database = database_path.resolve(strict=False)
            if database != database_path or authority_gate.database != database:
                raise H1RegistrationCustodyError("authority gate differs from installed database")
            try:
                checked_file_identity(
                    database, (str(database), database_identity[0], database_identity[1])
                )
            except AuthorityGateError as exc:
                raise H1RegistrationCustodyError("authority database identity differs") from exc
            with self._authority_condition:
                self._available()
                if self._authority_gate is not None:
                    raise H1RegistrationCustodyError("authority gate is already bound")
                self._authority_gate = authority_gate
        finally:
            if transition_started:
                with self._authority_condition:
                    self._authority_transition = False
                    self._authority_condition.notify_all()

    def unbind_authority_gate(self, authority_gate: object) -> None:
        """Release the exact runtime gate after its owner has drained dependent work."""
        if type(authority_gate) is not AuthorityGate:
            raise H1RegistrationCustodyError("authority gate binding is invalid")
        with self._authority_condition:
            if self._authority_gate is not authority_gate:
                raise H1RegistrationCustodyError("authority gate differs from bound gate")
            if self._authority_transition:
                raise H1RegistrationCustodyError("authority gate transition is in progress")
            self._authority_transition = True
            while self._authority_inflight:
                self._authority_condition.wait()
        try:
            with authority_gate.hold(), self._authority_condition:
                if self._authority_gate is not authority_gate:
                    raise H1RegistrationCustodyError("authority gate differs from bound gate")
                self._authority_gate = None
        finally:
            with self._authority_condition:
                self._authority_transition = False
                self._authority_condition.notify_all()

    def _available(self) -> None:
        if self._poisoned:
            raise H1RegistrationCustodyError("registry is poisoned pending reconciliation")
        if self._lock_fd < 0:
            raise H1RegistrationCustodyError("registry mount is closed")

    def _ensure_current(self) -> None:
        """Live out-of-process replacement is unsupported and fails closed."""
        try:
            raw, registry = _read_registry(self._binding._directory_fd)
        except BaseException as exc:
            self._poisoned = True
            raise H1RegistrationCustodyError(
                "registry currentness is uncertain; mount poisoned"
            ) from exc
        if raw != self._raw or registry != self._registry:
            self._poisoned = True
            raise H1RegistrationCustodyError(
                "registry changed outside trusted installer; mount poisoned"
            )

    @contextmanager
    def _mutation_scope(self) -> Iterator[None]:
        """Lease one binding before entering its gate, so drain-unbind cannot race a stale gate."""
        with self._authority_condition:
            if self._authority_transition:
                raise H1RegistrationCustodyError("authority gate transition is in progress")
            gate = self._authority_gate
            self._authority_inflight += 1
        try:
            if gate is None:
                yield
            else:
                with gate.hold():
                    yield
        finally:
            with self._authority_condition:
                self._authority_inflight -= 1
                self._authority_condition.notify_all()

    def select(self, tenant_id: str, principal_id: str, channel_id: str) -> H1RegistrationEntryV1:
        self._available()
        self._ensure_current()
        matches = [
            entry
            for entry in self._registry.entries
            if entry.key == (tenant_id, principal_id, channel_id)
        ]
        if len(matches) != 1:
            raise H1RegistrationCustodyError("registration selection is absent or ambiguous")
        if matches[0].status != "ACTIVE":
            raise H1RegistrationCustodyError("registration is revoked")
        return matches[0]

    def replace(
        self,
        installer: object,
        expected_bytes: bytes,
        tenant_id: str,
        principal_id: str,
        channel_id: str,
        expected_generation: int,
        replacement: H1RegistrationCustodyV1,
    ) -> None:
        """Trusted installer CAS. Any persistence uncertainty poisons this mount."""
        with self._mutation_scope():
            self._replace(
                installer,
                expected_bytes,
                tenant_id,
                principal_id,
                channel_id,
                expected_generation,
                replacement,
            )

    def revoke(
        self,
        installer: object,
        expected_bytes: bytes,
        tenant_id: str,
        principal_id: str,
        channel_id: str,
        expected_generation: int,
    ) -> None:
        """Write a durable tombstone; a revoked stable registration never reactivates."""
        with self._mutation_scope():
            self._available()
            self._ensure_current()
            key = (tenant_id, principal_id, channel_id)
            entries: list[H1RegistrationEntryV1] = []
            found = False
            for entry in self._registry.entries:
                if entry.key != key:
                    entries.append(entry)
                    continue
                if entry.status == "REVOKED":
                    raise H1RegistrationCustodyError("registration is already revoked")
                found = True
                entries.append(
                    H1RegistrationEntryV1.model_validate(
                        {
                            **entry.model_dump(),
                            "generation": entry.generation + 1,
                            "status": "REVOKED",
                        }
                    )
                )
            if not found:
                raise H1RegistrationCustodyError("registration selection is absent")
            replacement = H1RegistrationCustodyV1.model_validate(
                {**self._registry.model_dump(), "entries": entries}
            )
            self._replace(
                installer,
                expected_bytes,
                tenant_id,
                principal_id,
                channel_id,
                expected_generation,
                replacement,
            )

    def _replace(
        self,
        installer: object,
        expected_bytes: bytes,
        tenant_id: str,
        principal_id: str,
        channel_id: str,
        expected_generation: int,
        replacement: H1RegistrationCustodyV1,
    ) -> None:
        self._available()
        self._ensure_current()
        if installer is not self._binding or type(expected_bytes) is not bytes:
            raise H1RegistrationCustodyError("replacement requires trusted installer binding")
        try:
            current_raw, current = _read_registry(self._binding._directory_fd)
            if current_raw != self._raw or current_raw != expected_bytes:
                raise H1RegistrationCustodyError("replacement CAS bytes differ")
            if current != self._registry:
                raise H1RegistrationCustodyError("replacement CAS bytes differ")
            self._validate_replacement(
                current,
                replacement,
                (tenant_id, principal_id, channel_id),
                expected_generation,
            )
            raw = replacement.canonical_bytes()
            self._durable_replace(raw)
        except H1RegistrationCustodyError:
            raise
        except BaseException as exc:
            self._poisoned = True
            raise H1RegistrationCustodyError(
                "registry persistence uncertain; mount poisoned"
            ) from exc
        self._raw = raw
        self._registry = replacement

    @staticmethod
    def _validate_replacement(
        current: H1RegistrationCustodyV1,
        replacement: H1RegistrationCustodyV1,
        target: tuple[str, str, str],
        expected_generation: int,
    ) -> None:
        if (
            replacement.deployment_id != current.deployment_id
            or replacement.database_id != current.database_id
            or replacement.database_genesis_digest != current.database_genesis_digest
        ):
            raise H1RegistrationCustodyError("replacement binding differs")
        old = {entry.key: entry for entry in current.entries}
        new = {entry.key: entry for entry in replacement.entries}
        if set(old) != set(new):
            raise H1RegistrationCustodyError(
                "replacement must preserve every registration identity"
            )
        old_entry = old.get(target)
        new_entry = new.get(target)
        if old_entry is None or new_entry is None:
            raise H1RegistrationCustodyError("replacement target registration is absent")
        if old_entry.generation != expected_generation:
            raise H1RegistrationCustodyError("replacement CAS generation differs")
        if old_entry.registration_id != new_entry.registration_id:
            raise H1RegistrationCustodyError("registration ID is stable")
        if old_entry.status == "REVOKED" and new_entry.status != "REVOKED":
            raise H1RegistrationCustodyError("revoked registration cannot reactivate")
        if new_entry.generation != old_entry.generation + 1:
            raise H1RegistrationCustodyError("registration generation must advance by one")
        old_mapping = old_entry.model_dump(exclude={"generation", "status"})
        new_mapping = new_entry.model_dump(exclude={"generation", "status"})
        if new_entry.status == "REVOKED" and old_mapping != new_mapping:
            raise H1RegistrationCustodyError("revocation may change only status and generation")
        if any(old[key] != new[key] for key in old if key != target):
            raise H1RegistrationCustodyError("replacement may change only its target registration")

    def _durable_replace(self, raw: bytes) -> None:
        directory_fd = self._binding._directory_fd
        temporary = ".h1-registration-custody-" + secrets.token_hex(16) + ".tmp"
        fd = -1
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=directory_fd,
            )
            _private_regular(fd)
            offset = 0
            while offset < len(raw):
                written = os.write(fd, raw[offset:])
                if written <= 0:
                    raise OSError("incomplete registry write")
                offset += written
            os.fsync(fd)
            os.close(fd)
            fd = -1
            os.replace(temporary, _FILENAME, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            os.fsync(directory_fd)
        except BaseException:
            self._poisoned = True
            raise
        finally:
            if fd >= 0:
                os.close(fd)
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=directory_fd)
