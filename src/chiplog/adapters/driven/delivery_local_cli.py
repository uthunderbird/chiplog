"""Durable delivery into a private OS-user inbox; root owns ticket authorization."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import tempfile
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket

CONTRACT_VERSION = "chiplog.local-cli.delivery.v1"
_DOMAIN = b"chiplog.local-cli.delivery.receipt.v1\x00"


@dataclass(frozen=True)
class LocalCliReceiptObservation:
    occurred_members: tuple[str, ...]
    permanently_incapable_members: tuple[str, ...]


class _Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Receipt(_Wire):
    schema_id: Literal["chiplog.local-cli.delivery.receipt.v1"]
    ticket_fingerprint: str
    signature: str


class _Stored(_Wire):
    schema_id: Literal["chiplog.local-cli.delivery.stored.v1"]
    ticket_hex: str
    receipt_hex: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _ticket_bytes(ticket: IssuedEffectSendTicket) -> bytes:
    if (
        ticket.adapter_contract_version != CONTRACT_VERSION
        or type(ticket.payload) is not bytes
        or not ticket.payload
        or type(ticket.canonical_address) is not bytes
        or not ticket.canonical_address
        or hashlib.sha256(ticket.payload).hexdigest() != ticket.payload_fingerprint
        or type(ticket.bundle_members) is not tuple
        or not ticket.bundle_members
        or len(set(ticket.bundle_members)) != len(ticket.bundle_members)
        or any(type(member) is not str or not member for member in ticket.bundle_members)
    ):
        raise ValueError("invalid local CLI delivery ticket")
    value = asdict(ticket)
    for name, item in value.items():
        if name in {"payload", "canonical_address", "bundle_members"}:
            continue
        if name == "request_ordinal":
            if type(item) is not int or item < 0:
                raise ValueError("invalid ticket ordinal")
        elif type(item) is not str or not item:
            raise ValueError("invalid ticket identity")
    value["payload"] = ticket.payload.hex()
    value["canonical_address"] = ticket.canonical_address.hex()
    return _canonical(value)


def _decode_ticket(raw: bytes) -> IssuedEffectSendTicket:
    value = json.loads(raw)
    value["payload"] = bytes.fromhex(value["payload"])
    value["canonical_address"] = bytes.fromhex(value["canonical_address"])
    value["bundle_members"] = tuple(value["bundle_members"])
    ticket = IssuedEffectSendTicket(**value)
    if _ticket_bytes(ticket) != raw:
        raise ValueError("stored ticket is not canonical")
    return ticket


def verify_local_cli_receipt(
    ticket: IssuedEffectSendTicket, raw: bytes, *, registered_receipt_key: bytes
) -> LocalCliReceiptObservation:
    """Authenticate exact stored delivery; this does not authenticate root issuance."""
    fingerprint = hashlib.sha256(_ticket_bytes(ticket)).hexdigest()
    receipt = _Receipt.model_validate_json(raw)
    expected = hmac.digest(registered_receipt_key, _DOMAIN + fingerprint.encode(), "sha256")
    if (
        not registered_receipt_key
        or _canonical(receipt.model_dump()) != raw
        or receipt.ticket_fingerprint != fingerprint
        or not hmac.compare_digest(receipt.signature, expected.hex())
    ):
        raise ValueError("local CLI delivery receipt authentication failed")
    return LocalCliReceiptObservation(ticket.bundle_members, ())


class LocalCliDeliveryProvider:
    contract_version = CONTRACT_VERSION
    provider_id = "local-cli-inbox"

    def __init__(self, directory: Path, receipt_key: bytes, principal_id: str) -> None:
        if not receipt_key or not principal_id:
            raise ValueError("local delivery requires a stable key and explicit principal")
        self.directory, self._receipt_key = directory, receipt_key
        self.uid = os.getuid()
        self.account_id = "uid:" + str(self.uid)
        self.recipient_id = principal_id
        self.canonical_address = (
            "local-cli-inbox://uid/" + str(self.uid) + "/" + principal_id
        ).encode()
        # Operator provisions the parent; persist the newly created inbox entry.
        directory.mkdir(mode=0o700, exist_ok=True)
        self._check_owner()
        with ExitStack() as resources:
            parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            resources.callback(os.close, parent_fd)
            os.fsync(parent_fd)
        self.endpoint_bytes = _canonical(
            [CONTRACT_VERSION, self.uid, principal_id, str(directory.resolve())]
        )
        self.endpoint_fingerprint = hashlib.sha256(self.endpoint_bytes).hexdigest()
        self.endpoint_head = "local-cli:" + self.endpoint_fingerprint
        self.credential_fingerprint = hashlib.sha256(receipt_key).hexdigest()
        self.credential_binding_head = "local-cli-key:" + self.credential_fingerprint
        pinned = self.credential_fingerprint.encode()
        key_path = directory / "receipt-key.sha256"
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if self._read_private(key_path) != pinned:
                raise PermissionError("local inbox receipt key changed") from None
        else:
            with os.fdopen(fd, "wb") as output:
                output.write(pinned)
                output.flush()
                os.fsync(output.fileno())
            self._sync_directory()

    def _check_owner(self) -> None:
        info = self.directory.lstat()
        if (
            os.getuid() != self.uid
            or info.st_uid != self.uid
            or not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise PermissionError("local inbox must be private to the current UID")

    def _read_private(self, path: Path) -> bytes:
        self._check_owner()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (
                info.st_uid != self.uid
                or not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise PermissionError("local inbox file must be private to the current UID")
            return source.read()

    def _sync_directory(self) -> None:
        with ExitStack() as resources:
            fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
            resources.callback(os.close, fd)
            os.fsync(fd)

    def _path(self, transmission_id: str) -> Path:
        if not transmission_id:
            raise ValueError("transmission identity is required")
        return self.directory / (hashlib.sha256(transmission_id.encode()).hexdigest() + ".json")

    def _validate_target(self, ticket: IssuedEffectSendTicket) -> None:
        _ticket_bytes(ticket)
        if (
            ticket.provider,
            ticket.account,
            ticket.recipient,
            ticket.canonical_address,
            ticket.endpoint_head,
            ticket.credential_binding_head,
        ) != (
            self.provider_id,
            self.account_id,
            self.recipient_id,
            self.canonical_address,
            self.endpoint_head,
            self.credential_binding_head,
        ):
            raise PermissionError("ticket does not target this registered local CLI inbox")

    def _selected(self, transmission_id: str) -> tuple[IssuedEffectSendTicket, bytes] | None:
        try:
            raw = self._read_private(self._path(transmission_id))
        except FileNotFoundError:
            return None
        stored = _Stored.model_validate_json(raw)
        if _canonical(stored.model_dump()) != raw:
            raise ValueError("local inbox record is not canonical")
        ticket = _decode_ticket(bytes.fromhex(stored.ticket_hex))
        receipt = bytes.fromhex(stored.receipt_hex)
        self._validate_target(ticket)
        if ticket.transmission_id != transmission_id:
            raise ValueError("local inbox physical and logical identities differ")
        self.verify_receipt(ticket, receipt)
        # A previous process may have linked the file but lost directory fsync.
        self._sync_directory()
        return ticket, receipt

    async def emit_issued(self, ticket: IssuedEffectSendTicket) -> bytes:
        """Only root calls this after committing its authorized SEND/claim."""
        return self._emit(ticket)

    def _emit(self, ticket: IssuedEffectSendTicket) -> bytes:
        self._check_owner()
        self._validate_target(ticket)
        previous = self._selected(ticket.transmission_id)
        if previous is not None:
            if _ticket_bytes(previous[0]) != _ticket_bytes(ticket):
                raise ValueError("local delivery transmission ticket changed")
            return previous[1]
        fingerprint = hashlib.sha256(_ticket_bytes(ticket)).hexdigest()
        receipt = _Receipt(
            schema_id="chiplog.local-cli.delivery.receipt.v1",
            ticket_fingerprint=fingerprint,
            signature=hmac.digest(
                self._receipt_key, _DOMAIN + fingerprint.encode(), "sha256"
            ).hex(),
        )
        raw_receipt = _canonical(receipt.model_dump())
        stored = _Stored(
            schema_id="chiplog.local-cli.delivery.stored.v1",
            ticket_hex=_ticket_bytes(ticket).hex(),
            receipt_hex=raw_receipt.hex(),
        )
        fd, name = tempfile.mkstemp(prefix=".pending-", dir=self.directory)
        pending = Path(name)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(_canonical(stored.model_dump()))
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(pending, self._path(ticket.transmission_id))
            except FileExistsError:
                selected = self._selected(ticket.transmission_id)
                if selected is None or _ticket_bytes(selected[0]) != _ticket_bytes(ticket):
                    raise ValueError("local delivery transmission ticket changed") from None
            self._sync_directory()
        finally:
            pending.unlink(missing_ok=True)
        return raw_receipt

    def reconcile(self, transmission_id: str) -> bytes | None:
        selected = self._selected(transmission_id)
        return None if selected is None else selected[1]

    def read_payload(self, transmission_id: str) -> bytes | None:
        selected = self._selected(transmission_id)
        return None if selected is None else selected[0].payload

    def verify_receipt(
        self, ticket: IssuedEffectSendTicket, raw: bytes
    ) -> LocalCliReceiptObservation:
        self._validate_target(ticket)
        return verify_local_cli_receipt(ticket, raw, registered_receipt_key=self._receipt_key)
