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
import tomllib
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
_MANIFEST_V3 = Path(__file__).with_name("scheduler_source_manifest_v3.json")
_MANIFEST_V3_SHA = "36041f23cf61e8834cfe3ac89ce7e884f1224f68be9e9694f501950e719a8ca2"
_MANIFEST_V3_REVISION2 = Path(__file__).with_name("scheduler_source_manifest_v3_revision2.json")
_MANIFEST_V3_REVISION2_SHA = "5d01204db2e1bf02c02fbd815d682a4d707b66d88ca34ea95fe13e636bdc7eec"
_V2B_CLOSURE_SHA = "928b044048cfed0e9cd62677c5e8e040e221bd7d649945959b90918d17e3d49a"
_MANIFEST_V3_REVISION3 = Path(__file__).with_name("scheduler_source_manifest_v3_revision3.json")
_MANIFEST_V3_REVISION3_SHA = "384e474aa8260710972b7e447eadaeb35ea5d482fca8c10acff06b2a44ab7582"
_MANIFEST_V3_REVISION4 = Path(__file__).with_name("scheduler_source_manifest_v3_revision4.json")
_MANIFEST_V3_REVISION4_SHA = "641801b242ec5f5aed7423a194a811323e66b568a1b760c372b39daca87b18b9"
_REVIEWED_SHARED_CAPTURE_SOURCE_HASHES = (
    "cb35358fccf08a07acc22f3046e4a1d5ce5875754bd3f505c6109064db5ee80a",
    "e655c248a479b64f73d3d87ad36080b5a4216d4819ef011ad34e90da30b64c90",
)
_REVIEWED_FORMATTED_SOURCE_HASHES: dict[str, tuple[str, str]] = {
    "chiplog.capabilities.agent_loop.scheduler_mandate_lifecycle_contracts": (
        "f1fb581e0fe34d32ae0e45a0592b8e5792843c82fb5f47fd0d076554e195e365",
        "aea44a2e955bea4026dae719e3753e5fa0372c62e64653726c0bce43d4aa0791",
    ),
    "chiplog.composition.r15_interval_service": (
        "96a91c9cc34f2c216a935cbad8c06088169c27a8c8bae393b37013777a10f995",
        "e30e268d3bca91dc9a94556ab62777f854e332c6d5bad2292ef15d69372604c0",
    ),
    "chiplog.composition.r15_tick_runtime": (
        "a4522bf0df4a6abc78d7277a20dacc6381a7c253052a52824eb875f307c18be6",
        "797a14de2280add3ac8829e27a99a8c683f53c519d7e8a3353f2c02366354ff5",
    ),
}
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
_V3_MODULES = (
    *_MODULES,
    "chiplog.platform._owner_publication_contracts",
    "chiplog.composition.r15_interval_policy",
    "chiplog.composition.r15_interval_policy_publication",
    "chiplog.composition.r15_interval_service_auth",
    "chiplog.composition.r15_interval_service",
    "chiplog.composition.r15_resolution_contracts",
    "chiplog.composition.r15_resolution_evidence",
    "chiplog.composition.r15_resolution_authority",
    "chiplog.composition.r15_resolution_runtime",
    "chiplog.composition.r15_scheduler_runtime",
    "chiplog.composition.r15_scheduler_history",
    "chiplog.composition.r15_tick_runtime",
    "chiplog.composition.r15_tick_evidence",
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
        if path.name == ".DS_Store":
            continue
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


def _observe(*, v3: bool = False) -> tuple[dict[str, object], dict[str, bytes], dict[str, object]]:
    """Observed artifacts only. Their existence never creates a trusted pin."""
    sources: dict[str, bytes] = {}
    from chiplog.composition import r15_lease_publication_v2b

    _ = r15_lease_publication_v2b
    registered_scope_sources()
    if v3:
        from chiplog.composition import (
            r15_interval_policy,
            r15_interval_policy_publication,
            r15_interval_service,
            r15_interval_service_auth,
            r15_resolution_authority,
            r15_resolution_contracts,
            r15_resolution_evidence,
            r15_resolution_runtime,
            r15_scheduler_history,
            r15_scheduler_runtime,
            r15_tick_evidence,
            r15_tick_runtime,
        )
        from chiplog.platform import _owner_publication_contracts

        _ = (
            r15_interval_policy,
            r15_interval_policy_publication,
            r15_interval_service,
            r15_interval_service_auth,
            r15_resolution_authority,
            r15_resolution_contracts,
            r15_resolution_evidence,
            r15_resolution_runtime,
            r15_scheduler_history,
            r15_scheduler_runtime,
            r15_tick_evidence,
            r15_tick_runtime,
            _owner_publication_contracts,
        )
    for name in _V3_MODULES if v3 else _MODULES:
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
        "format": "chiplog.scheduler.source-profile.v3"
        if v3
        else "chiplog.scheduler.source-profile.v2d",
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
    if v3:
        profile["historical_v2d"] = {"manifest_sha256": _MANIFEST_V2D_SHA}
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
            if original.selection.prepared.request.operation == "scheduler.resolve_interval":
                from chiplog.composition.r15_resolution_evidence import (
                    validate_selected_resolution,
                )

                validate_selected_resolution(original.selection)
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


def _historical_source_matches(name: str, raw: bytes, fingerprint: str) -> bool:
    """Recognize only reviewed exact source changes; retain every historical byte pin."""
    if _sha(raw) == fingerprint:
        return True
    formatted = _REVIEWED_FORMATTED_SOURCE_HASHES.get(name)
    if formatted is not None:
        return (fingerprint, _sha(raw)) == formatted
    if name == "chiplog.composition.r15_scheduler_history":
        return (fingerprint, _sha(raw)) == _REVIEWED_SHARED_CAPTURE_SOURCE_HASHES
    if name == "chiplog.capabilities.agent_loop.contracts":
        addition = (
            b"class LoopHold(LoopRejected):\n"
            b'    """A valid transition is intentionally held pending reco'
            b'very or authority."""\n\n\n'
        )
        return raw.count(addition) == 1 and _sha(raw.replace(addition, b"")) == fingerprint
    if name == "chiplog.capabilities.agent_loop.delivery_work_contracts":
        changes: tuple[tuple[bytes, bytes], ...] = (
            (b"from typing import Final, Literal\n", b"from typing import Literal\n"),
            (b"DELIVERY_WORK_SCHEMA: Final = ", b"DELIVERY_WORK_SCHEMA = "),
            (
                b"DELIVERY_WORK_GENESIS_COMMAND_SCHEMA: Final = ",
                b"DELIVERY_WORK_GENESIS_COMMAND_SCHEMA = ",
            ),
            (
                b"DELIVERY_WORK_CLAIM_COMMAND_SCHEMA: Final = ",
                b"DELIVERY_WORK_CLAIM_COMMAND_SCHEMA = ",
            ),
        )
        if any(raw.count(before) != 1 for before, _ in changes):
            return False
        for before, after in changes:
            raw = raw.replace(before, after)
        return _sha(raw) == fingerprint
    if name == "chiplog.platform._owner_publication_contracts":
        changes = (
            (
                b"from typing import Annotated, Final, Literal, Protocol\n",
                b"from typing import Annotated, Literal, Protocol\n",
            ),
            (b"DELIVERY_WORK_CLAIM_OPERATION: Final = ", b"DELIVERY_WORK_CLAIM_OPERATION = "),
        )
        if all(raw.count(before) == 1 for before, _ in changes):
            for before, after in changes:
                raw = raw.replace(before, after)
        if _sha(raw) == fingerprint:
            return True
        addition = b'    "r15.adopt_interval_service_policy",\n'
        return raw.count(addition) == 1 and _sha(raw.replace(addition, b"")) == fingerprint
    return False


def _historical_profile_source_matches(
    current_profile: dict[str, object], name: str, fingerprint: str
) -> bool:
    raw = (_ROOT / "src" / Path(*name.split(".")).with_suffix(".py")).read_bytes()
    sources = cast(dict[str, str], current_profile["sources"])
    return sources.get(name) == _sha(raw) and _historical_source_matches(name, raw, fingerprint)


def _v1_compatibility_profile(current_profile: dict[str, object]) -> dict[str, object]:
    raw = _MANIFEST.read_bytes()
    if _sha(raw) != _MANIFEST_SHA:
        raise ValueError("immutable V1 scheduler manifest differs")
    old = json.loads(raw)
    sources = dict(cast(dict[str, str], current_profile["sources"]))
    for name, fingerprint in old["sources"].items():
        if (
            name
            not in {
                "chiplog.capabilities.agent_loop.contracts",
                "chiplog.capabilities.agent_loop.delivery_work_contracts",
                "chiplog.capabilities.agent_loop.scheduler_mandate_lifecycle_contracts",
                "chiplog.platform._owner_publication_contracts",
            }
            or sources.get(name) == fingerprint
        ):
            continue
        if not _historical_profile_source_matches(current_profile, name, fingerprint):
            raise ValueError("V1 shared source differs: " + name)
        sources[name] = fingerprint
    projected = dict(current_profile)
    projected["sources"] = sources
    return projected


def _require_historical_lock(current_profile: dict[str, object], fingerprint: str) -> None:
    raw = (_ROOT / "uv.lock").read_bytes()
    if _sha(raw) != current_profile["lock_sha256"]:
        raise ValueError("observed dependency lock differs")
    if _sha(raw) == fingerprint:
        return
    archived = (
        Path(__file__).with_name("scheduler_v2b_sources").joinpath("uv.lock.source").read_bytes()
    )
    if _sha(archived) != fingerprint:
        raise ValueError("historical dependency lock archive differs")
    old = tomllib.loads(archived.decode())
    current = tomllib.loads(raw.decode())
    packages = cast(list[dict[str, object]], current["package"])
    old_packages = cast(list[dict[str, object]], old["package"])
    names = [cast(str, item["name"]) for item in packages]
    old_names = [cast(str, item["name"]) for item in old_packages]
    if len(set(names)) != len(names) or len(set(old_names)) != len(old_names):
        raise ValueError("duplicate dependency lock package")
    additions = {"orjson", "psycopg", "psycopg-binary", "tzdata"}
    if set(names) - set(old_names) != additions:
        raise ValueError("unreviewed dependency lock additions")
    projected = [item for item in packages if item["name"] not in additions]
    project = next(item for item in projected if item["name"] == "chiplog")
    dependencies = cast(list[dict[str, object]], project["dependencies"])
    metadata = cast(dict[str, object], project["metadata"])
    requirements = cast(list[dict[str, object]], metadata["requires-dist"])
    dependency_additions: tuple[dict[str, object], ...] = (
        {"name": "orjson"},
        {"name": "psycopg", "extra": ["binary"]},
    )
    for item in dependency_additions:
        if dependencies.count(item) != 1:
            raise ValueError("reviewed dependency lock extension differs")
        dependencies.remove(item)
    requirement_additions: tuple[dict[str, object], ...] = (
        {"name": "orjson", "specifier": ">=3.13.0"},
        {"name": "psycopg", "extras": ["binary"], "specifier": ">=3.3,<4"},
    )
    for item in requirement_additions:
        if requirements.count(item) != 1:
            raise ValueError("reviewed dependency lock requirement differs")
        requirements.remove(item)
    current["package"] = projected
    if current != old:
        raise ValueError("historical dependency lock contents changed")


def _original_v3_profile() -> dict[str, object]:
    raw = _MANIFEST_V3.read_bytes()
    if _sha(raw) != _MANIFEST_V3_SHA:
        raise ValueError("original V3 reviewed manifest changed")
    return cast(dict[str, object], json.loads(raw))


def _revision2_v3_profile() -> dict[str, object]:
    raw = _MANIFEST_V3_REVISION2.read_bytes()
    if _sha(raw) != _MANIFEST_V3_REVISION2_SHA:
        raise ValueError("revision 2 V3 reviewed manifest changed")
    return cast(dict[str, object], json.loads(raw))


def _revision3_v3_profile() -> dict[str, object]:
    raw = _MANIFEST_V3_REVISION3.read_bytes()
    if _sha(raw) != _MANIFEST_V3_REVISION3_SHA:
        raise ValueError("revision 3 V3 reviewed manifest changed")
    return cast(dict[str, object], json.loads(raw))


def _historical_v3_registration(current_profile: dict[str, object]) -> SchedulerSourceRegistration:
    return _historical_v3_profile_registration(current_profile, _original_v3_profile())


def _historical_v3_revision2_registration(
    current_profile: dict[str, object],
) -> SchedulerSourceRegistration:
    return _historical_v3_profile_registration(current_profile, _revision2_v3_profile())


def _historical_v3_revision3_registration(
    current_profile: dict[str, object],
) -> SchedulerSourceRegistration:
    return _historical_v3_profile_registration(current_profile, _revision3_v3_profile())


def _historical_v3_profile_registration(
    current_profile: dict[str, object], old: dict[str, object]
) -> SchedulerSourceRegistration:
    for name, fingerprint in cast(dict[str, str], old["sources"]).items():
        if not _historical_profile_source_matches(current_profile, name, fingerprint):
            raise ValueError("historical V3 evaluator artifact changed: " + name)
    _require_historical_lock(current_profile, cast(str, old["lock_sha256"]))
    projected = dict(current_profile)
    projected["sources"] = old["sources"]
    projected["lock_sha256"] = old["lock_sha256"]
    if projected != old:
        raise ValueError("historical V3 source closure changed")
    graph = derive_schema_manifest(
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_materialization.py").read_bytes(),
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_rollover.py").read_bytes(),
    )
    canonical = _canonical({"profile": old, "generated_dependency_manifest": asdict(graph)})
    digest = _sha(canonical)
    return SchedulerSourceRegistration(
        Present(head="scheduler-source-registry-v3:" + digest, fingerprint=digest), canonical
    )


def _capture_scheduler_registration() -> tuple[
    SchedulerSourceRegistration | SourceRegistrationUnresolved, dict[str, object]
]:
    """Match reviewed V3 bytes; retain V1 through V2d selected history."""
    try:
        _require_v2b_closure()
        _original_v3_profile()
        _revision2_v3_profile()
        _revision3_v3_profile()
        raw_manifest = _MANIFEST_V3_REVISION4.read_bytes()
        if _sha(raw_manifest) != _MANIFEST_V3_REVISION4_SHA:
            return SourceRegistrationUnresolved("reviewed V3 manifest bytes differ"), {}
        expected = json.loads(raw_manifest)
        observed, sources, _ = _observe(v3=True)
        if observed != expected:
            return SourceRegistrationUnresolved("reviewed source/runtime artifacts differ"), {}
        require_v1_compatible(_v1_compatibility_profile(observed))
        prefix = "chiplog.capabilities.agent_loop."
        graph = derive_schema_manifest(
            sources[prefix + "scheduler_materialization"],
            sources[prefix + "scheduler_rollover"],
        )
        canonical = _canonical(
            {"profile": expected, "generated_dependency_manifest": asdict(graph)}
        )
        digest = _sha(canonical)
        return (
            SchedulerSourceRegistration(
                Present(head="scheduler-source-registry-v3:" + digest, fingerprint=digest),
                canonical,
            ),
            observed,
        )
    except (OSError, ValueError, TypeError, ImportError, KeyError) as error:
        return SourceRegistrationUnresolved(str(error)), {}


def current_scheduler_registration() -> SchedulerSourceRegistration | SourceRegistrationUnresolved:
    """Match a fresh observation against the reviewed source and runtime bytes."""
    registration, _ = _capture_scheduler_registration()
    return registration


def _historical_v2d_registration(current_profile: dict[str, object]) -> SchedulerSourceRegistration:
    raw = _MANIFEST_V2D.read_bytes()
    if _sha(raw) != _MANIFEST_V2D_SHA:
        raise ValueError("historical V2d reviewed manifest changed")
    old = json.loads(raw)
    _require_historical_lock(current_profile, old["lock_sha256"])
    for name, fingerprint in old["sources"].items():
        if not _historical_profile_source_matches(current_profile, name, fingerprint):
            raise ValueError("historical V2d evaluator artifact changed: " + name)
    for key in (
        "canonicalization",
        "runtime",
        "packages",
        "historical_v1",
        "historical_v2c",
        "historical_v2b_closure_sha256",
        "execution_crypto",
    ):
        if current_profile[key] != old[key]:
            raise ValueError("historical V2d source closure changed: " + key)
    graph = derive_schema_manifest(
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_materialization.py").read_bytes(),
        (_ROOT / "src/chiplog/capabilities/agent_loop/scheduler_rollover.py").read_bytes(),
    )
    canonical = _canonical({"profile": old, "generated_dependency_manifest": asdict(graph)})
    digest = _sha(canonical)
    return SchedulerSourceRegistration(
        Present(head="scheduler-source-registry-v2d:" + digest, fingerprint=digest), canonical
    )


def _require_v2b_closure() -> None:
    raw = Path(__file__).with_name("scheduler_v2b_sources").joinpath("closure.json").read_bytes()
    if _sha(raw) != _V2B_CLOSURE_SHA:
        raise ValueError("historical V2b evaluator closure manifest changed")
    for name, fingerprint in json.loads(raw).items():
        path = _ROOT / "src" / Path(*name.split(".")).with_suffix(".py")
        if name == "chiplog.platform._owner_publication_contracts":
            archived = (
                Path(__file__)
                .with_name("scheduler_v2b_sources")
                .joinpath("_owner_publication_contracts.py.source")
                .read_bytes()
            )
            current = path.read_bytes()
            if _sha(archived) != fingerprint or not _historical_source_matches(
                name, current, fingerprint
            ):
                raise ValueError("historical V2b broker operation extension differs")
            continue
        if not _historical_source_matches(name, path.read_bytes(), fingerprint):
            raise ValueError("historical V2b evaluator dependency changed: " + name)


def _historical_v2b_registration(current_profile: dict[str, object]) -> SchedulerSourceRegistration:
    _require_v2b_closure()
    raw = _MANIFEST_V2B.read_bytes()
    if _sha(raw) != _MANIFEST_V2B_SHA:
        raise ValueError("historical V2b reviewed manifest changed")
    old = json.loads(raw)
    _require_historical_lock(current_profile, old["lock_sha256"])
    for name, fingerprint in old["sources"].items():
        path = _ROOT / "src" / Path(*name.split(".")).with_suffix(".py")
        if name == "chiplog.composition.r15_lease_publication":
            path = (
                Path(__file__)
                .with_name("scheduler_v2b_sources")
                .joinpath("r15_lease_publication.py.source")
            )
        if not _historical_source_matches(name, path.read_bytes(), fingerprint):
            raise ValueError("historical V2b evaluator artifact changed: " + name)
    for key in ("runtime", "packages", "historical_v1"):
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
    _require_historical_lock(current_profile, old["lock_sha256"])
    _require_v2b_closure()
    _require_v2c_evaluator_equivalent(old)
    for name, fingerprint in old["sources"].items():
        if (
            name != "chiplog.composition.r15_lease_publication_v2b"
            and not _historical_profile_source_matches(current_profile, name, fingerprint)
        ):
            raise ValueError("historical V2c evaluator artifact changed: " + name)
    for key in (
        "canonicalization",
        "runtime",
        "packages",
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
    _require_historical_lock(current_profile, old["lock_sha256"])
    for name, fingerprint in old["sources"].items():
        if not _historical_profile_source_matches(current_profile, name, fingerprint):
            raise ValueError("historical V2a evaluator artifact changed: " + name)
    for key in ("runtime", "packages", "historical_v1"):
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
    old = require_v1_compatible(_v1_compatibility_profile(current_profile))
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
    registration, observed = _capture_scheduler_registration()
    if isinstance(registration, SourceRegistrationUnresolved):
        return registration
    cut = read_materialized_scheduler(database, tenant_id, journal, commitments)
    legacy = _historical_v1_registration(observed)
    v2a = _historical_v2a_registration(observed)
    v2b = _historical_v2b_registration(observed)
    v2c = _historical_v2c_registration(observed)
    v2d = _historical_v2d_registration(observed)
    original_v3 = _historical_v3_registration(observed)
    revision2_v3 = _historical_v3_revision2_registration(observed)
    revision3_v3 = _historical_v3_revision3_registration(observed)
    from chiplog.composition.r15_interval_policy_publication import (
        validate_selected_interval_policy_decision,
        validate_selected_interval_service_decision,
    )
    from chiplog.composition.r15_resolution_evidence import validate_selected_resolution

    for decision in cut.protected_decisions:
        validate_selected_interval_policy_decision(decision, cut.protected_decisions)
        validate_selected_interval_service_decision(decision, cut.protected_decisions)
        validate_selected_resolution(decision)
    rank = 0
    for historical, selected in zip(cut.historical_requests, cut.selected, strict=True):
        expected = historical.selection.prepared.request.expected
        admitted = {
            legacy.reference.head: (0, legacy.reference),
            v2a.reference.head: (1, v2a.reference),
            v2b.reference.head: (2, v2b.reference),
            v2c.reference.head: (3, v2c.reference),
            v2d.reference.head: (4, v2d.reference),
            original_v3.reference.head: (5, original_v3.reference),
            revision2_v3.reference.head: (6, revision2_v3.reference),
            revision3_v3.reference.head: (7, revision3_v3.reference),
            registration.reference.head: (8, registration.reference),
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
            5,
            6,
            7,
            8,
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
        if (
            historical.selection.prepared.request.operation == "scheduler.decide_interval"
            and getattr(
                historical.selection.prepared.request.authentication,
                "applicability_schema",
                None,
            )
            == "chiplog.scheduler.interval-service-issuance.v3"
            and rank not in (5, 6, 7, 8)
        ):
            return SourceRegistrationUnresolved("interval service requires V3 selected source")
        if (
            historical.selection.prepared.request.operation == "scheduler.resolve_interval"
            and rank not in (5, 6, 7, 8)
        ):
            return SourceRegistrationUnresolved("interval resolution requires V3 selected source")
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
