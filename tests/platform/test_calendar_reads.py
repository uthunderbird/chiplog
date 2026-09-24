import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.adapters.driven.calendar_hermetic import HermeticCalendarProvider
from chiplog.adapters.driven.calendar_reads import (
    AuthenticatedCalendarPeer,
    CalendarReadBroker,
    CalendarReadFailure,
    CalendarResponseIntegrityError,
    decode_h1_original_calendar_read,
)
from chiplog.capabilities.calendar_observations.boundary import (
    DisclosureLabel,
    WorkspaceReadContext,
)
from chiplog.capabilities.calendar_observations.contracts import CalendarBatch
from chiplog.capabilities.calendar_observations.observations import (
    observation_id,
)
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1CalendarOriginalReadV1
from chiplog.platform.calendar_read_ledger import (
    CALENDAR_INVALIDATORS,
    CalendarReadIntegrityError,
    CalendarReadLedger,
    CalendarReadOperation,
    CalendarReadState,
)
from chiplog.platform.read_ledger import ReadLedgerConflict, ReadOperation
from tests.support.calendar_reads import batch, request, state


async def setup(
    tmp_path: Path,
    observations: CalendarBatch | None = None,
) -> tuple[CalendarReadBroker, CalendarReadLedger, AuthenticatedCalendarPeer, WorkspaceReadContext]:
    observations = observations or batch()
    path = tmp_path / "reads.db"
    ledger = CalendarReadLedger(path)
    ledger.publish_initial_state(state(observations))
    broker = CalendarReadBroker(ledger, HermeticCalendarProvider(observations))
    peer = broker.authenticate_transport(
        tenant_id="t",
        principal_id="principal1",
        channel_id="channel1",
    )
    return broker, ledger, peer, await broker.acquire(peer, observed_at_ns=30)


@pytest.mark.parametrize("field", tuple(WorkspaceReadContext.model_fields))
async def test_every_issued_context_field_is_exact(tmp_path: Path, field: str) -> None:
    broker, _, peer, context = await setup(tmp_path)
    old = getattr(context, field)
    changed = (
        old + 1 if isinstance(old, int) else b"foreign" if isinstance(old, bytes) else old + "x"
    )
    result = await broker.connect(peer).read(request(context.model_copy(update={field: changed})))
    assert result.disposition == "DENIED" and not result.rows and result.next_cursor is None


async def test_original_read_snapshot_retains_exact_provider_batch_and_dequeued_receipt(
    tmp_path: Path,
) -> None:
    observations = batch(count=3)
    broker, ledger, peer, context = await setup(tmp_path, observations)
    original_request = request(context, max_rows=1)
    result = await broker.connect(peer).read(original_request)

    retained = broker._original_read(peer, original_request, result)

    assert retained.provider_batch == observations
    assert retained.state == state(observations)
    assert retained.result == result
    assert retained.receipt.state == "RELEASED"
    assert retained.receipt.enqueued and retained.receipt.dequeued
    assert (
        retained.receipt.result_digest
        == hashlib.sha256(result.model_dump_json().encode()).hexdigest()
    )
    assert retained.cursor_binding is not None
    assert retained.cursor_binding.token == result.next_cursor
    assert retained.cursor_binding.snapshot_id == context.snapshot_id
    assert retained.cursor_binding.last_order_key == result.rows[-1].order_key

    with closing(sqlite3.connect(ledger._path)) as connection:
        connection.execute(
            "UPDATE authority_read_releases SET result_bytes=? "
            "WHERE tenant_id=? AND read_attempt_id=?",
            (b"replaced", "t", result.read_attempt_id),
        )
        connection.commit()
    with pytest.raises(CalendarReadFailure, match="original calendar receipt"):
        broker._verify_original_read(peer, retained)


async def test_original_read_snapshot_requires_its_exact_request_and_result(tmp_path: Path) -> None:
    broker, _, peer, context = await setup(tmp_path)
    original_request = request(context, max_rows=1)
    result = await broker.connect(peer).read(original_request)

    with pytest.raises(CalendarReadFailure, match="original calendar read"):
        broker._original_read(peer, original_request.model_copy(update={"max_rows": 2}), result)
    with pytest.raises(CalendarReadFailure, match="original calendar read"):
        broker._original_read(peer, original_request, result.model_copy(update={"rows": ()}))


async def test_original_read_snapshot_keeps_empty_provider_evidence_without_requery(
    tmp_path: Path,
) -> None:
    class CountingProvider:
        def __init__(self, observations: CalendarBatch) -> None:
            self.observations = observations
            self.calls = 0

        async def observe(self) -> CalendarBatch:
            self.calls += 1
            return self.observations

    observations = batch(count=0)
    ledger = CalendarReadLedger(tmp_path / "reads.db")
    ledger.publish_initial_state(state(observations))
    provider = CountingProvider(observations)
    broker = CalendarReadBroker(ledger, provider)
    peer = broker.authenticate_transport(
        tenant_id="t", principal_id="principal1", channel_id="channel1"
    )
    context = await broker.acquire(peer, observed_at_ns=30)
    original_request = request(context)
    result = await broker.connect(peer).read(original_request)

    retained = broker._original_read(peer, original_request, result)
    broker._verify_original_read(peer, retained)

    assert retained.provider_batch.observations == ()
    assert result.rows == () and result.next_cursor is None
    assert provider.calls == 1


async def test_historical_decoder_rehydrates_original_calendar_read_without_broker_state(
    tmp_path: Path,
) -> None:
    broker, _, peer, context = await setup(tmp_path)
    original_request = request(context, max_rows=1)
    result = await broker.connect(peer).read(original_request)
    retained = broker._original_read(peer, original_request, result)
    display = result.model_copy(update={"disposition": "LAGGING"})
    evidence = H1CalendarOriginalReadV1(
        provider_batch_json=retained.provider_batch.model_dump_json(),
        state_json=retained.state.model_dump_json(),
        operation_json=retained.operation.model_dump_json(),
        result_json=retained.result.model_dump_json(),
        operation_fingerprint=retained.operation.fingerprint(),
        recipient_fingerprint=retained.operation.recipient_fingerprint(),
        proof_fingerprint=retained.receipt.proof_fingerprint or "0" * 64,
        result_digest=retained.receipt.result_digest or "0" * 64,
        cursor_token=None if retained.cursor_binding is None else retained.cursor_binding.token,
        cursor_snapshot_id=(
            None if retained.cursor_binding is None else retained.cursor_binding.snapshot_id
        ),
        cursor_query_binding_base64=(
            None
            if retained.cursor_binding is None
            else base64.b64encode(retained.cursor_binding.query_binding).decode("ascii")
        ),
        cursor_last_order_key=(
            None if retained.cursor_binding is None else retained.cursor_binding.last_order_key
        ),
        display_result_json=display.model_dump_json(),
    )

    decoded = decode_h1_original_calendar_read(evidence)

    assert decoded == retained


async def test_historical_decoder_rejects_cursor_without_exact_binding(tmp_path: Path) -> None:
    broker, _, peer, context = await setup(tmp_path)
    original_request = request(context, max_rows=1)
    result = await broker.connect(peer).read(original_request)
    retained = broker._original_read(peer, original_request, result)
    evidence = H1CalendarOriginalReadV1(
        provider_batch_json=retained.provider_batch.model_dump_json(),
        state_json=retained.state.model_dump_json(),
        operation_json=retained.operation.model_dump_json(),
        result_json=retained.result.model_dump_json(),
        operation_fingerprint=retained.operation.fingerprint(),
        recipient_fingerprint=retained.operation.recipient_fingerprint(),
        proof_fingerprint=retained.receipt.proof_fingerprint or "0" * 64,
        result_digest=retained.receipt.result_digest or "0" * 64,
        cursor_token=None,
        cursor_snapshot_id=None,
        cursor_query_binding_base64=None,
        cursor_last_order_key=None,
        display_result_json=result.model_copy(update={"disposition": "LAGGING"}).model_dump_json(),
    )

    with pytest.raises(CalendarReadFailure, match="historical calendar evidence"):
        decode_h1_original_calendar_read(evidence)


@pytest.mark.parametrize("field", tuple(f for f in CALENDAR_INVALIDATORS if f != "tenant_id"))
async def test_every_current_invalidator_fences_existing_snapshot(
    tmp_path: Path, field: str
) -> None:
    broker, ledger, peer, context = await setup(tmp_path)
    prior = ledger.current_state("t")
    old = getattr(prior, field)
    changed = True if isinstance(old, bool) else old + 1 if isinstance(old, int) else old + "x"
    ledger.invalidate(prior, **{field: changed})
    result = await broker.connect(peer).read(request(context))
    assert result.disposition == "STALE_OR_INDETERMINATE_READ" and not result.rows
    assert result.context == context


async def test_forged_and_other_authenticated_peer_cannot_reuse_context(tmp_path: Path) -> None:
    broker, _, _, context = await setup(tmp_path)
    peers = [
        AuthenticatedCalendarPeer(),
        broker.authenticate_transport(
            tenant_id="t",
            principal_id="principal1",
            channel_id="channel1",
        ),
    ]
    for peer in peers:
        result = await broker.connect(peer).read(request(context))
        assert result.disposition == "DENIED" and not result.rows


@pytest.mark.parametrize(
    "freshness,expected",
    [
        ("CURRENT", "CURRENT"),
        ("LAGGING", "LAGGING"),
        ("UNKNOWN", "STALE_OR_INDETERMINATE_READ"),
    ],
)
async def test_provider_freshness_is_typed_evidence(
    tmp_path: Path,
    freshness: str,
    expected: str,
) -> None:
    broker, _, peer, context = await setup(tmp_path, batch(freshness))
    result = await broker.connect(peer).read(request(context))
    assert result.disposition == expected
    if expected == "STALE_OR_INDETERMINATE_READ":
        assert not result.rows


async def test_invalidation_between_prepare_and_release_is_reached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker, ledger, peer, context = await setup(tmp_path)
    original = ledger.release_port().release
    reached = []

    def raced(
        operation: CalendarReadOperation,
        expected: CalendarReadState,
        proof: str,
        payload: bytes,
    ) -> object:
        reached.append(json.loads(payload)["rows"])
        ledger.invalidate(expected, policy_head="p2")
        return original(operation, expected, proof, payload)

    monkeypatch.setattr(ledger, "release", raced)
    result = await broker.connect(peer).read(request(context))
    assert reached and reached[0]
    assert result.disposition == "STALE_OR_INDETERMINATE_READ" and not result.rows
    assert result.context.session_id == context.session_id


async def test_release_before_invalidation_retains_linearized_result(tmp_path: Path) -> None:
    broker, ledger, peer, context = await setup(tmp_path)
    result = await broker.connect(peer).read(request(context))
    ledger.invalidate(ledger.current_state("t"), deletion_fence_head="fence2")
    assert result.disposition == "CURRENT" and result.rows
    stale = await broker.connect(peer).read(
        request(
            context,
            read_attempt_id="a2",
            response_slot_id="s2",
        )
    )
    assert stale.disposition == "STALE_OR_INDETERMINATE_READ" and not stale.rows


@pytest.mark.parametrize(
    "changes",
    [
        {"after_cursor": "unknown"},
        {"detail_id": "unexpected"},
        {"range_end_ns": 0},
        {"range_start_ns": None},
        {"query": "PLANNING_VIEW"},
    ],
)
async def test_query_mutations_fail_closed(tmp_path: Path, changes: dict[str, object]) -> None:
    broker, _, peer, context = await setup(tmp_path)
    result = await broker.connect(peer).read(request(context, **changes))
    assert result.disposition in {"DENIED", "STALE_OR_INDETERMINATE_READ"}
    assert not result.rows


async def test_cursor_cannot_change_query_or_snapshot(tmp_path: Path) -> None:
    broker, _, peer, context = await setup(tmp_path)
    port = broker.connect(peer)
    first = await port.read(request(context))
    second_context = await broker.acquire(peer, observed_at_ns=30)
    mutations: list[dict[str, object]] = [
        {"range_end_ns": 60},
        {"context": second_context},
    ]
    for index, values in enumerate(mutations):
        next_request = request(
            context,
            after_cursor=first.next_cursor,
            read_attempt_id=f"a{index}",
            response_slot_id=f"s{index}",
        )
        next_request = next_request.model_copy(update=values)
        result = await port.read(next_request)
        assert result.disposition == "STALE_OR_INDETERMINATE_READ" and not result.rows


async def test_same_attempt_and_slot_are_not_replay_shortcuts(tmp_path: Path) -> None:
    broker, _, peer, context = await setup(tmp_path)
    port = broker.connect(peer)
    assert (await port.read(request(context))).rows
    mutations: list[dict[str, object]] = [
        {"max_rows": 1},
        {"read_attempt_id": "changed"},
        {"response_slot_id": "new"},
    ]
    for changes in mutations:
        result = await port.read(request(context, **changes))
        assert result.disposition == "STALE_OR_INDETERMINATE_READ" and not result.rows


async def test_ledger_changed_operation_cannot_release(tmp_path: Path) -> None:
    _, ledger, _, context = await setup(tmp_path)
    operation = CalendarReadOperation(
        variant="CALENDAR_AGENDA",
        owner_id="calendar_observations",
        request_id="r",
        read_attempt_id="a",
        tenant_id="t",
        broker_epoch=1,
        generation_id="gen1",
        session_id=context.session_id,
        response_slot_id="s",
        request_bytes=b"bound-query",
    )
    initial = ledger.current_state("t")
    ledger.release_port().begin(operation, initial)
    for field, value in [
        ("variant", "CALENDAR_DETAIL"),
        ("request_bytes", b"other-query"),
        ("response_slot_id", "other"),
        ("session_id", "other"),
    ]:
        with pytest.raises(ReadLedgerConflict, match="changed read attempt"):
            ledger.release_port().release(
                operation.model_copy(update={field: value}), initial, "p", b"x"
            )


async def test_physical_database_replacement_is_not_logical_identity(tmp_path: Path) -> None:
    broker, _, peer, context = await setup(tmp_path)
    path = tmp_path / "reads.db"
    copied = tmp_path / "copy.db"
    copied.write_bytes(path.read_bytes())
    copied.replace(path)
    result = await broker.connect(peer).read(request(context))
    assert result.disposition == "STALE_OR_INDETERMINATE_READ" and not result.rows


@pytest.mark.parametrize("cut", ["release", "dequeue"])
async def test_replacement_inside_release_boundary_emits_no_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cut: str,
) -> None:
    broker, ledger, peer, context = await setup(tmp_path)
    path = tmp_path / "reads.db"
    reached = []
    original_release = ledger.release_port().release
    original_dequeue = ledger.release_port().dequeue

    def replace() -> None:
        reached.append(True)
        copy = tmp_path / "replacement.db"
        copy.write_bytes(path.read_bytes())
        copy.replace(path)

    def replaced_release(
        operation: CalendarReadOperation,
        expected: CalendarReadState,
        proof: str,
        payload: bytes,
    ) -> object:
        replace()
        return original_release(operation, expected, proof, payload)

    def replaced_dequeue(operation: CalendarReadOperation) -> bytes | None:
        replace()
        return original_dequeue(operation)

    monkeypatch.setattr(ledger, cut, replaced_release if cut == "release" else replaced_dequeue)
    result = await broker.connect(peer).read(request(context))
    assert reached and result.disposition == "STALE_OR_INDETERMINATE_READ" and not result.rows


async def test_authoritative_corruption_is_typed_not_partial(tmp_path: Path) -> None:
    broker, _, peer, context = await setup(tmp_path)
    with closing(sqlite3.connect(tmp_path / "reads.db")) as connection, connection:
        connection.execute("UPDATE authority_read_state SET canonical_state = ?", (b"broken",))
    with pytest.raises(
        CalendarReadIntegrityError, match=r"tenant=t.*record=authority_read_state/t"
    ) as exc:
        await broker.connect(peer).read(request(context))
    assert exc.value.__cause__ is not None


@pytest.mark.parametrize("mutation", ["duplicate", "reorder", "foreign", "missing", "digest"])
async def test_provenance_mutations_fail_at_public_acquisition(
    tmp_path: Path, mutation: str
) -> None:
    original = batch(count=1)
    observation = original.observations[0]
    envelope = observation.envelope
    source = envelope.sources[0]
    other = source.model_copy(update={"owner": "aaa", "record_id": "upstream"})
    sources = {
        "duplicate": (source, source),
        "reorder": (source, other),
        "foreign": (source.model_copy(update={"tenant_id": "foreign"}),),
        "missing": (other,),
        "digest": (source.model_copy(update={"content_digest": "bad"}),),
    }[mutation]
    mutated = original.model_copy(
        update={
            "observations": (
                observation.model_copy(
                    update={"envelope": envelope.model_copy(update={"sources": sources})}
                ),
            )
        }
    )
    # Even trusted pins cannot bless malformed owner provenance; shape is not policy.
    with pytest.raises(CalendarReadFailure):
        await setup(tmp_path, mutated)


async def test_source_closure_does_not_truncate_to_bound(tmp_path: Path) -> None:
    original = batch(count=1)
    observation = original.observations[0]
    envelope = observation.envelope
    source = envelope.sources[0]
    upstream = source.model_copy(update={"owner": "aaa", "record_id": "upstream"})
    enriched = original.model_copy(
        update={
            "observations": (
                observation.model_copy(
                    update={
                        "envelope": envelope.model_copy(
                            update={
                                "sources": (upstream, source),
                            }
                        )
                    }
                ),
            )
        }
    )
    broker, _, peer, context = await setup(tmp_path, enriched)
    denied = await broker.connect(peer).read(request(context, max_rows=1))
    assert denied.disposition == "STALE_OR_INDETERMINATE_READ" and not denied.rows
    complete = await broker.connect(peer).read(
        request(
            context,
            max_rows=2,
            read_attempt_id="a2",
            response_slot_id="s2",
        )
    )
    assert complete.disposition == "CURRENT" and len(complete.rows[0].envelope.sources) == 2


async def test_acquisition_race_issues_no_replacement_session(tmp_path: Path) -> None:
    original = batch()
    ledger = CalendarReadLedger(tmp_path / "reads.db")
    ledger.publish_initial_state(state(original))

    class RacingProvider:
        async def observe(self) -> CalendarBatch:
            ledger.invalidate(ledger.current_state("t"), policy_head="p2")
            return original

    broker = CalendarReadBroker(ledger, RacingProvider())
    peer = broker.authenticate_transport(
        tenant_id="t",
        principal_id="principal1",
        channel_id="channel1",
    )
    with pytest.raises(CalendarReadFailure, match="during acquisition"):
        await broker.acquire(peer, observed_at_ns=30)


async def test_snapshot_keeps_order_despite_provider_batch_reordering(tmp_path: Path) -> None:
    original = batch()
    broker, _, peer, context = await setup(
        tmp_path,
        original.model_copy(
            update={
                "observations": tuple(reversed(original.observations)),
            }
        ),
    )
    result = await broker.connect(peer).read(request(context, max_rows=3))
    assert tuple(row.row_id for row in result.rows) == tuple(
        observation_id(observation) for observation in original.observations
    )


async def test_bound_n_and_n_plus_one_and_unknown_fields(tmp_path: Path) -> None:
    broker, _, peer, context = await setup(tmp_path)
    for limit in (0, 1001):
        with pytest.raises(ValueError):
            request(context, max_rows=limit)
    with pytest.raises(ValueError):
        request(context, invented=True)
    result = await broker.connect(peer).read(request(context, max_rows=1000))
    assert len(result.rows) == 3 and result.next_cursor is None


@pytest.mark.parametrize("change_digest", [False, True])
async def test_durable_response_substitution_never_emits_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    change_digest: bool,
) -> None:
    broker, ledger, peer, context = await setup(tmp_path)
    original_dequeue = ledger.dequeue

    def corrupt_before_dequeue(operation: ReadOperation) -> bytes | None:
        with closing(sqlite3.connect(tmp_path / "reads.db")) as connection, connection:
            row = connection.execute(
                "SELECT result_bytes FROM authority_read_releases "
                "WHERE tenant_id=? AND read_attempt_id=?",
                (operation.tenant_id, operation.read_attempt_id),
            ).fetchone()
            assert row is not None
            payload = json.loads(row[0])
            payload["rows"][0]["row_id"] = "forged-record"
            forged = json.dumps(payload).encode()
            connection.execute(
                "UPDATE authority_read_releases SET result_bytes=? "
                "WHERE tenant_id=? AND read_attempt_id=?",
                (forged, operation.tenant_id, operation.read_attempt_id),
            )
            if change_digest:
                connection.execute(
                    "UPDATE authority_read_releases SET result_digest=? "
                    "WHERE tenant_id=? AND read_attempt_id=?",
                    (
                        hashlib.sha256(forged).hexdigest(),
                        operation.tenant_id,
                        operation.read_attempt_id,
                    ),
                )
        return original_dequeue(operation)

    monkeypatch.setattr(ledger, "dequeue", corrupt_before_dequeue)
    with pytest.raises(
        CalendarResponseIntegrityError, match=r"operation=calendar\.dequeue tenant=t"
    ) as failure:
        await broker.connect(peer).read(request(context))
    assert isinstance(failure.value.__cause__, ValueError)


async def test_restricted_envelope_requires_exact_join_and_endpoint(tmp_path: Path) -> None:
    original = batch(count=1)
    event = original.observations[0]
    restricted = DisclosureLabel(
        lattice_version="chiplog.disclosure.v1",
        value="ENDPOINT_RESTRICTED",
        allowed_endpoints=("elsewhere",),
    )
    source = event.envelope.sources[0].model_copy(update={"label": restricted})
    for index, label in enumerate((restricted, event.envelope.label)):
        path = tmp_path / str(index)
        path.mkdir()
        changed = original.model_copy(
            update={
                "observations": (
                    event.model_copy(
                        update={
                            "envelope": event.envelope.model_copy(
                                update={"label": label, "sources": (source,)}
                            ),
                        }
                    ),
                )
            }
        )
        with pytest.raises(CalendarReadFailure, match="disclosure"):
            await setup(path, changed)


@pytest.mark.parametrize("field,value", [("value", "UNKNOWN"), ("lattice_version", "future.v2")])
async def test_provider_model_copy_does_not_bypass_nested_schema(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    original = batch(count=1)
    event = original.observations[0]
    bad_label = event.envelope.label.model_copy(update={field: value})
    bad_source = event.envelope.sources[0].model_copy(update={"label": bad_label})
    bad = original.model_copy(
        update={
            "observations": (
                event.model_copy(
                    update={
                        "envelope": event.envelope.model_copy(
                            update={"label": bad_label, "sources": (bad_source,)}
                        ),
                    }
                ),
            )
        }
    )
    ledger = CalendarReadLedger(tmp_path / "reads.db")
    ledger.publish_initial_state(state(bad))

    class MalformedProvider:
        async def observe(self) -> CalendarBatch:
            return bad

    broker = CalendarReadBroker(ledger, MalformedProvider())
    peer = broker.authenticate_transport(
        tenant_id="t",
        principal_id="principal1",
        channel_id="channel1",
    )
    with pytest.raises(CalendarReadFailure):
        await broker.acquire(peer, observed_at_ns=30)
