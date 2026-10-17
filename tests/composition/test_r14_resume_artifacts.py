"""Exact immutable owner-wire artifacts; these fixtures do not authenticate a caller."""

import pytest

from chiplog.capabilities.agent_loop.execution_recovery_preparation import (
    CURRENT_ACTOR_RESUME_DISPOSITION,
)
from chiplog.composition import r14_execution_resume as resume
from tests.support.composition.r14_resume_artifacts import (
    _evidence,
)


async def test_resume_artifacts_bind_exact_bytes_without_repreparation(monkeypatch):
    evidence = await _evidence()
    events = []
    prepare = resume.prepare_execution_resume
    classify = resume.classify_execution_resume

    def counted_prepare(request):
        events.append("prepare")
        return prepare(request)

    def counted_classify(request, result):
        events.append("classify")
        return classify(request, result)

    monkeypatch.setattr(resume, "prepare_execution_resume", counted_prepare)
    monkeypatch.setattr(resume, "classify_execution_resume", counted_classify)
    artifacts = resume._prepare_resume_artifacts(
        evidence.request,
        evidence.result,
        evidence.owner_call,
        evidence.owner_reply,
    )
    assert events == ["prepare", "classify"]
    command = resume._bind_resume_artifacts(evidence, artifacts)
    assert events == ["prepare", "classify"]
    assert command.records[0].canonical_bytes == evidence.result.run.canonical_bytes()
    assert command.expected_preimage_commitment == evidence.request.cut.materialization_commitment
    assert resume._command(evidence) == command
    assert events == ["prepare", "classify", "prepare", "classify"]


@pytest.mark.parametrize("damage", ["request", "result", "call", "reply", "budget"])
async def test_resume_artifacts_reject_one_sided_evidence_change(damage: str) -> None:
    evidence = await _evidence()
    artifacts = resume._prepare_resume_artifacts(
        evidence.request,
        evidence.result,
        evidence.owner_call,
        evidence.owner_reply,
    )
    changes = {
        "request": (
            "request",
            evidence.request.model_copy(
                update={
                    "activation_payload": b"rival",
                }
            ),
        ),
        "result": (
            "result",
            evidence.result.model_copy(
                update={
                    "source_request_fingerprint": "f" * 64,
                }
            ),
        ),
        "call": ("owner_call", evidence.owner_call.model_copy(update={"request_id": "rival"})),
        "reply": ("owner_reply", evidence.owner_reply.model_copy(update={"request_id": "rival"})),
        "budget": (
            "actor_budget_v1",
            evidence.actor_budget_v1.model_copy(
                update={
                    "issued_monotonic_ns": 2,
                }
            ),
        ),
    }
    field, value = changes[damage]
    with pytest.raises(ValueError, match=r"differs|differ"):
        resume._bind_resume_artifacts(evidence.model_copy(update={field: value}), artifacts)


@pytest.mark.parametrize("damage", ["worker", "generation"])
async def test_current_actor_resume_rejects_fence_not_bound_to_owner_exchange(damage: str) -> None:
    evidence = await _evidence()
    callee = evidence.owner_call.callee
    fence = evidence.request.fence.model_copy(
        update={
            "worker_session_id": (
                f"{callee.broker_epoch}:{callee.generation_id}:{callee.session_id}"
            ),
            "runtime_generation": callee.generation_id,
        }
    )
    fence = fence.model_copy(
        update={
            "worker_session_id" if damage == "worker" else "runtime_generation": "foreign",
        }
    )
    request = evidence.request.model_copy(
        update={
            "disposition_version": CURRENT_ACTOR_RESUME_DISPOSITION,
            "fence": fence,
        }
    )
    call = evidence.owner_call.model_copy(
        update={
            "operation_id": resume.OWNER_CURRENT_ACTOR_OPERATION,
            "canonical_payload": request.canonical_bytes(),
        }
    )
    with pytest.raises(ValueError, match="fence differs from owner exchange"):
        resume._prepare_resume_artifacts(request, evidence.result, call, evidence.owner_reply)


@pytest.mark.parametrize("new_operation", [False, True])
async def test_retained_resume_rejects_mixed_operation_and_disposition(new_operation: bool) -> None:
    evidence = await _evidence()
    request = evidence.request
    call = evidence.owner_call
    if new_operation:
        call = call.model_copy(update={"operation_id": resume.OWNER_CURRENT_ACTOR_OPERATION})
    else:
        request = request.model_copy(
            update={
                "disposition_version": CURRENT_ACTOR_RESUME_DISPOSITION,
            }
        )
    with pytest.raises(ValueError, match="operation and disposition version differ"):
        resume._prepare_resume_artifacts(request, evidence.result, call, evidence.owner_reply)
