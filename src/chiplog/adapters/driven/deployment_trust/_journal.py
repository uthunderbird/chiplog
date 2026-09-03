from __future__ import annotations

import fcntl
import hmac
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Literal


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
        self._path = path
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
        self.entries()

    def append(self, decision: bytes, predecessor: str | None) -> str:
        with _lock(self._lock_path, fcntl.LOCK_EX):
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

    def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        with _lock(self._lock_path, fcntl.LOCK_SH):
            return self._entries()

    def _entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        result: list[tuple[str, str | None, bytes]] = []
        predecessor: str | None = None
        for raw in self._path.read_bytes().splitlines():
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
        anchored_head = self._head_path.read_text(encoding="ascii")
        actual_head = result[-1][0] if result else ""
        if anchored_head != actual_head:
            raise RuntimeError("journal rollback or incomplete head publication")
        return tuple(result)

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
