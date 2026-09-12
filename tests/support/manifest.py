"""Non-vacuous closed universe used to attack the R2 verifier."""

from hashlib import sha256

from chiplog.architecture import (
    R1_SIGNATURES,
    ArchitectureManifest,
    BridgeBinding,
    CapabilityDecl,
    ExecutableRoot,
    ExportDecl,
    ImportEdge,
    PackageDecl,
    ProviderBinding,
    RecordOwnership,
    RouteEdge,
    SurfaceDecl,
)


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


REFERENCE = ArchitectureManifest(
    generation="r2-synthetic-v1",
    schema_version=1,
    evidentiary=True,
    r1_signatures=R1_SIGNATURES,
    packages=(
        PackageDecl("chiplog.adapters.bridges", "adapter", "composition"),
        PackageDecl("chiplog.capabilities.effects", "capability", "effects"),
        PackageDecl("chiplog.capabilities.journal", "capability", "journal"),
        PackageDecl("chiplog.capabilities.planning", "capability", "planning"),
        PackageDecl("chiplog.composition", "composition", "composition"),
        PackageDecl("chiplog.domain_primitives", "domain_primitives", "shared"),
    ),
    exports=(
        ExportDecl(
            "chiplog.adapters.bridges:EffectsQueryProvider",
            "chiplog.adapters.bridges",
            "private",
            digest("effects-query-provider-v1"),
            "",
        ),
        ExportDecl(
            "chiplog.adapters.bridges:EffectsToPlanning",
            "chiplog.adapters.bridges",
            "private",
            digest("effects-to-planning-v1"),
            "",
        ),
        ExportDecl(
            "chiplog.adapters.bridges:JournalCommandProvider",
            "chiplog.adapters.bridges",
            "private",
            digest("journal-command-provider-v1"),
            "",
        ),
        ExportDecl(
            "chiplog.adapters.bridges:PlanningToJournal",
            "chiplog.adapters.bridges",
            "private",
            digest("planning-to-journal-v1"),
            "",
        ),
        ExportDecl(
            "chiplog.capabilities.effects:EvidenceQuery",
            "chiplog.capabilities.effects",
            "public",
            digest("effects-query-v1"),
            "effects",
        ),
        ExportDecl(
            "chiplog.capabilities.journal:CandidateCommands",
            "chiplog.capabilities.journal",
            "public",
            digest("journal-command-v1"),
            "journal",
        ),
        ExportDecl(
            "chiplog.capabilities.planning:PlanningCommands",
            "chiplog.capabilities.planning",
            "public",
            digest("planning-command-v1"),
            "planning",
        ),
        ExportDecl(
            "chiplog.capabilities.planning:_PlanningFactory",
            "chiplog.capabilities.planning",
            "private",
            digest("planning-private-v1"),
            "planning",
        ),
        ExportDecl(
            "chiplog.composition:build_graph",
            "chiplog.composition",
            "private",
            digest("build-graph-v1"),
            "",
        ),
        ExportDecl(
            "chiplog.composition:main",
            "chiplog.composition",
            "private",
            digest("composition-main-v1"),
            "",
        ),
        *(
            ExportDecl(reference, "chiplog.domain_primitives", "public", signature, "")
            for reference, signature in R1_SIGNATURES
        ),
    ),
    capabilities=(
        CapabilityDecl("effects", "chiplog.capabilities.effects"),
        CapabilityDecl("journal", "chiplog.capabilities.journal"),
        CapabilityDecl("planning", "chiplog.capabilities.planning"),
    ),
    record_ownership=(
        RecordOwnership("CandidateEvidence", "journal", "journal.commit"),
        RecordOwnership("PlanningRevision", "planning", "planning.commit"),
        RecordOwnership("ProviderReceipt", "effects", "effects.commit"),
    ),
    bridges=(
        BridgeBinding(
            "effects_to_planning",
            "effects",
            "planning",
            "chiplog.adapters.bridges:EffectsToPlanning",
            "chiplog.capabilities.effects:EvidenceQuery",
            "chiplog.capabilities.planning:PlanningCommands",
        ),
        BridgeBinding(
            "planning_to_journal",
            "planning",
            "journal",
            "chiplog.adapters.bridges:PlanningToJournal",
            "chiplog.capabilities.planning:PlanningCommands",
            "chiplog.capabilities.journal:CandidateCommands",
        ),
    ),
    providers=(
        ProviderBinding(
            "chiplog.capabilities.effects:EvidenceQuery",
            "effects",
            "chiplog.adapters.bridges:EffectsQueryProvider",
        ),
        ProviderBinding(
            "chiplog.capabilities.journal:CandidateCommands",
            "journal",
            "chiplog.adapters.bridges:JournalCommandProvider",
        ),
    ),
    surfaces=(
        SurfaceDecl("cli", "planning", True),
        SurfaceDecl("effects.commit", "effects", False),
        SurfaceDecl("journal.commit", "journal", False),
        SurfaceDecl("planning.commit", "planning", False),
    ),
    executable_roots=(
        ExecutableRoot(
            "cli",
            "chiplog.composition:build_graph",
            "chiplog.composition:main",
            ("database", "graph", "workers"),
            ("workers", "graph", "database"),
            True,
        ),
    ),
    imports=(
        ImportEdge(
            "bridge_effects_port",
            "chiplog.adapters.bridges",
            "chiplog.capabilities.effects",
            "chiplog.capabilities.effects:EvidenceQuery",
        ),
        ImportEdge(
            "bridge_planning_port",
            "chiplog.adapters.bridges",
            "chiplog.capabilities.planning",
            "chiplog.capabilities.planning:PlanningCommands",
        ),
        ImportEdge(
            "planning_tenant_id",
            "chiplog.capabilities.planning",
            "chiplog.domain_primitives",
            "chiplog.domain_primitives:TenantId",
        ),
    ),
    routes=(
        RouteEdge("effects_to_planning", "effects", "planning", "effects_to_planning", "reverse"),
        RouteEdge(
            "planning_to_journal",
            "planning",
            "journal",
            "planning_to_journal",
            "forward",
        ),
    ),
)
