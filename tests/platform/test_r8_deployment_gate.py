from __future__ import annotations

import hmac
import shutil
import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.platform.deployment_gate import (
    ExposureBounds,
)
from chiplog.platform.r8_gate import (
    BrokerDeploymentGate,
    GateDecisionIndeterminate,
    GateDecisionJournal,
)
from tests.support.deployment_gate import KEY, PAYLOAD, entitlement, request, signature


def gate(tmp_path: Path) -> BrokerDeploymentGate:
    return BrokerDeploymentGate(
        tmp_path / "gate.sqlite",
        tenant_id="t",
        surfaces=(("synthetic.send", "synthetic"),),
        authenticate=lambda payload, proof: hmac.compare_digest(
            hmac.digest(KEY, payload, "sha256"), proof
        ),
        journal=IndependentTenantDecisionJournal(tmp_path / "gate.journal"),
        clock=lambda: 10,
    )


def test_absence_and_untrusted_authority_never_commit(tmp_path: Path) -> None:
    broker = gate(tmp_path)
    value = entitlement()
    assert broker.check(request(value)).disposition == "HOLD"
    assert not broker.import_current(value, b"forged", expected=None)
    assert broker.commit_handoff(request(value), PAYLOAD).disposition == "HOLD"
    assert broker.crossed("op") is None


def test_positive_decision_is_durable_and_identical_replay_cannot_emit_twice(
    tmp_path: Path,
) -> None:
    broker = gate(tmp_path)
    value = entitlement()
    assert broker.import_current(value, signature(value), expected=None)
    assert broker.check(request(value)).disposition == "PERMIT_EXACT_EVALUATION"
    assert broker.crossed("op") is None
    assert broker.commit_handoff(request(value), PAYLOAD).disposition == "PERMIT_EXACT_EVALUATION"
    recorded = broker.crossed("op")
    assert recorded is not None and recorded[1] == PAYLOAD
    restarted = gate(tmp_path)
    assert restarted.crossed("op") == recorded
    assert restarted.commit_handoff(request(value), PAYLOAD).disposition == "HOLD"
    assert restarted.commit_handoff(request(value, "another"), PAYLOAD).disposition == "HOLD"


@pytest.mark.parametrize(
    "field",
    [
        "readiness_head",
        "entitlement_head",
        "evidence_cursors",
        "freshness_leases",
        "cohort_id",
        "cap",
        "purpose",
        "instrumentation",
        "stop_rules",
        "readiness",
        "status",
        "open_causes",
        "live_predicates",
        "mode",
        "expires_ns",
    ],
)
def test_every_eligibility_change_between_check_and_commit_blocks_handoff(
    tmp_path: Path, field: str
) -> None:
    broker = gate(tmp_path)
    value = entitlement()
    assert broker.import_current(value, signature(value), expected=None)
    assert broker.check(request(value)).disposition != "HOLD"
    changes: dict[str, object] = {
        "readiness_head": "ready:2",
        "entitlement_head": "evaluation:2",
        "evidence_cursors": (("evidence", "2"),),
        "freshness_leases": (("evidence", 9),),
        "cohort_id": "different",
        "cap": 2,
        "purpose": "different",
        "instrumentation": ("other",),
        "stop_rules": ("other",),
        "readiness": "HOLD",
        "status": "REVOKED",
        "open_causes": ("breach",),
        "live_predicates": (("scope-clear", False),),
        "mode": "PRODUCTION",
        "expires_ns": 10,
    }
    if field in ExposureBounds.model_fields:
        changed = value.model_copy(
            update={"bounds": value.bounds.model_copy(update={field: changes[field]})}
        )
    else:
        changed = value.model_copy(update={field: changes[field]})
    changed = changed.model_copy(
        update={"generation": value.generation.model_copy(update={"sequence": 1})}
    )
    assert broker.import_current(changed, signature(changed), expected=value)
    assert broker.commit_handoff(request(value), PAYLOAD).disposition == "HOLD"
    # Also match the new generation: each predicate must reject independently,
    # rather than all mutants being caught only by the generation comparison.
    stale_binding = request(value).model_copy(update={"generation": changed.generation})
    assert broker.commit_handoff(stale_binding, PAYLOAD).disposition == "HOLD"
    assert broker.crossed("op") is None


def test_last_cap_slot_has_one_winner_and_revocation_does_not_recall_crossed_work(
    tmp_path: Path,
) -> None:
    broker = gate(tmp_path)
    value = entitlement()
    assert broker.import_current(value, signature(value), expected=None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda op: broker.commit_handoff(request(value, op), PAYLOAD), ("a", "b"))
        )
    assert sorted(result.disposition for result in results) == ["HOLD", "PERMIT_EXACT_EVALUATION"]
    changed = value.model_copy(
        update={
            "status": "REVOKED",
            "generation": value.generation.model_copy(update={"sequence": 1}),
        }
    )
    assert broker.import_current(changed, signature(changed), expected=value)
    assert sum(broker.crossed(op) is not None for op in ("a", "b")) == 1


def test_evaluation_cannot_authorize_production_or_changed_payload(tmp_path: Path) -> None:
    broker = gate(tmp_path)
    value = entitlement()
    assert broker.import_current(value, signature(value), expected=None)
    assert (
        broker.commit_handoff(
            request(value).model_copy(update={"mode": "PRODUCTION"}), PAYLOAD
        ).disposition
        == "HOLD"
    )
    assert broker.commit_handoff(request(value), b"changed").disposition == "HOLD"
    assert broker.crossed("op") is None


def test_restoring_gate_cache_cannot_restore_revoked_authority_or_capacity(tmp_path: Path) -> None:
    broker = gate(tmp_path)
    value = entitlement()
    assert broker.import_current(value, signature(value), expected=None)
    shutil.copyfile(tmp_path / "gate.sqlite", tmp_path / "old.sqlite")
    assert broker.commit_handoff(request(value), PAYLOAD).disposition != "HOLD"
    revoked = value.model_copy(
        update={
            "status": "REVOKED",
            "generation": value.generation.model_copy(update={"sequence": 1}),
        }
    )
    assert broker.import_current(revoked, signature(revoked), expected=value)
    shutil.copyfile(tmp_path / "old.sqlite", tmp_path / "gate.sqlite")
    restarted = gate(tmp_path)
    assert restarted.observe() == revoked
    assert restarted.crossed("op") is not None
    assert restarted.commit_handoff(request(value, "new"), PAYLOAD).disposition == "HOLD"


@pytest.mark.parametrize(
    "surface",
    [
        "calendar.intent",
        "provider.dispatch",
        "telegram.deliver",
        "cli.deliver",
        "scheduler.effect",
        "compensation.dispatch",
        "recovery.retransmit",
        "adapter.new",
    ],
)
def test_future_surface_counterhistories_stop_without_entitlement(
    tmp_path: Path, surface: str
) -> None:
    # Boundary counterhistory only, not implementation of the later journeys.
    broker = BrokerDeploymentGate(
        tmp_path / "gate.sqlite",
        tenant_id="t",
        surfaces=((surface, "synthetic"),),
        authenticate=lambda payload, proof: hmac.compare_digest(
            hmac.digest(KEY, payload, "sha256"), proof
        ),
        journal=IndependentTenantDecisionJournal(tmp_path / "gate.journal"),
        clock=lambda: 10,
    )
    value = entitlement()
    attempt = request(value).model_copy(update={"surface_id": surface})
    external_log: list[bytes] = []

    def attempt_handoff() -> None:
        result = broker.commit_handoff(attempt, PAYLOAD)
        if result.disposition != "HOLD":
            external_log.append(PAYLOAD)

    attempt_handoff()
    assert external_log == [] and broker.crossed("op") is None
    assert broker.import_current(value, signature(value), expected=None)
    attempt_handoff()
    assert external_log == [PAYLOAD] and broker.crossed("op") is not None
    attempt_handoff()
    assert external_log == [PAYLOAD]


@pytest.mark.parametrize("unavailable", [False, True])
def test_lost_decision_ack_is_reconciled_or_indeterminate_never_denied(
    tmp_path: Path, unavailable: bool
) -> None:
    broker = gate(tmp_path)
    value = entitlement()
    assert broker.import_current(value, signature(value), expected=None)
    assert broker._journal is not None
    journal: GateDecisionJournal = broker._journal

    class LostAcknowledgement:
        lost = False

        def entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
            if self.lost and unavailable:
                raise OSError("temporarily unavailable journal authority")
            return journal.entries()

        def append(self, decision: bytes, predecessor: str | None) -> str:
            head = journal.append(decision, predecessor)
            if b'"kind":"HANDOFF"' in decision:
                self.lost = True
                raise OSError("lost acknowledgement after durable decision")
            return head

    broker._journal = LostAcknowledgement()
    if unavailable:
        with pytest.raises(GateDecisionIndeterminate):
            broker.commit_handoff(request(value), PAYLOAD)
    else:
        assert (
            broker.commit_handoff(request(value), PAYLOAD).disposition == "PERMIT_EXACT_EVALUATION"
        )
    broker._journal = journal
    assert broker.crossed("op") is not None
    assert broker.commit_handoff(request(value), PAYLOAD).disposition == "HOLD"


def test_cache_commit_error_after_decision_cannot_report_definite_denial(tmp_path: Path) -> None:
    broker = gate(tmp_path)
    value = entitlement()
    assert broker.import_current(value, signature(value), expected=None)
    transaction = broker._transaction

    @contextmanager
    def lost_cache_ack() -> Iterator[sqlite3.Connection]:
        with transaction() as connection:
            yield connection
        raise sqlite3.OperationalError("lost cache commit acknowledgement")

    broker._transaction = lost_cache_ack  # type: ignore[method-assign]
    with pytest.raises(GateDecisionIndeterminate):
        broker.commit_handoff(request(value), PAYLOAD)
    broker._transaction = transaction  # type: ignore[method-assign]
    assert broker.crossed("op") is not None
