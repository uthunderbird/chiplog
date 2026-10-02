"""One reviewed offline scheduler source/runtime registration.

Trusted bootstrap must load this module and its reviewed manifest. This is exact
artifact matching, not portable interpreter attestation or live authorization.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import sys
import sysconfig
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Literal, cast

from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerCanonicalMember
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    ConfigurationPreparationRequest,
    IntervalPreparationRequest,
    LeasePreparationRequest,
    MandateIssuancePreparationRequest,
    RolloverPreparationRequest,
    prepare_configuration,
    prepare_interval_request,
    prepare_lease,
    prepare_mandate_issuance,
    prepare_rollover_request,
)
from chiplog.capabilities.agent_loop.scheduler_startup import (
    SchedulerStartupHold,
    SchedulerStartupIndex,
    SelectedSchedulerBatch,
    validate_selected_scheduler_records,
)
from chiplog.composition.r15_mandate_scope_registry import registered_scope_sources
from chiplog.composition.scheduler_v1_compatibility import require_v1_compatible
from chiplog.platform.authority_reads import AuthorityCommitmentJournal
from chiplog.platform.owner_decision_journal import IndependentOwnerDecisionJournal
from chiplog.platform.scheduler_reads import (
    HistoricalSchedulerRequest,
    SchedulerSourceAdmissionUnresolved,
    read_materialized_scheduler,
)
from chiplog.verification.scheduler_schema import derive_schema_manifest

_ROOT = Path(__file__).resolve().parents[3]
_MANIFEST = Path(__file__).with_name("scheduler_source_manifest.json")
_MANIFEST_SHA = "8d8c28b7619703ff0e39d40e314de2427f361a6bc84050411b4dec1c11e3f7e7"
_MANIFEST_V2 = Path(__file__).with_name("scheduler_source_manifest_v2.json")
_MANIFEST_V2_SHA = "459d2ecb4477d751502ba80ac5742bb9fac719ba46e4a859c62c66a61897935e"
_MANIFEST_V2B = Path(__file__).with_name("scheduler_source_manifest_v2b.json")
_MANIFEST_V2B_SHA = "2b65fc6af4bbbe149e738295d725386e498b7977709bf85735bc15a6a2107717"
_MANIFEST_V2C = Path(__file__).with_name("scheduler_source_manifest_v2c.json")
_MANIFEST_V2C_SHA = "3fa9a22edbd83b5d77c84c42a2b5a8e8d4d7d304bed8a803896ac503861a7299"
_MANIFEST_V2D = Path(__file__).with_name("scheduler_source_manifest_v2d.json")
_MANIFEST_V2D_SHA = "2f0fa7506bd3edbd3f097104f4f868eb9fe907061468df326b1a2af37a2ce385"
_V2B_CLOSURE_SHA = "928b044048cfed0e9cd62677c5e8e040e221bd7d649945959b90918d17e3d49a"
_MODULES = (
    "chiplog.composition.r15_lease_service_auth",
    "chiplog.composition.r15_lease_publication_v2b",
    "chiplog.composition.r15_lease_v2b_canonical",
    "chiplog.capabilities.deployment_trust.operator_policy_command_verifier",
    "chiplog.capabilities.deployment_trust._r7_process",
    "chiplog.composition.r15_lease_acquire",
    "chiplog.composition.r15_lease_policy",
    "chiplog.composition.r15_lease_publication",
    "chiplog.capabilities.agent_loop.scheduler_startup",
    "chiplog.capabilities.agent_loop.scheduler_rollover",
    "chiplog.capabilities.agent_loop.scheduler_materialization",
    "chiplog.capabilities.agent_loop.scheduler_domain",
    "chiplog.capabilities.agent_loop.scheduler_contracts",
    "chiplog.capabilities.agent_loop.recovery_contracts",
    "chiplog.capabilities.agent_loop.contracts",
    "chiplog.capabilities.agent_loop.live_contract",
    "chiplog.capabilities.agent_loop.domain",
    "chiplog.capabilities.agent_loop.delivery_preparation",
    "chiplog.capabilities.agent_loop.delivery_contracts",
    "chiplog.capabilities.agent_loop.response_parsing",
    "chiplog.capabilities.agent_loop.scheduler_preparation",
    "chiplog.capabilities.agent_loop.scheduler_execution_contracts",
    "chiplog.capabilities.agent_loop.scheduler_mandate_lifecycle_contracts",
    "chiplog.capabilities.agent_loop.scheduler_configuration",
    "chiplog.capabilities.agent_loop.scheduler_leases",
    "chiplog.verification.scheduler_schema",
    "chiplog.composition.r15_mandate_scope_registry",
    "chiplog.composition.scheduler_v1_compatibility",
)
_PACKAGES = (
    ("pydantic", "pydantic"),
    ("pydantic-core", "pydantic_core"),
    ("annotated-types", "annotated_types"),
    ("typing-extensions", "typing_extensions"),
    ("typing-inspection", "typing_inspection"),
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _tree(root: Path, *, stdlib: bool = False) -> tuple[tuple[str, str], ...]:
    """Full relative inventory; caches excluded, symlink trees unsupported."""
    if root.is_symlink():
        raise ValueError("unsupported symbolic runtime root")
    if root.is_file():
        return ((root.name, _sha(root.read_bytes())),)
    result: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        if stdlib and "site-packages" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError("unsupported symbolic runtime tree")
        if path.is_file():
            result.append((relative.as_posix(), _sha(path.read_bytes())))
    if not result:
        raise ValueError("missing runtime artifact tree")
    return tuple(result)


def _loaded_module(name: str) -> ModuleType:
    module = sys.modules.get(name)
    if not isinstance(module, ModuleType) or getattr(module, "__file__", None) is None:
        raise ValueError("required compiler or serializer module is not loaded")
    return module


def _observe() -> tuple[dict[str, object], dict[str, bytes], dict[str, object]]:
    """Observed artifacts only. Their existence never creates a trusted pin."""
    sources: dict[str, bytes] = {}
    from chiplog.composition import r15_lease_publication_v2b

    _ = r15_lease_publication_v2b
    registered_scope_sources()
    for name in _MODULES:
        module = _loaded_module(name)
        source = _ROOT / "src" / Path(*name.split(".")).with_suffix(".py")
        if module.__file__ is None or Path(module.__file__).resolve() != source.resolve():
            raise ValueError("loaded compiler path differs from registered source")
        sources[name] = source.read_bytes()
    inventories: dict[str, object] = {}
    stdlib = _tree(Path(sysconfig.get_path("stdlib")), stdlib=True)
    inventories["stdlib"] = stdlib
    packages: dict[str, object] = {}
    for distribution, name in _PACKAGES:
        module = _loaded_module(name)
        if module.__file__ is None:
            raise ValueError("missing serializer artifact")
        origin = Path(module.__file__)
        inventory = _tree(origin.parent if origin.name == "__init__.py" else origin)
        inventories[distribution] = inventory
        packages[distribution] = {
            "version": version(distribution),
            "tree_sha256": _sha(_canonical(inventory)),
        }
    if sysconfig.get_config_var("Py_ENABLE_SHARED") != 1:
        raise ValueError("unsupported interpreter linkage")
    library = Path(sysconfig.get_config_var("LIBDIR")) / sysconfig.get_config_var("LDLIBRARY")
    profile: dict[str, object] = {
        "format": "chiplog.scheduler.source-profile.v2d",
        "historical_v2c": {
            "manifest_sha256": _MANIFEST_V2C_SHA,
            "evaluator_sha256": _sha(
                Path(__file__)
                .with_name("scheduler_v2c_sources")
                .joinpath("r15_lease_publication_v2b.py.source")
                .read_bytes()
            ),
            "closure_sha256": _sha(
                Path(__file__)
                .with_name("scheduler_v2c_sources")
                .joinpath("closure.json.source")
                .read_bytes()
            ),
        },
        "historical_v2b_closure_sha256": _V2B_CLOSURE_SHA,
        "execution_crypto": {
            "version": version("cryptography"),
            "tree_sha256": _sha(
                _canonical(_tree(Path(cast(str, _loaded_module("cryptography").__file__)).parent))
            ),
        },
        "canonicalization": "sorted-compact-utf8-json-no-ascii-escaping-v1",
        "sources": {name: _sha(raw) for name, raw in sorted(sources.items())},
        "lock_sha256": _sha((_ROOT / "uv.lock").read_bytes()),
        "runtime": {
            "implementation": sys.implementation.name,
            "cache_tag": sys.implementation.cache_tag,
            "version": sys.version,
            "platform": sys.platform,
            "soabi": sysconfig.get_config_var("SOABI"),
            "executable_sha256": _sha(Path(sys.executable).resolve(strict=True).read_bytes()),
            "shared_library_sha256": _sha(library.resolve(strict=True).read_bytes()),
            "stdlib_tree_sha256": _sha(_canonical(stdlib)),
        },
        "packages": packages,
        "historical_v1": {
            "manifest_sha256": _MANIFEST_SHA,
            "preparation_sha256": _sha(
                (
                    Path(__file__).with_name("scheduler_v1_sources")
                    / "scheduler_preparation.py.source"
                ).read_bytes()
            ),
            "startup_sha256": _sha(
                (
                    Path(__file__).with_name("scheduler_v1_sources") / "scheduler_startup.py.source"
                ).read_bytes()
            ),
            "delivery_sha256": _sha(
                (
                    Path(__file__).with_name("scheduler_v1_sources")
                    / "delivery_contracts.py.source"
                ).read_bytes()
            ),
            "lock_sha256": _sha(
                (Path(__file__).with_name("scheduler_v1_sources") / "uv.lock.source").read_bytes()
            ),
        },
    }
    return profile, sources, inventories


@dataclass(frozen=True)
class SourceRegistrationUnresolved:
    reason: str
    disposition: Literal["SOURCE_ADMISSION_UNRESOLVED"] = "SOURCE_ADMISSION_UNRESOLVED"


@dataclass(frozen=True)
class SchedulerSourceRegistration:
    reference: Present
    canonical_manifest: bytes


@dataclass(frozen=True)
class AdmittedSchedulerStartup:
    registration: SchedulerSourceRegistration
    cut: SchedulerSourceAdmissionUnresolved
    index: SchedulerStartupIndex
    disposition: Literal["SOURCE_ADMITTED"] = "SOURCE_ADMITTED"


def _require_historical_equality(
    original: HistoricalSchedulerRequest, selected: SelectedSchedulerBatch
) -> None:
    """Historical equality only: computed bytes never become recovery outputs."""
    request = original.preparation
    try:
        if original.selection.prepared.request.operation == "scheduler.decide_interval":
            from chiplog.composition.r15_tick_evidence import validate_selected_tick

            validate_selected_tick(original.selection)
        if isinstance(request, ConfigurationPreparationRequest):
            records = prepare_configuration(request).records
        elif isinstance(request, IntervalPreparationRequest):
            records = prepare_interval_request(request).records
        elif isinstance(request, LeasePreparationRequest):
            from chiplog.composition.r15_lease_publication import validate_selected_lease

            validate_selected_lease(original.selection.prepared.request)
            lease = prepare_lease(request)
            records = (
                SchedulerCanonicalMember(
                    record_kind=lease.record_kind,
                    record_id=lease.record_id,
                    schema_id=lease.schema_id,
                    canonical_base64=base64.b64encode(lease.canonical_record_bytes).decode(),
                    fingerprint=lease.fingerprint,
                ),
            )
        elif isinstance(request, RolloverPreparationRequest):
            records = prepare_rollover_request(request).records
        elif isinstance(request, MandateIssuancePreparationRequest):
            records = prepare_mandate_issuance(request).records
        else:
            raise ValueError("unknown historical compiler family")
        if records != selected.records:
            raise ValueError("original historical request differs from selected records")
    except ValueError as error:
        raise SchedulerStartupHold(selected.tenant_id, selected.command_id, str(error)) from error


def current_scheduler_registration() -> SchedulerSourceRegistration | SourceRegistrationUnresolved:
    """Match reviewed V2 current bytes; retain V1 only for exact selected history."""
    try:
        _require_v2b_closure()
        raw_manifest = _MANIFEST_V2D.read_bytes()
        if _sha(raw_manifest) != _MANIFEST_V2D_SHA:
            return SourceRegistrationUnresolved("reviewed V2 manifest bytes differ")
        expected = json.loads(raw_manifest)
        observed, sources, _ = _observe()
        if observed != expected:
            return SourceRegistrationUnresolved("reviewed source/runtime artifacts differ")
        require_v1_compatible(observed)
        prefix = "chiplog.capabilities.agent_loop."
        graph = derive_schema_manifest(
            sources[prefix + "scheduler_materialization"],
            sources[prefix + "scheduler_rollover"],
        )
        canonical = _canonical(
            {"profile": expected, "generated_dependency_manifest": asdict(graph)}
        )
        digest = _sha(canonical)
        return SchedulerSourceRegistration(
            Present(head="scheduler-source-registry-v2d:" + digest, fingerprint=digest), canonical
        )
    except (OSError, ValueError, TypeError, ImportError, KeyError) as error:
        return SourceRegistrationUnresolved(str(error))


def _require_v2b_closure() -> None:
    raw = Path(__file__).with_name("scheduler_v2b_sources").joinpath("closure.json").read_bytes()
    if _sha(raw) != _V2B_CLOSURE_SHA:
        raise ValueError("historical V2b evaluator closure manifest changed")
    for name, fingerprint in json.loads(raw).items():
        path = _ROOT / "src" / Path(*name.split(".")).with_suffix(".py")
        if _sha(path.read_bytes()) != fingerprint:
            raise ValueError("historical V2b evaluator dependency changed: " + name)


def _historical_v2b_registration(current_profile: dict[str, object]) -> SchedulerSourceRegistration:
    _require_v2b_closure()
    raw = _MANIFEST_V2B.read_bytes()
    if _sha(raw) != _MANIFEST_V2B_SHA:
        raise ValueError("historical V2b reviewed manifest changed")
    old = json.loads(raw)
    for name, fingerprint in old["sources"].items():
        path = _ROOT / "src" / Path(*name.split(".")).with_suffix(".py")
        if name == "chiplog.composition.r15_lease_publication":
            path = (
                Path(__file__)
                .with_name("scheduler_v2b_sources")
                .joinpath("r15_lease_publication.py.source")
            )
        if _sha(path.read_bytes()) != fingerprint:
            raise ValueError("historical V2b evaluator artifact changed: " + name)
    for key in ("runtime", "packages", "lock_sha256", "historical_v1"):
        if old[key] != current_profile[key]:
            raise ValueError("historical V2b source closure changed: " + key)
    graph = derive_schema_manifest(
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_materialization.py").read_bytes(),
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_rollover.py").read_bytes(),
    )
    canonical = _canonical({"profile": old, "generated_dependency_manifest": asdict(graph)})
    digest = _sha(canonical)
    return SchedulerSourceRegistration(
        Present(head="scheduler-source-registry-v2b:" + digest, fingerprint=digest), canonical
    )


def _require_v2c_evaluator_equivalent(old: dict[str, object]) -> None:
    """Only an annotation-only import may differ; all runtime semantics stay exact."""
    archive = Path(__file__).with_name("scheduler_v2c_sources")
    raw = archive.joinpath("r15_lease_publication_v2b.py.source").read_bytes()
    old_sources = cast(dict[str, str], old["sources"])
    if _sha(raw) != old_sources["chiplog.composition.r15_lease_publication_v2b"]:
        raise ValueError("historical V2c evaluator archive changed")
    prior = ast.parse(raw)
    current = ast.parse(Path(__file__).with_name("r15_lease_publication_v2b.py").read_bytes())
    guards = [
        node
        for node in current.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "TYPE_CHECKING"
    ]
    if len(guards) != 1:
        raise ValueError("historical V2c annotation import relocation differs")
    guard = guards[0]
    expected_import = ast.parse(
        "from chiplog.platform.owner_publications import SelectedOwnerDecision"
    ).body[0]
    if guard.orelse or len(guard.body) != 1 or ast.dump(guard.body[0]) != ast.dump(expected_import):
        raise ValueError("historical V2c annotation import relocation differs")
    current.body = [expected_import if node is guard else node for node in current.body]
    typings = [
        node
        for node in current.body
        if isinstance(node, ast.ImportFrom) and node.module == "typing"
    ]
    if len(typings) != 1:
        raise ValueError("historical V2c static import marker differs")
    markers = [alias for alias in typings[0].names if alias.name == "TYPE_CHECKING"]
    if len(markers) != 1 or markers[0].asname is not None:
        raise ValueError("historical V2c static import marker differs")
    typings[0].names = [alias for alias in typings[0].names if alias.name != "TYPE_CHECKING"]
    if ast.dump(current, include_attributes=False) != ast.dump(prior, include_attributes=False):
        raise ValueError("historical V2c evaluator runtime AST changed")
    raw_closure = archive.joinpath("closure.json.source").read_bytes()
    if _sha(raw_closure) != old["historical_v2b_closure_sha256"]:
        raise ValueError("historical V2c dependency archive changed")
    old_closure = json.loads(raw_closure)
    current_closure = json.loads(
        Path(__file__).with_name("scheduler_v2b_sources").joinpath("closure.json").read_bytes()
    )
    for name, fingerprint in current_closure.items():
        if (
            name != "chiplog.composition.r15_lease_publication_v2b"
            and old_closure.get(name) != fingerprint
        ):
            raise ValueError("historical V2c retained runtime dependency changed: " + name)


def _historical_v2c_registration(current_profile: dict[str, object]) -> SchedulerSourceRegistration:
    raw = _MANIFEST_V2C.read_bytes()
    if _sha(raw) != _MANIFEST_V2C_SHA:
        raise ValueError("historical V2c reviewed manifest changed")
    old = json.loads(raw)
    _require_v2b_closure()
    _require_v2c_evaluator_equivalent(old)
    for name, fingerprint in old["sources"].items():
        if (
            name != "chiplog.composition.r15_lease_publication_v2b"
            and cast(dict[str, str], current_profile["sources"]).get(name) != fingerprint
        ):
            raise ValueError("historical V2c evaluator artifact changed: " + name)
    for key in (
        "canonicalization",
        "runtime",
        "packages",
        "lock_sha256",
        "historical_v1",
        "execution_crypto",
    ):
        if old[key] != current_profile[key]:
            raise ValueError("historical V2c source closure changed: " + key)
    graph = derive_schema_manifest(
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_materialization.py").read_bytes(),
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_rollover.py").read_bytes(),
    )
    canonical = _canonical({"profile": old, "generated_dependency_manifest": asdict(graph)})
    digest = _sha(canonical)
    return SchedulerSourceRegistration(
        Present(head="scheduler-source-registry-v2c:" + digest, fingerprint=digest), canonical
    )


def _historical_v2a_registration(current_profile: dict[str, object]) -> SchedulerSourceRegistration:
    raw = _MANIFEST_V2.read_bytes()
    if _sha(raw) != _MANIFEST_V2_SHA:
        raise ValueError("historical V2a reviewed manifest changed")
    old = json.loads(raw)
    for name, fingerprint in old["sources"].items():
        if cast(dict[str, str], current_profile["sources"]).get(name) != fingerprint:
            raise ValueError("historical V2a evaluator artifact changed: " + name)
    for key in ("runtime", "packages", "lock_sha256", "historical_v1"):
        if old[key] != current_profile[key]:
            raise ValueError("historical V2a source closure changed: " + key)
    graph = derive_schema_manifest(
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_materialization.py").read_bytes(),
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_rollover.py").read_bytes(),
    )
    canonical = _canonical({"profile": old, "generated_dependency_manifest": asdict(graph)})
    digest = _sha(canonical)
    return SchedulerSourceRegistration(
        Present(head="scheduler-source-registry-v2:" + digest, fingerprint=digest), canonical
    )


def _historical_v1_registration(current_profile: dict[str, object]) -> SchedulerSourceRegistration:
    old = require_v1_compatible(current_profile)
    sources = cast(dict[str, str], old["sources"])
    prefix = "chiplog.capabilities.agent_loop."
    graph = derive_schema_manifest(
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_materialization.py").read_bytes(),
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_rollover.py").read_bytes(),
    )
    if (
        _sha(
            (
                _ROOT / "src/chiplog/capabilities/agent_loop/scheduler_materialization.py"
            ).read_bytes()
        )
        != sources[prefix + "scheduler_materialization"]
        or _sha((_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_rollover.py").read_bytes())
        != sources[prefix + "scheduler_rollover"]
    ):
        raise ValueError("V1 schema compiler sources differ")
    canonical = _canonical({"profile": old, "generated_dependency_manifest": asdict(graph)})
    digest = _sha(canonical)
    return SchedulerSourceRegistration(
        Present(head="scheduler-source-registry-v1:" + digest, fingerprint=digest), canonical
    )


def read_admitted_scheduler_startup(
    database: Path,
    tenant_id: str,
    journal: IndependentOwnerDecisionJournal,
    commitments: AuthorityCommitmentJournal,
) -> AdmittedSchedulerStartup | SourceRegistrationUnresolved:
    """Acquire the physical cut here; source admission does not release authority."""
    registration = current_scheduler_registration()
    if isinstance(registration, SourceRegistrationUnresolved):
        return registration
    cut = read_materialized_scheduler(database, tenant_id, journal, commitments)
    observed, _, _ = _observe()
    legacy = _historical_v1_registration(observed)
    v2a = _historical_v2a_registration(observed)
    v2b = _historical_v2b_registration(observed)
    v2c = _historical_v2c_registration(observed)
    rank = 0
    for historical, selected in zip(cut.historical_requests, cut.selected, strict=True):
        expected = historical.selection.prepared.request.expected
        admitted = {
            legacy.reference.head: (0, legacy.reference),
            v2a.reference.head: (1, v2a.reference),
            v2b.reference.head: (2, v2b.reference),
            v2c.reference.head: (3, v2c.reference),
            registration.reference.head: (4, registration.reference),
        }
        candidate = admitted.get(historical.registry.head)
        if candidate is None or historical.registry != candidate[1] or candidate[0] < rank:
            return SourceRegistrationUnresolved("unknown selected or regressed source registry")
        rank = candidate[0]
        if (
            rank == 0
            and historical.selection.prepared.request.operation == "scheduler.issue_mandate"
        ):
            return SourceRegistrationUnresolved("V1 registry cannot issue V2 mandates")
        if historical.selection.prepared.request.operation == "scheduler.acquire" and rank not in (
            2,
            3,
            4,
        ):
            return SourceRegistrationUnresolved("lease acquire requires V2b selected source")
        if (
            expected.registry_head != historical.registry.head
            or expected.registry_fingerprint != historical.registry.fingerprint
        ):
            return SourceRegistrationUnresolved("unknown selected historical source registry")
        if historical.selection.prepared.request.operation == "scheduler.acquire":
            from chiplog.composition.r15_lease_publication import validate_selected_lease_decision

            schema = (
                "chiplog.r15.lease-publication-evidence.v1"
                if rank == 2
                else "chiplog.r15.lease-publication-evidence.v2"
            )
            authentication = historical.selection.prepared.request.authentication
            if getattr(authentication, "applicability_schema", None) != schema:
                return SourceRegistrationUnresolved(
                    "lease evaluator differs from selected source version"
                )
            validate_selected_lease_decision(historical.selection, cut.protected_decisions)
        _require_historical_equality(historical, selected)
    physical_id = f"{cut.physical_database_path}:{cut.physical_device}:{cut.physical_inode}"
    index = validate_selected_scheduler_records(
        tenant_id=tenant_id,
        physical_database_id=physical_id,
        selected=cut.selected,
        materialized=cut.materialized,
    )
    if current_scheduler_registration() != registration:
        return SourceRegistrationUnresolved("source/runtime changed during historical validation")
    return AdmittedSchedulerStartup(registration, cut, index)
