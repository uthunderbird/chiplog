"""Retained v2 acceptance transport; values grant no publication or SEND authority.

The full effects preparation retains its original current observations. Historical
verification must not regenerate them. Runtime must authenticate both owner
exchanges independently and verify the exact selected initialization.
"""

from typing import Literal

from pydantic import ConfigDict, Field

from chiplog.capabilities.agent_loop import call_acceptance_contracts as call
from chiplog.capabilities.agent_loop.recovery_contracts import RecoveryDTO
from chiplog.capabilities.effects.dispatch_v2 import DispatchPreparationV2, DispatchRecordV2

from .r14_acceptance_contracts import AcceptancePhysicalMember


class RetainedAcceptancePreparationV2(RecoveryDTO):
    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    kind: Literal["RETAINED_CALL_EFFECT_ACCEPTANCE_V2"] = "RETAINED_CALL_EFFECT_ACCEPTANCE_V2"
    loop_request: call.AcceptConsequentialCallRequest
    loop_proposal: call.PreparedConsequentialAcceptance
    effects_request: DispatchPreparationV2
    effects_proposal: DispatchRecordV2


class AcceptancePhysicalEnvelopeV2(RecoveryDTO):
    """Exactly accepted, execution and effects record; semantic checks are separate."""

    kind: Literal["CALL_EFFECT_ACCEPTANCE_ENVELOPE_V2"] = "CALL_EFFECT_ACCEPTANCE_ENVELOPE_V2"
    tenant_id: str = Field(min_length=1)
    original_call_id: str = Field(min_length=1)
    expected_tenant_head: int = Field(ge=0)
    retained_preparation_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    complete_records: tuple[AcceptancePhysicalMember, ...] = Field(min_length=3, max_length=3)
    physical_batch_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
