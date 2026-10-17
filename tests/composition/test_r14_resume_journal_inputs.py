"""Real authenticated journal/file inputs; no owner/cut authority is inferred here."""

from contextlib import ExitStack
from types import SimpleNamespace as NS

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.composition import r14_call_history_witness as witness
from chiplog.platform._sqlite import SQLiteMaterializer
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.authority_reads import capture_authority_snapshot_commitment
from chiplog.platform.workspace_snapshot import workspace_snapshot


@pytest.fixture
def journals(tmp_path, monkeypatch):
    database = tmp_path / "inputs.sqlite"
    store = SQLiteMaterializer(database, record_contracts={"agent_loop": "record.v1"})
    gate = AuthorityGate.for_database(database)
    with ExitStack() as stack:
        streams = {}
        for name, path in (
            ("loop", tmp_path / "loop"),
            ("owner", tmp_path / "owner"),
            ("preview", tmp_path / "preview"),
            ("h1", database.with_suffix(".h1-workspace-issuance")),
            ("dashboard", database.with_suffix(".workspace-issuance")),
        ):
            stream = IndependentTenantDecisionJournal.for_authority_bundle(
                path,
                authority_gate=gate,
            )
            path.with_suffix(path.suffix + ".head").chmod(0o600)
            stack.callback(stream.close)
            streams[name] = stream, path
        with gate.hold():
            streams["loop"][0].append(
                b'{"kind":"DECIDED","operation_kind":"agent_loop.native-one-call-recovery-profile.v5",'
                b'"records":[{"schema":"chiplog.execution.native-one-call-recovery-profile.v5"}]}',
                None,
            )
        with workspace_snapshot(database) as snapshot:
            anchor = capture_authority_snapshot_commitment(snapshot.connection, "tenant")
        state = NS(configuration=("canonical fixture configuration",), operations=())
        runtime = NS(
            _database=database,
            _database_identity=(str(database), 1, 2),
            _tenant_id="tenant",
            _authority_gate=lambda: gate,
            _check_database_identity=lambda: None,
            _commitment_journal=NS(load=lambda _: anchor),
            _loop_decisions=lambda: streams["loop"][0],
            _owner_decisions=lambda: NS(
                _raw=streams["owner"][0],
                snapshot=lambda: NS(
                    decisions=tuple(
                        NS(prepared=NS(request=NS(operation=operation)))
                        for operation in state.operations
                    )
                ),
            ),
            _require_call_preview_journal=lambda: streams["preview"][0],
        )
        monkeypatch.setattr(witness, "_standard_configuration", lambda _: state.configuration)
        yield runtime, gate, streams, state, store


def test_journal_capture_preserves_full_history_capture_and_ignores_candidate_postimage(journals):
    runtime, gate, _, _, store = journals
    with gate.hold():
        before = witness.capture_call_history_journal_inputs(runtime)
        full = witness.capture_call_history_inputs(runtime)
        assert before is not None and full is not None
        assert all(getattr(full, key) == value for key, value in vars(before).items())
        # Simulate the visible candidate postimage; this helper is deliberately
        # not the physical admission proof. The real SQLite writer owns that.
        store._connection.execute(
            "INSERT INTO records VALUES (?,?,?,?,?,?)",
            ("tenant", "candidate", "agent_loop", "record.v1", b"candidate", 1),
        )
        store._connection.commit()
        assert witness.capture_call_history_journal_inputs(runtime) == before
        assert witness.capture_call_history_inputs(runtime) is None


@pytest.mark.parametrize("stream", ["loop", "owner", "preview", "h1", "dashboard"])
def test_same_head_journal_body_corruption_cannot_reuse_inputs(journals, stream: str):
    runtime, gate, streams, _, _ = journals
    _, path = streams[stream]
    with gate.hold():
        assert witness.capture_call_history_journal_inputs(runtime) is not None
        head = path.with_suffix(path.suffix + ".head")
        head_before = head.read_bytes()
        with path.open("ab") as body:
            body.write(b"corrupt-body-with-unchanged-head\n")
        assert head.read_bytes() == head_before
        with pytest.raises((RuntimeError, ValueError)):
            witness.capture_call_history_journal_inputs(runtime)


@pytest.mark.parametrize("change", ["configuration", "unsupported-owner", "unsupported-loop"])
def test_journal_capture_refuses_inputs_outside_closed_native_prefix(journals, change: str):
    runtime, gate, streams, state, _ = journals
    with gate.hold():
        assert witness.capture_call_history_journal_inputs(runtime) is not None
        if change == "configuration":
            state.configuration = None
        elif change == "unsupported-owner":
            state.operations = ("agent_loop.complete_delivery_batch.v2",)
        else:
            journal = streams["loop"][0]
            journal.append(
                b'{"kind":"DECIDED","operation_kind":"unknown","records":[]}',
                journal.entries()[-1][0],
            )
        assert witness.capture_call_history_journal_inputs(runtime) is None


@pytest.mark.parametrize("change", ["preview-key", "h1-replacement", "dashboard-append"])
def test_authenticated_file_and_auxiliary_changes_invalidate_input_equality(journals, change: str):
    runtime, gate, streams, _, _ = journals
    with gate.hold():
        before = witness.capture_call_history_journal_inputs(runtime)
        assert before is not None
        if change == "preview-key":
            _, path = streams["preview"]
            path.with_suffix(path.suffix + ".key").write_bytes(b"x" * 32)
            with pytest.raises(RuntimeError):
                witness.capture_call_history_journal_inputs(runtime)
        elif change == "h1-replacement":
            _, path = streams["h1"]
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(path)
            assert witness.capture_call_history_journal_inputs(runtime) != before
        else:
            journal = streams["dashboard"][0]
            journal.append(b'{"new":"authenticated issuance"}', None)
            assert witness.capture_call_history_journal_inputs(runtime) != before
