"""Historical builder seam: real Run bytes/SQLite, inert accepted-source ports.

The caller's full history authentication is isolated; this fixture grants no
publication authority and does not replace the mounted reopen witness.
"""

from types import SimpleNamespace as NS

import pytest

from chiplog.composition import r14_native_accepted_open_cut as builder
from chiplog.platform._sqlite import SQLiteMaterializer
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import workspace_snapshot
from tests.capabilities.agent_loop.test_execution_recovery_preparation import _cut, _run_ref


@pytest.mark.asyncio
@pytest.mark.parametrize("bound,damage", [
    (0, None), (1, None), (2, None), (3, None),
    (1, "missing"), (1, "owner"), (1, "schema"), (1, "bytes"),
    (1, "fingerprint"), (1, "foreign-run"), (1, "ambiguous"),
])
async def test_open_builder_uses_run_at_source_sequence_not_latest_lineage(
    tmp_path, monkeypatch, bound, damage,
):
    original = (await _cut()).complete_ordered_run_lineage[-1]
    suspended = original.model_copy(update={
        "head": "suspended", "state": "SUSPENDED", "predecessor": original.head,
    })
    resumed = suspended.model_copy(update={
        "head": "resumed", "state": "ACTIVE", "predecessor": suspended.head,
    })
    runs = (original, suspended, resumed)
    database = tmp_path / "prefix.sqlite"
    store = SQLiteMaterializer(database, record_contracts={"agent_loop": original.schema_id})
    for sequence, run in enumerate(runs, 1):
        store._connection.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (
            original.tenant, run.head, "agent_loop", run.schema_id,
            run.canonical_bytes(), sequence,
        ))
    if damage == "ambiguous":
        duplicate = original.model_copy(update={"head": "other-at-same-sequence"})
        runs = (original, duplicate, suspended, resumed)
        store._connection.execute("INSERT INTO records VALUES (?,?,?,?,?,?)", (
            original.tenant, duplicate.head, "agent_loop", duplicate.schema_id,
            duplicate.canonical_bytes(), 1,
        ))
    if damage == "missing":
        store._connection.execute("DELETE FROM records WHERE record_id=?", (original.head,))
    elif damage in ("owner", "schema", "bytes"):
        column, value = {
            "owner": ("owner", "foreign-owner"),
            "schema": ("schema_id", "foreign.schema.v1"),
            "bytes": ("canonical_bytes", b"different-run-bytes"),
        }[damage]
        store._connection.execute(
            f"UPDATE records SET {column}=? WHERE record_id=?", (value, original.head),
        )
    store._connection.execute("INSERT INTO tenant_heads VALUES (?,?)", (original.tenant, 3))
    store._connection.commit()
    gate = AuthorityGate.for_database(database)
    with workspace_snapshot(database) as snapshot:
        actual = capture_authority_snapshot_commitment(snapshot.connection, original.tenant)
    runtime = NS(
        _database=database, _tenant_id=original.tenant, _database_identity=("db", 1, 2),
        _authority_gate=lambda: gate, _check_database_identity=lambda: None,
        _commitment_journal=NS(load=lambda _: actual),
    )
    ref = _run_ref(original)
    intent = NS(
        execution_intent_id="execution-intent", external_intent=ref,
        canonical_bytes=lambda: b"inert-execution-intent",
    )
    accepted = NS(
        source=NS(
            target_run_lineage=tuple(_run_ref(run) for run in runs),
            target_inventory_rows=(NS(original_call_id="call", acceptance=NS(
                kind="CONSEQUENTIAL_ACCEPTED", accepted=ref.revision,
                execution_intent=ref.revision,
            )),),
        ),
        original_call_id="call", initialized=ref,
        selected_seal=NS(response_seal=ref),
        acceptance=NS(loop_proposal=NS(
            accepted=NS(accepted_id="accepted", canonical_bytes=lambda: b"inert-accepted"),
            execution_intent=intent,
        )),
    )
    selected = NS(
        materialization_commitment=actual, database_identity=runtime._database_identity,
        tenant_id=original.tenant, accepted=accepted, tenant_commit_sequence=3,
        outcome=NS(
            snapshot=NS(intent=NS(intent_id=ref.subject_id)),
            canonical_bytes=lambda: b"inert-selected-outcome",
        ),
        selected_member=NS(selected_decision=NS(
            subject_id="decision", head="decision-head", fingerprint="a" * 64,
        )),
        outcome_record_id="outcome", commit_sequence=1, publication_ordinal=0,
        batch_record_ids=("outcome",),
    )
    if damage in ("fingerprint", "foreign-run"):
        first, *rest = accepted.source.target_run_lineage
        changed = (
            first.model_copy(update={"revision": first.revision.model_copy(
                update={"fingerprint": "c" * 64}
            )})
            if damage == "fingerprint"
            else first.model_copy(update={"subject_id": "foreign-run"})
        )
        accepted.source.target_run_lineage = (changed, *rest)
    history = (NS(records=runs), None, None, (), (), ())
    monkeypatch.setattr(builder, "CommonCliExecutionRuntime", NS)
    monkeypatch.setattr(builder, "read_execution_history", lambda _: history[0])
    with gate.hold():
        live = builder.build_accepted_open_recovery_cut(runtime, selected)
        assert live is not None and live.current_run == _run_ref(resumed)
        result = builder.build_accepted_open_recovery_cut(
            runtime, selected, source_materialization_commitment="b" * 64,
            source_tenant_commit_sequence=bound, authenticated_history=history,
        )
    if bound == 0 or damage is not None:
        assert result is None
    else:
        assert result is not None
        expected = runs[bound - 1]
        assert result.current_run == _run_ref(expected)
        assert result.current_run_bytes == expected.canonical_bytes()
        assert result.materialization_commitment == "b" * 64
        assert result.tenant_commit_sequence == bound
    store._connection.close()
