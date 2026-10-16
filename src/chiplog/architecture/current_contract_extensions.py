"""Exact current additions; the historical R4/R5 universe remains immutable.

This is representation registration, not a running database version or authority.
Schema fingerprints include nested definitions; behavioral evidence is separate.
"""

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Literal

from .r4_r5_freeze import R4_R5_EXPORTS, R4_R5_RECORDS, FrozenRecord


@dataclass(frozen=True)
class CurrentExportExtension:
    reference: str
    kind: Literal["model", "union", "protocol", "exception"]
    shape_sha256: str
    fields: tuple[str, ...]
    evidence: tuple[str, ...]


_TRUST = "chiplog.capabilities.deployment_trust:"
_GENESIS_TESTS = "tests/capabilities/deployment_trust/test_genesis_contracts.py::"
_SCOPE_TESTS = "tests/capabilities/deployment_trust/test_hermetic_output_scope_contracts.py::"
_OWNER_TESTS = "tests/capabilities/deployment_trust/test_output_scope_owner_seam.py::"
_VERSION_FIELDS = (
    "genesis_version", "schema_version", "journal_schema_version",
    "canonicalization_version", "registry_version", "companion_manifest_version",
)

CURRENT_EXPORT_EXTENSIONS = (
    CurrentExportExtension(
        _TRUST + "CurrentHermeticExecutionScopeResultV1", "union",
        "76073747e00fab6120cc16527ea55c12db5caecf861e93e016694ab407f75ae8", (),
        (_SCOPE_TESTS + "test_closed_noncurrent_results",),
    ),
    CurrentExportExtension(
        _TRUST + "DatabaseGenesisV2", "model",
        "06dcc559482b656252a5cade34ca24c45829c7fcc73bfb36d71f0bd63f97fc21",
        (*_VERSION_FIELDS, "tenant_id", "database_instance_id"),
        (_GENESIS_TESTS +
         "test_public_complete_genesis_retains_every_explicit_member_and_is_immutable",),
    ),
    CurrentExportExtension(
        _TRUST + "DatabaseVersionTuple", "model",
        "8541ddd8676615c30cd07ef91fbea8c35602639428f11022c617bee5dad0ef90", _VERSION_FIELDS,
        (_GENESIS_TESTS +
         "test_tuple_can_represent_unknown_positive_version_without_admitting_it",),
    ),
    CurrentExportExtension(
        _TRUST + "HermeticOutputScopeOwner", "protocol",
        "abe21e7e076459e5ce1dc659202a97dac1c699157212530f83d35c0ece2089eb", (),
        (_OWNER_TESTS + "test_real_owner_route_remains_unsupported",),
    ),
    CurrentExportExtension(
        _TRUST + "IssueHermeticOutputScopeResultV1", "union",
        "a31ac477750b809176d2d07cfa39de71512f5360a0e967579539d5006cb7fe87", (),
        (_OWNER_TESTS + "test_issue_negative_results_cannot_carry_authority",),
    ),
    CurrentExportExtension(
        _TRUST + "IssueHermeticOutputScopeV1", "model",
        "829f1fffbcca16cb91512300c84d94e51e7e4a9200b614197efc6f1ce836b55d",
        ("schema_id", "slot_id", "database_id", "scope_id", "expected_trust_observation",
         "expected_scope_predecessor", "expected_revision", "authenticated_cli_ref",
         "admitted_authentication_ref", "selected_resource_observation_ref", "worker_session_id"),
        (_OWNER_TESTS + "test_issue_strictly_checks_preconstructed_trust_reference",),
    ),
    CurrentExportExtension(
        _TRUST + "ReadCurrentHermeticExecutionScopeV1", "model",
        "79fd367ca584ec99015252fc746ea0766b182ccfdb0ebfd188fc303e069d90c6",
        ("schema_id", "expected_trust_observation", "source_anchor", "expected_revision",
         "admitted_authentication_ref", "authenticated_cli_ref", "tenant_id", "database_id",
         "scope_id", "expected_scope_ref", "expected_worker_session_id",
         "selected_resource_observation_ref"),
        (_OWNER_TESTS + "test_current_request_rejects_anchor_mismatch_and_policy_injection",),
    ),
    CurrentExportExtension(
        _TRUST + "SelectedHermeticResourceObservationRefV1", "model",
        "87de6f912ea71d11254ae45fa69a1cd314be09e53003c3a3432a40cc33f647e5",
        ("signature_domain", "selected_initialization", "signed_observation_fingerprint"),
        (_SCOPE_TESTS + "test_canonical_roundtrip_preserves_original_sources",),
    ),
    CurrentExportExtension(
        _TRUST + "UnsupportedSchemaVersion", "exception",
        "64eefee35411fa131c5a743db528742cce29d685b3fe6f206cbd48fc2fce5816", (),
        (_GENESIS_TESTS + "test_unsupported_failure_has_fixed_typed_code_and_adoption_status",),
    ),
)

CURRENT_RECORD_EXTENSIONS = (
    FrozenRecord(
        "chiplog.deployment_trust.prepared_external_delivery_grant",
        "chiplog.deployment_trust.record.v1", "deployment_trust", "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.prepared_self_delivery_policy",
        "chiplog.deployment_trust.record.v1", "deployment_trust", "deployment_trust.commit",
    ),
)

CURRENT_RECORD_EXTENSION_EVIDENCE = (
    ("chiplog.deployment_trust.prepared_external_delivery_grant",
     "tests/composition/test_j7_mounted_external_delivery_grant.py::"
     "test_mounted_j7_issue_appends_and_reopens_historical_lifecycle"),
    ("chiplog.deployment_trust.prepared_self_delivery_policy",
     "tests/platform/test_prepared_self_delivery_policy_lineage.py::"
     "test_issued_policy_is_physically_anchored_current_and_survives_reopen"),
)

CURRENT_CONTRACT_EXTENSIONS_SHA256 = (
    "bbfdecc4469314da62d3fdcbfa38c5c55231dc450df34b332ea6665c6f62a52a"
)


def verify_current_contract_extensions(
    exports: tuple[CurrentExportExtension, ...] = CURRENT_EXPORT_EXTENSIONS,
    records: tuple[FrozenRecord, ...] = CURRENT_RECORD_EXTENSIONS,
) -> str:
    references = tuple(item.reference for item in exports)
    record_ids = tuple(item.record_type_id for item in records)
    if (
        references != tuple(sorted(set(references)))
        or record_ids != tuple(sorted(set(record_ids)))
        or set(references) & {item.reference for item in R4_R5_EXPORTS}
        or set(record_ids) & {item.record_type_id for item in R4_R5_RECORDS}
    ):
        raise ValueError("current contract extensions overlap, duplicate or change order")
    body = json.dumps({
        "exports": [asdict(item) for item in exports],
        "records": [asdict(item) for item in records],
        "record_evidence": CURRENT_RECORD_EXTENSION_EVIDENCE,
    }, sort_keys=True, separators=(",", ":")).encode()
    digest = sha256(b"chiplog.current-contract-extensions.v1\x00" + body).hexdigest()
    if digest != CURRENT_CONTRACT_EXTENSIONS_SHA256:
        raise ValueError("current contract extensions differ from registered bytes")
    return digest


def current_export_references() -> frozenset[str]:
    verify_current_contract_extensions()
    return frozenset(item.reference for item in (*R4_R5_EXPORTS, *CURRENT_EXPORT_EXTENSIONS))


def current_record_ids(owner: str) -> tuple[str, ...]:
    verify_current_contract_extensions()
    return tuple(sorted(
        item.record_type_id for item in (*R4_R5_RECORDS, *CURRENT_RECORD_EXTENSIONS)
        if item.owner == owner
    ))
