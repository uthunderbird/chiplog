"""Broker-private offline effect leaf with an independent observable transfer log.

The broker alone authenticates/consumes issued tickets and orders the deployment
gate against SEND_COMMITTED. This leaf cannot issue authority or write the tenant
journal. A ticket DTO by itself is not proof of issuance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class IssuedEffectSendTicket:
    tenant_id: str
    issued_operation_id: str
    journal_decision_id: str
    journal_decision_fingerprint: str
    transmission_id: str
    transmission_fingerprint: str
    request_ordinal: int
    intent_id: str
    intent_fingerprint: str
    provider: str
    account: str
    recipient: str
    canonical_address: bytes
    endpoint_head: str
    credential_binding_head: str
    adapter_contract_version: str
    payload: bytes
    payload_fingerprint: str
    idempotency_key: str
    bundle_members: tuple[str, ...]


@dataclass(frozen=True)
class HermeticTransfer:
    sequence: int
    ticket: IssuedEffectSendTicket
    occurred_members: tuple[str, ...]
    permanently_incapable_members: tuple[str, ...]
    signed_receipt: bytes


class HermeticResponseLost(TimeoutError):
    """The external fake effect occurred; local code did not receive its receipt."""


_RECEIPT_DOMAIN = b"chiplog.hermetic-effects.receipt.v1\x00"


class HermeticReceiptIntegrityError(ValueError):
    """Receipt bytes cannot be bound to the independently issued child."""


class _ReceiptWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()


class HermeticReceiptObservation(_ReceiptWire):
    schema_id: Literal["chiplog.hermetic-effects.observation.v1"]
    issued_ticket_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_version: Literal["chiplog.hermetic-effects.v1"]
    tenant_id: str
    provider: str
    account: str
    recipient: str
    canonical_address_hex: str
    endpoint_head: str
    credential_binding_head: str
    intent_id: str
    intent_fingerprint: str
    transmission_id: str
    transmission_fingerprint: str
    ordinal: int = Field(ge=0)
    payload_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str
    sequence: int = Field(ge=0)
    occurred_members: tuple[str, ...]
    permanently_incapable_members: tuple[str, ...]


class _ReceiptEnvelope(_ReceiptWire):
    schema_id: Literal["chiplog.hermetic-effects.receipt.v1"]
    observation_hex: str
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")


def _ticket_fingerprint(ticket: IssuedEffectSendTicket) -> str:
    if (
        ticket.adapter_contract_version != "chiplog.hermetic-effects.v1"
        or not isinstance(ticket.payload, bytes)
        or not ticket.payload
        or not isinstance(ticket.canonical_address, bytes)
        or not ticket.canonical_address
        or hashlib.sha256(ticket.payload).hexdigest() != ticket.payload_fingerprint
        or not ticket.bundle_members
        or len(set(ticket.bundle_members)) != len(ticket.bundle_members)
        or any(not member for member in ticket.bundle_members)
    ):
        raise ValueError("issued ticket has invalid exact bytes or bundle")
    value = asdict(ticket)
    value["canonical_address"] = ticket.canonical_address.hex()
    value["payload"] = ticket.payload.hex()
    raw = json.dumps(
        {"schema_id": "chiplog.hermetic-issued-ticket.v1", "ticket": value},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def verify_hermetic_receipt(
    ticket: IssuedEffectSendTicket, raw_receipt: bytes, *, registered_receipt_key: bytes
) -> HermeticReceiptObservation:
    """Verify bytes only; root separately authenticates the key and issued lineage."""
    try:
        if not registered_receipt_key:
            raise ValueError("registered receipt key missing")
        fingerprint = _ticket_fingerprint(ticket)
        envelope = _ReceiptEnvelope.model_validate_json(raw_receipt)
        raw = bytes.fromhex(envelope.observation_hex)
        if (
            envelope.canonical_bytes() != raw_receipt
            or raw.hex() != envelope.observation_hex
            or not hmac.compare_digest(
                hmac.digest(registered_receipt_key, _RECEIPT_DOMAIN + raw, "sha256").hex(),
                envelope.signature,
            )
        ):
            raise ValueError("receipt signature or exact envelope differs")
        observation = HermeticReceiptObservation.model_validate_json(raw)
        expected = _observation(
            ticket,
            observation.sequence,
            observation.occurred_members,
            observation.permanently_incapable_members,
        )
        occurred, incapable = (
            observation.occurred_members,
            observation.permanently_incapable_members,
        )
        if (
            observation.canonical_bytes() != raw
            or observation != expected
            or observation.issued_ticket_fingerprint != fingerprint
            or len(set(occurred)) != len(occurred)
            or len(set(incapable)) != len(incapable)
            or set(occurred) & set(incapable)
            or not (set(occurred) | set(incapable)) <= set(ticket.bundle_members)
        ):
            raise ValueError("receipt child binding or outcome partition differs")
        return observation
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise HermeticReceiptIntegrityError(
            f"operation=verify_hermetic_receipt tenant={ticket.tenant_id} "
            f"record={ticket.transmission_id}"
        ) from error


def _observation(
    ticket: IssuedEffectSendTicket,
    sequence: int,
    occurred: tuple[str, ...],
    incapable: tuple[str, ...],
) -> HermeticReceiptObservation:
    return HermeticReceiptObservation(
        schema_id="chiplog.hermetic-effects.observation.v1",
        issued_ticket_fingerprint=_ticket_fingerprint(ticket),
        contract_version="chiplog.hermetic-effects.v1",
        tenant_id=ticket.tenant_id,
        provider=ticket.provider,
        account=ticket.account,
        recipient=ticket.recipient,
        canonical_address_hex=ticket.canonical_address.hex(),
        endpoint_head=ticket.endpoint_head,
        credential_binding_head=ticket.credential_binding_head,
        intent_id=ticket.intent_id,
        intent_fingerprint=ticket.intent_fingerprint,
        transmission_id=ticket.transmission_id,
        transmission_fingerprint=ticket.transmission_fingerprint,
        ordinal=ticket.request_ordinal,
        payload_fingerprint=ticket.payload_fingerprint,
        idempotency_key=ticket.idempotency_key,
        sequence=sequence,
        occurred_members=occurred,
        permanently_incapable_members=incapable,
    )


class HermeticEffectsProvider:
    contract_version = "chiplog.hermetic-effects.v1"

    def __init__(
        self,
        *,
        receipt_key: bytes,
        scenarios: tuple[
            Literal["CONFIRM", "PERMANENT_NO_EFFECT", "LOST_RESPONSE_AFTER_EFFECT", "MIXED"], ...
        ],
        maximum_payload_bytes: int = 65536,
    ) -> None:
        if not receipt_key or maximum_payload_bytes <= 0:
            raise ValueError("independent fixture receipt key and positive raw bound required")
        self._receipt_key = receipt_key
        self._scenarios = scenarios
        self._maximum_payload_bytes = maximum_payload_bytes
        self._transfers: list[HermeticTransfer] = []

    @property
    def transfers(self) -> tuple[HermeticTransfer, ...]:
        return tuple(self._transfers)

    async def emit_issued(self, ticket: IssuedEffectSendTicket) -> bytes:
        """No retries; caller must already have consumed this exact broker issue."""
        if (
            ticket.provider != "hermetic-effects"
            or ticket.account != "hermetic-account"
            or ticket.recipient != "hermetic-principal"
            or ticket.canonical_address != b"hermetic://effects/hermetic-principal"
            or ticket.adapter_contract_version != self.contract_version
            or not ticket.issued_operation_id
            or not ticket.journal_decision_id
            or not ticket.transmission_id
            or ticket.request_ordinal < 0
            or not ticket.bundle_members
            or len(set(ticket.bundle_members)) != len(ticket.bundle_members)
            or len(ticket.payload) > self._maximum_payload_bytes
            or hashlib.sha256(ticket.payload).hexdigest() != ticket.payload_fingerprint
        ):
            raise ValueError("unregistered offline tuple, malformed ticket or changed exact bytes")
        if any(row.ticket.transmission_id == ticket.transmission_id for row in self._transfers):
            raise ValueError("broker attempted to reenqueue an already observed transmission")
        sequence = len(self._transfers)
        if sequence >= len(self._scenarios):
            raise ValueError("unregistered hermetic provider scenario")
        scenario = self._scenarios[sequence]
        if scenario == "MIXED" and len(ticket.bundle_members) < 2:
            raise ValueError("mixed outcome requires an inseparable multi-member bundle")
        occurred = (
            ticket.bundle_members if scenario in {"CONFIRM", "LOST_RESPONSE_AFTER_EFFECT"} else ()
        )
        incapable = ticket.bundle_members if scenario == "PERMANENT_NO_EFFECT" else ()
        if scenario == "MIXED":
            occurred, incapable = ticket.bundle_members[:1], ticket.bundle_members[1:]
        observation = _observation(ticket, sequence, occurred, incapable)
        raw = observation.canonical_bytes()
        receipt = _ReceiptEnvelope(
            schema_id="chiplog.hermetic-effects.receipt.v1",
            observation_hex=raw.hex(),
            signature=hmac.digest(self._receipt_key, _RECEIPT_DOMAIN + raw, "sha256").hex(),
        ).canonical_bytes()
        self._transfers.append(HermeticTransfer(sequence, ticket, occurred, incapable, receipt))
        if scenario == "LOST_RESPONSE_AFTER_EFFECT":
            raise HermeticResponseLost("provider effect observed; response unavailable")
        return receipt

    def reconcile(self, transmission_id: str) -> bytes | None:
        """Provider read only: no new send; absence is not permanent no-effect proof."""
        matches = [
            row.signed_receipt
            for row in self._transfers
            if row.ticket.transmission_id == transmission_id
        ]
        if len(matches) > 1:
            raise ValueError("rival provider transmission observations")
        return matches[0] if matches else None
