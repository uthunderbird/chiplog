from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCHEMA = ROOT / "src/chiplog/inert_shared/r7-planning-v1.json"

VALUES = {
    "tenant_id": "tenant-1",
    "principal_id": "principal-1",
    "command_id": "command-1",
    "intention_line_id": "intention-1",
    "revision_id": "revision-1",
    "purpose": "Prepare release",
    "authority_act_id": "act-1",
    "trust_reference_bytes": b'{"head":"trust-1"}',
    "planning_snapshot_bytes": b'{"commands":[],"head":0,"record_ids":[]}',
}
