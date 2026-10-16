"""Authentic V1 bootstrap bytes survive future initializer changes unchanged."""

import base64
import gzip
import hashlib
import json
from pathlib import Path

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.platform.authority_gate import AuthorityGate
from chiplog.platform.r7_trust_durability import BrokerTrustDurability


def test_authentic_legacy_genesis_and_materialization_remain_readable(tmp_path: Path) -> None:
    fixture = Path(__file__).with_name("fixtures") / "r14_legacy_genesis_v1.json.gz"
    raw = fixture.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "187a44494e4d41286ebd5207e0811bfe5bb516fb769d0699a0ad95d3a83f5543"
    )
    capture = json.loads(gzip.decompress(raw))
    assert capture["producer_revision"] == "08465384aa9127909909de49d9a9eb560a8b3cd4"
    for name, item in capture["files"].items():
        assert Path(name).name == name
        body = base64.b64decode(item["bytes_b64"], validate=True)
        assert hashlib.sha256(body).hexdigest() == item["sha256"]
        path = tmp_path / name
        path.write_bytes(body)
        path.chmod(item["mode"])
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    database = tmp_path / capture["database_name"]
    gate = AuthorityGate.for_database(database)
    journal = IndependentTenantDecisionJournal.for_existing_authority_bundle(
        database.with_suffix(database.suffix + ".trust-journal"), authority_gate=gate
    )
    materializer = SQLiteTrustMaterializer.for_authority_bundle(
        database.with_suffix(database.suffix + ".trust.sqlite3"), authority_gate=gate
    )
    try:
        trust = BrokerTrustDurability(journal, materializer, capture["operator_secret"].encode())
        entries = journal.entries()
        assert len(entries) == 2
        selected = trust.historical_record(entries[0][0], 0)
        envelope = json.loads(selected.envelope_bytes)
        assert envelope["kind"] == "INITIALIZE"
        genesis = envelope["payload"]["genesis"]
        assert genesis == {
            "database_instance_id": "legacy-fixture-database",
            "genesis_version": 1,
            "tenant_id": "legacy-fixture-tenant",
        }
        observation = trust.verify()
        assert observation is not None
        assert observation.phase == "ACTIVE"
        assert observation.genesis_head == envelope["payload"]["binding"]["genesis_digest"]
    finally:
        materializer.close()
        journal.close()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
    assert fixture.read_bytes() == raw
