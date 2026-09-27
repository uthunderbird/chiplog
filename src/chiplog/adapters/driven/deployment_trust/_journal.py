from __future__ import annotations

import fcntl
import hmac
import json
import os
import stat
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from hashlib import sha256
from pathlib import Path
from typing import Literal

from chiplog.platform.authority_gate import AuthorityGate, FileIdentity, checked_file_identity


@contextmanager
def _descriptor(path: Path, flags: int, mode: int = 0o600) -> Iterator[int]:
    value = os.open(path, flags, mode)
    try:
        yield value
    finally:
        os.close(value)


@contextmanager
def _lock(path: Path, operation: int) -> Iterator[None]:
    with _descriptor(path, os.O_CREAT | os.O_RDWR) as descriptor:
        fcntl.flock(descriptor, operation)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)


class IndependentTenantDecisionJournal:
    """Append-only hash-chain file intentionally separate from SQLite backups."""

    def __init__(self, path: Path) -> None:
        self._initialize_bound(path, None)

    @classmethod
    def for_authority_bundle(
        cls, path: Path, *, authority_gate: AuthorityGate
    ) -> IndependentTenantDecisionJournal:
        if not isinstance(authority_gate, AuthorityGate):
            raise TypeError("bound trust adapter requires an AuthorityGate")
        instance = cls.__new__(cls)
        instance._initialize_bound(path, authority_gate)
        return instance

    @classmethod
    def for_existing_authority_bundle(
        cls,
        path: Path,
        *,
        authority_gate: AuthorityGate,
        expected_body_identity: FileIdentity | None = None,
        expected_key_identity: FileIdentity | None = None,
        expected_lock_identity: FileIdentity | None = None,
    ) -> IndependentTenantDecisionJournal:
        """Open a provisioned journal without creating or repairing its sidecars."""
        if not isinstance(authority_gate, AuthorityGate):
            raise TypeError("bound trust adapter requires an AuthorityGate")
        instance = cls.__new__(cls)
        instance._initialize_existing(
            path,
            authority_gate,
            expected_body_identity,
            expected_key_identity,
            expected_lock_identity,
        )
        return instance

    def _initialize_bound(self, path: Path, authority_gate: AuthorityGate | None) -> None:
        self._authority_gate = authority_gate
        if authority_gate is not None and path.is_symlink():
            raise RuntimeError("canonical journal sidecar cannot be a symbolic link")
        with self._authority_scope():
            self._initialize(path.resolve(strict=False))

    @property
    def authority_gate(self) -> AuthorityGate | None:
        return self._authority_gate

    def _authority_scope(self) -> AbstractContextManager[None]:
        return nullcontext() if self._authority_gate is None else self._authority_gate.hold()

    def _initialize(self, path: Path) -> None:
        self._existing_only = False
        self._path = path
        self._decoded: tuple[bytes, tuple[tuple[str, str | None, bytes], ...]] | None = None
        self._head_path = path.with_suffix(path.suffix + ".head")
        self._key_path = path.with_suffix(path.suffix + ".key")
        self._lock_path = path.with_suffix(path.suffix + ".lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)
        path.chmod(0o600)
        if not self._key_path.exists():
            if path.stat().st_size:
                raise RuntimeError("non-empty journal has no authentication key")
            self._key_path.write_bytes(os.urandom(32))
            self._key_path.chmod(0o600)
        self._key = self._key_path.read_bytes()
        if len(self._key) != 32:
            raise RuntimeError("journal authentication key is invalid")
        if not self._head_path.exists():
            if path.stat().st_size:
                raise RuntimeError("non-empty journal has no protected head")
            self._head_path.write_text("", encoding="ascii")
        self._body_identity = checked_file_identity(self._path)
        self._key_identity = checked_file_identity(self._key_path)
        self.entries()

    def _initialize_existing(
        self,
        path: Path,
        authority_gate: AuthorityGate,
        expected_body_identity: FileIdentity | None,
        expected_key_identity: FileIdentity | None,
        expected_lock_identity: FileIdentity | None,
    ) -> None:
        """Bind descriptors first, so validation and consumption use one object."""
        self._authority_gate = authority_gate
        self._existing_only = True
        self._decoded = None
        self._existing_descriptors: dict[str, int] = {}
        self._parent_descriptor: int | None = None
        try:
            parent = path.parent.resolve(strict=True)
            if not parent.is_dir() or path.name in {"", ".", ".."}:
                raise RuntimeError("journal parent is unavailable")
            self._path = parent / path.name
            self._head_path = self._path.with_suffix(self._path.suffix + ".head")
            self._key_path = self._path.with_suffix(self._path.suffix + ".key")
            self._lock_path = self._path.with_suffix(self._path.suffix + ".lock")
            self._head_new_path = self._head_path.with_suffix(self._head_path.suffix + ".new")
            self._parent_descriptor = os.open(
                parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
            )
            self._existing_descriptors = {
                "body": self._open_existing("body", os.O_RDWR),
                "key": self._open_existing("key", os.O_RDONLY),
                "lock": self._open_existing("lock", os.O_RDWR),
            }
            self._body_identity = self._existing_identity("body", expected_body_identity)
            self._key_identity = self._existing_identity("key", expected_key_identity)
            self._lock_identity = self._existing_identity("lock", expected_lock_identity)
            self._key = self._read_descriptor(self._existing_descriptors["key"])
            if len(self._key) != 32:
                raise RuntimeError("journal authentication key is invalid")
            with self._authority_scope(), self._existing_lock(fcntl.LOCK_SH):
                self._existing_sources()
                self._entries()
        except OSError as error:
            self.close()
            raise RuntimeError("journal source is unavailable") from error
        except RuntimeError:
            self.close()
            raise

    def close(self) -> None:
        for descriptor in getattr(self, "_existing_descriptors", {}).values():
            os.close(descriptor)
        self._existing_descriptors = {}
        parent_descriptor = getattr(self, "_parent_descriptor", None)
        if parent_descriptor is not None:
            os.close(parent_descriptor)
            self._parent_descriptor = None

    def _existing_name(self, component: str) -> str:
        return {
            "body": self._path.name,
            "key": self._key_path.name,
            "head": self._head_path.name,
            "lock": self._lock_path.name,
        }[component]

    def _open_existing(self, component: str, flags: int) -> int:
        assert self._parent_descriptor is not None
        return os.open(
            self._existing_name(component),
            flags | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=self._parent_descriptor,
        )

    def _existing_identity(
        self, component: str, expected: FileIdentity | None = None, descriptor: int | None = None
    ) -> FileIdentity:
        assert self._parent_descriptor is not None
        descriptor = self._existing_descriptors.get(component) if descriptor is None else descriptor
        if descriptor is None:
            raise RuntimeError("journal descriptor is unavailable")
        actual = os.fstat(descriptor)
        named = os.stat(
            self._existing_name(component), dir_fd=self._parent_descriptor, follow_symlinks=False
        )
        path = getattr(self, f"_{component}_path", self._path)
        identity = (str(path), actual.st_dev, actual.st_ino)
        if (
            not stat.S_ISREG(actual.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or actual.st_nlink != 1
            or named.st_nlink != 1
            or actual.st_uid != os.geteuid()
            or stat.S_IMODE(actual.st_mode) != 0o600
            or (actual.st_dev, actual.st_ino) != (named.st_dev, named.st_ino)
            or (expected is not None and identity != expected)
        ):
            raise RuntimeError("journal source identity, ownership or permissions differ")
        return identity

    @contextmanager
    def _existing_lock(self, operation: int) -> Iterator[None]:
        descriptor = self._existing_descriptors["lock"]
        self._existing_identity("lock", self._lock_identity)
        fcntl.flock(descriptor, operation)
        try:
            self._existing_identity("lock", self._lock_identity)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)

    @staticmethod
    def _read_descriptor(descriptor: int) -> bytes:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 65536):
            chunks.append(chunk)
        return b"".join(chunks)

    def _existing_sources(self) -> tuple[FileIdentity, FileIdentity, FileIdentity]:
        if self._existing_head_new_present():
            raise RuntimeError("journal has an interrupted head publication")
        body = self._existing_identity("body", self._body_identity)
        key = self._existing_identity("key", self._key_identity)
        self._existing_identity("lock", self._lock_identity)
        head_descriptor = self._open_existing("head", os.O_RDONLY)
        try:
            head = self._existing_identity("head", descriptor=head_descriptor)
            key_bytes = self._read_descriptor(self._existing_descriptors["key"])
            if not hmac.compare_digest(key_bytes, self._key):
                raise RuntimeError("journal authentication key changed after opening")
            return body, key, head
        finally:
            os.close(head_descriptor)

    def _existing_head_new_present(self) -> bool:
        assert self._parent_descriptor is not None
        try:
            os.stat(
                self._head_new_path.name,
                dir_fd=self._parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return True

    def physical_sources(self) -> tuple[FileIdentity, FileIdentity, FileIdentity]:
        with self._authority_scope():
            if self._existing_only:
                return self._existing_sources()
            body = checked_file_identity(self._path, self._body_identity)
            key = checked_file_identity(self._key_path, self._key_identity)
            head = checked_file_identity(self._head_path)
            if not hmac.compare_digest(self._key_path.read_bytes(), self._key):
                raise RuntimeError("journal authentication key changed after opening")
            return body, key, head

    def append(self, decision: bytes, predecessor: str | None) -> str:
        if self._existing_only:
            return self._append_existing(decision, predecessor)
        with self._authority_scope(), _lock(self._lock_path, fcntl.LOCK_EX):
            self.physical_sources()
            entries = self._entries()
            current = entries[-1][0] if entries else None
            if predecessor != current:
                raise RuntimeError("journal predecessor mismatch")
            decision_id = sha256(
                (predecessor or "GENESIS").encode() + b"\x00" + decision
            ).hexdigest()
            line = (
                json.dumps(
                    {
                        "authentication": self._authenticate(decision_id, predecessor, decision),
                        "decision": decision.hex(),
                        "decision_id": decision_id,
                        "predecessor": predecessor,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
            with _descriptor(self._path, os.O_APPEND | os.O_WRONLY) as descriptor:
                os.write(descriptor, line)
                os.fsync(descriptor)
            temporary = self._head_path.with_suffix(self._head_path.suffix + ".new")
            with _descriptor(
                temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY
            ) as temporary_descriptor:
                os.write(temporary_descriptor, decision_id.encode("ascii"))
                os.fsync(temporary_descriptor)
            os.replace(temporary, self._head_path)
            with _descriptor(self._head_path.parent, os.O_RDONLY) as directory_descriptor:
                os.fsync(directory_descriptor)
            return decision_id

    def _append_existing(self, decision: bytes, predecessor: str | None) -> str:
        with self._authority_scope(), self._existing_lock(fcntl.LOCK_EX):
            self._existing_sources()
            entries = self._entries()
            current = entries[-1][0] if entries else None
            if predecessor != current:
                raise RuntimeError("journal predecessor mismatch")
            decision_id = sha256(
                (predecessor or "GENESIS").encode() + b"\x00" + decision
            ).hexdigest()
            line = (
                json.dumps(
                    {
                        "authentication": self._authenticate(decision_id, predecessor, decision),
                        "decision": decision.hex(),
                        "decision_id": decision_id,
                        "predecessor": predecessor,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
            body_descriptor = self._existing_descriptors["body"]
            os.lseek(body_descriptor, 0, os.SEEK_END)
            os.write(body_descriptor, line)
            os.fsync(body_descriptor)
            assert self._parent_descriptor is not None
            temporary = os.open(
                self._head_new_path.name,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=self._parent_descriptor,
            )
            try:
                os.write(temporary, decision_id.encode("ascii"))
                os.fsync(temporary)
            finally:
                os.close(temporary)
            # The checked head is deliberately not pinned across appends, but it
            # must still be the name being replaced immediately before publication.
            head = self._open_existing("head", os.O_RDONLY)
            try:
                self._existing_identity("head", descriptor=head)
                os.replace(
                    self._head_new_path.name,
                    self._head_path.name,
                    src_dir_fd=self._parent_descriptor,
                    dst_dir_fd=self._parent_descriptor,
                )
            finally:
                os.close(head)
            os.fsync(self._parent_descriptor)
            self._existing_sources()
            self._entries()
            return decision_id

    def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        if self._existing_only:
            with self._authority_scope(), self._existing_lock(fcntl.LOCK_SH):
                self._existing_sources()
                return self._entries()
        with self._authority_scope(), _lock(self._lock_path, fcntl.LOCK_SH):
            self.physical_sources()
            return self._entries()

    def _entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        # Reuse decoding only for exact bytes, never metadata or the head alone.
        # entries/append still check source identities and key bytes on every call.
        body = (
            self._read_descriptor(self._existing_descriptors["body"])
            if self._existing_only
            else self._path.read_bytes()
        )
        cached = self._decoded
        if cached is not None and body == cached[0]:
            anchored_head = self._read_head()
            actual_head = cached[1][-1][0] if cached[1] else ""
            if anchored_head != actual_head:
                raise RuntimeError("journal rollback or incomplete head publication")
            return cached[1]
        result: list[tuple[str, str | None, bytes]] = []
        predecessor: str | None = None
        for raw in body.splitlines():
            try:
                item = json.loads(raw)
                decision = bytes.fromhex(item["decision"])
                decision_id = str(item["decision_id"])
                observed_predecessor = item["predecessor"]
                authentication = str(item["authentication"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise RuntimeError("journal entry is unauthentic") from error
            expected = sha256((predecessor or "GENESIS").encode() + b"\x00" + decision).hexdigest()
            expected_authentication = self._authenticate(
                decision_id, observed_predecessor, decision
            )
            if (
                expected != decision_id
                or observed_predecessor != predecessor
                or not hmac.compare_digest(authentication, expected_authentication)
            ):
                raise RuntimeError("journal prefix or predecessor mismatch")
            result.append((decision_id, observed_predecessor, decision))
            predecessor = decision_id
        anchored_head = self._read_head()
        actual_head = result[-1][0] if result else ""
        if anchored_head != actual_head:
            raise RuntimeError("journal rollback or incomplete head publication")
        decoded = tuple(result)
        self._decoded = (body, decoded)
        return decoded

    def _read_head(self) -> str:
        if not self._existing_only:
            return self._head_path.read_text(encoding="ascii")
        descriptor = self._open_existing("head", os.O_RDONLY)
        try:
            self._existing_identity("head", descriptor=descriptor)
            try:
                return self._read_descriptor(descriptor).decode("ascii")
            except UnicodeDecodeError as error:
                raise RuntimeError("journal rollback or incomplete head publication") from error
        finally:
            os.close(descriptor)

    def _authenticate(self, decision_id: str, predecessor: str | None, decision: bytes) -> str:
        payload = (predecessor or "GENESIS").encode() + b"\x00" + decision_id.encode()
        payload += b"\x00" + decision
        return hmac.new(self._key, payload, sha256).hexdigest()

    def observation(self, decision_id: str) -> Literal["DECIDED", "NO_DECISION", "AMBIGUOUS"]:
        try:
            decided = any(item[0] == decision_id for item in self.entries())
            return "DECIDED" if decided else "NO_DECISION"
        except OSError:
            return "AMBIGUOUS"
