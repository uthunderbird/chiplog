"""Explicit R15 execution-mandate adoption and issuer-owned clock preview wires."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from chiplog.capabilities.agent_loop.recovery_contracts import (
    Identity,
    RecoveryDTO,
    TrustedClockProofRef,
    UInt64,
)
from chiplog.composition.r15_tick_contracts import TickClockReading, TickClockSource


class MandateDTO(RecoveryDTO):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, ser_json_bytes="base64", val_json_bytes="base64"
    )


class MandateClockPreview(MandateDTO):
    kind: Literal["R15_MANDATE_CLOCK_PREVIEW_V1"] = "R15_MANDATE_CLOCK_PREVIEW_V1"
    adoption_act_id: Identity
    canonical_scope_bytes: bytes = Field(min_length=1)
    proof: TrustedClockProofRef
    source: TickClockSource
    reading: TickClockReading
    valid_until_monotonic_ns: UInt64


class MandateIssuanceAdoption(MandateDTO):
    """The administrator adopts exact canonical scope and policy bytes."""

    adoption_act_id: Identity
    canonical_scope_bytes: bytes = Field(min_length=1)
    canonical_policy_bytes: bytes = Field(min_length=1)
