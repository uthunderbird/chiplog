import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.adapters.driven.calendar_reads import (
    CalendarReadFailure,
)
from chiplog.capabilities.calendar_observations.boundary import (
    DisclosureLabel,
)
from chiplog.capabilities.calendar_observations.observations import (
    observation_payload,
)
from chiplog.composition.r11 import open_hermetic_calendar
from tests.support.calendar_reads import batch, request, state


async def test_public_composition_agenda_detail_bound_progress_and_provenance(
    tmp_path: Path,
) -> None:
    observations = batch()
    runtime = await open_hermetic_calendar(
        ledger_path=tmp_path / "reads.db",
        batch=observations,
        trusted_state=state(observations),
        authenticated_tenant="t",
        authenticated_principal="principal1",
        authenticated_channel="channel1",
        observed_at_ns=30,
    )
    first = await runtime.queries.read(request(runtime.context))
    assert first.disposition == "CURRENT" and len(first.rows) == 2
    second = await runtime.queries.read(
        request(
            runtime.context,
            read_attempt_id="attempt2",
            response_slot_id="slot2",
            after_cursor=first.next_cursor,
        )
    )
    assert len(second.rows) == 1 and second.next_cursor is None
    rows = (*first.rows, *second.rows)
    assert len({row.row_id for row in rows}) == 3
    assert tuple(row.order_key for row in rows) == tuple(sorted(row.order_key for row in rows))
    for row, observation in zip(rows, observations.observations, strict=True):
        assert row.envelope == observation.envelope
        assert row.canonical_payload == observation_payload(observation)
        assert json.loads(row.canonical_payload)["status"] == "provider observed"
    detail = await runtime.queries.read(
        request(
            runtime.context,
            query="CALENDAR_DETAIL",
            detail_id=rows[1].row_id,
            range_start_ns=None,
            range_end_ns=None,
            read_attempt_id="detail",
            response_slot_id="detail",
        )
    )
    assert detail.rows == (rows[1],) and detail.context == first.context == second.context
    assert len(runtime.context.verified_snapshot_bytes) == 32
    with closing(sqlite3.connect(tmp_path / "reads.db")) as connection:
        tables = {str(row[0]) for row in connection.execute("SELECT name FROM sqlite_master")}
        assert not {"records", "publications", "plans", "facts", "effects"}.intersection(tables)
        released = connection.execute(
            "SELECT COUNT(*) FROM authority_read_releases WHERE state='RELEASED' AND dequeued=1"
        ).fetchone()
        assert released == (3,)


async def test_independent_pins_reject_joint_provider_label_substitution(tmp_path: Path) -> None:
    original = batch()
    pinned = state(original)
    observation = original.observations[0]
    envelope = observation.envelope
    changed_source = envelope.sources[0].model_copy(update={"label_head": "attacker"})
    changed = original.model_copy(
        update={
            "observations": (
                observation.model_copy(
                    update={
                        "envelope": envelope.model_copy(
                            update={
                                "sources": (changed_source,),
                            }
                        )
                    }
                ),
            )
        }
    )
    with pytest.raises(CalendarReadFailure, match="source heads"):
        await open_hermetic_calendar(
            ledger_path=tmp_path / "reads.db",
            batch=changed,
            trusted_state=pinned,
            authenticated_tenant="t",
            authenticated_principal="principal1",
            authenticated_channel="channel1",
            observed_at_ns=30,
        )


async def test_joint_label_and_head_downgrade_cannot_self_certify(tmp_path: Path) -> None:
    original = batch(count=1)
    event = original.observations[0]
    restricted = DisclosureLabel(
        lattice_version="chiplog.disclosure.v1",
        value="ENDPOINT_RESTRICTED",
        allowed_endpoints=("channel1",),
    )
    restricted_source = event.envelope.sources[0].model_copy(
        update={
            "label": restricted,
            "label_head": "restricted1",
        }
    )
    restricted_event = event.model_copy(
        update={
            "envelope": event.envelope.model_copy(
                update={
                    "label": restricted,
                    "sources": (restricted_source,),
                }
            )
        }
    )
    trusted_batch = original.model_copy(update={"observations": (restricted_event,)})
    # Returned batch replaces BOTH label levels and their head with an internally
    # consistent unrestricted envelope. Only independently pinned state catches it.
    with pytest.raises(CalendarReadFailure, match="source heads"):
        await open_hermetic_calendar(
            ledger_path=tmp_path / "reads.db",
            batch=original,
            trusted_state=state(trusted_batch),
            authenticated_tenant="t",
            authenticated_principal="principal1",
            authenticated_channel="channel1",
            observed_at_ns=30,
        )
