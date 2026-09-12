from __future__ import annotations

import json
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.architecture.r7_runtime import R7_PRODUCTION_MANIFEST
from chiplog.capabilities.deployment_trust._r7_process import _evaluate, _TrustOwnerCall
from chiplog.composition.r7_supervisor import R4RuntimeAdmission, R7RuntimeSupervisor
from chiplog.platform.r7_trust import encode_trust_journal
from chiplog.platform.r7_trust_durability import BrokerTrustDurability


def test_supervisor_admits_generation_only_after_real_r4_recovery(tmp_path: Path) -> None:
    journal = IndependentTenantDecisionJournal(tmp_path / "trust.journal")
    materializer = SQLiteTrustMaterializer(tmp_path / "trust.sqlite3")
    trust = BrokerTrustDurability(journal, materializer, b"operator-secret")
    decision = _evaluate(
        _TrustOwnerCall(
            mode="BOOTSTRAP",
            snapshot_bytes=encode_trust_journal(journal.entries()),
            request_bytes=(
                b'{"credential_id":"credential-1","database_instance_id":"database-1",'
                b'"expected_peer":"uid:test","peer":"uid:test","principal_id":"principal-1",'
                b'"session_id":"session-1","tenant_id":"tenant-1",'
                b'"token_fingerprint":"bootstrap-token-fingerprint"}'
            ),
        )
    )
    assert decision.reference_bytes is not None
    authorized = json.loads(decision.reference_bytes)["decisions"]
    boolean_versions = json.loads(decision.reference_bytes)
    boolean_versions["decisions"][0]["payload"]["binding"]["journal_epoch"] = True
    boolean_versions["decisions"][0]["payload"]["genesis"]["genesis_version"] = True
    malformed = json.dumps(boolean_versions, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(RuntimeError, match="field types"):
        trust.apply_authorized(malformed)
    assert journal.entries() == ()
    trust.apply_authorized(decision.reference_bytes)
    with pytest.raises(RuntimeError, match="trust decision authentication"):
        BrokerTrustDurability(journal, materializer, b"forged-operator-secret").verify()
    durable = [json.loads(raw) for _, _, raw in journal.entries()]
    assert [item["kind"] for item in durable] == [item["kind"] for item in authorized]
    for expected, actual in zip(authorized, durable, strict=True):
        if expected["kind"] == "INITIALIZE":
            expected = json.loads(json.dumps(expected))
            expected["payload"]["binding"].pop("signature")
            actual = json.loads(json.dumps(actual))
            actual["payload"]["binding"].pop("signature")
        assert actual["payload"] == expected["payload"]
    before = journal.entries()
    with pytest.raises(RuntimeError, match="payload is incomplete"):
        trust.apply_authorized(b'{"decisions":[{"kind":"BOOTSTRAP","payload":{}}]}')
    assert journal.entries() == before
    supervisor = R7RuntimeSupervisor(
        "tenant-1",
        tmp_path / "authority.sqlite3",
        R7_PRODUCTION_MANIFEST,
        R4RuntimeAdmission(trust, journal),
    )
    try:
        attestations = supervisor.start_generation("generation-1")
        evidence = supervisor.admission_evidence
        assert evidence is not None and evidence.accepted
        state = trust.verify()
        assert state is not None
        assert evidence.trust_head == state.trust_head
        assert evidence.journal_head == state.materialization_head
        assert len(attestations) == 3
    finally:
        supervisor.close()
        materializer.close()
