"""Shared builders extracted without changing their behavior."""

from tests.support.capabilities.agent_loop.execution_recovery_preparation import _resume_request

from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    prepare_execution_resume,
)
from chiplog.composition import r14_execution_resume as resume
from chiplog.composition.r14_recovery_publication import RecoveryActorBudgetV1
from chiplog.platform.broker import BrokerSession, CallBudget, PublicPortCall, PublicPortSuccess
from chiplog.platform.r7_trust import TrustOwnerCall, TrustOwnerResult


def _session(owner: str, tenant: str) -> BrokerSession:
    return BrokerSession(
        tenant_id=tenant,
        broker_epoch=1,
        generation_id="test-generation",
        owner_id=owner,
        session_id=owner + "-session",
    )


def _call(owner, tenant, operation, schema, payload, *, policy=1):
    return PublicPortCall(
        operation_id=operation,
        request_id="call-" + owner,
        caller=_session("broker", tenant),
        callee=_session(owner, tenant),
        schema_id=schema,
        canonical_payload=payload,
        budget=CallBudget(
            remaining_calls=1,
            remaining_depth=1,
            policy_version=policy,
            absolute_deadline_ns=30_000_000_001,
        ),
    )


def _reply(call, schema, payload):
    return PublicPortSuccess(
        request_id=call.request_id,
        responder=call.callee,
        schema_id=schema,
        canonical_payload=payload,
    )


async def _evidence() -> resume.RetainedExecutionResume:
    request = await _resume_request()
    result = prepare_execution_resume(request)
    assert result.kind == "PREPARED_EXECUTION_RESUME_V2"
    tenant = request.cut.tenant_id
    call = _call(
        "agent_loop",
        tenant,
        resume.OWNER_OPERATION,
        "chiplog.execution.recovery-request.v1",
        request.canonical_bytes(),
    )
    reply = _reply(call, "chiplog.execution.recovery-result.v1", result.canonical_bytes())
    trust_wire = TrustOwnerCall(mode="AUTHENTICATE", snapshot_bytes=b"trust", request_bytes=b"{}")
    actor_call = _call(
        "deployment_trust",
        tenant,
        "deployment_trust.authenticate",
        "chiplog.deployment-trust.owner-call.v1",
        trust_wire.canonical_bytes(),
        policy=26,
    )
    trust_result = TrustOwnerResult(disposition="VALID", reference_bytes=b"reference", reason=None)
    actor = RecoveryActorBudgetV1(
        issued_monotonic_ns=1,
        trust_snapshot_bytes=b"trust",
        trust_journal_head="trust-head",
        request=actor_call,
        response=_reply(
            actor_call, "chiplog.deployment-trust.owner-result.v1", trust_result.canonical_bytes()
        ),
    )
    return resume.RetainedExecutionResume(
        request=request,
        result=result,
        owner_call=call,
        owner_reply=reply,
        actor_budget_v1=actor,
    )
