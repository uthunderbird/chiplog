"""Authenticated read-only views over historical deployment-trust state."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.deployment_trust._r7_process import _evaluate, _TrustOwnerCall
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r7_trust import encode_trust_journal
from chiplog.platform.r7_trust_durability import BrokerTrustDurability


@dataclass(frozen=True)
class Bundle:
    journal: IndependentTenantDecisionJournal
    materializer: SQLiteTrustMaterializer
    durability: BrokerTrustDurability
    materializer_path: Path


@contextmanager
def _bundle(tmp_path: Path) -> Iterator[Bundle]:
    gate = AuthorityGate.for_database(tmp_path / "authority.sqlite")
    journal = IndependentTenantDecisionJournal.for_authority_bundle(
        tmp_path / "trust.journal", authority_gate=gate
    )
    materializer_path = tmp_path / "trust.sqlite"
    materializer = SQLiteTrustMaterializer.for_authority_bundle(
        materializer_path, authority_gate=gate
    )
    try:
        yield Bundle(
            journal,
            materializer,
            BrokerTrustDurability(journal, materializer, b"operator-secret"),
            materializer_path,
        )
    finally:
        materializer.close()


def _bootstrap_request() -> bytes:
    return json.dumps(
        {
            "credential_id": "credential-1",
            "database_instance_id": "database-1",
            "expected_peer": "uid:test",
            "peer": "uid:test",
            "principal_id": "principal-1",
            "session_id": "session-1",
            "tenant_id": "tenant-1",
            "token_fingerprint": "bootstrap-token-fingerprint",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _bootstrap(bundle: Bundle) -> None:
    snapshot = bundle.durability.capture_verified_observation()
    result = _evaluate(
        _TrustOwnerCall(
            mode="BOOTSTRAP",
            snapshot_bytes=snapshot.snapshot_bytes,
            request_bytes=_bootstrap_request(),
        )
    )
    assert result.reference_bytes is not None
    bundle.durability.apply_authorized(result.reference_bytes)


def _observation(bundle: Bundle, index: int) -> HermeticTrustObservationV1:
    decision_id, _, raw = bundle.journal.entries()[index]
    logical_id = bundle.durability.owner_snapshot_entries()[index][0]
    return HermeticTrustObservationV1(
        physical_journal_head=ExactHead(
            identity="deployment-trust/journal",
            head=decision_id,
            fingerprint=hashlib.sha256(raw).hexdigest(),
        ),
        logical_snapshot_head=logical_id,
    )


def test_historical_record_returns_exact_physical_envelope_and_full_materialized_row(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        decision_id, predecessor, envelope = bundle.journal.entries()[1]
        logical_id, logical_predecessor, _ = bundle.durability.owner_snapshot_entries()[1]

        actual = bundle.durability.historical_record(decision_id, 1)

        assert actual.physical_decision_id == decision_id
        assert actual.physical_predecessor == predecessor
        assert actual.envelope_bytes == envelope
        assert actual.envelope_fingerprint == hashlib.sha256(envelope).hexdigest()
        assert actual.logical_decision_id == logical_id
        assert actual.logical_predecessor == logical_predecessor
        assert actual.record_ordinal == 1
        assert actual.record_bytes == bundle.materializer.record(decision_id, 1)
        with pytest.raises(RuntimeError, match="physical"):
            bundle.durability.historical_record(logical_id, 1)
        with pytest.raises(ValueError, match="ordinal"):
            bundle.durability.historical_record(decision_id, True)
        with pytest.raises(ValueError, match="ordinal"):
            bundle.durability.historical_record(decision_id, 2**63)


def test_historical_prefix_resolves_an_old_authenticated_physical_cut(tmp_path: Path) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        observation = _observation(bundle, 0)
        expected_physical = bundle.journal.entries()[:1]
        expected_logical = bundle.durability.owner_snapshot_entries()[:1]

        actual = bundle.durability.historical_prefix(observation)

        assert actual.observation == observation
        assert actual.physical_entries == expected_physical
        assert actual.snapshot_bytes == encode_trust_journal(expected_logical)


def test_historical_prefix_rejects_a_logical_head_substituted_for_physical_head(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        observation = _observation(bundle, 0)
        substituted = observation.model_copy(
            update={
                "physical_journal_head": observation.physical_journal_head.model_copy(
                    update={"head": observation.logical_snapshot_head}
                )
            }
        )

        with pytest.raises(RuntimeError, match="physical journal head"):
            bundle.durability.historical_prefix(substituted)


def test_historical_record_rejects_extra_materialized_row_even_with_recomputed_digest(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        decision_id = bundle.journal.entries()[1][0]
        with sqlite3.connect(bundle.materializer_path) as connection:
            rows = connection.execute(
                "SELECT canonical_bytes FROM trust_records WHERE decision_id = ? ORDER BY ordinal",
                (decision_id,),
            ).fetchall()
            connection.execute(
                "INSERT INTO trust_records(decision_id, ordinal, canonical_bytes) VALUES (?, ?, ?)",
                (decision_id, len(rows), b"{}"),
            )
            digest = hashlib.sha256(
                b"\x00".join(bytes(row[0]) for row in (*rows, (b"{}",)))
            ).hexdigest()
            connection.execute(
                "UPDATE trust_decisions SET records_digest = ? WHERE decision_id = ?",
                (digest, decision_id),
            )

        with pytest.raises(RuntimeError, match="materialization"):
            bundle.durability.historical_record(decision_id, 1)


def test_historical_prefix_rejects_zero_record_orphan_materialization_decision(
    tmp_path: Path,
) -> None:
    with _bundle(tmp_path) as bundle:
        _bootstrap(bundle)
        with sqlite3.connect(bundle.materializer_path) as connection:
            connection.execute(
                "INSERT INTO trust_decisions(decision_id, records_digest) VALUES (?, ?)",
                ("orphan", hashlib.sha256(b"").hexdigest()),
            )

        with pytest.raises(RuntimeError, match="materialization"):
            bundle.durability.historical_prefix(_observation(bundle, 0))


@pytest.mark.parametrize("bootstrapped", [False, True])
def test_verify_rejects_zero_record_orphan_materialization_decision(
    tmp_path: Path,
    bootstrapped: bool,
) -> None:
    with _bundle(tmp_path) as bundle:
        if bootstrapped:
            _bootstrap(bundle)
        with sqlite3.connect(bundle.materializer_path) as connection:
            connection.execute(
                "INSERT INTO trust_decisions(decision_id, records_digest) VALUES (?, ?)",
                ("orphan", hashlib.sha256(b"").hexdigest()),
            )

        with pytest.raises(RuntimeError, match="materialization"):
            bundle.durability.verify()
