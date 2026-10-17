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


async def _exercise_custom_source_writer(tmp_path, monkeypatch, mutation, *, witness_stage=None):
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
        input_reads = []
        receipt = (await resume_evidence()).actor_budget_v1
        actor = object()
        state = SimpleNamespace(now=1, issued=False, tampered_preimage=None)

        def check_actor(observed):
            assert observed is actor
            if state.issued and state.now >= receipt.request.budget.absolute_deadline_ns:
                raise LoopRejected("synthetic actor deadline expired")

        class CustomSource:
            def collect_held(self, actual_runtime):
                assert actual_runtime is runtime
                counts["collect"] += 1
                if counts["collect"] == 2 and mutation == "synthetic-actor-expiry":
                    state.now += 31_000_000_000
                if counts["collect"] == 2 and mutation == "semantic-change":
                    return cut.model_copy(update={"materialization_commitment": "d" * 64})
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
            assert batch.expected_preimage_commitment == predecessor
            if mutation == "same-head-tamper":
                store._connection.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?,?)",
                    (
                        runtime._tenant_id,
                        "tamper",
                        "agent_loop",
                        publication.ACCOUNTING_SCHEMA,
                        b"tamper",
                        1,
                    ),
                )
                store._connection.commit()
                with workspace_snapshot(runtime._database) as snapshot:
                    state.tampered_preimage = capture_authority_snapshot_commitment(
                        snapshot.connection, runtime._tenant_id
                    )
                assert state.tampered_preimage != predecessor
                assert store._connection.execute("SELECT head FROM tenant_heads").fetchall() == [
                    (1,)
                ]

            def admission():
                counts["admission"] += 1
                return batch.admission_guard()

            def decision(resulting):
                counts["decision"] += 1
                return batch.decision_guard(resulting)

            result = await appender.submit(
                replace(batch, admission_guard=admission, decision_guard=decision)
            )
            if mutation == "same-head-tamper":
                assert result.disposition == "STALE", result
                return result
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

        if witness_stage is not None:
            from chiplog.composition import r14_call_history_witness as history

            # Control boundary only: neither this custom cut nor these fabricated
            # inputs authenticate a native runtime. The separate native test
            # does not replace eligibility, inputs or collector results.
            original_inputs, changed_inputs = object(), object()
            runtime._trust = SimpleNamespace(capture_verified_observation=lambda: "trust")

            def capture(actual_runtime):
                assert actual_runtime is runtime
                input_reads.append(len(input_reads) + 1)
                position = input_reads[-1]
                if witness_stage == "before-absent" and position == 1:
                    return None
                if witness_stage == "before-error" and position == 1:
                    raise OSError("before capture failed")
                if witness_stage == "after-absent" and position == 2:
                    return None
                if witness_stage == "after-error" and position == 2:
                    raise ValueError("after capture failed")
                if witness_stage == "after-changed" and position == 2:
                    return changed_inputs
                if witness_stage == "admission-changed-restored" and position == 3:
                    return changed_inputs
                if (
                    witness_stage in ("decision-changed", "semantic-change", "slow-fallback")
                    and position == 4
                ):
                    return changed_inputs
                if witness_stage == "decision-error" and position == 4:
                    raise OSError("decision capture failed")
                return original_inputs

            monkeypatch.setattr(history, "capture_call_history_journal_inputs", capture)
            monkeypatch.setattr(
                publication, "_resumed_original_readiness_cut", lambda cut: True, raising=False
            )

        if mutation == "same-head-tamper":
            with pytest.raises(LoopRejected, match="recovery atomic publication STALE"):
                await publication.publish_execution_accounting_readiness(runtime)
            assert counts == {"collect": 1}
            assert not written and not selected
            assert store._connection.execute(
                "SELECT record_id, canonical_bytes FROM records"
            ).fetchall() == [("tamper", b"tamper")]
            assert store._connection.execute("SELECT * FROM publications").fetchall() == []
            assert store._connection.execute("SELECT head FROM tenant_heads").fetchall() == [(1,)]
            with workspace_snapshot(runtime._database) as snapshot:
                assert (
                    capture_authority_snapshot_commitment(snapshot.connection, runtime._tenant_id)
                    == state.tampered_preimage
                )
            return
        if mutation in ("synthetic-actor-expiry", "semantic-change"):
            expected_reason = (
                "STALE:authority-after-cut"
                if mutation == "synthetic-actor-expiry"
                else "STALE:recovery-cut-changed"
            )
            with pytest.raises(LoopRejected, match=expected_reason):
                await publication.publish_execution_accounting_readiness(runtime)
            assert counts == {"collect": 2, "admission": 1, "decision": 1}
            assert not written and not selected
            assert store._connection.execute("SELECT * FROM records").fetchall() == []
            assert store._connection.execute("SELECT * FROM publications").fetchall() == []
            assert store._connection.execute("SELECT head FROM tenant_heads").fetchall() == [(1,)]
            with workspace_snapshot(runtime._database) as snapshot:
                assert (
                    capture_authority_snapshot_commitment(snapshot.connection, runtime._tenant_id)
                    == predecessor
                )
            if witness_stage is not None:
                assert input_reads == [1, 2, 3, 4]
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
        expected_collections = 1 if witness_stage == "unchanged" else 2
        assert counts == {"collect": expected_collections, "admission": 1, "decision": 1}
        expected_input_reads = {
            "unchanged": [1, 2, 3, 4],
            "before-absent": [1],
            "before-error": [1],
            "after-absent": [1, 2],
            "after-error": [1, 2],
            "after-changed": [1, 2],
            "admission-changed-restored": [1, 2, 3],
            "decision-changed": [1, 2, 3, 4],
            "decision-error": [1, 2, 3, 4],
            None: [],
        }
        assert input_reads == expected_input_reads[witness_stage]
        public_batch = publication.command(evidence)
        assert public_batch.expected_preimage_commitment is None
        assert (
            replace(
                batch, expected_preimage_commitment=None, admission_guard=None, decision_guard=None
            )
            == public_batch
        )

        def unexpected_guard(*args):
            raise AssertionError("SQL replay must not run fresh-publication guards")

        replay = await appender.submit(
            replace(batch, admission_guard=unexpected_guard, decision_guard=unexpected_guard)
        )
        assert replay.disposition == "REPLAY"
        assert replay.commit_sequence == 2
        assert len(selected) == 1
        assert store._connection.execute("SELECT count(*) FROM records").fetchone() == (2,)
        assert store._connection.execute("SELECT count(*) FROM publications").fetchone() == (1,)
        assert store._connection.execute("SELECT head FROM tenant_heads").fetchall() == [(2,)]
    finally:
        await appender.close()
        store.close()


@pytest.mark.parametrize("mutation", (None, "synthetic-actor-expiry", "same-head-tamper"))
async def test_custom_source_keeps_final_collection_at_real_writer(tmp_path, monkeypatch, mutation):
    await _exercise_custom_source_writer(tmp_path, monkeypatch, mutation)


@pytest.mark.parametrize(
    "stage",
    (
        "unchanged",
        "before-absent",
        "before-error",
        "after-absent",
        "after-error",
        "after-changed",
        "admission-changed-restored",
        "decision-changed",
        "decision-error",
        "semantic-change",
        "slow-fallback",
    ),
)
async def test_fabricated_witness_guard_wiring_at_real_writer(tmp_path, monkeypatch, stage):
    mutation = {
        "semantic-change": "semantic-change",
        "slow-fallback": "synthetic-actor-expiry",
    }.get(stage)
    await _exercise_custom_source_writer(tmp_path, monkeypatch, mutation, witness_stage=stage)
