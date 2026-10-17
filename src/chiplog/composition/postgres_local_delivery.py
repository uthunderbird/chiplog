"""Bind native accepted delivery to the current OS user's durable CLI inbox."""

import hashlib
from dataclasses import dataclass, field

from chiplog.adapters.driven.delivery_local_cli import LocalCliDeliveryProvider
from chiplog.adapters.driven.effects_hermetic import IssuedEffectSendTicket
from chiplog.capabilities.effects.contracts import ExactHead, ProviderRecipient
from chiplog.composition.postgres_delivery_contracts import DeliveryReceiptObservation


@dataclass(frozen=True)
class PostgresLocalDeliveryBinding:
    provider: LocalCliDeliveryProvider
    receipt_key: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if hashlib.sha256(self.receipt_key).hexdigest() != self.provider.credential_fingerprint:
            raise ValueError("local delivery receipt key differs from registered provider")

    @property
    def adapter_contract_version(self) -> str:
        return self.provider.contract_version

    def target_for_principal(self, principal_id: str) -> ProviderRecipient:
        if principal_id != self.provider.recipient_id:
            raise PermissionError("local inbox principal differs from authenticated caller")
        return ProviderRecipient(
            provider=self.provider.provider_id,
            account=self.provider.account_id,
            recipient=principal_id,
            canonical_address=self.provider.canonical_address,
            endpoint=ExactHead(
                subject_id="local-cli-endpoint",
                head=self.provider.endpoint_head,
                fingerprint=self.provider.endpoint_fingerprint,
            ),
            credential_binding=ExactHead(
                subject_id="local-cli-receipt-key",
                head=self.provider.credential_binding_head,
                fingerprint=self.provider.credential_fingerprint,
            ),
        )

    def verify_receipt(
        self, ticket: IssuedEffectSendTicket, raw: bytes
    ) -> DeliveryReceiptObservation:
        observed = self.provider.verify_receipt(ticket, raw)
        return DeliveryReceiptObservation(
            observed.occurred_members, observed.permanently_incapable_members
        )
