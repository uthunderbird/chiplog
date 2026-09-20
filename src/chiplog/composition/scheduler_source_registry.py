"""One reviewed offline scheduler source/runtime registration.

Trusted bootstrap must load this module and its reviewed manifest. This is exact
artifact matching, not portable interpreter attestation or live authorization.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
import sysconfig
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Literal

from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.capabilities.agent_loop.scheduler_materialization import SchedulerCanonicalMember
from chiplog.capabilities.agent_loop.scheduler_preparation import (
    ConfigurationPreparationRequest,
    IntervalPreparationRequest,
    LeasePreparationRequest,
    RolloverPreparationRequest,
    prepare_configuration,
    prepare_interval_request,
    prepare_lease,
    prepare_rollover_request,
)
from chiplog.capabilities.agent_loop.scheduler_startup import (
    SchedulerStartupHold,
    SchedulerStartupIndex,
    SelectedSchedulerBatch,
    validate_selected_scheduler_records,
)
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
_MANIFEST_SHA = "92b67ed0d2a1759967aede361a14379f5f00c3d5864dbe984eee8e083cf27e54"
_MODULES = (
    "chiplog.capabilities.agent_loop.scheduler_startup",
    "chiplog.capabilities.agent_loop.scheduler_rollover",
    "chiplog.capabilities.agent_loop.scheduler_materialization",
    "chiplog.capabilities.agent_loop.scheduler_domain",
    "chiplog.capabilities.agent_loop.scheduler_contracts",
    "chiplog.capabilities.agent_loop.recovery_contracts",
    "chiplog.capabilities.agent_loop.contracts",
    "chiplog.capabilities.agent_loop.domain",
    "chiplog.capabilities.agent_loop.delivery_preparation",
    "chiplog.capabilities.agent_loop.delivery_contracts",
    "chiplog.capabilities.agent_loop.response_parsing",
    "chiplog.capabilities.agent_loop.scheduler_preparation",
    "chiplog.capabilities.agent_loop.scheduler_configuration",
    "chiplog.capabilities.agent_loop.scheduler_leases",
    "chiplog.verification.scheduler_schema",
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
        "format": "chiplog.scheduler.source-profile.v1",
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
        if isinstance(request, ConfigurationPreparationRequest):
            records = prepare_configuration(request).records
        elif isinstance(request, IntervalPreparationRequest):
            records = prepare_interval_request(request).records
        elif isinstance(request, LeasePreparationRequest):
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
        else:
            raise ValueError("unknown historical compiler family")
        if records != selected.records:
            raise ValueError("original historical request differs from selected records")
    except ValueError as error:
        raise SchedulerStartupHold(selected.tenant_id, selected.command_id, str(error)) from error


def current_scheduler_registration() -> SchedulerSourceRegistration | SourceRegistrationUnresolved:
    """Match one independent reviewed profile; never accept newly observed hashes."""
    try:
        raw_manifest = _MANIFEST.read_bytes()
        if _sha(raw_manifest) != _MANIFEST_SHA:
            return SourceRegistrationUnresolved("reviewed manifest bytes differ")
        expected = json.loads(raw_manifest)
        observed, sources, _ = _observe()
        if observed != expected:
            return SourceRegistrationUnresolved("reviewed source/runtime artifacts differ")
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
            Present(head="scheduler-source-registry-v1:" + digest, fingerprint=digest), canonical
        )
    except (OSError, ValueError, TypeError, ImportError, KeyError) as error:
        return SourceRegistrationUnresolved(str(error))


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
    for historical, selected in zip(cut.historical_requests, cut.selected, strict=True):
        expected = historical.selection.prepared.request.expected
        if (
            historical.registry != registration.reference
            or expected.registry_head != registration.reference.head
            or expected.registry_fingerprint != registration.reference.fingerprint
        ):
            return SourceRegistrationUnresolved("unknown selected historical source registry")
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
