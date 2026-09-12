from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError
from tests.support.evidence_journal import confirm_transport, fixture, rows

from chiplog.adapters.driven.journal_sqlite import JournalIntegrityError
from chiplog.capabilities.evidence_journal.commands import (
    Command,
    JournalQuery,
    Subject,
)
from chiplog.composition.r10 import r10_component


async def test_direct_fact_replay_conflict_and_no_plan(tmp_path: Path) -> None:
    identity, command = fixture()
    path = tmp_path / "db"
    async with r10_component(path, {"trusted-peer": identity}) as component:
        assert (await component.journal.execute("forged-peer", command)).disposition == "DENIED"
        assert (
            await component.journal.execute(
                "trusted-peer", command.model_copy(update={"principal": "other"})
            )
        ).disposition == "DENIED"
        result = await component.journal.execute("trusted-peer", command)
        assert result.disposition == "COMMITTED" and result.record is not None
        assert result.record.kind == "FactClaim"
        replay = await component.journal.execute("trusted-peer", command)
        assert replay.disposition == "REPLAY" and replay.record == result.record
        conflict = await component.journal.execute(
            "trusted-peer",
            command.model_copy(
                update={"heads": command.heads.model_copy(update={"policy": "other"})}
            ),
        )
        assert conflict.disposition == "CONFLICT"
        assert [row[0] for row in rows(path)] == ["evidence_journal"]
        assert component.journal.lineage("trusted-peer", "tenant", result.record.family) == (
            result.record,
        )
        view = component.queries.project(
            "trusted-peer",
            JournalQuery(
                tenant="tenant",
                expected_heads=identity.heads.model_copy(update={"journal": 1}),
                max_rows=1,
            ),
        )
        assert view.disposition == "CURRENT" and view.rows[0].status == "user reported"
        assert view.rows[0].current_positive


@pytest.mark.parametrize(
    "utterance",
    [
        "Я выполнил occurrence:pool 2026-08-18?",
        "Запиши, что я выполнил occurrence:pool 2026-08-18.",
        "Выполни occurrence:pool 2026-08-18.",
        "Я хочу выполнить occurrence:pool 2026-08-18.",
        "Я сомневаюсь, что выполнил occurrence:pool 2026-08-18.",
        "Если я выполнил occurrence:pool 2026-08-18.",
        "Я бы выполнил occurrence:pool 2026-08-18.",
        "«Я выполнил occurrence:pool 2026-08-18.»",
        "Он сказал: Я выполнил occurrence:pool 2026-08-18.",
        "Исправляю: Я выполнил occurrence:pool 2026-08-18.",
        "Отзываю: Я выполнил occurrence:pool 2026-08-18.",
        "Я не выполнил occurrence:pool 2026-08-18.",
        "Я не ходил в бассейн во вторник.",
        "Я выполнил occurrence:pool или occurrence:gym 2026-08-18.",
        "Я выполнил occurrence:pool 2026-02-30.",
        "Я выполнил occurrence:pool 2026-08-18. И ещё gym.",
    ],
)
async def test_negative_closed_grammar(tmp_path: Path, utterance: str) -> None:
    identity, command = fixture(utterance)
    path = tmp_path / "db"
    async with r10_component(path, {"peer": identity}) as component:
        result = await component.journal.execute("peer", command)
        assert result.disposition == "NEEDS_CONFIRMATION"
        assert rows(path) == ()


@pytest.mark.parametrize("source", ["model", "provider"])
async def test_inference_never_direct(tmp_path: Path, source: Literal["model", "provider"]) -> None:
    identity, command = fixture(source=source)
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        assert (
            await component.journal.execute("peer", command)
        ).disposition == "NEEDS_CONFIRMATION"


async def test_t02_exact_confirmation_and_head_substitutions(tmp_path: Path) -> None:
    identity, command = fixture("Я не ходил в бассейн во вторник.")
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        candidate = await component.journal.prepare("peer", command)
        assert candidate.record is not None and candidate.record.display is not None
        display = candidate.record.display
        confirmation = command.model_copy(
            update={
                "command_id": "confirm-1",
                "action": "confirm_candidate",
                "display": display,
                "heads": display.heads,
            }
        )
        for field in ("policy", "credential", "session", "contour", "deletion", "journal"):
            value: str | int = 99 if field == "journal" else "foreign"
            mutated = confirmation.model_copy(
                update={"heads": confirmation.heads.model_copy(update={field: value})}
            )
            assert (await component.journal.execute("peer", mutated)).disposition in {
                "STALE",
                "DENIED",
            }
        for field, value in (
            ("interpretation", "different"),
            ("consequence", "Plan changed"),
            ("candidate_id", "other"),
            ("candidate_version", 2),
            ("principal", "other"),
            ("digest", "other"),
        ):
            bad = confirmation.model_copy(
                update={"display": display.model_copy(update={field: value})}
            )
            confirm_transport(component, bad)
            assert (await component.journal.execute("peer", bad)).disposition in {"DENIED", "STALE"}
        confirm_transport(component, confirmation)
        result = await component.journal.execute("peer", confirmation)
        assert result.disposition == "COMMITTED" and result.record is not None
        assert (await component.journal.execute("peer", confirmation)).record == result.record
        assert [row[0] for row in rows(tmp_path / "db")] == ["evidence_journal", "evidence_journal"]


async def test_race_cas(tmp_path: Path) -> None:
    identity, command = fixture()
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        values = await asyncio.gather(
            component.journal.execute("peer", command),
            component.journal.execute("peer", command.model_copy(update={"command_id": "rival"})),
        )
        assert sorted(v.disposition for v in values) == ["COMMITTED", "STALE"]


async def test_correction_retraction_lineage_and_stale(tmp_path: Path) -> None:
    identity, command = fixture()
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        initial = await component.journal.execute("peer", command)
        assert initial.record is not None
        predecessor = initial.record
        for index, action in enumerate(("correct_claim", "retract_claim")):
            head = component.storage.snapshot("tenant").head
            proposal = command.model_copy(
                update={
                    "command_id": f"prepare-{index}",
                    "action": action,
                    "predecessor": predecessor.record_id,
                    "heads": command.heads.model_copy(update={"journal": head}),
                }
            )
            candidate = await component.journal.prepare("peer", proposal)
            assert candidate.record is not None and candidate.record.display is not None
            display = candidate.record.display
            confirmation = proposal.model_copy(
                update={
                    "command_id": f"confirm-{index}",
                    "action": "confirm_candidate",
                    "heads": display.heads,
                    "display": display,
                }
            )
            confirm_transport(component, confirmation)
            result = await component.journal.execute("peer", confirmation)
            assert result.disposition == "COMMITTED" and result.record is not None
            predecessor = result.record
            replay = await component.journal.execute("peer", confirmation)
            assert replay.disposition == "REPLAY" and replay.record == result.record
        view = component.queries.project(
            "peer",
            JournalQuery(
                tenant="tenant",
                expected_heads=command.heads.model_copy(update={"journal": 5}),
                max_rows=10,
            ),
        )
        assert view.rows[-1].status == "retracted" and not view.rows[-1].current_positive
        stale = command.model_copy(
            update={
                "command_id": "stale",
                "action": "correct_claim",
                "predecessor": initial.record.record_id,
                "heads": command.heads.model_copy(update={"journal": 5}),
            }
        )
        assert (await component.journal.prepare("peer", stale)).disposition == "STALE"


async def test_durable_valid_json_tamper_and_fence(tmp_path: Path) -> None:
    identity, command = fixture()
    path = tmp_path / "db"
    async with r10_component(path, {"peer": identity}) as component:
        result = await component.journal.execute("peer", command)
        assert result.record is not None
        with closing(sqlite3.connect(path)) as connection, connection:
            tampered = result.record.model_copy(update={"principal": "attacker"}).canonical_bytes()
            connection.execute("UPDATE records SET canonical_bytes = ?", (tampered,))
        with pytest.raises(JournalIntegrityError, match="tenant=tenant"):
            component.storage.snapshot("tenant")
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(
                "UPDATE records SET canonical_bytes = ?", (result.record.canonical_bytes(),)
            )
            connection.execute("UPDATE deletion_fences SET generation = 'deleted', frontier = 1")
        with pytest.raises(JournalIntegrityError):
            component.journal.lineage("peer", "tenant", result.record.family)


async def test_disclosure_cannot_self_certify(tmp_path: Path) -> None:
    identity, command = fixture()
    restricted = identity.sources[0].label.model_copy(
        update={"value": "ENDPOINT_RESTRICTED", "allowed_endpoints": ("private",)}
    )
    identity = identity.model_copy(
        update={"sources": (identity.sources[0].model_copy(update={"label": restricted}),)}
    )
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        assert (await component.journal.execute("peer", command)).disposition == "DENIED"
        assert component.storage.snapshot("tenant").records == ()


def test_unknown_contract_fields_rejected() -> None:
    _, command = fixture()
    with pytest.raises(ValidationError):
        Command.model_validate({**command.model_dump(), "approved": True})


async def test_unconfirmed_provider_candidate_cannot_dispute_personal_fact(tmp_path: Path) -> None:
    identity, direct = fixture()
    provider_identity, candidate = fixture(
        "Provider observed a pool visit", source="provider", ingress_id="provider-event"
    )
    identity = identity.model_copy(
        update={
            "items": (*identity.items, *provider_identity.items),
            "sources": (*identity.sources, *provider_identity.sources),
        }
    )
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        fact = await component.journal.execute("peer", direct)
        assert fact.record is not None and fact.disposition == "COMMITTED"
        candidate = candidate.model_copy(
            update={
                "command_id": "provider-candidate",
                "heads": candidate.heads.model_copy(update={"journal": 1}),
            }
        )
        prepared = await component.journal.prepare("peer", candidate)
        assert prepared.disposition == "COMMITTED" and prepared.record is not None
        view = component.queries.project(
            "peer",
            JournalQuery(
                tenant="tenant",
                expected_heads=identity.heads.model_copy(update={"journal": 2}),
                max_rows=10,
            ),
        )
        assert view.disposition == "CURRENT"
        personal = next(row for row in view.rows if row.record.record_id == fact.record.record_id)
        observation = next(
            row for row in view.rows if row.record.record_id == prepared.record.record_id
        )
        assert personal.status == "user reported" and personal.current_positive
        assert personal.record == fact.record
        assert observation.status == "provider observed" and not observation.current_positive


async def test_foreign_candidate_cannot_be_superseded_or_enter_projection_lineage(
    tmp_path: Path,
) -> None:
    owner, command = fixture(source="provider")
    other = owner.model_copy(update={"principal": "other"})
    async with r10_component(tmp_path / "db", {"owner-peer": owner, "peer": other}) as component:
        original = await component.journal.prepare("owner-peer", command)
        assert original.record is not None
        attack = command.model_copy(
            update={
                "command_id": "other-candidate",
                "principal": "other",
                "heads": command.heads.model_copy(update={"journal": 1}),
                "supersedes": (original.record.record_id,),
            }
        )
        assert (await component.journal.prepare("peer", attack)).disposition == "DENIED"
        assert component.storage.snapshot("tenant").head == 1
        own = attack.model_copy(update={"supersedes": ()})
        proposal = await component.journal.prepare("peer", own)
        assert proposal.record is not None and proposal.record.display is not None
        display = proposal.record.display
        confirm = own.model_copy(
            update={
                "command_id": "other-confirm",
                "action": "confirm_candidate",
                "display": display,
                "heads": display.heads,
            }
        )
        confirm_transport(component, confirm)
        assert (await component.journal.execute("peer", confirm)).disposition == "COMMITTED"
        query = JournalQuery(
            tenant="tenant",
            expected_heads=owner.heads.model_copy(update={"journal": 3}),
            max_rows=10,
        )
        owner_view = component.queries.project("owner-peer", query)
        assert owner_view.disposition == "CURRENT" and len(owner_view.rows) == 1
        assert owner_view.rows[0].record == original.record
        other_view = component.queries.project("peer", query)
        assert other_view.disposition == "CURRENT" and len(other_view.rows) == 1
        assert all(record.principal == "other" for record in other_view.rows[0].lineage)
        assert component.journal.lineage("peer", "tenant", original.record.family) == ()


async def test_confirmation_requires_independent_transport_event(tmp_path: Path) -> None:
    identity, command = fixture("Я не ходил в бассейн во вторник.")
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        proposal = await component.journal.prepare("peer", command)
        assert proposal.record is not None and proposal.record.display is not None
        display = proposal.record.display
        confirm = command.model_copy(
            update={
                "command_id": "confirmation",
                "action": "confirm_candidate",
                "display": display,
                "heads": display.heads,
            }
        )
        assert (await component.journal.execute("peer", confirm)).disposition == "DENIED"
        confirm_transport(component, confirm)
        assert (await component.journal.execute("peer", confirm)).disposition == "COMMITTED"


async def test_candidate_rivals_and_exact_selection(tmp_path: Path) -> None:
    identity, command = fixture(source="provider")
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        candidates = []
        for i in range(2):
            proposal = command.model_copy(
                update={
                    "command_id": f"candidate-{i}",
                    "heads": command.heads.model_copy(update={"journal": i}),
                }
            )
            result = await component.journal.prepare("peer", proposal)
            assert result.record is not None and result.record.display is not None
            candidates.append(result.record)
        display = candidates[-1].display
        assert display is not None
        confirm = command.model_copy(
            update={
                "command_id": "conflict-confirm",
                "action": "confirm_candidate",
                "display": display,
                "heads": display.heads,
            }
        )
        confirm_transport(component, confirm)
        assert (await component.journal.execute("peer", confirm)).disposition == "UNRESOLVED"
        view = component.queries.project(
            "peer", JournalQuery(tenant="tenant", expected_heads=display.heads, max_rows=10)
        )
        assert {row.status for row in view.rows} == {"disputed"}
        supersedes = tuple(sorted(c.record_id for c in candidates))
        refreshed = command.model_copy(
            update={"command_id": "selected", "heads": display.heads, "supersedes": supersedes}
        )
        selected = await component.journal.prepare("peer", refreshed)
        assert selected.record is not None and selected.record.display is not None
        display = selected.record.display
        confirm = refreshed.model_copy(
            update={
                "command_id": "selected-confirm",
                "action": "confirm_candidate",
                "display": display,
                "heads": display.heads,
            }
        )
        confirm_transport(component, confirm)
        assert (await component.journal.execute("peer", confirm)).disposition == "COMMITTED"
        view = component.queries.project(
            "peer",
            JournalQuery(
                tenant="tenant",
                expected_heads=display.heads.model_copy(update={"journal": 4}),
                max_rows=10,
            ),
        )
        assert len(view.rows) == 1 and view.rows[0].status == "user confirmed provider observation"


async def test_projection_bound_cursor_and_current_snapshot(tmp_path: Path) -> None:
    identity, command = fixture()
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        for i in range(3):
            result = await component.journal.execute(
                "peer",
                command.model_copy(
                    update={
                        "command_id": f"fact-{i}",
                        "heads": command.heads.model_copy(update={"journal": i}),
                    }
                ),
            )
            assert result.disposition == "COMMITTED"
        query = JournalQuery(
            tenant="tenant",
            expected_heads=command.heads.model_copy(update={"journal": 3}),
            max_rows=1,
        )
        ids: list[str] = []
        while True:
            page = component.queries.project("peer", query)
            assert page.disposition == "CURRENT" and len(page.rows) == 1
            assert page.rows[0].status == "disputed"
            ids.append(page.rows[0].record.record_id)
            if page.next_cursor is None:
                break
            query = query.model_copy(update={"after_id": page.next_cursor})
        assert ids == sorted(set(ids)) and len(ids) == 3
        assert (
            component.queries.project(
                "peer", query.model_copy(update={"after_id": "unknown"})
            ).disposition
            == "INDETERMINATE"
        )
        assert (
            component.queries.project(
                "peer", query.model_copy(update={"expected_heads": command.heads})
            ).disposition
            == "STALE"
        )


async def test_missing_record_never_partial(tmp_path: Path) -> None:
    identity, command = fixture()
    path = tmp_path / "db"
    async with r10_component(path, {"peer": identity}) as component:
        assert (await component.journal.execute("peer", command)).disposition == "COMMITTED"
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("DELETE FROM records")
        with pytest.raises(JournalIntegrityError):
            component.storage.snapshot("tenant")


async def test_lineage_closure_cannot_be_truncated(tmp_path: Path) -> None:
    identity, command = fixture("Я не ходил в бассейн во вторник.")
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        prepared = await component.journal.prepare("peer", command)
        assert prepared.record is not None and prepared.record.display is not None
        display = prepared.record.display
        confirm = command.model_copy(
            update={
                "command_id": "confirmed",
                "action": "confirm_candidate",
                "display": display,
                "heads": display.heads,
            }
        )
        confirm_transport(component, confirm)
        result = await component.journal.execute("peer", confirm)
        assert result.record is not None
        assert result.record.claim.payload.outcome == "not_completed"
        query = JournalQuery(
            tenant="tenant",
            expected_heads=display.heads.model_copy(update={"journal": 2}),
            max_rows=1,
        )
        assert component.queries.project("peer", query).disposition == "INDETERMINATE"
        view = component.queries.project("peer", query.model_copy(update={"max_rows": 2}))
        assert view.disposition == "CURRENT" and len(view.rows[0].lineage) == 2
        assert {
            r.record_id for r in component.journal.lineage("peer", "tenant", result.record.family)
        } == {prepared.record.record_id, result.record.record_id}


@pytest.mark.parametrize(
    "field,value", [("action", "REPLACE"), ("schema_version", 2), ("canonicalization_version", 2)]
)
async def test_untrusted_constructed_command_is_revalidated(
    tmp_path: Path, field: str, value: object
) -> None:
    identity, command = fixture()
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        forged = command.model_copy(update={field: value})
        assert (await component.journal.execute("peer", forged)).disposition == "DENIED"
        assert (await component.journal.prepare("peer", forged)).disposition == "DENIED"
        invalid_claim = command.claim.model_copy(
            update={"subject": command.claim.subject.model_copy(update={"kind": "person"})}
        )
        assert (
            await component.journal.execute(
                "peer", command.model_copy(update={"claim": invalid_claim})
            )
        ).disposition == "DENIED"
        query = JournalQuery(tenant="tenant", expected_heads=command.heads, max_rows=1).model_copy(
            update={"max_rows": 0}
        )
        assert component.queries.project("peer", query).disposition == "DENIED"


async def test_rehashed_authenticated_display_is_not_owner_display(tmp_path: Path) -> None:
    identity, command = fixture(source="provider")
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        prepared = await component.journal.prepare("peer", command)
        assert prepared.record is not None and prepared.record.display is not None
        display = prepared.record.display.model_copy(update={"consequence": "Also modify Plan"})
        encoded = json.dumps(
            display.model_dump(mode="json", exclude={"digest"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        display = display.model_copy(update={"digest": hashlib.sha256(encoded).hexdigest()})
        command = command.model_copy(
            update={
                "command_id": "tampered-confirm",
                "action": "confirm_candidate",
                "display": display,
                "heads": display.heads,
            }
        )
        confirm_transport(component, command)
        assert (await component.journal.execute("peer", command)).disposition == "STALE"
        assert len(component.storage.snapshot("tenant").records) == 1


async def test_correction_cannot_replace_subject(tmp_path: Path) -> None:
    identity, command = fixture()
    async with r10_component(tmp_path / "db", {"peer": identity}) as component:
        initial = await component.journal.execute("peer", command)
        assert initial.record is not None and all(
            value for _, value in initial.record.positive_proof
        )
        claim = command.claim.model_copy(
            update={"subject": Subject(kind="occurrence", identity="other")}
        )
        raw = json.dumps(
            claim.model_dump(mode="json", exclude={"envelope"}),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        claim = claim.model_copy(
            update={
                "envelope": claim.envelope.model_copy(
                    update={"content_digest": hashlib.sha256(raw).hexdigest()}
                )
            }
        )
        correction = command.model_copy(
            update={
                "command_id": "replace-pretending-correct",
                "action": "correct_claim",
                "claim": claim,
                "predecessor": initial.record.record_id,
                "heads": command.heads.model_copy(update={"journal": 1}),
            }
        )
        assert (await component.journal.prepare("peer", correction)).disposition == "DENIED"


async def test_foreign_principal_cannot_correct_or_read_family(tmp_path: Path) -> None:
    identity, command = fixture()
    other = identity.model_copy(update={"principal": "other"})
    async with r10_component(tmp_path / "db", {"peer": identity, "other-peer": other}) as component:
        result = await component.journal.execute("peer", command)
        assert result.record is not None
        correction = command.model_copy(
            update={
                "command_id": "foreign-correction",
                "principal": "other",
                "action": "correct_claim",
                "predecessor": result.record.record_id,
                "heads": command.heads.model_copy(update={"journal": 1}),
            }
        )
        assert (await component.journal.prepare("other-peer", correction)).disposition == "STALE"
        assert component.journal.lineage("other-peer", "tenant", result.record.family) == ()
        query = JournalQuery(tenant="tenant", expected_heads=correction.heads, max_rows=10)
        assert component.queries.project("other-peer", query).rows == ()
