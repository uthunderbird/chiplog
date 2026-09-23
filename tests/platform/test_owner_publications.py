"""Real writer/journal mechanics with a deliberately inert semantic-authority fixture.

These histories do not prove canonical owner routing or authentication issuance.
"""

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.platform._owner_publication_contracts import (
    ExactReplayQuery,
    InvocationProofRef,
    JournalSelectedPublication,
    PublicationRejected,
    RegisteredPublication,
    SingleOwnerBatch,
)
from chiplog.platform._sqlite import (
    EventAppender,
    FenceAdvanceCommand,
    PhysicalPublicationCommand,
    PublicationResult,
    SQLiteMaterializer,
)
from chiplog.platform.authority_reads import capture_authority_storage_state
from chiplog.platform.owner_decision_journal import (
    IndependentOwnerDecisionJournal,
    OwnerJournalIntegrityError,
)
from chiplog.platform.owner_publications import (
    BrokerPublicationCoordinator,
    OwnerPublicationUncertain,
    PreparedOwnerPublication,
    SelectedOwnerDecision,
    source_commands,
)
from chiplog.platform.workspace_snapshot import read_connection


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def invocation() -> InvocationProofRef:
    return InvocationProofRef(
        issuance_id="fixture-issued",
        issuance_fingerprint=digest(b"issued"),
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
        operation_subject="command",
    )


def request(commitment: str) -> SingleOwnerBatch:
    return SingleOwnerBatch.model_validate(
        {
            "operation": "effects.authorize",
            "identity": {
                "tenant_id": "tenant",
                "command_id": "command",
                "command_fingerprint": digest(b"command"),
                "canonicalization_version": "chiplog.owner-publication.v1",
            },
            "authentication": {
                "kind": "WORKER",
                "invocation": invocation(),
                "applicability_schema": "fixture.v1",
                "applicability_bytes": b"fixture-non-authoritative-fence",
                "applicability_fingerprint": digest(b"fixture-non-authoritative-fence"),
            },
            "expected": {
                "tenant_id": "tenant",
                "tenant_frontier": 0,
                "expected_materialization_commitment": commitment,
                "registry_head": "fixture-registry",
                "registry_fingerprint": digest(b"registry"),
                "ordered_heads": (),
                "complete_manifest_fingerprint": digest(b"empty"),
            },
            "command": {
                "owner": "effects",
                "schema_id": "fixture.command.v1",
                "canonical_bytes": b"command",
                "fingerprint": digest(b"command"),
            },
            "complete_records": tuple(
                {
                    "owner": "effects",
                    "record_kind": "fixture",
                    "record_id": name,
                    "schema_id": "fixture.record.v1",
                    "canonical_bytes": name.encode(),
                    "fingerprint": digest(name.encode()),
                }
                for name in ("record1", "record2")
            ),
            "complete_batch_fingerprint": digest(b"complete fixture batch"),
        }
    )


class FixtureJournal(IndependentOwnerDecisionJournal):
    """Real adapter with only a post-selection crash injection seam."""

    def __init__(self, path: Path) -> None:
        self.raw = IndependentTenantDecisionJournal(path)
        self.fail_after_select = False
        super().__init__(self.raw, "tenant")

    def select(self, prepared: PreparedOwnerPublication, commitment: str) -> SelectedOwnerDecision:
        selected = super().select(prepared, commitment)
        if self.fail_after_select:
            raise RuntimeError("crash after selected decision before SQLite commit")
        return selected


class FixtureAuthority:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.prepare_calls = 0
        self.force_stale = False

    def authenticate_replay(self, query: ExactReplayQuery) -> PublicationRejected | None:
        # Real issuance lives in canonical composition; this exact fixture is
        # intentionally not evidence that a string authenticates a real worker.
        if query.current_invocation != invocation():
            return PublicationRejected(
                kind="DENIED",
                tenant_id=query.identity.tenant_id,
                command_id=query.identity.command_id,
                reason="wrong fixture invocation",
            )
        return None

    async def prepare(self, value: RegisteredPublication) -> PreparedOwnerPublication:
        self.prepare_calls += 1
        return PreparedOwnerPublication(
            value,
            "prepared-fixture",
            "fence",
            0,
            value.expected.expected_materialization_commitment,
        )

    def check_prepared(self, value: PreparedOwnerPublication) -> Literal["STALE"] | None:
        actual, _ = capture_authority_storage_state(self.path)
        if self.force_stale or actual != value.predecessor_commitment:
            return "STALE"
        return None

    def materialization_state(
        self, decision: SelectedOwnerDecision
    ) -> Literal["ABSENT", "COMPLETE", "CONFLICT"]:
        expected = decision.prepared.request
        with read_connection(self.path) as connection:
            rows = connection.execute(
                "SELECT record_id, owner, schema_id, canonical_bytes, commit_sequence FROM records "
                "WHERE tenant_id=? ORDER BY record_id",
                ("tenant",),
            ).fetchall()
        if not rows:
            return "ABSENT"
        wanted = sorted(
            (
                row.record_id,
                row.owner,
                row.schema_id,
                row.canonical_bytes,
                decision.tenant_commit_sequence,
            )
            for row in expected.complete_records
        )
        return "COMPLETE" if rows == wanted else "CONFLICT"

    def check_selected_predecessor(self, decision: SelectedOwnerDecision) -> bool:
        return (
            capture_authority_storage_state(self.path)[0]
            == decision.prepared.predecessor_commitment
        )


class LostAckAppender(EventAppender):
    async def submit(self, command: PhysicalPublicationCommand) -> PublicationResult:
        return await super().submit(replace(command, fault="after_commit"))


def test_independent_owner_journal_reopens_exact_binary_decision(tmp_path: Path) -> None:
    path = tmp_path / "journal"
    journal = FixtureJournal(path)
    empty = journal.snapshot()
    assert empty.tenant_id == "tenant" and empty.head is None
    assert empty.decisions == () and empty.materialized_command_ids == frozenset()
    proposed = request(digest(b"predecessor"))
    binary = proposed.complete_records[0].model_copy(
        update={"canonical_bytes": bytes(range(256)), "fingerprint": digest(bytes(range(256)))}
    )
    proposed = proposed.model_copy(update={"complete_records": (binary,)})
    prepared = PreparedOwnerPublication(proposed, "issuance", "fence", 0, digest(b"predecessor"))
    selected = journal.select(prepared, digest(b"result"))
    pending = journal.snapshot()
    assert pending.head == selected.decision_head and pending.decisions == (selected,)
    assert pending.materialized_command_ids == frozenset()
    restarted = FixtureJournal(path)
    assert restarted.lookup("tenant", "command") == selected
    assert restarted.select(prepared, digest(b"result")) == selected
    restarted.materialized(selected)
    restarted.materialized(selected)
    complete = restarted.snapshot()
    assert complete.decisions == (selected,)
    assert complete.materialized_command_ids == frozenset({"command"})
    assert complete.head == restarted.raw.entries()[-1][0] != pending.head
    assert pending.materialized_command_ids == frozenset() and empty.decisions == ()
    assert len(restarted.raw.entries()) == 2
    with pytest.raises(OwnerJournalIntegrityError, match=r"select.*tenant.*command") as caught:
        restarted.select(prepared, digest(b"changed result"))
    assert caught.value.__cause__ is not None
    assert len(restarted.raw.entries()) == 2


@pytest.mark.parametrize("variant", ["plan", "call", "call_planning", "delivery"])
def test_owner_journal_roundtrips_complete_atomic_publication_variants(
    tmp_path: Path, variant: str
) -> None:
    original = request(digest(b"before"))
    data = original.model_dump(exclude={"kind", "operation", "command"})
    command = original.command
    loop = command.model_copy(update={"owner": "agent_loop"})
    planning = command.model_copy(update={"owner": "planning"})
    if variant == "plan":
        data.update(
            kind="PLAN_EFFECT_ATOMIC",
            operation="effects.publish_plan_effect",
            planning_command=planning,
            effects_command=command,
        )
    elif variant.startswith("call"):
        participant = (
            {"kind": "PLANNING_PUBLICATION", "command": planning}
            if variant == "call_planning"
            else {"kind": "NOT_APPLICABLE"}
        )
        data.update(
            kind="CALL_EFFECT_ATOMIC",
            operation="effects.accept_call",
            loop_command=loop,
            effects_command=command,
            planning=participant,
        )
        data["complete_records"] = (
            *original.complete_records,
            original.complete_records[0].model_copy(
                update={"record_id": "record3", "owner": "agent_loop"}
            ),
        )
    else:
        data.update(
            kind="COMPLETE_DELIVERY_ATOMIC",
            operation="agent_loop.complete_acceptance",
            loop_command=loop,
            prepared_effects_commands=(command,),
        )
    proposed: RegisteredPublication = TypeAdapter(RegisteredPublication).validate_python(data)
    path = tmp_path / "journal"
    journal = FixtureJournal(path)
    prepared = PreparedOwnerPublication(proposed, "issued", "fence", 0, digest(b"before"))
    selected = journal.select(prepared, digest(b"after"))
    reopened = FixtureJournal(path).lookup("tenant", "command")
    assert reopened == selected
    assert reopened is not None
    assert source_commands(reopened.prepared.request) == source_commands(proposed)


@pytest.mark.parametrize(
    "mutation",
    [
        "unknown_schema",
        "duplicate",
        "orphan",
        "rival",
        "noncanonical",
        "record_digest",
        "command_digest",
    ],
)
def test_authenticated_bad_journal_entry_never_yields_partial_lookup(
    tmp_path: Path, mutation: str
) -> None:
    journal = FixtureJournal(tmp_path / "journal")
    prepared = PreparedOwnerPublication(
        request(digest(b"before")), "issuance", "fence", 0, digest(b"before")
    )
    selected = journal.select(prepared, digest(b"after"))
    original = journal.raw.entries()[0][2]
    if mutation in {
        "unknown_schema",
        "duplicate",
        "noncanonical",
        "record_digest",
        "command_digest",
    }:
        value = json.loads(original)
        if mutation == "unknown_schema":
            value["schema_id"] = "unknown"
        if mutation == "record_digest":
            value["request"]["complete_records"][0]["fingerprint"] = digest(b"rival")
        if mutation == "command_digest":
            value["request"]["command"]["fingerprint"] = digest(b"rival")
    else:
        value = {
            "kind": "MATERIALIZED",
            "schema_id": "chiplog.owner-decision.v1",
            "tenant_id": "tenant",
            "command_id": "absent" if mutation == "orphan" else "command",
            "decision_id": selected.decision_id,
            "decision_fingerprint": digest(b"rival"),
            "resulting_commitment": selected.resulting_commitment,
        }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    if mutation == "noncanonical":
        raw += b" "
    bad_id = journal.raw.append(raw, journal.raw.entries()[-1][0])
    with pytest.raises(OwnerJournalIntegrityError, match=bad_id) as caught:
        journal.lookup("tenant", "command")
    assert caught.value.__cause__ is not None
    with pytest.raises(OwnerJournalIntegrityError, match=f"snapshot.*tenant.*{bad_id}") as caught:
        journal.snapshot()
    assert caught.value.__cause__ is not None


def test_owner_journal_rejects_foreign_tenant_before_append(tmp_path: Path) -> None:
    journal = FixtureJournal(tmp_path / "journal")
    proposed = request(digest(b"before"))
    proposed = proposed.model_copy(
        update={"identity": proposed.identity.model_copy(update={"tenant_id": "other"})}
    )
    with pytest.raises(OwnerJournalIntegrityError):
        journal.select(
            PreparedOwnerPublication(proposed, "issuance", "fence", 0, digest(b"before")),
            digest(b"after"),
        )
    assert journal.raw.entries() == ()


def test_materialized_decision_requires_monotone_consistent_successor(tmp_path: Path) -> None:
    journal = FixtureJournal(tmp_path / "journal")
    original = request(digest(b"before"))
    prepared = PreparedOwnerPublication(original, "issuance", "fence", 0, digest(b"before"))
    selected = journal.select(prepared, digest(b"after"))
    journal.materialized(selected)
    successor = original.model_copy(
        update={"identity": original.identity.model_copy(update={"command_id": "successor"})}
    )
    with pytest.raises(OwnerJournalIntegrityError):
        journal.select(replace(prepared, request=successor), digest(b"rival"))
    successor = successor.model_copy(
        update={
            "expected": successor.expected.model_copy(
                update={
                    "tenant_frontier": 1,
                    "expected_materialization_commitment": digest(b"wrong predecessor"),
                }
            )
        }
    )
    with pytest.raises(OwnerJournalIntegrityError):
        journal.select(
            replace(
                prepared, request=successor, predecessor_commitment=digest(b"wrong predecessor")
            ),
            digest(b"next"),
        )
    successor = successor.model_copy(
        update={
            "expected": successor.expected.model_copy(
                update={"expected_materialization_commitment": digest(b"after")}
            )
        }
    )
    result = journal.select(
        replace(prepared, request=successor, predecessor_commitment=digest(b"after")),
        digest(b"next"),
    )
    assert result.tenant_commit_sequence == 2
    assert len(journal.raw.entries()) == 3
    snapshot = journal.snapshot()
    assert snapshot.decisions == (selected, result)
    assert snapshot.materialized_command_ids == frozenset({"command"})
    assert snapshot.head == result.decision_head


def test_independent_journal_append_rival_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = FixtureJournal(tmp_path / "journal")
    prepared = PreparedOwnerPublication(
        request(digest(b"before")), "issuance", "fence", 0, digest(b"before")
    )
    append = journal.raw.append

    def interpose(payload: bytes, predecessor: str | None) -> str:
        rival = json.loads(payload)
        rival["request"]["identity"]["command_id"] = "rival"
        append(json.dumps(rival, sort_keys=True, separators=(",", ":")).encode(), predecessor)
        return append(payload, predecessor)

    monkeypatch.setattr(journal.raw, "append", interpose)
    with pytest.raises(OwnerJournalIntegrityError, match="select") as caught:
        journal.select(prepared, digest(b"after"))
    assert caught.value.__cause__ is not None
    assert journal.lookup("tenant", "command") is None
    assert journal.lookup("tenant", "rival") is not None
    assert len(journal.raw.entries()) == 1


@pytest.mark.parametrize("stale", [False, True])
async def test_exact_decision_replay_never_reenters_owner_preparation(
    tmp_path: Path, stale: bool
) -> None:
    path = tmp_path / "store.sqlite"
    with SQLiteMaterializer(path, record_contracts={"effects": "fixture.record.v1"}) as store:
        async with EventAppender(store, capacity=4) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
            authority, journal = FixtureAuthority(path), FixtureJournal(tmp_path / "journal")
            coordinator = BrokerPublicationCoordinator(appender, authority, journal)
            proposed = request(capture_authority_storage_state(path)[0])
            authority.force_stale = stale
            result = await coordinator.commit(proposed)
            if stale:
                assert result.kind == "STALE"
                assert store.durable_records() == () and journal.raw.entries() == ()
            else:
                assert result.kind == "COMMITTED"
                authority.force_stale = True  # Historical exact replay does not recheck old lease.
                replayed = await coordinator.commit(proposed)
                assert replayed.kind == "EXACT_REPLAY" and authority.prepare_calls == 1
                changed = proposed.model_copy(
                    update={
                        "command": proposed.command.model_copy(update={"canonical_bytes": b"rival"})
                    }
                )
                assert (await coordinator.commit(changed)).kind == "CONFLICT"


async def test_selected_decision_survives_sqlite_rollback_without_semantic_retry(
    tmp_path: Path,
) -> None:
    path = tmp_path / "store.sqlite"
    with SQLiteMaterializer(path, record_contracts={"effects": "fixture.record.v1"}) as store:
        async with EventAppender(store, capacity=4) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
            authority, journal = FixtureAuthority(path), FixtureJournal(tmp_path / "journal")
            coordinator = BrokerPublicationCoordinator(appender, authority, journal)
            proposed = request(capture_authority_storage_state(path)[0])
            journal.fail_after_select = True
            with pytest.raises(OwnerPublicationUncertain):
                await coordinator.commit(proposed)
            assert store.durable_records() == ()
            assert journal.lookup("tenant", "command") is not None
            pending = journal.snapshot()
            assert len(pending.decisions) == 1 and not pending.materialized_command_ids
            assert (await coordinator.commit(proposed)).kind == "HOLD"
            assert authority.prepare_calls == 1
            rival = proposed.model_copy(
                update={"identity": proposed.identity.model_copy(update={"command_id": "rival"})}
            )
            assert (await coordinator.commit(rival)).kind == "HOLD"
            assert journal.lookup("tenant", "rival") is None
            assert len(journal.raw.entries()) == 1 and store.durable_records() == ()
            authority.force_stale = True
            recovered = await coordinator.recover_selected("tenant", "command")
            assert recovered.kind == "EXACT_REPLAY" and authority.prepare_calls == 2
            assert len(store.durable_records()) == 2
            recovered_cut = journal.snapshot()
            assert recovered_cut.decisions == pending.decisions
            assert recovered_cut.materialized_command_ids == frozenset({"command"})
            assert recovered_cut.head != pending.head


async def test_lost_ack_returns_exact_journal_result_after_reconstruction(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"
    with SQLiteMaterializer(path, record_contracts={"effects": "fixture.record.v1"}) as store:
        async with LostAckAppender(store, capacity=4) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
            authority, journal = FixtureAuthority(path), FixtureJournal(tmp_path / "journal")
            coordinator = BrokerPublicationCoordinator(appender, authority, journal)
            proposed = request(capture_authority_storage_state(path)[0])
            with pytest.raises(OwnerPublicationUncertain):
                await coordinator.commit(proposed)
            restarted = BrokerPublicationCoordinator(
                appender, FixtureAuthority(path), FixtureJournal(tmp_path / "journal")
            )
            result = restarted.lookup_exact(
                ExactReplayQuery(
                    identity=proposed.identity,
                    operation=proposed.operation,
                    current_invocation=invocation(),
                    original_commands=source_commands(proposed),
                )
            )
            assert isinstance(result, JournalSelectedPublication) and result.kind == "EXACT_REPLAY"
            assert len(store.durable_records()) == 2


async def test_concurrent_identical_prepare_observes_exact_journal_winner(tmp_path: Path) -> None:
    path = tmp_path / "store.sqlite"

    class BarrierAuthority(FixtureAuthority):
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self.ready = asyncio.Event()

        async def prepare(self, value: RegisteredPublication) -> PreparedOwnerPublication:
            prepared = await super().prepare(value)
            if self.prepare_calls == 2:
                self.ready.set()
            await self.ready.wait()
            return prepared

    with SQLiteMaterializer(path, record_contracts={"effects": "fixture.record.v1"}) as store:
        async with EventAppender(store, capacity=4) as appender:
            await appender.advance_fence(FenceAdvanceCommand("tenant", "fence", 0))
            authority, journal = BarrierAuthority(path), FixtureJournal(tmp_path / "journal")
            coordinator = BrokerPublicationCoordinator(appender, authority, journal)
            proposed = request(capture_authority_storage_state(path)[0])
            results = await asyncio.gather(
                coordinator.commit(proposed), coordinator.commit(proposed)
            )
            assert {item.kind for item in results} == {"COMMITTED", "EXACT_REPLAY"}
            assert len(store.durable_records()) == 2 and len(journal.raw.entries()) == 2
