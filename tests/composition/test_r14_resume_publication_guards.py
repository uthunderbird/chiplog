"""Publication control boundary: inert owner/source ports, actual SQLite guards/rollback."""

import asyncio
import hashlib
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition import r14_execution_resume as resume
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import workspace_snapshot
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _cut


class _Value(NS):
    def model_copy(self, *, update):
        return type(self)(**(vars(self) | update))

    def canonical_bytes(self):
        return b'"inert owner/source boundary"'


@pytest.mark.parametrize("mutation", [
    None, "admission-input", "decision-input", "admission-clock", "decision-clock",
    "decision-before-deadline", "decision-worker", "decision-session", "decision-trust",
    "decision-pending", "physical-preimage",
    "unsupported-prefix", "legacy-profile",
    "collection-trust",
])
async def test_resume_validates_once_and_preserves_both_writer_guards(
    tmp_path, monkeypatch, mutation,
):
    original = (await _cut()).complete_ordered_run_lineage[-1]
    suspended = original.model_copy(update={"state": "SUSPENDED"})
    active = suspended.model_copy(update={"state": "ACTIVE", "head": "resumed"})
    database = tmp_path / "guard.sqlite"
    store = SQLiteMaterializer(database, record_contracts={"agent_loop": resume.RUN_SCHEMA})
    appender = EventAppender(store, capacity=1)
    await appender.advance_fence(FenceAdvanceCommand(original.tenant, "r6", 0))
    with workspace_snapshot(database) as snapshot:
        preimage = capture_authority_snapshot_commitment(snapshot.connection, original.tenant)
    state = NS(now=1, issued=None, input=0, selected=None, evidence=None)
    counts = {name: 0 for name in ("collect", "prepare", "classify", "admission", "decision")}
    result = NS(run=active)
    suspension = NS(baseline=NS(original_obligations=("frozen-original",)))
    cut = NS(
        tenant_id=original.tenant, database_id="database", materialization_commitment=preimage,
        tenant_commit_sequence=0, complete_ordered_run_lineage=(suspended,),
        complete_sources=(NS(schema_id="chiplog.execution.native-one-call-recovery-profile.v5"),),
    )
    if mutation == "legacy-profile":
        cut.complete_sources = (
            NS(schema_id="chiplog.execution.native-one-call-recovery-profile.v4"),
        )
    source = NS()

    def collect(_):
        counts["collect"] += 1
        if mutation == "collection-trust":
            state.trust = "trust-changed-during-source-collection"
        if counts["collect"] > 1:
            state.now += 31_000_000_000
        return cut, suspension

    source.collect_for_resume_held = collect
    session = NS(generation_id="test-generation")
    state.session = session
    state.worker = suspended.worker_session
    state.trust = "exact-trust"
    state.pending = False
    engine = NS(
        session=lambda _: state.session,
        graph_generation=lambda: NS(owners=(NS(owner_id="agent_loop", capability_ids=(
            resume.OWNER_OBLIGATION_OPERATION,
        )),)),
    )
    actor = NS()

    async def initial_actor(_):
        return actor

    async def fresh_actor(*_):
        state.issued = state.now
        budget = NS(request=NS(budget=NS(absolute_deadline_ns=state.now + 30_000_000_000)))
        return actor, budget

    def check_actor(_):
        if state.issued is not None and state.now >= state.issued + 30_000_000_000:
            raise LoopRejected("inert actor deadline expired")

    def require_no_pending():
        if state.pending:
            raise LoopRejected("inert publication pending")

    runtime = NS(
        _tenant_id=original.tenant, _database=database, _database_identity=("database",),
        _execution_lane=asyncio.Lock(), _execution_actor=initial_actor,
        _authority_gate=lambda: NS(hold=nullcontext), _check_execution_actor=check_actor,
        _check_database_identity=lambda: None, _require_no_pending=require_no_pending,
        _commitment_journal=NS(load=lambda _: preimage),
        _trust=NS(capture_verified_observation=lambda: state.trust),
        current_worker=lambda: state.worker,
        _supervisor=NS(runtime=lambda: engine),
        _loop_decisions=lambda: NS(entries=lambda: ()),
        _same_execution_actor_identity=lambda *_: True,
    )

    def append_decision(entry):
        state.selected = ("selected", None, entry)

    runtime._append_decision = append_decision

    def prepare(_):
        if mutation not in ("unsupported-prefix", "legacy-profile"):
            assert state.issued is None
        counts["prepare"] += 1
        state.now += 31_000_000_000
        return result

    def classify(*_):
        counts["classify"] += 1
        return NS(disposition="SAME_RUN")

    def evidence(**kwargs):
        state.evidence = _Value(**kwargs)
        return state.evidence

    def command(_):
        prepare(None)
        return physical_command()

    def physical_command():
        payload = active.canonical_bytes()
        return PhysicalPublicationCommand(
            original.tenant, "inert.resume-guard-boundary", "resume", "fingerprint", 0,
            "r6", 0, 0,
            (PhysicalRecord("resumed", "agent_loop", resume.RUN_SCHEMA, payload,
                            hashlib.sha256(payload).hexdigest()),),
            expected_preimage_commitment=preimage,
        )

    async def exchange(*_):
        return result, "inert-owner-call", "inert-owner-reply"

    async def finish(*_):
        return active

    async def submit(batch):
        if mutation == "physical-preimage":
            store._connection.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?)",
                (original.tenant, "tamper", "agent_loop", resume.RUN_SCHEMA, b"tamper", 0),
            )
            store._connection.commit()

        def admission():
            counts["admission"] += 1
            if mutation == "admission-input":
                state.input += 1
            if mutation == "admission-clock":
                state.now = state.issued + 30_000_000_000
            return batch.admission_guard()

        def decision(resulting):
            counts["decision"] += 1
            if mutation == "decision-input":
                state.input += 1
            if mutation == "decision-clock":
                state.now = state.issued + 30_000_000_000
            if mutation == "decision-before-deadline":
                state.now = state.issued + 30_000_000_000 - 1
            if mutation == "decision-worker":
                state.worker = "rival-worker"
            if mutation == "decision-session":
                state.session = NS(generation_id="rival-generation")
            if mutation == "decision-trust":
                state.trust = "changed-trust"
            if mutation == "decision-pending":
                state.pending = True
            return batch.decision_guard(resulting)

        return await appender.submit(replace(
            batch, admission_guard=admission, decision_guard=decision,
        ))

    runtime._appender = NS(submit=submit)
    monkeypatch.setattr(resume, "PrepareExecutionResume", _Value)
    monkeypatch.setattr(resume, "RetainedExecutionResume", evidence)
    monkeypatch.setattr(resume, "source", lambda _: source)
    monkeypatch.setattr(resume, "_entry_for", lambda *_: state.selected)
    monkeypatch.setattr(resume, "_retained", lambda _: state.evidence)
    monkeypatch.setattr(resume, "_exchange", exchange)
    monkeypatch.setattr(resume, "_finish", finish)
    monkeypatch.setattr(resume, "issue_execution_recovery_actor_v1", fresh_actor)
    monkeypatch.setattr(resume, "prepare_execution_resume", prepare)
    monkeypatch.setattr(resume, "classify_execution_resume", classify)
    monkeypatch.setattr(resume, "_command", command)
    # Isolate source authentication and owner wire validation. Their real
    # implementations have separate tests; this fixture grants no authority.
    monkeypatch.setattr(resume, "capture_call_history_journal_inputs",
                        lambda _: None if mutation == "unsupported-prefix" else state.input,
                        raising=False)
    monkeypatch.setattr(resume, "_prepare_resume_artifacts",
                        lambda *args: (prepare(None), classify(None, None)), raising=False)
    monkeypatch.setattr(resume, "_bind_resume_artifacts",
                        lambda *_: physical_command(), raising=False)

    monkeypatch.setattr(resume.time, "monotonic_ns", lambda: state.now)
    try:
        if mutation in (None, "decision-before-deadline"):
            assert await resume.resume_execution(runtime, "peer", suspended.run_id,
                                                 suspended.head, b"activate") == active
            assert counts == {"collect": 1, "prepare": 1, "classify": 1,
                              "admission": 1, "decision": 1}
            assert len(store.durable_records()) == 1
            assert state.selected is not None
        else:
            with pytest.raises(LoopRejected):
                await resume.resume_execution(runtime, "peer", suspended.run_id,
                                              suspended.head, b"activate")
            assert not any(row[1] == "resumed" for row in store.durable_records())
            assert state.selected is None
            assert counts["admission"] == int(
                mutation not in ("physical-preimage", "collection-trust")
            )
            if mutation == "collection-trust":
                assert counts["prepare"] == counts["classify"] == counts["decision"] == 0
            if mutation.startswith("decision"):
                assert counts["decision"] == 1
            if mutation in ("unsupported-prefix", "legacy-profile"):
                assert counts["collect"] == 2
    finally:
        await appender.close()
