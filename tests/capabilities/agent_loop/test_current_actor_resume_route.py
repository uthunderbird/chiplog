"""Version routing controls; pure requests do not authenticate a live caller."""

import pytest
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _resume_request

from chiplog.capabilities.agent_loop import _execution_recovery_process as process
from chiplog.capabilities.agent_loop.execution_recovery_preparation import prepare_execution_resume

OPERATION = "agent_loop.prepare_execution_resume_current_actor.v2"
DISPOSITION = "r14-same-run-current-actor.v2"


def test_current_actor_operation_is_an_exact_owner_route() -> None:
    assert (OPERATION, "broker", "agent_loop", process.REQUEST_SCHEMA, process.RESULT_SCHEMA) in (
        process.ROUTES
    )


@pytest.mark.parametrize(
    "operation", [process.RESUME_OPERATION, process.RESUME_OBLIGATION_OPERATION],
)
async def test_old_resume_routes_reject_new_disposition_before_preparation(
    monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    request = (await _resume_request()).model_copy(update={"disposition_version": DISPOSITION})

    def forbidden(_: object) -> None:
        pytest.fail("historical route invoked new-version preparation")

    monkeypatch.setattr(process, "prepare_execution_resume", forbidden)
    reply = process.dispatch(operation, request.canonical_bytes())
    assert reply["failure"] == "PROTOCOL_REJECTED"


@pytest.mark.parametrize("version", ["r14-same-run-equality.v1", "unknown"])
async def test_current_actor_route_rejects_foreign_disposition_before_preparation(
    monkeypatch: pytest.MonkeyPatch, version: str,
) -> None:
    request = (await _resume_request()).model_copy(update={"disposition_version": version})

    def forbidden(_: object) -> None:
        pytest.fail("current-actor route invoked foreign-version preparation")

    monkeypatch.setattr(process, "prepare_execution_resume", forbidden)
    reply = process.dispatch(OPERATION, request.canonical_bytes())
    assert reply["failure"] == "PROTOCOL_REJECTED"


async def test_current_actor_preparation_requires_closed_v5_original_obligation() -> None:
    request = await _resume_request()
    changed = request.model_copy(update={
        "disposition_version": DISPOSITION,
        "fence": request.fence.model_copy(update={
            "worker_session_id": "4:fresh-generation:fresh-owner-session",
            "runtime_generation": "fresh-generation",
        }),
    })
    result = prepare_execution_resume(changed)
    assert result.kind == "CALL_PREPARATION_REJECTED_V1"
    assert result.code == "HOLD"
    assert result.reason == "current-actor resume requires one V5 profile and frozen obligation"
