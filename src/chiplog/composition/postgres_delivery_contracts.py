"""The two mounted native delivery leaves share this narrow composition seam."""

from dataclasses import dataclass
from typing import Protocol

from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
from chiplog.capabilities.effects.contracts import ProviderRecipient


@dataclass(frozen=True)
class DeliveryReceiptObservation:
    occurred_members: tuple[str, ...]
    permanently_incapable_members: tuple[str, ...]


class DeliveryTransport(Protocol):
    async def emit_issued(self, ticket: IssuedEffectSendTicket) -> bytes: ...

    def reconcile(self, transmission_id: str) -> bytes | None: ...


class NativeDeliveryBinding(Protocol):
    @property
    def provider(self) -> DeliveryTransport: ...

    @property
    def receipt_key(self) -> bytes: ...

    @property
    def adapter_contract_version(self) -> str: ...

    def target_for_principal(self, principal_id: str) -> ProviderRecipient: ...

    def verify_receipt(
        self, ticket: IssuedEffectSendTicket, raw: bytes
    ) -> DeliveryReceiptObservation: ...
