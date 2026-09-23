"""Mechanical query bridge checks; broker authentication and writer checks are separate."""

import hashlib
import json
from dataclasses import fields, replace

import pytest

from chiplog.adapters.driven.effects_broker import (
    EffectsBatchContext,
    EffectsIntegrityError,
    EffectsReplaySelected,
)
from chiplog.adapters.driven.effects_queries import (
    BoundEffectsQueries,
    EffectsCutSource,
    EffectsObservedCut,
    StoredEffectRow,
)
from chiplog.capabilities.effects.application import prepare_transition
from chiplog.capabilities.effects.contracts import (
    AuthorizeDispatchCommand,
    BeforeSendDispositionCommand,
    CommandIdentity,
    CurrentEffectInputs,
    EffectCommand,
    EffectPreparationRequest,
    EffectRecord,
    EffectStoreSnapshot,
    ExactHead,
    PreparedEffectPublication,
    PublishPlanEffectCommand,
)
from chiplog.capabilities.effects.denial import prepare_denial
from chiplog.capabilities.effects.fences import NonSchedulerFence, NotApplicable
from chiplog.platform._owner_publication_contracts import (
    AuthoritativeReadManifest,
    ExactReplayQuery,
    InvocationProofRef,
    OwnerCommandBytes,
    OwnerRecordBytes,
    PublicationIdentity,
    WorkerAuthentication,
)
from tests.support.effects import head, intent
from tests.support.effects_denial import make_denial_request


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("alias_predecessor", [False, True])
def test_snapshot_reads_denial_history_and_rejects_rehashed_predecessor_alias(
    alias_predecessor: bool,
) -> None:
    queries, _, _ = _fixture()
    original = EffectRecord.model_validate_json(queries.cut.rows[0].record.canonical_bytes)
    snapshot = original.snapshot
    reference = ExactHead(
        subject_id=snapshot.intent.intent_id,
        head=snapshot.intent.intent_id + "/" + snapshot.intent.fingerprint,
        fingerprint=snapshot.intent.fingerprint,
    )
    template = make_denial_request()
    authority = template.command.authority.model_copy(
        update={
            "intent": reference,
            "expected_attempt": snapshot.attempt,
        }
    )
    command = template.command.model_copy(
        update={
            "intent": reference,
            "expected_attempt": snapshot.attempt,
            "authority": authority,
        }
    )
    request = template.model_copy(
        update={
            "command": command,
            "current": template.current.model_copy(update={"authority": authority}),
            "expected": EffectStoreSnapshot(tenant_id="tenant", tenant_head=1, records=(original,)),
        }
    )
    record = prepare_denial(request).record
    if alias_predecessor:
        command = command.model_copy(
            update={
                "expected_attempt": head("alias"),
                "authority": authority.model_copy(update={"expected_attempt": head("alias")}),
            }
        )
        record = record.model_copy(update={"source_command": command.canonical_bytes()})
        body = {
            "command": record.command.model_dump(mode="json"),
            "predecessor": original.record.model_dump(mode="json"),
            "kind": record.kind,
            "snapshot": record.snapshot.model_dump(mode="json"),
            "source_command": record.source_command.hex(),
        }
        digest = _hash(
            json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        )
        subject = "effects/" + record.command.command_id
        record = record.model_copy(
            update={
                "record": ExactHead(
                    subject_id=subject,
                    head=subject + "/" + digest,
                    fingerprint=digest,
                )
            }
        )
    raw = record.canonical_bytes()
    row = StoredEffectRow(
        2,
        0,
        (record.record.head,),
        OwnerRecordBytes(
            owner="effects",
            record_kind="effects." + record.kind,
            record_id=record.record.head,
            schema_id="chiplog.effects.record.v1",
            canonical_bytes=raw,
            fingerprint=_hash(raw),
        ),
    )
    queries = replace(queries, cut=replace(queries.cut, rows=(queries.cut.rows[0], row)))
    if alias_predecessor:
        with pytest.raises(EffectsIntegrityError) as error:
            queries.effects_snapshot()
        assert "predecessor attempt" in str(error.value.__cause__)
    else:
        assert queries.effects_snapshot().records == (original, record)


def _fixture(
    name: str = "first",
) -> tuple[BoundEffectsQueries, BeforeSendDispositionCommand, PreparedEffectPublication]:
    original = intent().model_copy(
        update={
            "intent_id": name,
            "payload": b"\xff\x00payload",
            "effect_fingerprint": _hash(b"\xff\x00payload"),
        }
    )
    data = original.model_dump(mode="json")
    del data["fingerprint"]
    original = original.model_copy(
        update={
            "fingerprint": _hash(
                json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            )
        }
    )
    reference = ExactHead(
        subject_id=name, head=name + "/" + original.fingerprint, fingerprint=original.fingerprint
    )
    fence = NonSchedulerFence(
        lineage=NotApplicable(),
        physical_root=NotApplicable(),
        lease=NotApplicable(),
        clock_proof=NotApplicable(),
        run_id="run",
        run_head="run-head",
        worker_session_id="worker",
        runtime_generation="generation",
    )
    store = EffectStoreSnapshot(tenant_id="tenant", tenant_head=0, records=())

    def identity(suffix: str) -> CommandIdentity:
        return CommandIdentity(
            command_id=name + suffix,
            fingerprint=_hash(suffix.encode()),
            expected_tenant_head=store.tenant_head,
        )

    def observe(command: EffectCommand) -> CurrentEffectInputs:
        return CurrentEffectInputs(
            command_id=command.identity.command_id,
            command_fingerprint=command.identity.fingerprint,
            store_frontier=store.tenant_head,
            observed_time_ns=10,
            authority=original.authority,
            supported_semantics=original.semantics,
            fence=fence,
            authority_decision=head("decision"),
            blocking_effect_heads=(),
            current_original_ambiguity_heads=(),
            initialized_call=None,
            active_run_head="run-head",
            independently_verified_safe_proof=None,
            authenticated_evidence=None,
            original_reducer_semantics=original.semantics,
            authorized_reconciler=None,
        )

    create = PublishPlanEffectCommand(
        identity=identity("-create"),
        intent=original,
        planning_publication=head("plan"),
        planning_owner_bytes=b"plan",
        complete_publication_manifest=(head("plan"), reference),
        fence=fence,
    )
    first = prepare_transition(create, store, observe(create)).record
    store = store.model_copy(update={"tenant_head": 1, "records": (first,)})
    authorize = AuthorizeDispatchCommand(
        identity=identity("-authorize"),
        intent=reference,
        expected_attempt=first.snapshot.attempt,
        semantics=original.semantics,
        current_authority=original.authority,
        fence=fence,
    )
    second = prepare_transition(authorize, store, observe(authorize)).record
    store = store.model_copy(update={"tenant_head": 2, "records": (first, second)})
    hold = BeforeSendDispositionCommand(
        identity=identity("-hold"),
        intent=reference,
        expected_attempt=second.snapshot.attempt,
        disposition="HELD_BEFORE_SEND",
        current_authority=original.authority,
        decision_evidence=head("decision"),
        fence=fence,
    )
    current = observe(hold)
    prepared = prepare_transition(hold, store, current)
    invocation = InvocationProofRef(
        issuance_id="issuance",
        issuance_fingerprint="a" * 64,
        broker_epoch="epoch",
        broker_session="session",
        runtime_generation="generation",
        operation_subject="subject",
    )
    preparation = EffectPreparationRequest(
        operation="effects.before_send",
        command_bytes=hold.canonical_bytes(),
        expected=store,
        current=current,
    )
    context = EffectsBatchContext(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id=hold.identity.command_id,
            command_fingerprint="f" * 64,
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        preparation=preparation,
        prepared=prepared,
        expected=AuthoritativeReadManifest(
            tenant_id="tenant",
            tenant_frontier=2,
            expected_materialization_commitment="a" * 64,
            registry_head="registry",
            registry_fingerprint="b" * 64,
            ordered_heads=(),
            complete_manifest_fingerprint="c" * 64,
        ),
        authentication=WorkerAuthentication(
            invocation=invocation,
            applicability_schema="fence",
            applicability_bytes=b"fixture",
            applicability_fingerprint=_hash(b"fixture"),
        ),
        planning_command=None,
        loop_command=None,
        companion_records=(),
    )

    def row(record: EffectRecord, sequence: int) -> StoredEffectRow:
        return StoredEffectRow(
            sequence,
            1,
            ("z-companion", record.record.head, "a-companion"),
            OwnerRecordBytes(
                owner="effects",
                record_kind="effects." + record.kind,
                record_id=record.record.head,
                schema_id="chiplog.effects.record.v1",
                canonical_bytes=record.canonical_bytes(),
                fingerprint=_hash(record.canonical_bytes()),
            ),
        )

    cut = EffectsObservedCut(
        hold.canonical_bytes(),
        "tenant",
        2,
        "a" * 64,
        (row(first, 1), row(second, 2)),
        current,
        context,
    )
    replay = ExactReplayQuery(
        identity=PublicationIdentity(
            tenant_id="tenant",
            command_id=hold.identity.command_id,
            command_fingerprint=hold.identity.fingerprint,
            canonicalization_version="chiplog.owner-publication.v1",
        ),
        operation="effects.before_send",
        current_invocation=invocation,
        original_commands=(
            OwnerCommandBytes(
                owner="effects",
                schema_id="chiplog.effects.prepare.v1",
                canonical_bytes=preparation.canonical_bytes(),
                fingerprint=_hash(preparation.canonical_bytes()),
            ),
        ),
    )
    return (
        BoundEffectsQueries("tenant", EffectsReplaySelected(replay, preparation, prepared), cut),
        hold,
        prepared,
    )


def test_per_invocation_cut_exposes_exact_immutable_read_linkage() -> None:
    assert vars(EffectsObservedCut)["__dataclass_params__"].frozen
    assert vars(BoundEffectsQueries)["__dataclass_params__"].frozen
    assert {field.name for field in fields(EffectsObservedCut)} == {
        "command_bytes",
        "tenant_id",
        "tenant_frontier",
        "materialization_commitment",
        "rows",
        "current",
        "context",
    }
    assert {field.name for field in fields(StoredEffectRow)} == {
        "commit_sequence",
        "publication_ordinal",
        "batch_record_ids",
        "record",
    }
    assert callable(EffectsCutSource.read_effect_cut)


def test_exact_owner_history_and_context_preserve_bytes_and_manifest_order() -> None:
    bound, command, prepared = _fixture()
    snapshot = bound.effects_snapshot()
    assert snapshot == prepared.expected_store
    assert snapshot.records[0].snapshot.intent.payload == b"\xff\x00payload"
    assert bound.observe(command, snapshot) == bound.cut.current
    assert bound.replay_observation(command) == bound.replay
    assert bound.publication_context(prepared) == bound.cut.context


@pytest.mark.parametrize(
    "mutation",
    [
        "digest",
        "alias",
        "schema",
        "owner",
        "kind",
        "bytes",
        "logical_digest",
        "duplicate",
        "reorder",
        "omit_predecessor",
        "ordinal",
        "manifest",
        "frontier",
        "bool_sequence",
    ],
)
def test_corrupted_rows_never_return_a_partial_snapshot(mutation: str) -> None:
    bound, _, _ = _fixture()
    first, second = bound.cut.rows
    rows: tuple[StoredEffectRow, ...] = (first, second)
    changes: dict[str, dict[str, str | bytes]] = {
        "digest": {"fingerprint": "0" * 64},
        "alias": {"record_id": "alias"},
        "schema": {"schema_id": "unknown"},
        "owner": {"owner": "planning"},
        "kind": {"record_kind": "effects.UNKNOWN"},
        "bytes": {"canonical_bytes": b" " + second.record.canonical_bytes},
    }
    if mutation in changes:
        rows = (first, replace(second, record=second.record.model_copy(update=changes[mutation])))
    elif mutation == "logical_digest":
        record = EffectRecord.model_validate_json(second.record.canonical_bytes)
        changed = record.model_copy(
            update={"record": record.record.model_copy(update={"fingerprint": "forged"})}
        )
        rows = (
            first,
            replace(
                second,
                record=second.record.model_copy(
                    update={
                        "canonical_bytes": changed.canonical_bytes(),
                        "fingerprint": _hash(changed.canonical_bytes()),
                    }
                ),
            ),
        )
    elif mutation == "duplicate":
        rows = (first, second, second)
    elif mutation == "reorder":
        rows = (second, first)
    elif mutation == "omit_predecessor":
        rows = (second,)
    elif mutation == "ordinal":
        rows = (first, replace(second, publication_ordinal=0))
    elif mutation == "manifest":
        rows = (first, replace(second, batch_record_ids=(second.record.record_id,) * 3))
    elif mutation == "frontier":
        rows = (first, replace(second, commit_sequence=3))
    else:
        rows = (replace(first, commit_sequence=True), second)
    with pytest.raises(
        EffectsIntegrityError, match=r"operation=effects\.snapshot tenant=tenant"
    ) as caught:
        replace(bound, cut=replace(bound.cut, rows=rows)).effects_snapshot()
    assert caught.value.__cause__ is not None


def test_immutable_cut_rejects_cross_command_snapshot_and_context_substitution() -> None:
    bound, command, prepared = _fixture()
    with pytest.raises(ValueError, match="command differs"):
        bound.observe(
            command.model_copy(update={"decision_evidence": head("other")}), prepared.expected_store
        )
    with pytest.raises(ValueError, match="snapshot differs"):
        bound.observe(command, prepared.expected_store.model_copy(update={"records": ()}))
    with pytest.raises(ValueError, match="prepared publication differs"):
        bound.publication_context(
            prepared.model_copy(
                update={
                    "expected_store": prepared.expected_store.model_copy(update={"tenant_head": 1})
                }
            )
        )
    with pytest.raises(EffectsIntegrityError, match="exact cut"):
        replace(
            bound, cut=replace(bound.cut, materialization_commitment="d" * 64)
        ).effects_snapshot()
    assert isinstance(bound.replay, EffectsReplaySelected)
    changed_query = bound.replay.query.model_copy(update={"original_commands": ()})
    with pytest.raises(ValueError, match="original command"):
        replace(bound, replay=replace(bound.replay, query=changed_query)).replay_observation(
            command
        )


def test_unrelated_intents_have_separate_predecessor_chains() -> None:
    first, _, _ = _fixture("one")
    second, _, _ = _fixture("two")
    rows: tuple[StoredEffectRow, ...] = (first.cut.rows[0], second.cut.rows[0])
    manifest = tuple(row.record.record_id for row in rows)
    rows = tuple(
        replace(row, publication_ordinal=index, batch_record_ids=manifest)
        for index, row in enumerate(rows)
    )
    assert len(replace(first, cut=replace(first.cut, rows=rows)).effects_snapshot().records) == 2


def test_two_owner_candidates_from_one_predecessor_cannot_form_a_linear_history() -> None:
    bound, _, prepared = _fixture()
    first, second = bound.cut.rows
    original = AuthorizeDispatchCommand.model_validate_json(
        prepared.expected_store.records[1].source_command
    )
    rival = original.model_copy(
        update={
            "identity": original.identity.model_copy(
                update={
                    "command_id": "rival-authorize",
                    "fingerprint": _hash(b"rival-authorize"),
                }
            ),
        }
    )
    predecessor = prepared.expected_store.model_copy(
        update={
            "tenant_head": 1,
            "records": prepared.expected_store.records[:1],
        }
    )
    current = bound.cut.current.model_copy(
        update={
            "command_id": rival.identity.command_id,
            "command_fingerprint": rival.identity.fingerprint,
            "store_frontier": 1,
        }
    )
    record = prepare_transition(rival, predecessor, current).record
    raw = OwnerRecordBytes(
        owner="effects",
        record_kind="effects." + record.kind,
        record_id=record.record.head,
        schema_id="chiplog.effects.record.v1",
        canonical_bytes=record.canonical_bytes(),
        fingerprint=_hash(record.canonical_bytes()),
    )
    manifest = (second.record.record_id, raw.record_id)
    rows = (
        first,
        replace(second, publication_ordinal=0, batch_record_ids=manifest),
        StoredEffectRow(2, 1, manifest, raw),
    )
    with pytest.raises(EffectsIntegrityError, match="rival intent chain"):
        replace(bound, cut=replace(bound.cut, rows=rows)).effects_snapshot()


def test_context_cannot_use_foreign_tenant_or_different_observation_frontier() -> None:
    bound, _, _ = _fixture()
    for current in (
        bound.cut.current.model_copy(update={"store_frontier": 1}),
        bound.cut.current.model_copy(
            update={
                "authority": bound.cut.current.authority.model_copy(
                    update={"tenant_id": "foreign"}
                ),
            }
        ),
    ):
        with pytest.raises(EffectsIntegrityError, match="exact cut"):
            replace(bound, cut=replace(bound.cut, current=current)).effects_snapshot()


async def test_first_lookup_and_replay_retain_full_preparation_without_current_reads() -> None:
    from chiplog.adapters.driven.effects_broker import BrokerEffectsStore, EffectsReplayAbsent
    from chiplog.adapters.driven.effects_queries import ReplayEffectsQueries
    from chiplog.platform._owner_publication_contracts import (
        JournalSelectedPublication,
        NoSelectedDecision,
        RegisteredPublication,
        SingleOwnerBatch,
    )

    bound, command, prepared = _fixture()
    assert isinstance(bound.replay, EffectsReplaySelected)
    absent = EffectsReplayAbsent(
        command.canonical_bytes(),
        NoSelectedDecision(tenant_id="tenant", command_id=command.identity.command_id),
    )

    class Broker:
        request: SingleOwnerBatch | None = None
        selected: JournalSelectedPublication | None = None

        def lookup_exact(self, query: ExactReplayQuery) -> JournalSelectedPublication:
            assert self.request is not None and self.selected is not None
            assert query.identity == self.request.identity
            assert query.original_commands == (self.request.command,)
            return self.selected.model_copy(update={"kind": "EXACT_REPLAY"})

        async def commit(self, request: RegisteredPublication) -> JournalSelectedPublication:
            assert isinstance(request, SingleOwnerBatch)
            self.request = request
            self.selected = JournalSelectedPublication(
                kind="COMMITTED",
                tenant_id="tenant",
                command_id=command.identity.command_id,
                decision_id="decision",
                decision_head="head",
                decision_fingerprint="a" * 64,
                predecessor_commitment="b" * 64,
                resulting_commitment="c" * 64,
                tenant_commit_sequence=3,
                complete_records=request.complete_records,
            )
            return self.selected

    broker = Broker()
    # No current cut exists on the first authenticated no-selection path.
    replay_only = ReplayEffectsQueries("tenant", absent)
    store = BrokerEffectsStore(broker, replay_only)
    assert store.replay(command) is None
    with pytest.raises(ValueError, match="fresh effects cut unavailable"):
        store.snapshot()
    fresh = BrokerEffectsStore(broker, replace(bound, replay=absent))
    assert (await fresh.publish(prepared)).disposition == "COMMITTED"
    assert broker.request is not None
    saved = broker.request.command
    assert saved.schema_id == "chiplog.effects.prepare.v1"
    assert saved.canonical_bytes == bound.cut.context.preparation.canonical_bytes()
    assert saved.canonical_bytes != command.canonical_bytes()
    assert saved.fingerprint == _hash(saved.canonical_bytes)
    assert broker.request.identity == bound.cut.context.identity
    # Historical replay has no source/current cut, even after publication advanced it.
    selected = replace(
        bound.replay,
        query=bound.replay.query.model_copy(
            update={
                "identity": broker.request.identity,
                "original_commands": (saved,),
            }
        ),
    )
    historical = BrokerEffectsStore(broker, ReplayEffectsQueries("tenant", selected))
    replayed = historical.replay(command)
    assert replayed is not None and replayed.disposition == "REPLAY"
    assert broker.selected is not None
    broker.selected = broker.selected.model_copy(
        update={
            "complete_records": (
                broker.selected.complete_records[0].model_copy(
                    update={"record_id": "substitution"}
                ),
            )
        }
    )
    with pytest.raises(EffectsIntegrityError):
        historical.replay(command)


@pytest.mark.parametrize(
    "mutation", ["expected", "current", "inner", "issued_output", "outer_tenant"]
)
def test_publication_requires_exact_issued_preparation_and_observed_cut(mutation: str) -> None:
    bound, command, prepared = _fixture()
    context = bound.cut.context
    if mutation == "expected":
        context = replace(
            context,
            preparation=context.preparation.model_copy(
                update={
                    "expected": prepared.expected_store.model_copy(update={"records": ()}),
                }
            ),
        )
    elif mutation == "current":
        context = replace(
            context,
            preparation=context.preparation.model_copy(
                update={
                    "current": context.preparation.current.model_copy(
                        update={"observed_time_ns": 999}
                    ),
                }
            ),
        )
    elif mutation == "inner":
        context = replace(
            context,
            preparation=context.preparation.model_copy(
                update={
                    "command_bytes": command.model_copy(
                        update={"decision_evidence": head("changed")}
                    ).canonical_bytes(),
                }
            ),
        )
    elif mutation == "issued_output":
        context = replace(
            context,
            prepared=prepared.model_copy(update={"exact_companion_manifest": (head("rival"),)}),
        )
    else:
        context = replace(
            context,
            preparation=context.preparation.model_copy(
                update={
                    "expected": prepared.expected_store.model_copy(update={"tenant_id": "other"}),
                }
            ),
        )
    with pytest.raises(ValueError):
        replace(bound, cut=replace(bound.cut, context=context)).publication_context(prepared)


@pytest.mark.parametrize(
    "mutation", ["missing", "schema", "digest", "noncanonical", "inner", "current"]
)
def test_replay_cannot_lose_or_substitute_full_preimage(mutation: str) -> None:
    from chiplog.adapters.driven.effects_queries import ReplayEffectsQueries

    bound, command, _ = _fixture()
    assert isinstance(bound.replay, EffectsReplaySelected)
    selected = bound.replay
    own = selected.query.original_commands[0]
    if mutation == "missing":
        query = selected.query.model_copy(update={"original_commands": ()})
        selected = replace(selected, query=query)
    elif mutation in {"schema", "digest", "noncanonical"}:
        changes: dict[str, dict[str, str | bytes]] = {
            "schema": {"schema_id": "chiplog.effects.command.v1"},
            "digest": {"fingerprint": "0" * 64},
            "noncanonical": {"canonical_bytes": own.canonical_bytes + b" "},
        }
        change = changes[mutation]
        selected = replace(
            selected,
            query=selected.query.model_copy(
                update={
                    "original_commands": (own.model_copy(update=change),),
                }
            ),
        )
    else:
        preparation = selected.preparation
        if mutation == "inner":
            preparation = preparation.model_copy(update={"command_bytes": b"{}"})
        else:
            preparation = preparation.model_copy(
                update={
                    "current": preparation.current.model_copy(update={"observed_time_ns": 999}),
                }
            )
        selected = replace(selected, preparation=preparation)
    with pytest.raises(ValueError):
        ReplayEffectsQueries("tenant", selected).replay_observation(command)
