"""Actual V3 function and SQLite writer; source/actor/IPC ports are isolated.

This is a transactional control test, not mounted source authority evidence.
"""

from types import SimpleNamespace as NS

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.capabilities.agent_loop.native_sealed_accounting_v3_preparation import (
    prepare_native_sealed_accounting_v3,
)
from chiplog.composition import r14_native_sealed_accounting_v3_publication as publication
from chiplog.composition.r14_native_accounting_records import (
    NATIVE_SEALED_ACCOUNTING_V3_RECORD_SCHEMA,
)
from chiplog.platform._sqlite import EventAppender, FenceAdvanceCommand, SQLiteMaterializer
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.broker import PublicPortSuccess
from chiplog.platform.workspace_snapshot import read_connection, workspace_snapshot
from tests.composition.test_r14_native_sealed_accounting_records import _retained


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", [False, True])
async def test_v3_live_writer_checks_physical_preimage_before_publishing(
    tmp_path,
    monkeypatch,
    tamper,
):
    template = _retained()
    selected_response = template.request.selected_response
    identity = "native-sealed-accounting:" + selected_response.revision.fingerprint
    request = template.request.model_copy(update={"command_id": identity})
    response = prepare_native_sealed_accounting_v3(request)
    tenant = template.source_stamp.tenant_id
    path = tmp_path / "v3-preimage.sqlite"
    store = SQLiteMaterializer(
        path, record_contracts={"agent_loop": NATIVE_SEALED_ACCOUNTING_V3_RECORD_SCHEMA}
    )
    gate = AuthorityGate.for_database(path)
    appender = EventAppender(store, capacity=1)
    await appender.advance_fence(FenceAdvanceCommand(tenant, "r6", 0))
    store._connection.execute("INSERT INTO tenant_heads VALUES (?, ?)", (tenant, 0))
    store._connection.commit()
    assert store._connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    with workspace_snapshot(path) as snapshot:
        source_commitment = capture_authority_snapshot_commitment(snapshot.connection, tenant)
    state = NS(entry=None, submitted=None, disposition=None, actors=0)
    facts = object()
    projection = NS(prepared_run=object())
    owner = template.owner_request.callee

    async def actor(_):
        state.actors += 1
        return "actor"

    async def owner_call(sent):
        assert sent.canonical_payload == request.canonical_bytes()
        return PublicPortSuccess(
            request_id=sent.request_id,
            responder=sent.callee,
            schema_id=publication.NATIVE_SEALED_ACCOUNTING_V3_RESULT_SCHEMA,
            canonical_payload=response.canonical_bytes(),
        )

    engine = NS(session=lambda _: owner, call=owner_call)
    runtime = NS(
        _tenant_id=tenant,
        _database=path,
        _database_identity=("database", 1, 2),
        _authority_gate=lambda: gate,
        _execution_actor=actor,
        _check_execution_actor=lambda _: None,
        _same_execution_actor_identity=lambda *_: True,
        _pending=lambda: (),
        _require_no_pending=lambda: None,
        _commitment_journal=NS(load=lambda _: source_commitment),
        _supervisor=NS(runtime=lambda: engine),
        current_worker=lambda: "worker",
        _append_decision=lambda entry: setattr(state, "entry", entry),
    )

    class Source:
        def __init__(self, *args) -> None:
            pass

        def collect(self, *args, **kwargs):
            return facts

        def prepare_sealed_accounting_v3(self, *args, **kwargs):
            return request

    async def finish(actual_runtime, entry):
        assert actual_runtime is runtime and entry is state.entry
        return response

    async def submit(command):
        assert state.actors == 3
        state.submitted = command
        if tamper:
            # Only physical bytes change: original head, selected journal and
            # trust/actor inputs remain exact, isolating the missing predicate.
            store._connection.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?)",
                (
                    tenant,
                    "orphan",
                    "agent_loop",
                    NATIVE_SEALED_ACCOUNTING_V3_RECORD_SCHEMA,
                    b"orphan",
                    0,
                ),
            )
            store._connection.commit()
            store._connection.execute("BEGIN")
            try:
                changed_commitment = capture_authority_snapshot_commitment(
                    store._connection, tenant
                )
            finally:
                store._connection.rollback()
            assert changed_commitment != source_commitment
            assert store._connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?",
                (tenant,),
            ).fetchone() == (0,)
            assert state.entry is None
        result = await appender.submit(command)
        state.disposition = result.disposition
        return result

    runtime._appender = NS(submit=submit)
    monkeypatch.setattr(publication, "_selected", lambda *_: state.entry)
    monkeypatch.setattr(publication, "read_execution_recovery_history", lambda *a, **kw: object())
    monkeypatch.setattr(publication, "_authenticated_accepted_open_recoveries", lambda _: ())
    monkeypatch.setattr(
        publication, "R14OriginalNativePrepareSource", lambda _: NS(collect=lambda _: projection)
    )
    monkeypatch.setattr(publication, "R14NativeSelectedAccountingSource", Source)
    monkeypatch.setattr(publication, "bind_native_execution_preparation_v2", lambda _: object())
    monkeypatch.setattr(
        publication, "TypeAdapter", lambda _: NS(dump_json=lambda _: b"inert source")
    )
    monkeypatch.setattr(publication, "_finish", finish)
    error = None
    try:
        with workspace_snapshot(path):
            try:
                result = await publication.publish_native_sealed_accounting_v3(
                    runtime, selected_response
                )
                assert result == response
            except LoopRejected as rejected:
                error = str(rejected)
        # Read outside the source snapshot, so a stale reader cannot hide writes.
        with read_connection(path) as connection:
            rows = connection.execute(
                "SELECT record_id FROM records WHERE tenant_id=? AND commit_sequence=1",
                (tenant,),
            ).fetchall()
            head = connection.execute(
                "SELECT head FROM tenant_heads WHERE tenant_id=?",
                (tenant,),
            ).fetchone()
        observed = (state.disposition, len(rows), state.entry is not None, head)
        assert observed == (("STALE", 0, False, (0,)) if tamper else ("COMMITTED", 1, True, (1,)))
        if tamper:
            assert error == "native sealed accounting V3 publication STALE"
        else:
            assert error is None
    finally:
        await appender.close()
        store._connection.close()
