"""Executable R7 predecessor ledger and canonical parity corpus."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Literal, cast

from .manifests import R1_SIGNATURES
from .r4_r5_freeze import (
    R4_R5_DERIVATIVE_SINKS,
    R4_R5_EXPORTS,
    R4_R5_PROVIDERS,
    R4_R5_RECORDS,
)

type Disposition = Literal["RETAIN", "ADAPT", "HISTORICAL_ONLY", "DEPRECATE_AS_EXECUTABLE"]
type EntryKind = Literal["export", "schema", "record", "fixture", "provider", "bridge", "sink"]


@dataclass(frozen=True)
class CompatibilityEntry:
    predecessor_id: str
    kind: EntryKind
    disposition: Disposition
    successor_id: str
    successor_evidence: str


@dataclass(frozen=True)
class ParityCase:
    case_id: str
    operation_id: str
    canonical_request: bytes
    expected_dispositions: tuple[str, ...]
    compared_artifacts: tuple[str, ...]


R1_EXPORT_PREDECESSORS = tuple(f"export:{reference}" for reference, _ in R1_SIGNATURES)
R4_R5_EXPORT_PREDECESSORS = tuple(f"export:{item.reference}" for item in R4_R5_EXPORTS)
R4_R5_SCHEMA_PREDECESSORS = tuple(
    f"schema:{schema_id}" for schema_id in sorted({item.schema_id for item in R4_R5_RECORDS})
)
R4_R5_RECORD_PREDECESSORS = tuple(
    f"record:{item.record_type_id}:{item.owner}:{item.commit_boundary}" for item in R4_R5_RECORDS
)
R4_R5_PROVIDER_PREDECESSORS = tuple(
    f"provider:{item.port}:{item.implementation}" for item in R4_R5_PROVIDERS
)
R4_R5_SINK_PREDECESSORS = tuple(f"sink:{item}" for item in R4_R5_DERIVATIVE_SINKS)

FROZEN_FIXTURE_PREDECESSORS = (
    "fixture:tests/architecture/test_inert_shared.py",
    "fixture:tests/architecture/test_manifest_contract.py",
    "fixture:tests/architecture/test_manifest_mutants.py",
    "fixture:tests/architecture/test_r4_r5_freeze.py",
    "fixture:tests/capabilities/test_r5_planning.py",
    "fixture:tests/capabilities/test_r5_projection.py",
    "fixture:tests/conformance/test_canonicalization.py",
    "fixture:tests/contracts/test_r6_public_component.py",
    "fixture:tests/contracts/test_r6_trust_bridge.py",
    "fixture:tests/deployment_trust/test_deployment_trust.py",
    "fixture:tests/integration/test_r1_r2_contract.py",
    "fixture:tests/integration/test_r4_r5_convergence.py",
    "fixture:tests/integration/test_r6_cli.py",
    "fixture:tests/platform/test_deletion_provenance.py",
    "fixture:tests/platform/test_evidence_inbox.py",
    "fixture:tests/platform/test_sqlite_substrate.py",
)

R6_BRIDGE_PREDECESSORS = (
    "bridge:chiplog.composition.r6:R4PlanningTrustBridge",
    "bridge:chiplog.composition:build_r6_component",
    "bridge:chiplog.composition.r6:open_r6_runtime",
)

PREDECESSOR_UNIVERSE = tuple(
    sorted(
        (
            *R1_EXPORT_PREDECESSORS,
            *R4_R5_EXPORT_PREDECESSORS,
            *R4_R5_SCHEMA_PREDECESSORS,
            *R4_R5_RECORD_PREDECESSORS,
            *R4_R5_PROVIDER_PREDECESSORS,
            *R4_R5_SINK_PREDECESSORS,
            *FROZEN_FIXTURE_PREDECESSORS,
            *R6_BRIDGE_PREDECESSORS,
        )
    )
)


def _entry(predecessor_id: str) -> CompatibilityEntry:
    kind = cast(EntryKind, predecessor_id.partition(":")[0])
    if kind in {"record", "schema", "sink"}:
        disposition: Disposition = "RETAIN"
        successor = predecessor_id
    elif kind == "fixture":
        disposition = "HISTORICAL_ONLY"
        successor = f"r7-successor:{predecessor_id.removeprefix('fixture:')}"
    elif kind == "bridge":
        disposition = "DEPRECATE_AS_EXECUTABLE"
        successor = "route:broker:planning.create_intention_line.v1"
    else:
        disposition = "ADAPT"
        successor = f"r7-owner-local:{predecessor_id}"
    return CompatibilityEntry(
        predecessor_id,
        kind,
        disposition,
        successor,
        "tests/r7/test_compatibility_ledger.py",
    )


R7_COMPATIBILITY_LEDGER = tuple(_entry(item) for item in PREDECESSOR_UNIVERSE)

_CREATE_REQUEST = {
    "authority_act_id": "act-1",
    "command_id": "command-1",
    "credential_id": "credential-1",
    "database_instance_id": "database-1",
    "intention_line_id": "intention-1",
    "principal_id": "principal-1",
    "purpose": "Prepare release",
    "revision_id": "revision-1",
    "session_id": "session-1",
    "tenant_id": "tenant-1",
}

R7_PARITY_CORPUS = (
    ParityCase(
        "r6.cli.create-intention-line.v1",
        "planning.create_intention_line",
        json.dumps(_CREATE_REQUEST, sort_keys=True, separators=(",", ":")).encode(),
        ("COMMITTED", "REPLAY", "CONFLICT", "DENIED", "STALE"),
        (
            "canonical_durable_record_bytes",
            "planning_committed_result_bytes",
            "restart_projection_bytes",
        ),
    ),
)


def verify_r7_compatibility_ledger(
    candidate: tuple[CompatibilityEntry, ...] = R7_COMPATIBILITY_LEDGER,
) -> str:
    keys = tuple(item.predecessor_id for item in candidate)
    if keys != PREDECESSOR_UNIVERSE:
        raise ValueError("R7 compatibility ledger must equal the canonical predecessor universe")
    if len(keys) != len(set(keys)):
        raise ValueError("R7 compatibility ledger contains duplicate predecessors")
    if any(not item.successor_id or not item.successor_evidence for item in candidate):
        raise ValueError("R7 compatibility entry lacks successor ownership or evidence")
    payload = json.dumps(
        [asdict(item) for item in candidate], sort_keys=True, separators=(",", ":")
    ).encode()
    return sha256(payload).hexdigest()


__all__ = [
    "FROZEN_FIXTURE_PREDECESSORS",
    "PREDECESSOR_UNIVERSE",
    "R7_COMPATIBILITY_LEDGER",
    "R7_PARITY_CORPUS",
    "CompatibilityEntry",
    "ParityCase",
    "verify_r7_compatibility_ledger",
]
