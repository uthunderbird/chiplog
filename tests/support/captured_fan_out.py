"""Shared inert fan-out builders; supplied values confer no authority."""

import base64
import hashlib

import chiplog.capabilities.agent_loop.call_acceptance_contracts as call
import chiplog.capabilities.agent_loop.fan_out_contracts as fan
from chiplog.adapters.driven.loop_prompts import render_delivery_prompt, render_prompt
from chiplog.capabilities.agent_loop import contracts as loop
from chiplog.capabilities.agent_loop.delivery_preparation import (
    Commentary,
    DeliveryCompletion,
    ProposedDelivery,
)
from chiplog.capabilities.agent_loop.recovery_contracts import NotApplicable, Present
from tests.support.fan_out_shapes import shape_request


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def ref(subject: str, raw: bytes) -> call.CallSubjectHead:
    digest = sha(raw)
    return call.CallSubjectHead(
        subject_id=subject, revision=Present(head="record:" + digest, fingerprint=digest)
    )


def bind_run(value: fan.CapturedFanOutRequest, run: loop.RunRecord) -> fan.CapturedFanOutRequest:
    run = run.model_copy(update={"head": "pending"})
    run = run.model_copy(update={"head": "loop:" + sha(run.canonical_bytes())})
    capture = call.CallSubjectHead(
        subject_id=run.run_id,
        revision=Present(head=run.head, fingerprint=sha(run.canonical_bytes())),
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
                update={
                    "captured_response": capture,
                    "ordered_calls": calls,
                    "cut": cut,
                }
            ),
        }
    )


def bind_registry(
    value: fan.CapturedFanOutRequest,
    entries: tuple[fan.FanOutToolPolicy, ...],
) -> fan.CapturedFanOutRequest:
    registry = value.tool_registry.model_copy(update={"entries": entries})
    return value.model_copy(
        update={
            "tool_registry": registry,
            "tool_registry_head": ref(registry.registry_id, registry.canonical_bytes()),
        }
    )


def with_raw(value: fan.CapturedFanOutRequest, raw: bytes) -> fan.CapturedFanOutRequest:
    encoded = base64.b64encode(raw).decode()
    turn = value.captured_run.turns[-1]
    attempt = turn.attempts[-1].model_copy(update={"response_base64": encoded})
    run = value.captured_run.model_copy(
        update={
            "turns": (
                turn.model_copy(
                    update={
                        "attempts": (attempt,),
                    }
                ),
            )
        }
    )
    value = value.model_copy(
        update={
            "request": value.request.model_copy(
                update={
                    "canonical_response_base64": encoded,
                }
            )
        }
    )
    return bind_run(value, run)


async def fixture(delivery: bool = False, complete: bool = False) -> fan.CapturedFanOutRequest:
    value = shape_request()
    artifact = await (render_delivery_prompt("Plan") if delivery else render_prompt("Plan"))
    entries = tuple(
        fan.FanOutToolPolicy(
            tool_name=tool.name,
            tool_version=tool.version,
            schema_id=tool.schema_id,
            tool_schema=ref(tool.schema_id, tool.canonical_bytes()),
            tool_policy=ref("policy:" + tool.name, tool.name.encode()),
            classification="PROPOSAL_ONLY",
            retry_policy=NotApplicable(),
        )
        for tool in sorted(
            artifact.tools, key=lambda tool: (tool.name, tool.version, tool.schema_id)
        )
    )
    value = bind_registry(value, entries)
    parsed = loop.Continue(
        kind="Continue",
        tool_calls=tuple(
            loop.ToolCall(call_id=f"model:{i}", tool=tool.name, text=f"Plan {i}")
            for i, tool in enumerate(artifact.tools)
        ),
    )
    if complete:
        if delivery:
            raw = DeliveryCompletion(
                tenant="tenant",
                run_id="run",
                turn_id="turn",
                deliveries=(ProposedDelivery(payload=(Commentary(text="Done"),)),),
            ).canonical_bytes()
        else:
            raw = loop.Complete(
                kind="Complete",
                deliveries=(loop.Delivery(kind="NonAuthoritativeText", text="Done"),),
            ).canonical_bytes()
    else:
        raw = parsed.canonical_bytes()
    by_name = {entry.tool_name: entry for entry in entries}
    template = value.request.ordered_calls[0]
    calls = (
        tuple(
            template.model_copy(
                update={
                    "original": template.original.model_copy(
                        update={"ordinal": i, "model_call_label": item.call_id}
                    ),
                    "tool_schema": by_name[item.tool].tool_schema,
                    "tool_policy": by_name[item.tool].tool_policy,
                    "canonical_call_base64": base64.b64encode(item.canonical_bytes()).decode(),
                }
            )
            for i, item in enumerate(parsed.tool_calls)
        )
        if not complete
        else ()
    )
    inventory = value.request.cut.predecessor_inventory
    cut = value.request.cut.model_copy(
        update={
            "complete_call_inventory": ref("call-inventory:tenant", inventory.canonical_bytes()),
        }
    )
    value = value.model_copy(
        update={
            "request": value.request.model_copy(
                update={
                    "ordered_calls": calls,
                    "cut": cut,
                }
            )
        }
    )
    turn = value.captured_run.turns[-1]
    attempt = turn.attempts[-1]
    manifest = attempt.manifest.model_copy(update={"artifact": artifact})
    attempt = attempt.model_copy(update={"manifest": manifest})
    value = value.model_copy(
        update={
            "captured_run": value.captured_run.model_copy(
                update={
                    "turns": (turn.model_copy(update={"attempts": (attempt,)}),),
                }
            )
        }
    )
    return with_raw(value, raw)
