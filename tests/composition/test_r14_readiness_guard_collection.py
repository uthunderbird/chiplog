"""Custom-source baseline: real SQLite publication, no native cut authority."""

import json
from collections import Counter
from dataclasses import replace
from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop import execution_recovery_preparation as preparation
from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition import r14_recovery_publication as publication
from chiplog.platform._sqlite import EventAppender, FenceAdvanceCommand, SQLiteMaterializer
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import workspace_snapshot
from tests.composition.test_r14_recovery_pending_prefix import _fixture
from tests.composition.test_r14_resume_artifacts import _evidence as resume_evidence


@pytest.mark.parametrize("synthetic_repeat_expires_actor", (False, True))
async def test_custom_source_keeps_final_collection_at_real_writer(
    tmp_path, monkeypatch, synthetic_repeat_expires_actor
):
    runtime, entry, _, _, _ = await _fixture(tmp_path, False)
    template = publication.retained(entry)
    store = SQLiteMaterializer(
        runtime._database,
        record_contracts={"agent_loop": publication.ACCOUNTING_SCHEMA},
        record_schema_variants=(("agent_loop", publication.CONTINUATION_SCHEMA),),
    )
    appender = EventAppender(store, capacity=1)
    try:
        await appender.advance_fence(FenceAdvanceCommand(runtime._tenant_id, "r6", 0))
        with workspace_snapshot(runtime._database) as snapshot:
            predecessor = capture_authority_snapshot_commitment(
                snapshot.connection, runtime._tenant_id
            )
        cut = template.accounting_request.cut.model_copy(
            update={"materialization_commitment": predecessor}
        )
        counts = Counter()
        selected = []
        written = []
        receipt = (await resume_evidence()).actor_budget_v1
        actor = object()
        state = SimpleNamespace(now=1, issued=False)

        def check_actor(observed):
            assert observed is actor
            if state.issued and state.now >= receipt.request.budget.absolute_deadline_ns:
                raise LoopRejected("synthetic actor deadline expired")

        class CustomSource:
            def collect_held(self, actual_runtime):
                assert actual_runtime is runtime
                counts["collect"] += 1
                if counts["collect"] == 2 and synthetic_repeat_expires_actor:
                    state.now += 31_000_000_000
                return cut

            def authenticate_selected_held(self, *args):
                return False

        async def exchange(actual_runtime, request, operation, result_type):
            assert actual_runtime is runtime
            if operation == "agent_loop.prepare_execution_accounting.v1":
                result = preparation.prepare_execution_accounting(request)
                sent, reply = template.accounting_call, template.accounting_reply
            else:
                assert operation == "agent_loop.prepare_execution_continuation.v1"
                result = preparation.prepare_execution_continuation(request)
                sent, reply = template.continuation_call, template.continuation_reply
            assert isinstance(result, result_type)
            return (
                result,
                sent.model_copy(update={"canonical_payload": request.canonical_bytes()}),
                reply.model_copy(update={"canonical_payload": result.canonical_bytes()}),
            )

        async def actor_read(peer):
            return actor

        async def issue(actual_runtime, peer):
            state.issued = True
            return actor, receipt

        async def submit(batch):
            def admission():
                counts["admission"] += 1
                return batch.admission_guard()

            def decision(resulting):
                counts["decision"] += 1
                return batch.decision_guard(resulting)

            result = await appender.submit(
                replace(batch, admission_guard=admission, decision_guard=decision)
            )
            assert result.disposition == "COMMITTED", result
            written.append(batch)
            return result

        async def finish(actual_runtime, selected_entry):
            # Journal persistence, native authentication and restart are outside
            # this control-boundary fixture; do not grant authority via _finish.
            assert actual_runtime is runtime
            return publication.retained(selected_entry)

        runtime._full_recovery_cut_source = CustomSource()
        runtime._execution_actor = actor_read
        runtime._check_execution_actor = check_actor
        runtime._same_execution_actor_identity = lambda initial, fresh: initial is fresh
        runtime._pending = lambda: ()
        runtime._require_no_pending = lambda: None
        runtime._loop_decisions = lambda: SimpleNamespace(
            entries=lambda: tuple(("decision", None, json.dumps(row).encode()) for row in selected)
        )
        runtime._append_decision = selected.append
        runtime.current_worker = lambda: "synthetic-worker"
        runtime._supervisor = SimpleNamespace(
            runtime=lambda: SimpleNamespace(session=lambda owner: template.accounting_call.callee)
        )
        runtime._commitment_journal = SimpleNamespace(load=lambda tenant: predecessor)
        runtime._appender = SimpleNamespace(submit=submit)
        monkeypatch.setattr(publication, "_exchange", exchange)
        monkeypatch.setattr(publication, "issue_execution_recovery_actor_v1", issue)
        monkeypatch.setattr(publication, "_finish", finish)

        if synthetic_repeat_expires_actor:
            with pytest.raises(LoopRejected, match="STALE:authority-after-cut"):
                await publication.publish_execution_accounting_readiness(runtime)
            assert counts == {"collect": 2, "admission": 1, "decision": 1}
            assert not written and not selected
            assert store._connection.execute("SELECT * FROM records").fetchall() == []
            assert store._connection.execute("SELECT * FROM publications").fetchall() == []
            assert store._connection.execute("SELECT head FROM tenant_heads").fetchall() == [(1,)]
            with workspace_snapshot(runtime._database) as snapshot:
                assert capture_authority_snapshot_commitment(
                    snapshot.connection, runtime._tenant_id
                ) == predecessor
            return
        evidence = await publication.publish_execution_accounting_readiness(runtime)
        assert len(written) == len(selected) == 1
        batch = written[0]
        assert batch.expected_head == 1
        rows = store._connection.execute(
            "SELECT record_id, canonical_bytes, commit_sequence FROM records ORDER BY record_id"
        ).fetchall()
        assert [(row[0], bytes(row[1]), row[2]) for row in rows] == sorted(
            (record.record_id, record.canonical_bytes, 2) for record in batch.records
        )
        assert len(rows) == 2
        assert store._connection.execute("SELECT head FROM tenant_heads").fetchall() == [(2,)]
        assert evidence.accounting_request.cut == cut
        assert counts == {"collect": 2, "admission": 1, "decision": 1}
    finally:
        await appender.close()
        store.close()
