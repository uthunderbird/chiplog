"""Real journal/SQLite extraction with inert semantic authorization fixtures.

These tests do not establish canonical scheduler issuance or source admission.
"""

import base64
import hashlib
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

import pytest

import chiplog.platform.scheduler_reads as reader_module
from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, EndpointSelection
from chiplog.capabilities.agent_loop.recovery_contracts import Absent, Present
from chiplog.capabilities.agent_loop.scheduler_configuration import (
    ConfigurationSnapshot,
    FixedIntervalDefinition,
)
from chiplog.capabilities.agent_loop.scheduler_contracts import (
    SchedulerCommandIdentity,
    SchedulerContextRef,
    SchedulerIntervalBound,
)
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerRunInputs
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    BatchPreparationCut,
    ConfigurationGenesisCommand,
    ConfigurationPreparationRequest,
    ConfigurationPreparationSnapshot,
    prepare_configuration,
)
from chiplog.composition.scheduler_source_registry import (
    AdmittedSchedulerStartup,
    SchedulerSourceRegistration,
    SourceRegistrationUnresolved,
    _require_historical_equality,
    _tree,
    current_scheduler_registration,
    read_admitted_scheduler_startup,
)
from chiplog.platform._owner_publication_contracts import SingleOwnerBatch
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PhysicalRecord,
    SQLiteMaterializer,
)
from chiplog.platform.authority_reads import (
    AuthorityCommitmentJournal,
    capture_authority_snapshot_commitment,
    capture_authority_storage_state,
)
from chiplog.platform.owner_decision_journal import IndependentOwnerDecisionJournal
from chiplog.platform.owner_publications import PreparedOwnerPublication, SelectedOwnerDecision
from chiplog.platform.scheduler_reads import (
    SchedulerReadIntegrityError,
    read_materialized_scheduler,
)
from chiplog.platform.workspace_snapshot import workspace_snapshot


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def preparation(commitment: str) -> ConfigurationPreparationRequest:
    context = SchedulerContextRef(
        tenant_id="tenant",
        service_identity="scheduler",
        session_id="historical-session",
        mandate_head="mandate",
        issuance_id="context-issuance",
        issuance_fingerprint="a" * 64,
    )
    definition = FixedIntervalDefinition(
        start_ns=0,
        period_ns=10,
        end_exclusive_ns="NO_END",
        run_inputs=SchedulerRunInputs(
            tenant="tenant",
            principal="principal",
            prompt="prompt",
            policy=BudgetPolicy(),
            origin=EndpointSelection(
                kind="ORIGIN_EXACT",
                ingress_binding_head="ingress",
                endpoint_head="endpoint",
                endpoint_id="endpoint",
                provider="hermetic-local",
                recipient="principal",
                canonical_address="local://principal",
                credential_binding_head="credential",
            ),
            contour_head="contour",
            policy_head="policy",
            worker_session="worker",
            authority_epoch="epoch",
        ),
    )
    command = ConfigurationGenesisCommand(
        identity=SchedulerCommandIdentity(
            command_id="genesis", schema_version="1", canonicalization_version="1"
        ),
        schedule_id="schedule",
        definition=definition,
        policy="COALESCE",
        bound=SchedulerIntervalBound(
            max_member_count=10, max_manifest_bytes=100000, max_serialized_batch_bytes=1000000
        ),
    )
    return ConfigurationPreparationRequest(
        operation="scheduler.genesis",
        context=context,
        snapshot=ConfigurationPreparationSnapshot(
            cut=BatchPreparationCut(
                tenant_id="tenant",
                tenant_frontier=0,
                materialization_commitment=commitment,
                registry_head="historical-registry",
                registry_fingerprint="b" * 64,
                submission_id="historical-submission",
                authorized_context=context,
                authorized_command_fingerprint=digest(command.canonical_bytes()),
                authority_proof=Present(head="historical-authority-proof", fingerprint="c" * 64),
            ),
            current=ConfigurationSnapshot(
                schedule=None, policy=None, bound=None, active_hold=Absent()
            ),
            authority_epoch="epoch",
            broker_generation="broker",
            runtime_graph_generation="runtime",
            scheduler_authority_head="scheduler-authority",
        ),
        command_bytes=command.canonical_bytes(),
    )


@dataclass(frozen=True)
class Fixture:
    database: Path
    journal: IndependentOwnerDecisionJournal
    raw: IndependentTenantDecisionJournal
    anchor: AuthorityCommitmentJournal
    original: ConfigurationPreparationRequest
    selected: SelectedOwnerDecision


async def materialized(
    tmp_path: Path, variant: str = "full", registry: Present | None = None
) -> Fixture:
    database = tmp_path / "scheduler.sqlite"
    raw = IndependentTenantDecisionJournal(tmp_path / "owners.journal")
    journal = IndependentOwnerDecisionJournal(raw, "tenant")
    anchor = AuthorityCommitmentJournal(database, b"fixture-independent-secret")
    selected: SelectedOwnerDecision | None = None
    with SQLiteMaterializer(
        database,
        record_contracts={"agent_loop": "chiplog.scheduler.schedule-definition.v1"},
        record_schema_variants=(
            ("agent_loop", "chiplog.scheduler.missed-policy.v1"),
            ("agent_loop", "chiplog.scheduler.interval-bound.v1"),
        ),
    ) as store:
        async with EventAppender(store, capacity=2) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
            before, _ = capture_authority_storage_state(database)
            original = preparation(before)
            if registry is not None:
                original = original.model_copy(
                    update={
                        "snapshot": original.snapshot.model_copy(
                            update={
                                "cut": original.snapshot.cut.model_copy(
                                    update={
                                        "registry_head": registry.head,
                                        "registry_fingerprint": registry.fingerprint,
                                    }
                                )
                            }
                        )
                    }
                )
            produced = prepare_configuration(original)
            source = original.canonical_bytes()
            schema = "chiplog.scheduler.configuration-preparation.v1"
            if variant == "bare":
                source, schema = original.command_bytes, "chiplog.scheduler.genesis.v1"
            elif variant == "changed_identity":
                inner = ConfigurationGenesisCommand.model_validate_json(original.command_bytes)
                inner = inner.model_copy(
                    update={
                        "identity": inner.identity.model_copy(update={"command_id": "different"})
                    }
                )
                source = original.model_copy(
                    update={"command_bytes": inner.canonical_bytes()}
                ).canonical_bytes()
            elif variant == "changed_definition":
                inner = ConfigurationGenesisCommand.model_validate_json(original.command_bytes)
                inner = inner.model_copy(
                    update={"definition": inner.definition.model_copy(update={"period_ns": 99})}
                )
                original = original.model_copy(
                    update={
                        "command_bytes": inner.canonical_bytes(),
                        "snapshot": original.snapshot.model_copy(
                            update={
                                "cut": original.snapshot.cut.model_copy(
                                    update={
                                        "authorized_command_fingerprint": digest(
                                            inner.canonical_bytes()
                                        )
                                    }
                                )
                            }
                        ),
                    }
                )
                source = original.canonical_bytes()
            request = SingleOwnerBatch.model_validate(
                {
                    "operation": "scheduler.genesis",
                    "identity": {
                        "tenant_id": "tenant",
                        "command_id": "genesis",
                        "command_fingerprint": digest(source),
                        "canonicalization_version": "chiplog.owner-publication.v1",
                    },
                    "authentication": {
                        "kind": "WORKER",
                        "invocation": {
                            "issuance_id": "inert-fixture",
                            "issuance_fingerprint": "d" * 64,
                            "broker_epoch": "epoch",
                            "broker_session": "session",
                            "runtime_generation": "generation",
                            "operation_subject": "genesis",
                        },
                        "applicability_schema": "inert.fixture",
                        "applicability_bytes": b"no-authority-claim",
                        "applicability_fingerprint": digest(b"no-authority-claim"),
                    },
                    "expected": {
                        "tenant_id": "tenant",
                        "tenant_frontier": 0,
                        "expected_materialization_commitment": before,
                        "registry_head": original.snapshot.cut.registry_head,
                        "registry_fingerprint": original.snapshot.cut.registry_fingerprint,
                        "ordered_heads": (),
                        "complete_manifest_fingerprint": "e" * 64,
                    },
                    "command": {
                        "owner": "agent_loop",
                        "schema_id": schema,
                        "canonical_bytes": source,
                        "fingerprint": digest(source),
                    },
                    "complete_records": tuple(
                        {
                            "owner": "agent_loop",
                            "record_kind": row.record_kind,
                            "record_id": row.record_id,
                            "schema_id": row.schema_id,
                            "canonical_bytes": base64.b64decode(row.canonical_base64),
                            "fingerprint": row.fingerprint,
                        }
                        for row in produced.records
                    ),
                    "complete_batch_fingerprint": digest(b"inert-fixture-batch"),
                }
            )
            prepared = PreparedOwnerPublication(request, "fixture-prepared", "fence", 0, before)

            def select(commitment: str) -> None:
                nonlocal selected
                selected = journal.select(prepared, commitment)

            result = await appender.submit(
                PhysicalPublicationCommand(
                    tenant_id="tenant",
                    operation_kind="scheduler.genesis",
                    idempotency_key="genesis",
                    request_fingerprint=request.identity.command_fingerprint,
                    expected_head=0,
                    fence_generation="fence",
                    expected_fence_frontier=0,
                    minimum_fence_frontier=0,
                    records=tuple(
                        PhysicalRecord(
                            row.record_id,
                            row.owner,
                            row.schema_id,
                            row.canonical_bytes,
                            row.fingerprint,
                        )
                        for row in request.complete_records
                    ),
                    decision_guard=select,
                )
            )
            assert result.disposition == "COMMITTED" and selected is not None
            if variant != "pending":
                journal.materialized(selected)
            commitment, _ = capture_authority_storage_state(database)
            anchor.commit("tenant", commitment)
    return Fixture(database, journal, raw, anchor, original, selected)


async def test_complete_physical_and_protected_request_are_retained_but_not_source_admitted(
    tmp_path: Path,
) -> None:
    fixture = await materialized(tmp_path)
    cut = read_materialized_scheduler(fixture.database, "tenant", fixture.journal, fixture.anchor)
    assert cut.disposition == "SOURCE_ADMISSION_UNRESOLVED"
    assert cut.protected_decisions == (fixture.selected,)
    assert cut.historical_requests[0].preparation == fixture.original
    assert cut.historical_requests[0].registry == Present(
        head="historical-registry", fingerprint="b" * 64
    )
    assert cut.selected[0].command_id == fixture.selected.prepared.request.identity.command_id
    assert cut.selected[0].context == fixture.original.context
    assert tuple(row.record.record_id for row in cut.materialized) == tuple(
        row.record_id for row in fixture.selected.prepared.request.complete_records
    )
    assert tuple(row.member_ordinal for row in cut.materialized) == (0, 1, 2)
    assert (cut.physical_device, cut.physical_inode) == (
        fixture.database.stat().st_dev,
        fixture.database.stat().st_ino,
    )
    assert not hasattr(cut, "admitted_index")


@pytest.mark.parametrize("variant", ["bare", "changed_identity", "pending"])
async def test_incomplete_original_or_pending_selection_never_becomes_a_startup_cut(
    tmp_path: Path, variant: str
) -> None:
    fixture = await materialized(tmp_path, variant)
    with pytest.raises(SchedulerReadIntegrityError) as caught:
        read_materialized_scheduler(fixture.database, "tenant", fixture.journal, fixture.anchor)
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "extra",
        "bytes",
        "order",
        "owner",
        "sequence",
        "unknown-schema",
        "omitted-journal",
    ],
)
async def test_reanchored_sql_cannot_substitute_the_independent_exact_manifest(
    tmp_path: Path, mutation: str
) -> None:
    fixture = await materialized(tmp_path)
    identity = fixture.selected.prepared.request.complete_records[-1].record_id
    with sqlite3.connect(fixture.database) as connection:
        if mutation == "missing":
            connection.execute("DELETE FROM records WHERE record_id = ?", (identity,))
        elif mutation == "extra":
            connection.execute(
                "INSERT INTO records SELECT tenant_id, 'orphan', owner, schema_id, "
                "canonical_bytes, commit_sequence FROM records WHERE record_id = ?",
                (identity,),
            )
        elif mutation == "bytes":
            connection.execute(
                "UPDATE records SET canonical_bytes = canonical_bytes || ' ' WHERE record_id = ?",
                (identity,),
            )
        elif mutation == "order":
            connection.execute(
                "UPDATE publications SET record_ids = ?",
                (
                    "\n".join(
                        reversed(
                            [
                                row.record_id
                                for row in fixture.selected.prepared.request.complete_records
                            ]
                        )
                    ),
                ),
            )
        elif mutation == "owner":
            connection.execute(
                "UPDATE records SET owner = 'effects' WHERE record_id = ?", (identity,)
            )
        elif mutation == "sequence":
            connection.execute(
                "UPDATE records SET commit_sequence = 99 WHERE record_id = ?", (identity,)
            )
        elif mutation == "unknown-schema":
            connection.execute(
                "UPDATE records SET schema_id = 'chiplog.scheduler.unknown.v1' WHERE record_id = ?",
                (identity,),
            )
    commitment, _ = capture_authority_storage_state(fixture.database)
    fixture.anchor.commit("tenant", commitment)
    journal = (
        fixture.journal
        if mutation != "omitted-journal"
        else IndependentOwnerDecisionJournal(
            IndependentTenantDecisionJournal(tmp_path / "empty.journal"), "tenant"
        )
    )
    with pytest.raises(SchedulerReadIntegrityError):
        read_materialized_scheduler(fixture.database, "tenant", journal, fixture.anchor)


@pytest.mark.parametrize("mutation", ["path", "anchor"])
async def test_cut_rejects_changed_independent_identity_after_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    fixture = await materialized(tmp_path)
    original_capture = capture_authority_snapshot_commitment

    def capture(connection: sqlite3.Connection, tenant: str) -> str:
        result = original_capture(connection, tenant)
        if mutation == "path":
            replacement = tmp_path / "replacement.sqlite"
            replacement.write_bytes(fixture.database.read_bytes())
            replacement.replace(fixture.database)
        else:
            fixture.anchor.commit("tenant", "f" * 64)
        return result

    monkeypatch.setattr(reader_module, "capture_authority_snapshot_commitment", capture)
    with pytest.raises(SchedulerReadIntegrityError) as caught:
        read_materialized_scheduler(fixture.database, "tenant", fixture.journal, fixture.anchor)
    assert "changed during cut acquisition" in str(caught.value.__cause__)


async def test_temp_shadow_cannot_replace_main_authority(tmp_path: Path) -> None:
    fixture = await materialized(tmp_path)
    with workspace_snapshot(fixture.database) as physical:
        physical.connection.execute("PRAGMA query_only = OFF")
        physical.connection.execute("CREATE TEMP TABLE records (record_id TEXT)")
        physical.connection.execute("PRAGMA query_only = ON")
        cut = read_materialized_scheduler(
            fixture.database, "tenant", fixture.journal, fixture.anchor
        )
    assert tuple(row.record.record_id for row in cut.materialized) == tuple(
        record.record_id for record in fixture.selected.prepared.request.complete_records
    )


async def test_scheduler_reader_joins_and_leaves_the_callers_transaction_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = await materialized(tmp_path)
    standalone = read_materialized_scheduler(
        fixture.database, "tenant", fixture.journal, fixture.anchor
    )
    observed: list[sqlite3.Connection] = []

    def capture(connection: sqlite3.Connection, tenant: str) -> str:
        observed.append(connection)
        return capture_authority_snapshot_commitment(connection, tenant)

    monkeypatch.setattr(reader_module, "capture_authority_snapshot_commitment", capture)
    with workspace_snapshot(fixture.database) as physical:
        joined = read_materialized_scheduler(
            fixture.database, "tenant", fixture.journal, fixture.anchor
        )
        assert observed == [physical.connection]
        assert physical.connection.in_transaction
        assert joined == standalone


async def test_scheduler_reader_rejects_old_workspace_cut_after_reanchored_commit(
    tmp_path: Path,
) -> None:
    fixture = await materialized(tmp_path)
    with closing(sqlite3.connect(fixture.database)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        with workspace_snapshot(fixture.database) as physical:
            previous = physical.connection.execute(
                "SELECT head FROM main.tenant_heads WHERE tenant_id='tenant'"
            ).fetchone()
            writer.execute("UPDATE tenant_heads SET head=head+1 WHERE tenant_id='tenant'")
            writer.commit()
            # Compute the new anchor on the writer's current cut, not the ambient old cut.
            writer.execute("BEGIN")
            commitment = capture_authority_snapshot_commitment(writer, "tenant")
            writer.rollback()
            fixture.anchor.commit("tenant", commitment)
            assert (
                physical.connection.execute(
                    "SELECT head FROM main.tenant_heads WHERE tenant_id='tenant'"
                ).fetchone()
                == previous
            )
            with pytest.raises(SchedulerReadIntegrityError) as caught:
                read_materialized_scheduler(
                    fixture.database, "tenant", fixture.journal, fixture.anchor
                )
            assert "independent AMR anchor" in str(caught.value.__cause__)
        current = read_materialized_scheduler(
            fixture.database, "tenant", fixture.journal, fixture.anchor
        )
        assert previous is not None
        assert current.tenant_frontier == previous[0] + 1


@pytest.mark.parametrize("invalid", ["foreign_database", "ended_transaction", "replaced_database"])
async def test_scheduler_reader_rejects_invalid_ambient_cut(tmp_path: Path, invalid: str) -> None:
    fixture = await materialized(tmp_path)
    database = fixture.database
    with workspace_snapshot(database) as physical:
        if invalid == "foreign_database":
            database = tmp_path / "other.sqlite"
            database.write_bytes(fixture.database.read_bytes())
        elif invalid == "ended_transaction":
            physical.connection.rollback()
        else:
            replacement = tmp_path / "replacement.sqlite"
            replacement.write_bytes(database.read_bytes())
            replacement.replace(database)
        with pytest.raises(SchedulerReadIntegrityError) as caught:
            read_materialized_scheduler(database, "tenant", fixture.journal, fixture.anchor)
        expected = "ended early" if invalid == "ended_transaction" else "another physical"
        assert expected in str(caught.value.__cause__)


async def test_missing_database_is_not_created_by_scheduler_reader(tmp_path: Path) -> None:
    fixture = await materialized(tmp_path)
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(SchedulerReadIntegrityError) as caught:
        read_materialized_scheduler(missing, "tenant", fixture.journal, fixture.anchor)
    assert isinstance(caught.value.__cause__, FileNotFoundError)
    assert not missing.exists()


async def test_registered_actual_producer_history_is_source_admitted(tmp_path: Path) -> None:
    registration = current_scheduler_registration()
    assert isinstance(registration, SchedulerSourceRegistration)
    fixture = await materialized(tmp_path, registry=registration.reference)
    result = read_admitted_scheduler_startup(
        fixture.database, "tenant", fixture.journal, fixture.anchor
    )
    assert isinstance(result, AdmittedSchedulerStartup)
    assert result.registration == registration
    assert result.index.selected_record_ids == tuple(
        row.record_id for row in fixture.selected.prepared.request.complete_records
    )
    assert result.cut.historical_requests[0].preparation == fixture.original
    with workspace_snapshot(fixture.database):
        joined = read_admitted_scheduler_startup(
            fixture.database, "tenant", fixture.journal, fixture.anchor
        )
        assert joined == result


async def test_unknown_selected_registry_stays_unresolved(tmp_path: Path) -> None:
    fixture = await materialized(tmp_path)
    result = read_admitted_scheduler_startup(
        fixture.database, "tenant", fixture.journal, fixture.anchor
    )
    assert isinstance(result, SourceRegistrationUnresolved)
    assert "unknown selected" in result.reason


async def test_source_admission_rejects_retained_command_selected_result_substitution(
    tmp_path: Path,
) -> None:
    registration = current_scheduler_registration()
    assert isinstance(registration, SchedulerSourceRegistration)
    fixture = await materialized(tmp_path, "changed_definition", registry=registration.reference)
    with pytest.raises(ValueError, match="original historical request differs"):
        read_admitted_scheduler_startup(fixture.database, "tenant", fixture.journal, fixture.anchor)


@pytest.mark.parametrize(
    "name",
    [
        "scheduler_contracts.py",
        "scheduler_schema.py",
        "scheduler_source_manifest.json",
        "uv.lock",
        "libpython3.14.dylib",
        "typing_extensions.py",
        "INTERPRETER",
        "PYDANTIC_NATIVE",
    ],
)
def test_changed_actual_artifact_bytes_never_create_a_new_registration(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    original = Path.read_bytes
    reached: list[Path] = []
    if name == "INTERPRETER":
        name = Path(sys.executable).resolve().name
    elif name == "PYDANTIC_NATIVE":
        import pydantic_core._pydantic_core as native

        name = Path(native.__file__).name

    def read(path: Path) -> bytes:
        raw = original(path)
        if path.name == name:
            reached.append(path)
            return raw + b"\nchanged-actual-artifact"
        return raw

    monkeypatch.setattr(Path, "read_bytes", read)
    result = current_scheduler_registration()
    assert reached
    assert isinstance(result, SourceRegistrationUnresolved)


def test_runtime_inventory_covers_artifact_set_and_rejects_symbolic_substitution(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    package.mkdir()
    source = package / "core.py"
    source.write_bytes(b"original")
    original = _tree(package)
    extra = package / "extra.so"
    extra.write_bytes(b"native-code")
    assert _tree(package) != original
    extra.unlink()
    assert _tree(package) == original
    source.unlink()
    with pytest.raises(ValueError, match="missing runtime"):
        _tree(package)
    target = tmp_path / "target.py"
    target.write_bytes(b"original")
    source.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic"):
        _tree(package)
    root_alias = tmp_path / "alias"
    root_alias.symlink_to(package)
    with pytest.raises(ValueError, match="symbolic"):
        _tree(root_alias)


async def test_original_command_must_compile_to_exact_selected_historical_bytes(
    tmp_path: Path,
) -> None:
    positive = tmp_path / "positive"
    positive.mkdir()
    fixture = await materialized(positive)
    cut = read_materialized_scheduler(fixture.database, "tenant", fixture.journal, fixture.anchor)
    _require_historical_equality(cut.historical_requests[0], cut.selected[0])
    negative = tmp_path / "negative"
    negative.mkdir()
    changed = await materialized(negative, "changed_definition")
    altered_cut = read_materialized_scheduler(
        changed.database, "tenant", changed.journal, changed.anchor
    )
    with pytest.raises(ValueError, match="original historical request differs"):
        _require_historical_equality(altered_cut.historical_requests[0], altered_cut.selected[0])


@pytest.mark.parametrize(
    "name", ["chiplog.capabilities.agent_loop.scheduler_startup", "pydantic_core"]
)
@pytest.mark.parametrize("present_none", [False, True])
def test_source_profile_never_loads_a_missing_module(
    monkeypatch: pytest.MonkeyPatch, name: str, present_none: bool
) -> None:
    import sys

    assert isinstance(current_scheduler_registration(), SchedulerSourceRegistration)
    if present_none:
        monkeypatch.setitem(sys.modules, name, None)
    else:
        monkeypatch.delitem(sys.modules, name)
    result = current_scheduler_registration()
    assert isinstance(result, SourceRegistrationUnresolved)
    assert "not loaded" in result.reason
    assert sys.modules.get(name) is None
    assert (name in sys.modules) == present_none
