"""Shared inert fan-out builders; supplied values confer no authority."""

import base64
import hashlib
import json

from chiplog.adapters.driven.execution_prompts import render_execution_prompt
from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.contracts import Frozen, ToolCall
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.execution_contracts import (
    ConsequentialToolCall,
    ExecutionContinue,
    ExecutionRunRecord,
    SelfEffectArguments,
)
from chiplog.capabilities.agent_loop.execution_fan_out_contracts import (
    ExecutionCapturedFanOutRequest,
)
from chiplog.capabilities.agent_loop.fan_out_contracts import FanOutToolPolicy, FanOutToolRegistry
from chiplog.capabilities.agent_loop.recovery_contracts import NotApplicable, Present
from tests.support.captured_fan_out import fixture as legacy_fixture


def ref(subject: str, record: Frozen) -> CallSubjectHead:
    digest = hashlib.sha256(record.canonical_bytes()).hexdigest()
    return CallSubjectHead(
        subject_id=subject, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def bind_run(
    value: ExecutionCapturedFanOutRequest, run: ExecutionRunRecord
) -> ExecutionCapturedFanOutRequest:
    run = run.model_copy(update={"head": "pending"})
    run = run.model_copy(update={"head": "loop:" + run.digest()})
    capture = CallSubjectHead(
        subject_id=run.run_id, revision=Present(head=run.head, fingerprint=run.digest())
    )
    inner = value.request
    fence = inner.cut.fence.model_copy(update={"run_id": run.run_id, "run_head": run.head})
    cut = inner.cut.model_copy(update={"current_run": capture, "fence": fence})
    calls = tuple(
        item.model_copy(
            update={"original": item.original.model_copy(update={"captured_response": capture})}
        )
        for item in inner.ordered_calls
    )
    return value.model_copy(
        update={
            "captured_run": run,
            "request": inner.model_copy(
                update={"captured_response": capture, "ordered_calls": calls, "cut": cut}
            ),
        }
    )


async def fixture(complete: bool = False) -> ExecutionCapturedFanOutRequest:
    old = await legacy_fixture()
    artifact = await render_execution_prompt("Plan")
    parsed = ExecutionContinue(
        kind="Continue",
        tool_calls=(
            ToolCall(call_id="planning", tool="propose_planning", text="Plan"),
            ToolCall(call_id="proposal", tool="propose_intent", text="Proposal"),
            ConsequentialToolCall(
                call_id="effect",
                tool="request_self_effect",
                arguments=SelfEffectArguments(payload=b"\xff\x00exact", bundle_members=("b", "a")),
            ),
        ),
    )
    response = (
        DeliveryCompletion(
            tenant="tenant",
            run_id="run",
            turn_id="turn",
            deliveries=(ProposedDelivery(payload=(Commentary(text="Done"),)),),
        )
        if complete
        else parsed
    )
    # Exact captured bytes include significant retention of noncanonical whitespace.
    raw = json.dumps(response.model_dump(mode="json"), indent=2).encode() + b"\n"
    encoded = base64.b64encode(raw).decode()
    attempt = old.captured_run.turns[-1].attempts[-1].model_dump(mode="json")
    attempt.update(live_model=None, response_base64=encoded)
    attempt["manifest"]["artifact"] = artifact.model_dump(mode="json")
    turn = old.captured_run.turns[-1].model_dump(mode="json", exclude={"sealed_calls"})
    turn.update(attempts=[attempt], response_seal=None, initialized_calls=None)
    values = old.captured_run.model_dump(
        mode="json", exclude={"deliveries", "accepted_text", "planning_receipts"}
    )
    head = {"identity": "endpoint", "head": "endpoint/head", "fingerprint": "a" * 64}
    values.update(
        schema_id="chiplog.agent-loop.execution-record.v2",
        origin={
            "kind": "ORIGIN_EXACT",
            "ingress_binding": head,
            "recipient": {
                "provider_id": "local-cli",
                "account_id": "account",
                "recipient_id": "actor",
                "endpoint": head,
                "canonical_address": "bG9jYWw=",
                "credential_binding": head,
            },
        },
        turns=[turn],
        delivery_acceptance=None,
        suspension_baseline=None,
        original_obligations=[],
        no_retry_references=[],
    )
    run = ExecutionRunRecord.model_validate_json(json.dumps(values))
    entries = tuple(
        FanOutToolPolicy(
            tool_name=tool.name,
            tool_version=tool.version,
            schema_id=tool.schema_id,
            tool_schema=ref(tool.schema_id, tool),
            tool_policy=old.tool_registry.entries[0].tool_policy,
            classification="CONSEQUENTIAL"
            if tool.name == "request_self_effect"
            else "PROPOSAL_ONLY",
            retry_policy=NotApplicable(),
        )
        for tool in sorted(
            artifact.tools, key=lambda tool: (tool.name, tool.version, tool.schema_id)
        )
    )
    registry = FanOutToolRegistry(registry_id="execution-registry", version="2", entries=entries)
    by_name = {entry.tool_name: entry for entry in entries}
    template = old.request.ordered_calls[0]
    calls = tuple(
        template.model_copy(
            update={
                "original": template.original.model_copy(
                    update={"ordinal": index, "model_call_label": call.call_id}
                ),
                "classification": by_name[call.tool].classification,
                "tool_schema": by_name[call.tool].tool_schema,
                "tool_policy": by_name[call.tool].tool_policy,
                "canonical_call_base64": base64.b64encode(call.canonical_bytes()).decode(),
            }
        )
        for index, call in enumerate(parsed.tool_calls)
    )
    request = ExecutionCapturedFanOutRequest(
        request=old.request.model_copy(
            update={
                "canonical_response_base64": encoded,
                "ordered_calls": () if complete else calls,
            }
        ),
        captured_run=run,
        tool_registry=registry,
        tool_registry_head=ref(registry.registry_id, registry),
    )
    return bind_run(request, run)
