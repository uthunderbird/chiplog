"""Bounded hermetic PRE_AUTH source custody; no source authentication or ACK.

Provisioning is source-side I/O. Observations inspect signed metadata and file
identity without transferring raw bodies. Composition must supply an independent
materialized-token lookup before read can deliver any body to the broker.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from weakref import WeakValueDictionary

from chiplog.platform._ingress_contracts import Head

PROFILE = "chiplog.ingress.hermetic-retained-cli-pre-auth.v1"
FileIdentity = tuple[int, int, int, int, int]


class RetainedSourceError(ValueError):
    """Source custody cannot be established; no transfer or release is authorized."""


@contextmanager
def _descriptor(fd: int) -> Iterator[int]:
    try:
        yield fd
    finally:
        os.close(fd)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _identity(value: os.stat_result) -> FileIdentity:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _proof_digest(metadata: bytes, root: tuple[int, int], identity: FileIdentity) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "schema": "chiplog.ingress.retained-source-proof.v1",
                "signed_metadata_base64": base64.b64encode(metadata).decode(),
                "root_identity": list(root),
                "metadata_identity": list(identity),
            }
        )
    ).hexdigest()


@dataclass(frozen=True)
class RetainedSourceObservation:
    slot_id: str
    raw_digest: str
    byte_count: int
    proof: Head
    root_identity: tuple[int, int]
    raw_identity: FileIdentity
    metadata_identity: FileIdentity
    signed_metadata_bytes: bytes

    def canonical_bytes(self) -> bytes:
        return _canonical(
            {
                "schema": "chiplog.ingress.retained-source-observation.v1",
                "slot_id": self.slot_id,
                "raw_digest": self.raw_digest,
                "byte_count": self.byte_count,
                "proof": self.proof.model_dump(),
                "root_identity": list(self.root_identity),
                "raw_identity": list(self.raw_identity),
                "metadata_identity": list(self.metadata_identity),
                "signed_metadata_base64": base64.b64encode(self.signed_metadata_bytes).decode(),
            }
        )


def decode_retained_observation(raw: bytes) -> RetainedSourceObservation:
    """Decode historical bytes, never issuing live access or verifying source auth.

    Proof hashes the canonical v1 envelope in _proof_digest: exact signed metadata,
    root device/inode and metadata physical identity. Its head is
    retained-source:<digest> and identity is the slot. HMAC authenticity remains
    a fresh adapter check; historical provenance belongs to the selected journal.
    """
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "slot_id",
            "raw_digest",
            "byte_count",
            "proof",
            "root_identity",
            "raw_identity",
            "metadata_identity",
            "signed_metadata_base64",
        }
        or value["schema"] != "chiplog.ingress.retained-source-observation.v1"
    ):
        raise RetainedSourceError("unknown retained observation schema")
    for field, length in (("root_identity", 2), ("raw_identity", 5), ("metadata_identity", 5)):
        identity = value[field]
        if (
            not isinstance(identity, list)
            or len(identity) != length
            or any(type(n) is not int or n < 0 for n in identity)
        ):
            raise RetainedSourceError("invalid retained physical identity")
    if (
        type(value["slot_id"]) is not str
        or not value["slot_id"]
        or type(value["byte_count"]) is not int
        or value["byte_count"] < 0
        or not isinstance(value["raw_digest"], str)
        or len(value["raw_digest"]) != 64
        or any(c not in "0123456789abcdef" for c in value["raw_digest"])
    ):
        raise RetainedSourceError("invalid retained source fields")
    metadata = base64.b64decode(value["signed_metadata_base64"], validate=True)
    envelope = json.loads(metadata)
    if (
        not isinstance(envelope, dict)
        or set(envelope) != {"value", "signature"}
        or not isinstance(envelope["signature"], str)
        or len(envelope["signature"]) != 64
        or any(c not in "0123456789abcdef" for c in envelope["signature"])
        or _canonical(envelope) != metadata
    ):
        raise RetainedSourceError("invalid retained signed metadata envelope")
    original = envelope["value"]
    if (
        not isinstance(original, dict)
        or set(original) != {"scope", "slot_id", "raw_digest", "byte_count", "raw_identity"}
        or any(
            original[key] != value[key]
            for key in ("slot_id", "raw_digest", "byte_count", "raw_identity")
        )
    ):
        raise RetainedSourceError("retained observation differs from original metadata")
    scope = original["scope"]
    if (
        not isinstance(scope, dict)
        or set(scope) != {"profile", "tenant_id", "database_id", "limits"}
        or scope["profile"] != PROFILE
        or any(
            type(scope[key]) is not str or not scope[key] for key in ("tenant_id", "database_id")
        )
        or not isinstance(scope["limits"], list)
        or len(scope["limits"]) != 3
        or any(type(n) is not int or n <= 0 for n in scope["limits"])
        or value["byte_count"] > scope["limits"][2]
        or value["byte_count"] != value["raw_identity"][2]
        or len(metadata) != value["metadata_identity"][2]
    ):
        raise RetainedSourceError("retained source scope or physical size differs")
    digest = _proof_digest(
        metadata, tuple(value["root_identity"]), tuple(value["metadata_identity"])
    )
    proof = Head.model_validate(value["proof"])
    if proof != Head(
        identity=value["slot_id"], head="retained-source:" + digest, fingerprint=digest
    ):
        raise RetainedSourceError("retained proof differs from original metadata")
    result = RetainedSourceObservation(
        value["slot_id"],
        value["raw_digest"],
        value["byte_count"],
        proof,
        tuple(value["root_identity"]),
        tuple(value["raw_identity"]),
        tuple(value["metadata_identity"]),
        metadata,
    )
    if result.canonical_bytes() != raw:
        raise RetainedSourceError("noncanonical retained observation")
    return result


@dataclass(frozen=True)
class RetainedSourceTransfer:
    token_id: str
    slot_id: str
    source_proof: Head
    raw_digest: str
    byte_count: int


class RetainedSourceAdapter:
    def __init__(
        self,
        directory: Path,
        tenant_id: str,
        database_id: str,
        *,
        secret: bytes,
        allow_create: bool,
        maximum_items: int = 8,
        maximum_total_bytes: int = 524288,
        maximum_item_bytes: int = 65536,
        token_validator: Callable[[str, RetainedSourceObservation], bool] | None = None,
    ) -> None:
        if not tenant_id or not database_id or type(secret) is not bytes or len(secret) < 16:
            raise RetainedSourceError("missing source identity or signing secret")
        if any(
            type(n) is not int or n <= 0
            for n in (maximum_items, maximum_total_bytes, maximum_item_bytes)
        ):
            raise RetainedSourceError("source bounds must be positive integers")
        self.directory = directory.absolute()
        self._secret = secret
        self._validator = token_validator
        self._issued: WeakValueDictionary[int, RetainedSourceObservation] = WeakValueDictionary()
        self._limits = (maximum_items, maximum_total_bytes, maximum_item_bytes)
        self._scope = {
            "profile": PROFILE,
            "tenant_id": tenant_id,
            "database_id": database_id,
            "limits": list(self._limits),
        }
        created = False
        if allow_create:
            try:
                self.directory.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
        self._check_path()
        root = self.directory.stat()
        self._root = (root.st_dev, root.st_ino)
        with self._directory() as fd:
            if created:
                self._write_new(fd, "lock", b"")
                self._write_new(fd, "transfers", b"")
                self._write_new(
                    fd,
                    "registry",
                    self._signed(
                        {
                            **self._scope,
                            "root": list(self._root),
                            "controls": self._control_identities(fd),
                        }
                    ),
                )
                os.fsync(fd)
                with _descriptor(
                    os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY)
                ) as parent:
                    os.fsync(parent)
            self._registry(fd)
        with self._locked() as fd:
            self._inventory(fd)
            self._transfers(fd)

    def _check_path(self) -> None:
        for path in (self.directory, *self.directory.parents):
            if stat.S_ISLNK(path.lstat().st_mode):
                raise RetainedSourceError("symbolic source path")
        if not self.directory.is_dir():
            raise RetainedSourceError("source root is not a directory")

    @contextmanager
    def _directory(self) -> Iterator[int]:
        self._check_path()
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            observed = os.fstat(fd)
            if (observed.st_dev, observed.st_ino) != self._root:
                raise RetainedSourceError("source root replaced")
            yield fd
            self._check_path()
            current = self.directory.stat()
            if (current.st_dev, current.st_ino) != self._root:
                raise RetainedSourceError("source root changed during operation")
        finally:
            os.close(fd)

    def _open(self, fd: int, name: str, flags: int = os.O_RDONLY) -> int:
        opened = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            value = os.fstat(opened)
            path = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(value.st_mode)
                or value.st_nlink != 1
                or _identity(value) != _identity(path)
            ):
                raise RetainedSourceError("source file is aliased or not a regular file")
            return opened
        except BaseException:
            os.close(opened)
            raise

    @contextmanager
    def _locked(self) -> Iterator[int]:
        with self._directory() as fd:
            lock = self._open(fd, "lock", os.O_RDWR)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self._registry(fd)
                yield fd
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
                os.close(lock)

    def _signed(self, value: dict[str, object]) -> bytes:
        return _canonical(
            {
                "value": value,
                "signature": hmac.digest(self._secret, _canonical(value), "sha256").hex(),
            }
        )

    def _decode(self, raw: bytes) -> dict[str, object]:
        envelope = json.loads(raw)
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"value", "signature"}
            or not isinstance(envelope["value"], dict)
            or not isinstance(envelope["signature"], str)
            or _canonical(envelope) != raw
            or not hmac.compare_digest(
                envelope["signature"],
                hmac.digest(self._secret, _canonical(envelope["value"]), "sha256").hex(),
            )
        ):
            raise RetainedSourceError("source metadata signature or canonical form differs")
        return cast(dict[str, object], envelope["value"])

    def _read_file(self, fd: int, name: str, maximum: int) -> tuple[bytes, FileIdentity]:
        with _descriptor(self._open(fd, name)) as opened:
            before = _identity(os.fstat(opened))
            if before[2] > maximum:
                raise RetainedSourceError("source file exceeds bounded read")
            chunks = bytearray()
            while len(chunks) <= maximum:
                chunk = os.read(opened, min(65536, maximum + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            after = _identity(os.fstat(opened))
            if (
                len(chunks) > maximum
                or len(chunks) != before[2]
                or after != before
                or _identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != before
            ):
                raise RetainedSourceError("source changed during bounded read")
            return bytes(chunks), before

    def _write_new(self, fd: int, name: str, raw: bytes) -> None:
        with _descriptor(
            os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        ) as opened:
            remaining = memoryview(raw)
            while remaining:
                count = os.write(opened, remaining)
                if count <= 0:
                    raise RetainedSourceError("source write made no progress")
                remaining = remaining[count:]
            os.fsync(opened)

    def _registry(self, fd: int) -> None:
        raw, _ = self._read_file(fd, "registry", 8192)
        if self._decode(raw) != {
            **self._scope,
            "root": list(self._root),
            "controls": self._control_identities(fd),
        }:
            raise RetainedSourceError("source registry identity or limits changed")

    def _control_identities(self, fd: int) -> dict[str, list[int]]:
        result = {}
        for name in ("lock", "transfers"):
            with _descriptor(self._open(fd, name)) as opened:
                value = os.fstat(opened)
                result[name] = [value.st_dev, value.st_ino]
        return result

    @staticmethod
    def _name(slot_id: str) -> str:
        if type(slot_id) is not str or not slot_id or len(slot_id.encode()) > 1024:
            raise RetainedSourceError("invalid bounded source slot")
        return hashlib.sha256(slot_id.encode()).hexdigest()

    def _metadata(self, fd: int, name: str) -> tuple[dict[str, object], FileIdentity, bytes]:
        raw, metadata_identity = self._read_file(fd, name + ".json", 8192)
        value = self._decode(raw)
        if set(value) != {"scope", "slot_id", "raw_digest", "byte_count", "raw_identity"}:
            raise RetainedSourceError("unknown source metadata schema")
        slot = value["slot_id"]
        digest, count = value["raw_digest"], value["byte_count"]
        if (
            value["scope"] != self._scope
            or not isinstance(slot, str)
            or self._name(slot) != name
            or type(count) is not int
            or not 0 <= count <= self._limits[2]
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise RetainedSourceError("source metadata scope or bounds differ")
        with _descriptor(self._open(fd, name + ".raw")) as opened:
            identity = _identity(os.fstat(opened))
            if list(identity) != value["raw_identity"] or identity[2] != count:
                raise RetainedSourceError("retained raw source identity changed")
        return value, metadata_identity, raw

    def _inventory(self, fd: int) -> dict[str, dict[str, object]]:
        names = set(os.listdir(fd))
        controls = {"lock", "registry", "transfers"}
        metadata = {name[:-5] for name in names if name.endswith(".json")}
        expected = controls | {name + suffix for name in metadata for suffix in (".raw", ".json")}
        if names != expected or len(metadata) > self._limits[0]:
            raise RetainedSourceError("unknown, dangling or overfull source inventory")
        result = {name: self._metadata(fd, name)[0] for name in sorted(metadata)}
        if sum(cast(int, value["byte_count"]) for value in result.values()) > self._limits[1]:
            raise RetainedSourceError("source byte inventory exceeds reserve")
        return result

    def provision(self, slot_id: str, raw: bytes) -> None:
        name = self._name(slot_id)
        if type(raw) is not bytes or len(raw) > self._limits[2]:
            raise RetainedSourceError("source item exceeds configured byte bound")
        with self._locked() as fd:
            inventory = self._inventory(fd)
            if name in inventory:
                raise RetainedSourceError("source slot is immutable and already provisioned")
            if (
                len(inventory) >= self._limits[0]
                or sum(cast(int, item["byte_count"]) for item in inventory.values()) + len(raw)
                > self._limits[1]
            ):
                raise RetainedSourceError("source reserve exhausted before transfer")
            self._write_new(fd, name + ".raw", raw)
            raw_identity = _identity(os.stat(name + ".raw", dir_fd=fd, follow_symlinks=False))
            self._write_new(
                fd,
                name + ".json",
                self._signed(
                    {
                        "scope": self._scope,
                        "slot_id": slot_id,
                        "byte_count": len(raw),
                        "raw_digest": hashlib.sha256(raw).hexdigest(),
                        "raw_identity": list(raw_identity),
                    }
                ),
            )
            os.fsync(fd)
            self._inventory(fd)

    def observe(self, slot_id: str) -> RetainedSourceObservation:
        with self._locked() as fd:
            self._inventory(fd)
            value, metadata, raw = self._metadata(fd, self._name(slot_id))
            digest = _proof_digest(raw, self._root, metadata)
            observation = RetainedSourceObservation(
                slot_id,
                cast(str, value["raw_digest"]),
                cast(int, value["byte_count"]),
                Head(identity=slot_id, head="retained-source:" + digest, fingerprint=digest),
                self._root,
                cast(FileIdentity, tuple(cast(list[int], value["raw_identity"]))),
                metadata,
                raw,
            )
            self._issued[id(observation)] = observation
            return observation

    def _verify(self, fd: int, observed: RetainedSourceObservation) -> None:
        if self._issued.get(id(observed)) is not observed:
            raise RetainedSourceError("source observation was not issued by this adapter")
        self._inventory(fd)
        value, identity, raw = self._metadata(fd, self._name(observed.slot_id))
        if (
            observed.root_identity != self._root
            or identity != observed.metadata_identity
            or value["raw_identity"] != list(observed.raw_identity)
            or _proof_digest(raw, self._root, identity) != observed.proof.fingerprint
            or value["raw_digest"] != observed.raw_digest
            or value["byte_count"] != observed.byte_count
        ):
            raise RetainedSourceError("source observation changed")

    def verify(self, observed: RetainedSourceObservation) -> bool:
        with self._locked() as fd:
            self._verify(fd, observed)
            return True

    def _transfers(self, fd: int) -> list[RetainedSourceTransfer]:
        raw, _ = self._read_file(fd, "transfers", 8 * 1024 * 1024)
        result = []
        previous = ""
        for line in raw.splitlines():
            value = self._decode(line)
            if set(value) != {"previous", "token_id", "slot_id", "proof", "digest", "count"}:
                raise RetainedSourceError("unknown source transfer record")
            if value["previous"] != previous:
                raise RetainedSourceError("source transfer sequence differs")
            result.append(
                RetainedSourceTransfer(
                    cast(str, value["token_id"]),
                    cast(str, value["slot_id"]),
                    Head.model_validate(value["proof"]),
                    cast(str, value["digest"]),
                    cast(int, value["count"]),
                )
            )
            previous = hashlib.sha256(line).hexdigest()
        return result

    @property
    def transfers(self) -> tuple[RetainedSourceTransfer, ...]:
        with self._locked() as fd:
            return tuple(self._transfers(fd))

    def read(self, observed: RetainedSourceObservation, token_id: str) -> bytes:
        # Never call composition while holding the source lock: writer admission
        # takes its authority gate before verifying retained source observations.
        self.verify(observed)
        if (
            type(token_id) is not str
            or not token_id
            or self._validator is None
            or self._validator(token_id, observed) is not True
        ):
            raise RetainedSourceError("independently materialized token is required")
        with self._locked() as fd:
            self._verify(fd, observed)
            prior = self._transfers(fd)
            if any(
                (item.token_id == token_id) != (item.slot_id == observed.slot_id) for item in prior
            ):
                raise RetainedSourceError("source transfer token/slot identity changed")
            body, identity = self._read_file(
                fd, self._name(observed.slot_id) + ".raw", self._limits[2]
            )
            if (
                identity != observed.raw_identity
                or hashlib.sha256(body).hexdigest() != observed.raw_digest
            ):
                raise RetainedSourceError("source body differs from retained promise")
            self._verify(fd, observed)
            old, _ = self._read_file(fd, "transfers", 8 * 1024 * 1024)
            line = (
                self._signed(
                    {
                        "previous": hashlib.sha256(old.splitlines()[-1]).hexdigest() if old else "",
                        "token_id": token_id,
                        "slot_id": observed.slot_id,
                        "proof": observed.proof.model_dump(),
                        "digest": observed.raw_digest,
                        "count": observed.byte_count,
                    }
                )
                + b"\n"
            )
            if len(old) + len(line) > 8 * 1024 * 1024:
                raise RetainedSourceError("source transfer observer full; retain custody")
            with _descriptor(self._open(fd, "transfers", os.O_WRONLY | os.O_APPEND)) as opened:
                if os.write(opened, line) != len(line):
                    raise RetainedSourceError("partial transfer observation; retain custody")
                os.fsync(opened)
            return body
