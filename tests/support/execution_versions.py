"""Versioned executable-Run fixtures shared by contract consumers."""

import base64

from chiplog.capabilities.agent_loop.contracts import (
    BudgetPolicy,
    DisclosureLabel,
    VisibilityMember,
)
from chiplog.capabilities.agent_loop.delivery_contracts import (
    ExactHead,
    OriginSelection,
    ProviderRecipient,
)
from chiplog.capabilities.agent_loop.execution_history_contracts import (
    EXECUTION_TOOLS_V3,
    ExecutionContinueV3,
    ExecutionModelAttemptV3,
    ExecutionPromptArtifactV3,
    ExecutionRunRecordV3,
    ExecutionTurnV3,
    ExecutionVisibilityManifestV3,
    execution_response_schema_v3,
)
from chiplog.capabilities.agent_loop.readonly_history_tool_contracts import (
    ConversationHistoryQuery,
    ReadOnlyHistoryToolCall,
)


def exact(identity: str) -> ExactHead:
    return ExactHead(identity=identity, head="head:" + identity, fingerprint="a" * 64)


def execution_run_v3() -> ExecutionRunRecordV3:
    """A nonempty v3 Run whose captured response invokes the history tool."""

    response = ExecutionContinueV3(
        kind="Continue",
        tool_calls=(
            ReadOnlyHistoryToolCall(
                call_id="history-call",
                tool="read_conversation_history",
                arguments=ConversationHistoryQuery(limit=1, after_cursor=None),
            ),
        ),
    )
    artifact = ExecutionPromptArtifactV3(
        content_hash="a" * 64,
        library_version="version-fixture",
        tools=EXECUTION_TOOLS_V3,
        response_schema_json=execution_response_schema_v3(),
        rendered="history-aware prompt",
    )
    label = DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=())
    members = (
        VisibilityMember(
            record_id="prompt",
            revision_head="revision:prompt",
            content=artifact.rendered,
            provenance_head="provenance:prompt",
            label_head="label:prompt",
            label=label,
            producer="fixture",
            surface="prompt",
        ),
    )
    manifest = ExecutionVisibilityManifestV3(
        tenant="tenant",
        principal="principal",
        contour_head="contour",
        run_id="run-v3",
        turn_id="turn-v3",
        generation=0,
        worker_session="worker",
        members=members,
        joined_label=label,
        artifact=artifact,
    )
    attempt = ExecutionModelAttemptV3(
        attempt_id="attempt-v3",
        lineage_id="lineage-v3",
        generation=0,
        state="RESPONSE_CAPTURED",
        head="attempt-head",
        manifest=manifest,
        request="request",
        provider_contract="hermetic-model.v1",
        recipient="hermetic-model",
        live_model=None,
        worker_session="worker",
        response_base64=base64.b64encode(response.canonical_bytes()).decode(),
        receipt="receipt",
        rejection=None,
    )
    turn = ExecutionTurnV3(
        turn_id="turn-v3",
        ordinal=1,
        head="turn-head",
        state="RESPONSE_AVAILABLE",
        accumulator=members,
        attempts=(attempt,),
        selector=0,
        response_seal=None,
        initialized_calls=None,
    )
    run = ExecutionRunRecordV3(
        tenant="tenant",
        principal="principal",
        run_id="run-v3",
        state="ACTIVE",
        head="pending",
        predecessor=None,
        prompt="history-aware prompt",
        policy=BudgetPolicy(),
        origin=OriginSelection(
            ingress_binding=exact("ingress"),
            recipient=ProviderRecipient(
                provider_id="hermetic-local",
                account_id="account",
                recipient_id="principal",
                endpoint=exact("endpoint"),
                canonical_address=b"local://principal",
                credential_binding=exact("credential"),
            ),
        ),
        contour_head="contour",
        policy_head="policy",
        worker_session="worker",
        root_binding="NOT_APPLICABLE",
        turns=(turn,),
        delivery_acceptance=None,
        suspension_baseline=None,
        original_obligations=(),
        no_retry_references=(),
        event="ModelResponseCaptured",
    )
    return run.model_copy(update={"head": "loop:" + run.digest()})
