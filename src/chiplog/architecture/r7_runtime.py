"""Closed runtime-generation manifest for R7 composition and isolation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from typing import Literal

from .r8_implementation import R8_IMPLEMENTATION_FILES


@dataclass(frozen=True)
class OwnerProcessDecl:
    owner_id: str
    capability_ids: tuple[str, ...]
    public_operations: tuple[str, ...]
    provider_ids: tuple[str, ...]
    target_ids: tuple[str, ...]
    factory_ids: tuple[str, ...]
    scopes: tuple[str, ...]
    raw_capabilities: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutedCallDecl:
    operation_id: str
    caller_owner_id: str
    callee_owner_id: str
    request_schema_id: str
    result_schema_id: str


@dataclass(frozen=True)
class LeafBinding:
    leaf_id: str
    owner_id: str
    implementation: str


@dataclass(frozen=True)
class RuntimeAssemblyManifest:
    manifest_version: int
    environment: Literal["production", "evaluation"]
    broker_implementation: str
    broker_version: str
    config_schema_id: str
    ipc_security_profile: str
    application_loop_id: str
    owners: tuple[OwnerProcessDecl, ...]
    routes: tuple[RoutedCallDecl, ...]
    exclusive_resources: tuple[str, ...]
    broker_raw_capabilities: tuple[str, ...]
    leaves: tuple[LeafBinding, ...]

    def canonical_bytes(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()

    def fingerprint(self) -> str:
        return sha256(self.canonical_bytes()).hexdigest()


R7_PRODUCTION_MANIFEST = RuntimeAssemblyManifest(
    manifest_version=1,
    environment="production",
    broker_implementation="chiplog.platform.r7_runtime:AuthorityBrokerRuntime",
    broker_version="r7-broker-v1",
    config_schema_id="chiplog.runtime.config.v1",
    ipc_security_profile="unix-peercred-hmac-sha256-v1",
    application_loop_id="chiplog.r7.application-loop.v1",
    owners=(
        OwnerProcessDecl(
            "deployment_trust",
            (
                "deployment_trust.authenticate",
                "deployment_trust.bootstrap",
                "deployment_trust.revalidate",
                "deployment_trust.runtime_admission",
            ),
            (
                "deployment_trust.authenticate",
                "deployment_trust.bootstrap",
                "deployment_trust.revalidate",
                "deployment_trust.runtime_admission",
            ),
            ("chiplog.platform.r7_runtime:_OwnerProvider",),
            ("chiplog.capabilities.deployment_trust._r7_process:dispatch",),
            ("chiplog.platform.r7_runtime:_OwnerProvider.service",),
            ("APP",),
        ),
        OwnerProcessDecl(
            "planning",
            ("planning.create_intention_line",),
            ("planning.create_intention_line",),
            ("chiplog.platform.r7_runtime:_OwnerProvider",),
            ("chiplog.capabilities.planning._r7_process:dispatch",),
            ("chiplog.platform.r7_runtime:_OwnerProvider.service",),
            ("APP",),
        ),
        OwnerProcessDecl(
            "projections",
            ("projections.render_planning",),
            ("projections.render_planning",),
            ("chiplog.platform.r7_runtime:_OwnerProvider",),
            ("chiplog.capabilities.projections._r7_process:dispatch",),
            ("chiplog.platform.r7_runtime:_OwnerProvider.service",),
            ("APP",),
        ),
    ),
    routes=(
        RoutedCallDecl(
            "deployment_trust.authenticate",
            "broker",
            "deployment_trust",
            "chiplog.deployment-trust.owner-call.v1",
            "chiplog.deployment-trust.owner-result.v1",
        ),
        RoutedCallDecl(
            "deployment_trust.bootstrap",
            "broker",
            "deployment_trust",
            "chiplog.deployment-trust.owner-call.v1",
            "chiplog.deployment-trust.owner-result.v1",
        ),
        RoutedCallDecl(
            "deployment_trust.revalidate",
            "broker",
            "deployment_trust",
            "chiplog.deployment-trust.owner-call.v1",
            "chiplog.deployment-trust.owner-result.v1",
        ),
        RoutedCallDecl(
            "deployment_trust.runtime_admission",
            "broker",
            "deployment_trust",
            "chiplog.deployment-trust.owner-call.v1",
            "chiplog.deployment-trust.owner-result.v1",
        ),
        RoutedCallDecl(
            "planning.create_intention_line",
            "broker",
            "planning",
            "chiplog.planning.public.create.v1",
            "chiplog.planning.public.result.v1",
        ),
        RoutedCallDecl(
            "projections.render_planning",
            "broker",
            "projections",
            "chiplog.planning.public.render.v1",
            "chiplog.planning.public.render-result.v1",
        ),
    ),
    exclusive_resources=(
        "file_lock",
        "mutex",
        "owner_serialization_lane",
        "scheduler_lease_guard",
        "semaphore_permit",
        "sqlite_transaction",
        "sqlite_writer_slot",
    ),
    broker_raw_capabilities=(
        "broker_clock",
        "channel_transport",
        "credential_store",
        "event_appender",
        "lease_authority",
        "provider_sdk",
        "raw_sqlite_connection",
        "socket_handle",
    ),
    leaves=(
        LeafBinding("clock", "broker", "chiplog.platform.r7_leaves:ProductionClock"),
        LeafBinding(
            "planning_store", "broker", "chiplog.platform.r7_leaves:ProductionPlanningStore"
        ),
    ),
)

R7_EVALUATION_MANIFEST = replace(
    R7_PRODUCTION_MANIFEST,
    environment="evaluation",
    leaves=(
        LeafBinding("clock", "broker", "chiplog.platform.r7_leaves:EvaluationClock"),
        LeafBinding(
            "planning_store", "broker", "chiplog.platform.r7_leaves:EvaluationPlanningStore"
        ),
    ),
)


R8_PRODUCTION_MANIFEST = replace(
    R7_PRODUCTION_MANIFEST,
    manifest_version=2,
    broker_version="r8-broker-v1:"
    + sha256(json.dumps(R8_IMPLEMENTATION_FILES, sort_keys=True).encode()).hexdigest(),
    application_loop_id="chiplog.r8.application-loop.v1",
    owners=tuple(
        replace(
            owner,
            capability_ids=("planning.r8_create_intention_line",),
            public_operations=("planning.r8_create_intention_line",),
            target_ids=("chiplog.capabilities.planning._r8_process:dispatch",),
        )
        if owner.owner_id == "planning"
        else owner
        for owner in R7_PRODUCTION_MANIFEST.owners
    ),
    routes=tuple(
        replace(
            route,
            operation_id="planning.r8_create_intention_line",
            request_schema_id="chiplog.planning.public.create.v2",
        )
        if route.operation_id == "planning.create_intention_line"
        else route
        for route in R7_PRODUCTION_MANIFEST.routes
    ),
)
R8_EVALUATION_MANIFEST = replace(
    R8_PRODUCTION_MANIFEST, environment="evaluation", leaves=R7_EVALUATION_MANIFEST.leaves
)

R13_PRODUCTION_MANIFEST = replace(
    R8_PRODUCTION_MANIFEST,
    manifest_version=3,
    application_loop_id="chiplog.agent-loop.v1",
    leaves=(
        R8_PRODUCTION_MANIFEST.leaves[0],
        LeafBinding("model", "broker", "chiplog.adapters.driven.loop_hermetic:HermeticModel"),
        R8_PRODUCTION_MANIFEST.leaves[1],
    ),
    owners=(
        OwnerProcessDecl(
            "agent_loop",
            ("agent_loop.validate_transition",),
            ("agent_loop.validate_transition",),
            ("chiplog.platform.r7_runtime:_OwnerProvider",),
            ("chiplog.capabilities.agent_loop._r13_process:dispatch",),
            ("chiplog.platform.r7_runtime:_OwnerProvider.service",),
            ("APP",),
        ),
        *R8_PRODUCTION_MANIFEST.owners,
    ),
    routes=(
        RoutedCallDecl(
            "agent_loop.validate_transition",
            "broker",
            "agent_loop",
            "chiplog.agent-loop.transition.v1",
            "chiplog.agent-loop.record.v1",
        ),
        *R8_PRODUCTION_MANIFEST.routes,
    ),
)
R13_EVALUATION_MANIFEST = replace(R13_PRODUCTION_MANIFEST, environment="evaluation")

# Explicit local dialogue composition; never a substitution inside the offline manifest.
CODEX_CLI_MANIFEST = replace(
    R13_PRODUCTION_MANIFEST,
    manifest_version=8,
    leaves=tuple(
        replace(leaf, implementation="chiplog.adapters.driven.codex_model:CodexModel")
        if leaf.leaf_id == "model"
        else leaf
        for leaf in R13_PRODUCTION_MANIFEST.leaves
    ),
)

_SCHEDULER_ROUTES = tuple(
    RoutedCallDecl(
        f"scheduler.prepare_{kind}",
        "broker",
        "agent_loop",
        f"chiplog.scheduler.{kind}-preparation.v1",
        "chiplog.scheduler.prepared-lease.v1"
        if kind == "lease"
        else "chiplog.scheduler.prepared-batch.v1",
    )
    for kind in ("configuration", "interval", "lease", "rollover")
)
_DELIVERY_ROUTES = tuple(
    RoutedCallDecl(
        f"agent_loop.{operation}_delivery_completion",
        "broker",
        "agent_loop",
        f"chiplog.delivery.{operation}-completion.v1",
        "chiplog.agent-loop.record.v1",
    )
    for operation in ("prepare", "validate")
)
_LOOP_PREPARATION_OPERATIONS = (
    *(route.operation_id for route in _DELIVERY_ROUTES),
    "agent_loop.validate_transition",
    *(route.operation_id for route in _SCHEDULER_ROUTES),
)
_TELEGRAM_CANDIDATE_ROUTE = RoutedCallDecl(
    "deployment_trust.normalize_telegram_candidate",
    "broker",
    "deployment_trust",
    "chiplog.telegram.normalize.v1",
    "chiplog.telegram.normalized-candidate.v1",
)
_TRUST_CANDIDATE_OPERATIONS = tuple(
    sorted(
        (
            *R8_PRODUCTION_MANIFEST.owners[0].public_operations,
            _TELEGRAM_CANDIDATE_ROUTE.operation_id,
        )
    )
)
R14_PRODUCTION_MANIFEST = replace(
    R13_PRODUCTION_MANIFEST,
    manifest_version=4,
    owners=tuple(
        sorted(
            (
                replace(
                    R13_PRODUCTION_MANIFEST.owners[0],
                    capability_ids=_LOOP_PREPARATION_OPERATIONS,
                    public_operations=_LOOP_PREPARATION_OPERATIONS,
                    target_ids=("chiplog.capabilities.agent_loop._r14_process:dispatch",),
                ),
                OwnerProcessDecl(
                    "effects",
                    ("effects.prepare_transition",),
                    ("effects.prepare_transition",),
                    ("chiplog.platform.r7_runtime:_OwnerProvider",),
                    ("chiplog.capabilities.effects._process:dispatch",),
                    ("chiplog.platform.r7_runtime:_OwnerProvider.service",),
                    ("APP",),
                ),
                *(
                    replace(
                        owner,
                        capability_ids=_TRUST_CANDIDATE_OPERATIONS,
                        public_operations=_TRUST_CANDIDATE_OPERATIONS,
                        target_ids=("chiplog.capabilities.deployment_trust._r17_process:dispatch",),
                    )
                    if owner.owner_id == "deployment_trust"
                    else owner
                    for owner in R8_PRODUCTION_MANIFEST.owners
                ),
            ),
            key=lambda owner: owner.owner_id,
        )
    ),
    routes=tuple(
        sorted(
            (
                R13_PRODUCTION_MANIFEST.routes[0],
                *_DELIVERY_ROUTES,
                _TELEGRAM_CANDIDATE_ROUTE,
                *_SCHEDULER_ROUTES,
                RoutedCallDecl(
                    "effects.prepare_transition",
                    "broker",
                    "effects",
                    "chiplog.effects.prepare.v1",
                    "chiplog.effects.prepared-publication.v1",
                ),
                *R8_PRODUCTION_MANIFEST.routes,
            ),
            key=lambda route: (route.callee_owner_id, route.operation_id),
        )
    ),
)
R14_EVALUATION_MANIFEST = replace(R14_PRODUCTION_MANIFEST, environment="evaluation")

# Versions 5 and 6 belong to the independently developed R14 calls/fanout profiles.
# Preserve the v4 partition; preparation is not permission to publish a disposition.
R16_PRODUCTION_MANIFEST = replace(
    R14_PRODUCTION_MANIFEST,
    manifest_version=7,
    owners=tuple(
        replace(
            owner,
            capability_ids=("effects.prepare_denial", "effects.prepare_transition"),
            public_operations=("effects.prepare_denial", "effects.prepare_transition"),
            target_ids=("chiplog.capabilities.effects._r16_process:dispatch",),
        )
        if owner.owner_id == "effects"
        else owner
        for owner in R14_PRODUCTION_MANIFEST.owners
    ),
    routes=tuple(
        sorted(
            (
                *R14_PRODUCTION_MANIFEST.routes,
                RoutedCallDecl(
                    "effects.prepare_denial",
                    "broker",
                    "effects",
                    "chiplog.effects.before-send-preparation.v2",
                    "chiplog.effects.before-send-prepared-publication.v2",
                ),
            ),
            key=lambda route: (route.callee_owner_id, route.operation_id),
        )
    ),
)
R16_EVALUATION_MANIFEST = replace(R16_PRODUCTION_MANIFEST, environment="evaluation")


_CALL_PREPARATION_ROUTES = (
    RoutedCallDecl(
        "agent_loop.prepare_consequential_acceptance",
        "broker",
        "agent_loop",
        "chiplog.call.acceptance-preparation.v1",
        "chiplog.call.preparation-result.v1",
    ),
    RoutedCallDecl(
        "agent_loop.prepare_pre_accept_cancellation",
        "broker",
        "agent_loop",
        "chiplog.call.cancellation-preparation.v1",
        "chiplog.call.preparation-result.v1",
    ),
)
_CALL_PREPARATION_OPERATIONS = tuple(
    sorted((*_LOOP_PREPARATION_OPERATIONS, *(row.operation_id for row in _CALL_PREPARATION_ROUTES)))
)
R14_CALLS_PRODUCTION_MANIFEST = replace(
    R14_PRODUCTION_MANIFEST,
    manifest_version=5,
    owners=tuple(
        replace(
            owner,
            capability_ids=_CALL_PREPARATION_OPERATIONS,
            public_operations=_CALL_PREPARATION_OPERATIONS,
            target_ids=("chiplog.capabilities.agent_loop._r14_calls_process:dispatch",),
        )
        if owner.owner_id == "agent_loop"
        else owner
        for owner in R14_PRODUCTION_MANIFEST.owners
    ),
    routes=tuple(
        sorted(
            (*R14_PRODUCTION_MANIFEST.routes, *_CALL_PREPARATION_ROUTES),
            key=lambda route: (route.callee_owner_id, route.operation_id),
        )
    ),
)
R14_CALLS_EVALUATION_MANIFEST = replace(R14_CALLS_PRODUCTION_MANIFEST, environment="evaluation")


_FANOUT_ROUTE = RoutedCallDecl(
    "agent_loop.prepare_captured_fan_out",
    "broker",
    "agent_loop",
    "chiplog.call.captured-fanout-preparation.v1",
    "chiplog.call.captured-fanout-result.v1",
)
_FANOUT_OPERATIONS = tuple(sorted((*_CALL_PREPARATION_OPERATIONS, _FANOUT_ROUTE.operation_id)))
R14_FANOUT_PRODUCTION_MANIFEST = replace(
    R14_CALLS_PRODUCTION_MANIFEST,
    manifest_version=6,
    owners=tuple(
        replace(
            owner,
            capability_ids=_FANOUT_OPERATIONS,
            public_operations=_FANOUT_OPERATIONS,
            target_ids=("chiplog.capabilities.agent_loop._r14_fanout_process:dispatch",),
        )
        if owner.owner_id == "agent_loop"
        else owner
        for owner in R14_CALLS_PRODUCTION_MANIFEST.owners
    ),
    routes=tuple(
        sorted(
            (*R14_CALLS_PRODUCTION_MANIFEST.routes, _FANOUT_ROUTE),
            key=lambda route: (route.callee_owner_id, route.operation_id),
        )
    ),
)
R14_FANOUT_EVALUATION_MANIFEST = replace(R14_FANOUT_PRODUCTION_MANIFEST, environment="evaluation")


_CLI_CUSTODY_ROUTE = RoutedCallDecl(
    "deployment_trust.authenticate_cli_custody",
    "broker",
    "deployment_trust",
    "chiplog.cli.custody-owner-call.v1",
    "chiplog.cli.custody-decision.v1",
)
R17_CUSTODY_PRODUCTION_MANIFEST = replace(
    R14_PRODUCTION_MANIFEST,
    manifest_version=9,
    owners=tuple(
        replace(
            owner,
            capability_ids=tuple(sorted((*owner.capability_ids, _CLI_CUSTODY_ROUTE.operation_id))),
            public_operations=tuple(
                sorted((*owner.public_operations, _CLI_CUSTODY_ROUTE.operation_id))
            ),
            target_ids=("chiplog.capabilities.deployment_trust._cli_custody_process:dispatch",),
        )
        if owner.owner_id == "deployment_trust"
        else owner
        for owner in R14_PRODUCTION_MANIFEST.owners
    ),
    routes=tuple(
        sorted(
            (*R14_PRODUCTION_MANIFEST.routes, _CLI_CUSTODY_ROUTE),
            key=lambda route: (route.callee_owner_id, route.operation_id),
        )
    ),
)
R17_CUSTODY_EVALUATION_MANIFEST = replace(R17_CUSTODY_PRODUCTION_MANIFEST, environment="evaluation")


class RuntimeManifestViolation(ValueError):
    pass


def _require_canonical_unique(values: tuple[str, ...], label: str) -> None:
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise RuntimeManifestViolation(f"{label} must be a canonical unique exact set")


def verify_runtime_manifest(manifest: RuntimeAssemblyManifest) -> str:
    if manifest.manifest_version not in (1, 2, 3, 4, 5, 6, 7, 8, 9):
        raise RuntimeManifestViolation("unknown runtime manifest version")
    expected_manifest = {
        1: R7_PRODUCTION_MANIFEST,
        2: R8_PRODUCTION_MANIFEST,
        3: R13_PRODUCTION_MANIFEST,
        4: R14_PRODUCTION_MANIFEST,
        5: R14_CALLS_PRODUCTION_MANIFEST,
        6: R14_FANOUT_PRODUCTION_MANIFEST,
        7: R16_PRODUCTION_MANIFEST,
        8: CODEX_CLI_MANIFEST,
        9: R17_CUSTODY_PRODUCTION_MANIFEST,
    }[manifest.manifest_version]
    owner_ids = tuple(item.owner_id for item in manifest.owners)
    _require_canonical_unique(owner_ids, "owners")
    if owner_ids != tuple(owner.owner_id for owner in expected_manifest.owners):
        raise RuntimeManifestViolation("owner process partition differs from R7 freeze")
    for owner in manifest.owners:
        _require_canonical_unique(owner.capability_ids, f"{owner.owner_id} capabilities")
        _require_canonical_unique(owner.public_operations, f"{owner.owner_id} operations")
        _require_canonical_unique(owner.provider_ids, f"{owner.owner_id} providers")
        _require_canonical_unique(owner.target_ids, f"{owner.owner_id} targets")
        _require_canonical_unique(owner.factory_ids, f"{owner.owner_id} factories")
        _require_canonical_unique(owner.scopes, f"{owner.owner_id} scopes")
        if owner.raw_capabilities:
            raise RuntimeManifestViolation("owner process must be authority-empty")
    expected_owners = expected_manifest.owners
    if manifest.owners != expected_owners:
        raise RuntimeManifestViolation(
            "owner capabilities or public operations differ from R7 freeze"
        )
    _require_canonical_unique(manifest.exclusive_resources, "exclusive resources")
    _require_canonical_unique(manifest.broker_raw_capabilities, "broker raw capabilities")
    if manifest.broker_raw_capabilities != R7_PRODUCTION_MANIFEST.broker_raw_capabilities:
        raise RuntimeManifestViolation("broker raw capability closure differs from R7 freeze")
    expected_leaf_owners = tuple((leaf.leaf_id, leaf.owner_id) for leaf in expected_manifest.leaves)
    if tuple((item.leaf_id, item.owner_id) for item in manifest.leaves) != expected_leaf_owners:
        raise RuntimeManifestViolation(
            "registered leaf set or original owner differs from R7 freeze"
        )
    if any(not item.implementation for item in manifest.leaves):
        raise RuntimeManifestViolation("registered leaf implementation is empty")
    if (
        manifest.manifest_version in (3, 4, 5, 6, 7, 8, 9)
        and manifest.leaves != expected_manifest.leaves
    ):
        raise RuntimeManifestViolation("R13 admits only registered hermetic leaf implementations")
    route_callers = (*owner_ids, "broker")
    if any(route.caller_owner_id not in route_callers for route in manifest.routes) or any(
        route.callee_owner_id not in owner_ids for route in manifest.routes
    ):
        raise RuntimeManifestViolation("route references unknown owner")
    graph: dict[str, set[str]] = {owner_id: set() for owner_id in route_callers}
    for route in manifest.routes:
        graph[route.caller_owner_id].add(route.callee_owner_id)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(owner: str) -> None:
        if owner in visiting:
            raise RuntimeManifestViolation("synchronous public-port route cycle")
        if owner in visited:
            return
        visiting.add(owner)
        for callee in graph[owner]:
            visit(callee)
        visiting.remove(owner)
        visited.add(owner)

    for owner_id in owner_ids:
        visit(owner_id)
    if manifest.routes != expected_manifest.routes:
        raise RuntimeManifestViolation("public-port route set differs from R7 freeze")
    return manifest.fingerprint()


def verify_production_evaluation_equivalence(
    production: RuntimeAssemblyManifest, evaluation: RuntimeAssemblyManifest
) -> None:
    verify_runtime_manifest(production)
    verify_runtime_manifest(evaluation)
    if production.environment != "production" or evaluation.environment != "evaluation":
        raise RuntimeManifestViolation("production/evaluation environment identity mismatch")
    production_core = replace(production, environment="evaluation", leaves=evaluation.leaves)
    if production_core != evaluation:
        raise RuntimeManifestViolation("production/evaluation runtime security topology differs")
    production_leaf_owners = {(item.leaf_id, item.owner_id) for item in production.leaves}
    evaluation_leaf_owners = {(item.leaf_id, item.owner_id) for item in evaluation.leaves}
    if production_leaf_owners != evaluation_leaf_owners:
        raise RuntimeManifestViolation("leaf substitution escaped its original owner")


__all__ = [
    "R7_EVALUATION_MANIFEST",
    "R7_PRODUCTION_MANIFEST",
    "R8_EVALUATION_MANIFEST",
    "R8_PRODUCTION_MANIFEST",
    "LeafBinding",
    "OwnerProcessDecl",
    "RoutedCallDecl",
    "RuntimeAssemblyManifest",
    "RuntimeManifestViolation",
    "verify_production_evaluation_equivalence",
    "verify_runtime_manifest",
]
