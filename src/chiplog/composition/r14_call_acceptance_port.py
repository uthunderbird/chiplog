"""Public application boundary for exact call adoption, never a publication grant.

Composition obtains source observations and authenticated owner results itself.
Consumers submit the exact preview bytes; no source cut, lease proof, prepared
batch or caller assertion of authority is accepted at this boundary.
"""

from typing import Literal, Protocol

from pydantic import ConfigDict, Field

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Digest, Identity, RecoveryDTO


class CallAcceptanceTarget(RecoveryDTO):
    kind: Literal["CALL_ACCEPTANCE_TARGET_V2"] = "CALL_ACCEPTANCE_TARGET_V2"
    original_call_id: Identity
    initialized: CallSubjectHead
    current_run: CallSubjectHead


class CallAcceptancePreview(RecoveryDTO):
    """Untrusted transport of a complete display and its original mandate.

    The runtime must verify the display binds these exact target/mandate bytes
    against selected initialization and independent current sources. A consumer
    can construct this DTO; construction grants neither acceptance nor SEND.
    """

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    kind: Literal["CALL_ACCEPTANCE_PREVIEW_V2"] = "CALL_ACCEPTANCE_PREVIEW_V2"
    preview_id: Identity
    target: CallAcceptanceTarget
    display_bytes: bytes = Field(min_length=1)
    mandate_bytes: bytes = Field(min_length=1)
    preview_fingerprint: Digest


class CallAcceptanceAdoption(RecoveryDTO):
    """Explicit requested act over unchanged preview bytes, not authentication.

    The stable replay identity is tenant/principal/operation/act_id. Changing the
    bytes under the same act conflicts; replay cannot refresh an expired mandate.
    Malformed submitted bytes remain lossless for typed runtime rejection.
    """

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    kind: Literal["CALL_ACCEPTANCE_ADOPTION_V2"] = "CALL_ACCEPTANCE_ADOPTION_V2"
    act_id: Identity
    preview_bytes: bytes = Field(min_length=1)


class AcceptedCallReceipt(RecoveryDTO):
    """References to the selected complete batch, not provider-success evidence.

    Exact replay returns the original receipt even when the current Run changes.
    The effect reference identifies the immutable intent, not its physical record.
    """

    kind: Literal["ACCEPTED_CALL_RECEIPT_V2"] = "ACCEPTED_CALL_RECEIPT_V2"
    original_call_id: Identity
    initialized: CallSubjectHead
    accepted: CallSubjectHead
    execution_intent: CallSubjectHead
    external_intent: CallSubjectHead
    publication_id: Identity
    publication_fingerprint: Digest


class CallAcceptancePort(Protocol):
    async def preview_call_acceptance(
        self, peer: str, target: CallAcceptanceTarget
    ) -> CallAcceptancePreview:
        """Capture an exact display after initialization; grant no permission."""
        ...

    async def accept_call(self, peer: str, adoption: CallAcceptanceAdoption) -> AcceptedCallReceipt:
        """Authenticate the actual invocation and atomically select all companions.

        LoopRejected represents denied/stale/conflicting/unsupported or malformed
        input. An uncertain durable selection remains a recoverable failure, never
        an assertion that no acceptance occurred. No provider is invoked here.
        """
        ...
