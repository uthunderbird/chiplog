"""Fresh readiness orchestration counts; synthetic ports grant no authority."""

from collections import Counter
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop import execution_recovery_preparation as preparation
from chiplog.composition import r14_recovery_publication as publication
from tests.composition.test_r14_recovery_pending_prefix import _fixture
from tests.composition.test_r14_resume_artifacts import _evidence as resume_evidence


async def _evidence(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    _, entry, _, _, _ = await _fixture(tmp_path, False)
    evidence = publication.retained(entry)
    receipt = (await resume_evidence()).actor_budget_v1
    return evidence, evidence.model_copy(update={"actor_budget_v1": receipt})


async def test_readiness_binding_reuses_derivation_but_public_reconstruction_revalidates(
    tmp_path, monkeypatch
):
    evidence, issued = await _evidence(tmp_path)
    calls = []
    accounting = preparation.prepare_execution_accounting
    continuation = preparation.prepare_execution_continuation

    def count_accounting(request):
        calls.append("accounting")
        return accounting(request)

    def count_continuation(request):
        calls.append("continuation")
        return continuation(request)

    monkeypatch.setattr(preparation, "prepare_execution_accounting", count_accounting)
    monkeypatch.setattr(preparation, "prepare_execution_continuation", count_continuation)
    artifacts = publication._prepare_readiness_artifacts(evidence)
    assert calls == ["accounting", "continuation"]
    bound = publication._bind_readiness_artifacts(issued, artifacts)
    assert calls == ["accounting", "continuation"]
    assert bound == publication.command(issued)
    assert calls == ["accounting", "continuation", "accounting", "continuation"]


@pytest.mark.parametrize(
    "damage",
    (
        "request",
        "result",
        "call",
        "reply",
        "missing-receipt",
        "receipt",
        "foreign-tenant",
        "whole-exchange",
    ),
)
async def test_readiness_binding_rejects_changed_validated_artifacts(tmp_path, damage: str) -> None:
    evidence, issued = await _evidence(tmp_path / "original")
    artifacts = publication._prepare_readiness_artifacts(evidence)
    if damage == "request":
        changed = issued.model_copy(
            update={
                "accounting_request": issued.accounting_request.model_copy(
                    update={"command_id": "foreign"}
                )
            }
        )
    elif damage == "result":
        changed = issued.model_copy(
            update={
                "accounting_result": issued.accounting_result.model_copy(
                    update={"source_request_fingerprint": "0" * 64}
                )
            }
        )
    elif damage == "call":
        changed = issued.model_copy(
            update={
                "accounting_call": issued.accounting_call.model_copy(update={"request_id": "rival"})
            }
        )
    elif damage == "reply":
        changed = issued.model_copy(
            update={
                "accounting_reply": issued.accounting_reply.model_copy(
                    update={"request_id": "rival"}
                )
            }
        )
    elif damage == "missing-receipt":
        changed = evidence
    elif damage == "receipt":
        changed = issued.model_copy(
            update={
                "actor_budget_v1": issued.actor_budget_v1.model_copy(
                    update={"issued_monotonic_ns": issued.actor_budget_v1.issued_monotonic_ns + 1}
                )
            }
        )
    elif damage == "foreign-tenant":
        receipt = issued.actor_budget_v1
        callee = receipt.request.callee.model_copy(update={"tenant_id": "foreign"})
        request = receipt.request.model_copy(
            update={
                "caller": receipt.request.caller.model_copy(update={"tenant_id": "foreign"}),
                "callee": callee,
            }
        )
        foreign_receipt = receipt.model_copy(
            update={
                "request": request,
                "response": receipt.response.model_copy(update={"responder": callee}),
            }
        )
        publication.validate_recovery_actor_budget(foreign_receipt)
        changed = issued.model_copy(update={"actor_budget_v1": foreign_receipt})
    else:
        # A separately valid complete exchange remains foreign to this artifact.
        _, changed = await _evidence(tmp_path / "other")
        assert publication.command(changed)
    with pytest.raises(ValueError, match=r"differs|requires"):
        publication._bind_readiness_artifacts(changed, artifacts)


async def test_fresh_readiness_does_not_reprepare_after_actor_issuance(tmp_path, monkeypatch):
    # Build all fixture evidence outside the counter window. This fixture does
    # not authenticate a native source or exercise a registered writer.
    runtime, entry, _, _, _ = await _fixture(tmp_path, False)
    evidence = publication.retained(entry)
    actor_receipt = (await resume_evidence()).actor_budget_v1
    publication.validate_recovery_actor_budget(actor_receipt)
    cut = evidence.accounting_request.cut
    assert actor_receipt.request.callee.tenant_id == cut.tenant_id
    counts = Counter()
    phase = "before_actor"
    original_accounting = preparation.prepare_execution_accounting
    original_continuation = preparation.prepare_execution_continuation

    def counted_accounting(request):
        counts[phase, "accounting"] += 1
        return original_accounting(request)

    def counted_continuation(request):
        counts[phase, "continuation"] += 1
        return original_continuation(request)

    monkeypatch.setattr(preparation, "prepare_execution_accounting", counted_accounting)
    monkeypatch.setattr(preparation, "prepare_execution_continuation", counted_continuation)

    async def exchange(runtime, request, operation, result_type):
        if operation == "agent_loop.prepare_execution_accounting.v1":
            result = preparation.prepare_execution_accounting(request)
            sent, reply = evidence.accounting_call, evidence.accounting_reply
        else:
            assert operation == "agent_loop.prepare_execution_continuation.v1"
            result = preparation.prepare_execution_continuation(request)
            sent, reply = evidence.continuation_call, evidence.continuation_reply
        assert isinstance(result, result_type)
        sent = sent.model_copy(update={"canonical_payload": request.canonical_bytes()})
        reply = reply.model_copy(update={"canonical_payload": result.canonical_bytes()})
        return result, sent, reply

    actor = object()

    async def actor_read(peer):
        return actor

    async def issue(runtime, peer):
        nonlocal phase
        assert phase == "before_actor"
        phase = "after_actor"
        return actor, actor_receipt

    class UnauthenticatedSyntheticSource:
        def collect_held(self, runtime):
            return cut

        def authenticate_selected_held(self, *args):
            return False

    class BeforeWrite(Exception):
        pass

    async def submit(batch) -> None:
        assert phase == "after_actor"
        assert batch.records[0].canonical_bytes == (
            evidence.accounting_result.complete_accounting[0].canonical_bytes()
        )
        assert batch.records[1].canonical_bytes == (
            evidence.continuation_result.complete_continuations[0].canonical_bytes()
        )
        raise BeforeWrite

    runtime._full_recovery_cut_source = UnauthenticatedSyntheticSource()
    runtime._execution_actor = actor_read
    runtime._check_execution_actor = lambda observed: None
    runtime._same_execution_actor_identity = lambda initial, refreshed: initial is refreshed
    runtime._pending = lambda: ()
    runtime._loop_decisions = lambda: SimpleNamespace(entries=lambda: ())
    runtime.current_worker = lambda: "synthetic-worker"
    runtime._supervisor = SimpleNamespace(
        runtime=lambda: SimpleNamespace(session=lambda owner: evidence.accounting_call.callee)
    )
    runtime._appender = SimpleNamespace(submit=submit)
    monkeypatch.setattr(publication, "_exchange", exchange)
    monkeypatch.setattr(publication, "issue_execution_recovery_actor_v1", issue)

    with pytest.raises(BeforeWrite):
        await publication.publish_execution_accounting_readiness(runtime)

    assert {name: counts["after_actor", name] for name in ("accounting", "continuation")} == {
        "accounting": 0,
        "continuation": 0,
    }
    # One real exchange and one independent local validation for each owner result.
    assert {name: counts["before_actor", name] for name in ("accounting", "continuation")} == {
        "accounting": 2,
        "continuation": 2,
    }
