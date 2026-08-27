"""Closed R4/R5 parallel-lane contract selected before either schema exists."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .manifests import (
    R1_SIGNATURES,
    ArchitectureManifest,
    CapabilityDecl,
    ExportDecl,
    PackageDecl,
    ProviderBinding,
    RecordOwnership,
    SurfaceDecl,
    signature_digest,
)
from .verifier import verify_manifest


@dataclass(frozen=True)
class FrozenExport:
    reference: str
    owner: str
    fields: tuple[tuple[str, str], ...]
    visibility: Literal["public", "private"] = "public"

    @property
    def signature_digest(self) -> str:
        return signature_digest(self.reference.rpartition(":")[2], self.fields)


@dataclass(frozen=True)
class FrozenRecord:
    record_type_id: str
    schema_id: str
    owner: str
    commit_boundary: str


R4_R5_PACKAGES: tuple[tuple[str, str], ...] = (
    ("chiplog.adapters.driven.deployment_trust", "composition"),
    ("chiplog.capabilities.deployment_trust", "deployment_trust"),
    ("chiplog.capabilities.planning", "planning"),
    ("chiplog.capabilities.projections", "projections"),
)

R4_R5_EXPORTS: tuple[FrozenExport, ...] = (
    FrozenExport(
        "chiplog.adapters.driven.deployment_trust:IndependentTenantDecisionJournal",
        "",
        (("path", "Path"),),
        "private",
    ),
    FrozenExport(
        "chiplog.adapters.driven.deployment_trust:SQLiteTrustMaterializer",
        "",
        (("database", "Path"),),
        "private",
    ),
    FrozenExport(
        "chiplog.capabilities.deployment_trust:AuthenticationRequest",
        "deployment_trust",
        (
            ("contour", "str"),
            ("credential_id", "str"),
            ("session_id", "str"),
            ("source_id", "str"),
            ("transport_witness_id", "str | None"),
        ),
    ),
    FrozenExport(
        "chiplog.capabilities.deployment_trust:TenantDecisionJournalPort",
        "deployment_trust",
        (("decision", "bytes"), ("predecessor", "str | None"), ("result", "str")),
    ),
    FrozenExport(
        "chiplog.capabilities.deployment_trust:TrustDecision",
        "deployment_trust",
        (
            ("disposition", "VALID | DENIED | STALE | INDETERMINATE"),
            ("reference", "TrustReference | None"),
            ("reason", "str | None"),
        ),
    ),
    FrozenExport(
        "chiplog.capabilities.deployment_trust:TrustMaterializationPort",
        "deployment_trust",
        (("decision_id", "str"), ("records", "tuple[bytes, ...]"), ("result", "str")),
    ),
    FrozenExport(
        "chiplog.capabilities.deployment_trust:TrustReference",
        "deployment_trust",
        (
            ("tenant_id", "TenantId"),
            ("principal_id", "PrincipalId"),
            ("contour", "str"),
            ("credential_head", "str"),
            ("session_head", "str"),
            ("source_head", "str"),
            ("trust_head", "str"),
            ("materialization_head", "str"),
            ("freshness_sequence", "int"),
        ),
    ),
    FrozenExport(
        "chiplog.capabilities.deployment_trust:TrustReferenceRevalidation",
        "deployment_trust",
        (("reference", "TrustReference"), ("operation", "str"), ("subject_id", "RecordId")),
    ),
    FrozenExport(
        "chiplog.capabilities.deployment_trust:TrustRevalidator",
        "deployment_trust",
        (("request", "TrustReferenceRevalidation"), ("result", "TrustDecision")),
    ),
    FrozenExport(
        "chiplog.capabilities.planning:CreateIntentionLine",
        "planning",
        (
            ("command_id", "RecordId"),
            ("intention_line_id", "RecordId"),
            ("revision_id", "RecordId"),
            ("purpose", "str"),
            ("authority_act_id", "str"),
        ),
    ),
    FrozenExport(
        "chiplog.capabilities.planning:InvocationContext",
        "planning",
        (
            ("tenant_id", "TenantId"),
            ("principal_id", "PrincipalId"),
            ("permission_scope", "PermissionScope"),
            ("trust_reference", "PlanningTrustReference"),
        ),
    ),
    FrozenExport(
        "chiplog.capabilities.planning:PlanningCommands",
        "planning",
        (("context", "InvocationContext"), ("command", "CreateIntentionLine")),
    ),
    FrozenExport(
        "chiplog.capabilities.planning:PlanningTrustDecision",
        "planning",
        (("disposition", "VALID | DENIED | STALE | INDETERMINATE"), ("reason", "str | None")),
    ),
    FrozenExport(
        "chiplog.capabilities.planning:PlanningTrustReference",
        "planning",
        (
            ("tenant_id", "TenantId"),
            ("principal_id", "PrincipalId"),
            ("contour", "str"),
            ("credential_head", "str"),
            ("session_head", "str"),
            ("source_head", "str"),
            ("trust_head", "str"),
            ("materialization_head", "str"),
            ("freshness_sequence", "int"),
        ),
    ),
    FrozenExport(
        "chiplog.capabilities.planning:TrustRevalidationPort",
        "planning",
        (
            ("reference", "PlanningTrustReference"),
            ("operation", "str"),
            ("subject_id", "RecordId"),
            ("result", "PlanningTrustDecision"),
        ),
    ),
    FrozenExport(
        "chiplog.capabilities.projections:PlanningProjectionQueries",
        "projections",
        (("tenant_id", "TenantId"), ("intention_line_id", "RecordId")),
    ),
)

R4_R5_RECORDS: tuple[FrozenRecord, ...] = (
    FrozenRecord(
        "chiplog.deployment_trust.channel_authentication_binding",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.current_database_state",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.database_genesis",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.deployment_tenant_binding",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.evidence_authentication_binding",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.evidence_source_head",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.identity_credential_head",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.poll_cursor_advance_authorized",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.poll_cursor_applied",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.poll_member_disposition",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.poll_response_page_manifest",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.principal_contour_prerequisite",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.principal_registry_entry",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.session_head",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.tenant_decision",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.tenant_principal_contour",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.tenant_registry_entry",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.transport_origin_witness",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.deployment_trust.trust_transition",
        "chiplog.deployment_trust.record.v1",
        "deployment_trust",
        "deployment_trust.commit",
    ),
    FrozenRecord(
        "chiplog.planning.authorization_evidence",
        "chiplog.planning.record.v1",
        "planning",
        "planning.commit",
    ),
    FrozenRecord(
        "chiplog.planning.committed_result",
        "chiplog.planning.record.v1",
        "planning",
        "planning.commit",
    ),
    FrozenRecord(
        "chiplog.planning.intention_line",
        "chiplog.planning.record.v1",
        "planning",
        "planning.commit",
    ),
    FrozenRecord(
        "chiplog.planning.intention_line_revision",
        "chiplog.planning.record.v1",
        "planning",
        "planning.commit",
    ),
    FrozenRecord(
        "chiplog.planning.planning_command",
        "chiplog.planning.record.v1",
        "planning",
        "planning.commit",
    ),
)

R4_R5_DERIVATIVE_SINKS = ("chiplog.projections.planning.v1",)
R4_R5_CAPABILITIES = ("deployment_trust", "planning", "projections")
R4_R5_SURFACES = (
    "deployment_trust.commit",
    "planning.commit",
    "projections.rebuild",
)
R4_R5_PROVIDERS = (
    ProviderBinding(
        "chiplog.capabilities.deployment_trust:TenantDecisionJournalPort",
        "deployment_trust",
        "chiplog.adapters.driven.deployment_trust:IndependentTenantDecisionJournal",
    ),
    ProviderBinding(
        "chiplog.capabilities.deployment_trust:TrustMaterializationPort",
        "deployment_trust",
        "chiplog.adapters.driven.deployment_trust:SQLiteTrustMaterializer",
    ),
)

R4_R5_ARCHITECTURE = ArchitectureManifest(
    generation="r4-r5-parallel-freeze-v1",
    schema_version=1,
    evidentiary=False,
    r1_signatures=R1_SIGNATURES,
    packages=tuple(
        PackageDecl(name, "adapter" if ".adapters." in name else "capability", owner)
        for name, owner in R4_R5_PACKAGES
    ),
    exports=tuple(
        ExportDecl(
            item.reference,
            item.reference.rpartition(":")[0],
            item.visibility,
            item.signature_digest,
            item.owner,
        )
        for item in R4_R5_EXPORTS
    ),
    capabilities=tuple(
        CapabilityDecl(name, f"chiplog.capabilities.{name}") for name in R4_R5_CAPABILITIES
    ),
    record_ownership=tuple(
        RecordOwnership(item.record_type_id, item.owner, item.commit_boundary)
        for item in R4_R5_RECORDS
    ),
    bridges=(),
    providers=R4_R5_PROVIDERS,
    surfaces=(
        SurfaceDecl("deployment_trust.commit", "deployment_trust", False),
        SurfaceDecl("planning.commit", "planning", False),
        SurfaceDecl("projections.rebuild", "projections", False),
    ),
    executable_roots=(),
    imports=(),
    routes=(),
)


def verify_r4_r5_freeze() -> None:
    """Reject any accidental ambiguity in the closed pre-parallel declaration."""

    collections = (
        tuple(name for name, _ in R4_R5_PACKAGES),
        tuple(item.reference for item in R4_R5_EXPORTS),
        tuple(item.record_type_id for item in R4_R5_RECORDS),
        R4_R5_DERIVATIVE_SINKS,
        R4_R5_CAPABILITIES,
        R4_R5_SURFACES,
        tuple(item.port for item in R4_R5_PROVIDERS),
    )
    for values in collections:
        if values != tuple(sorted(values)) or len(values) != len(set(values)):
            raise ValueError("R4/R5 freeze sets must be unique and canonically ordered")
    owners = set(R4_R5_CAPABILITIES)
    if any(owner not in owners | {"composition"} for _, owner in R4_R5_PACKAGES):
        raise ValueError("R4/R5 package owner is not frozen")
    if any(item.owner not in owners | {""} for item in R4_R5_EXPORTS):
        raise ValueError("R4/R5 export owner is not frozen")
    if any(item.owner not in owners for item in R4_R5_RECORDS):
        raise ValueError("R4/R5 record owner is not frozen")
    if {item.commit_boundary for item in R4_R5_RECORDS} - set(R4_R5_SURFACES):
        raise ValueError("R4/R5 record commit boundary is not frozen")
    package_owners = dict(R4_R5_PACKAGES)
    if {owner for owner in package_owners.values() if owner != "composition"} != owners:
        raise ValueError("R4/R5 package/capability sets differ")
    if set(R4_R5_DERIVATIVE_SINKS) != {"chiplog.projections.planning.v1"}:
        raise ValueError("R4/R5 derivative sink differs from freeze")
    expected = R4_R5_ARCHITECTURE
    if tuple(item.reference for item in expected.exports) != tuple(
        item.reference for item in R4_R5_EXPORTS
    ):
        raise ValueError("R4/R5 architecture export set differs from freeze")
    if tuple(item.record_type for item in expected.record_ownership) != tuple(
        item.record_type_id for item in R4_R5_RECORDS
    ):
        raise ValueError("R4/R5 architecture record set differs from freeze")
    if tuple(item.name for item in expected.surfaces) != R4_R5_SURFACES:
        raise ValueError("R4/R5 architecture surface set differs from freeze")
    if expected.providers != R4_R5_PROVIDERS:
        raise ValueError("R4/R5 architecture provider set differs from freeze")
    verify_manifest(expected, expected)
