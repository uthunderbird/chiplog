"""Strict broker-owned references shared by diagnostic declarations.

This leaf intentionally has no dependency on owner publication contracts: broker
diagnostic retained sources are not owner records and never extend ``Owner``.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identity = Annotated[str, Field(min_length=1)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class BrokerDiagnosticSourceRefV1(BaseModel):
    """Reference to immutable broker diagnostic content, never an owner record head."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    source_owner: Literal["broker"] = "broker"
    source_kind: Literal[
        "FAULT_CAPTURE", "FAULT_OBSERVATION", "FAULT_OBSERVER_REGISTRY", "FAULT_ISSUANCE"
    ]
    schema_id: Identity
    source_id: Identity
    fingerprint: Digest
