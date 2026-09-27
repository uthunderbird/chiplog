"""Closed retained binding for an H1 delivery decision.

The binding records evidence identity only.  It is deliberately not a live
writer capability.
"""

from typing import Literal

from chiplog.platform._owner_publication_contracts import BrokerDTO, Digest, Identity


class H1DeliveryBinding(BrokerDTO):
    schema_id: Literal["chiplog.execution.h1-selected-delivery-binding.v1"] = (
        "chiplog.execution.h1-selected-delivery-binding.v1"
    )
    deployment_id: Identity
    database_id: Identity
    database_genesis_digest: Digest
    tenant_id: Identity
    journal_role: Literal["h1-delivery-evidence"] = "h1-delivery-evidence"
    journal_instance_id: Identity
    closure_entry_id: Digest
    closure_payload_digest: Digest
    closure_schema_id: Literal["chiplog.execution.h1-delivery-selection-closure.v1"] = (
        "chiplog.execution.h1-delivery-selection-closure.v1"
    )
    command_id: Identity
    command_fingerprint: Digest
    request_digest: Digest
