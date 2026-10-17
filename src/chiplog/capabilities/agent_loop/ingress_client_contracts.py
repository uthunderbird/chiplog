"""Native channel contracts; constructing these values grants no authority.

The runtime verifies evidence from a registered transport issuer against the full
identity, exact raw bytes and current source binding. Neither message fields nor
an arbitrary ``peer`` string authenticate a caller. Adapters supply ``peer`` from
their trusted transport context; the runtime independently authenticates it.
"""

from typing import Literal, Protocol

from pydantic import Field

from .contracts import BudgetPolicy
from .delivery_contracts import DeliveryDTO, ExactHead, Identity, OriginSelection
from .execution_client_transitions import ExecutionRun


class NativeIngressIdentityV1(DeliveryDTO):
    """Replay namespace includes the independently registered source."""

    tenant_id: Identity
    channel: Literal["CLI", "TELEGRAM"]
    registered_source_id: Identity
    replay_id: Identity


class NativeTransportWitnessV1(DeliveryDTO):
    """Inert issuer evidence, never private credentials or body-derived authority.

    Issuer verification must bind identity, exact raw-byte digest and this source
    binding; copied evidence for another submission must fail authentication.
    The registered issuer defines the evidence format; DTO validity proves none.
    """

    issuer_id: Identity
    source_binding: ExactHead
    evidence: bytes = Field(min_length=1)


class NativeIngressSubmissionV1(DeliveryDTO):
    schema_id: Literal["chiplog.agent-loop.native-ingress-submission.v1"] = (
        "chiplog.agent-loop.native-ingress-submission.v1"
    )
    identity: NativeIngressIdentityV1
    raw_bytes: bytes
    witness: NativeTransportWitnessV1


class NativeIngressReceiptV1(DeliveryDTO):
    """Description of one selected atomic inbox/Run admission, not ACK authority.

    Before ACK, cursor advance or source release, the runtime must resolve the
    receipt against actual selected custody. All fields belong to that same
    commit. Origin is authenticated by the runtime, never taken from the body.
    """

    schema_id: Literal["chiplog.agent-loop.native-ingress-receipt.v1"] = (
        "chiplog.agent-loop.native-ingress-receipt.v1"
    )
    identity: NativeIngressIdentityV1
    inbox_id: Identity
    run_id: Identity
    raw_bytes: bytes
    origin: OriginSelection
    selected_record: ExactHead
    commit_sequence: int = Field(gt=0)


class NativeIngressPort(Protocol):
    """Authenticate before disclosure; return only selected durable admission.

    Exact replay returns the original inbox/Run; changed bytes under the same
    identity reject. Unknown/forged issuers reject without admission or ACK.
    Uncertain publication is resolved by lookup, not a fresh command identity.
    """

    async def submit(
        self, peer: str, submission: NativeIngressSubmissionV1, policy: BudgetPolicy
    ) -> NativeIngressReceiptV1: ...

    def lookup(self, peer: str, identity: NativeIngressIdentityV1) -> NativeIngressReceiptV1 | None:
        """Reject unauthorized scope; None means authorized but not admitted."""
        ...


class NativeApplicationDriver(Protocol):
    """One native loop for both channels, consuming authorized selected inboxes.

    Reopen uses the existing Run, completion and effects delivery lifecycle.
    Accepted completion/delivery recovery must not call the model again.
    """

    async def drive(self, peer: str, inbox_id: str) -> ExecutionRun: ...
