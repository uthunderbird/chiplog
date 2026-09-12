from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from chiplog.architecture.r7_compatibility import verify_r7_compatibility_ledger

from .artifacts import write_artifact
from .identity import digest_file, sha256_bytes
from .invariants import extract_invariant_manifest
from .models import CheckResult, FixtureRegistration
from .r8_surface import SURFACES as R8_SURFACES
from .r8_surface import verify_offline_import_boundary, verify_r8_surfaces
from .registries import (
    CLOSED_PROFILES,
    FIXTURES,
    IMPLEMENTED_PROFILES,
    STAGE0_EVIDENCED_INCREMENTS,
    STAGE0_INCREMENT_REGISTRY,
    SURFACE_REGISTRY_GENERATION,
    SURFACES,
)
from .transcripts import compile_transcript

BOUND_INPUTS = (
    "pyproject.toml",
    "uv.lock",
    "design-docs/project-architecture/NORMATIVE.md",
    "design-docs/TRANSCRIPTS.md",
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def _git_bytes(root: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return completed.stdout


def _workspace_file_identity(path: Path) -> dict[str, str]:
    if path.is_symlink():
        return {"kind": "symlink", "digest": sha256_bytes(os.readlink(path).encode())}
    if path.is_file():
        return {"kind": "file", "digest": digest_file(path)}
    if not path.exists():
        return {"kind": "missing", "digest": sha256_bytes(b"MISSING")}
    return {"kind": "other", "digest": sha256_bytes(str(path.stat().st_mode).encode())}


def _input_identity(
    root: Path,
    bound_inputs: tuple[str, ...] = BOUND_INPUTS,
    fixtures: tuple[FixtureRegistration, ...] = FIXTURES,
) -> dict[str, object]:
    bound_files = {
        path: digest_file(root / path) for path in bound_inputs if (root / path).is_file()
    }
    for path in sorted((root / "src/chiplog/verification").glob("*.py")):
        bound_files[str(path.relative_to(root))] = digest_file(path)
    for registration in fixtures:
        if registration.relative_path is not None:
            path = root / registration.relative_path
            bound_files[registration.relative_path] = digest_file(path)
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    workspace_paths = {
        item.decode()
        for item in _git_bytes(root, "ls-files", "-co", "--exclude-standard", "-z").split(b"\0")
        if item
    }
    workspace_files = {
        path: _workspace_file_identity(root / path) for path in sorted(workspace_paths)
    }
    identity = {
        "head": _git(root, "rev-parse", "--verify", "HEAD"),
        "index": sha256_bytes(_git_bytes(root, "ls-files", "--stage", "-z")),
        "git_status": status.splitlines(),
        "bound_files": bound_files,
        "workspace_files": workspace_files,
    }
    return {**identity, "digest": sha256_bytes(json.dumps(identity, sort_keys=True).encode())}


def _check_fast(root: Path) -> list[CheckResult]:
    _validate_fixture_registry()
    _validate_surface_registry()
    source_digest, invariants = extract_invariant_manifest(
        root / "design-docs/project-architecture/NORMATIVE.md"
    )
    invariant_result = CheckResult(
        "V0.invariant-source-exact-set",
        "PASS",
        "canonical invariant source is contiguous, unique, and source-bound",
        {
            "count": len(invariants),
            "source_digest": source_digest,
            "entries": [
                {
                    "invariant_id": item.invariant_id,
                    "number": item.number,
                    "text_digest": item.text_digest,
                }
                for item in invariants
            ],
            "evidenced_invariants": [],
        },
    )
    active_paths = {
        registration.relative_path for registration in FIXTURES if registration.state == "ACTIVE"
    }
    discovered = {
        str(path.relative_to(root)) for path in (root / "design-docs/transcripts").glob("*.md")
    }
    if active_paths != discovered:
        raise ValueError(
            f"active transcript registry mismatch: active={active_paths}, discovered={discovered}"
        )
    compiled = []
    for registration in FIXTURES:
        if registration.state == "RESERVED":
            if registration.relative_path is not None:
                raise ValueError("reserved fixture must not have a path")
            continue
        assert registration.relative_path is not None
        item = compile_transcript(root / registration.relative_path)
        if item.scenario_id != registration.scenario_id:
            raise ValueError(f"scenario registry mismatch for {registration.fixture_id}")
        compiled.append(
            {
                "fixture_id": registration.fixture_id,
                "scenario_id": item.scenario_id,
                "source_digest": item.source_digest,
                "compiled_bundle_digest": item.compiled_bundle_digest,
            }
        )
    fixture_result = CheckResult(
        "V8.transcript-compile",
        "PASS",
        "active authored transcripts compile under the closed R0 schema",
        {
            "compiled": compiled,
            "reserved": [item.fixture_id for item in FIXTURES if item.state == "RESERVED"],
        },
    )
    surface_result = CheckResult(
        "V0.surface-registry-generation",
        "PASS",
        "R0 explicitly has no production surfaces",
        {"generation": SURFACE_REGISTRY_GENERATION, "surfaces": list(SURFACES)},
    )
    return [invariant_result, fixture_result, surface_result]


def _validate_fixture_registry(
    fixtures: tuple[FixtureRegistration, ...] = FIXTURES,
) -> None:
    expected = {
        "T01": (
            "calendar-proposal-confirmation",
            "ACTIVE",
            "design-docs/transcripts/calendar-proposal-confirmation.md",
        ),
        "T02": (
            "fact-claim-without-plan-change",
            "ACTIVE",
            "design-docs/transcripts/fact-claim-without-plan-change.md",
        ),
        "T03": (
            "unknown-calendar-outcome",
            "ACTIVE",
            "design-docs/transcripts/unknown-calendar-outcome.md",
        ),
        "T04": ("stale-proposal-after-head-change", "RESERVED", None),
    }
    if len(fixtures) != len(expected):
        raise ValueError("fixture registry must contain exactly T01-T04")
    observed: dict[str, tuple[str, str, str | None]] = {}
    for item in fixtures:
        if item.fixture_id in observed:
            raise ValueError(f"duplicate fixture id: {item.fixture_id}")
        observed[item.fixture_id] = (item.scenario_id, item.state, item.relative_path)
    if observed != expected:
        raise ValueError(f"fixture registry differs from closed R0 registry: {observed}")
    active = [item for item in fixtures if item.state == "ACTIVE"]
    if len({item.scenario_id for item in fixtures}) != len(fixtures):
        raise ValueError("fixture scenario ids must be unique")
    if len({item.relative_path for item in active}) != len(active):
        raise ValueError("active fixture paths must be unique")


def _validate_surface_registry(
    generation: str | None = None,
    surfaces: tuple[str, ...] | None = None,
) -> None:
    if generation is None:
        generation = SURFACE_REGISTRY_GENERATION
    if surfaces is None:
        surfaces = SURFACES
    if generation != "R0_NO_PRODUCTION_SURFACES":
        raise ValueError(f"unknown R0 surface registry generation: {generation}")
    if surfaces != ():
        raise ValueError(f"R0 production surface registry must be empty: {surfaces}")


def _validate_stage0_registry(
    increments: tuple[str, ...] = STAGE0_INCREMENT_REGISTRY,
    evidenced: frozenset[str] = STAGE0_EVIDENCED_INCREMENTS,
) -> None:
    expected = tuple(f"R{number}" for number in range(1, 9))
    if increments != expected or len(increments) != len(set(increments)):
        raise ValueError("stage0 increment registry must be the exact ordered R1-R8 set")
    if not evidenced or not evidenced <= set(increments):
        raise ValueError("stage0 evidenced increment set is empty or contains unknown rows")
    if evidenced != frozenset(expected):
        raise ValueError("stage0 evidenced increment set silently omits or adds a stage")


def _run_test_slice(root: Path, increment: str, paths: tuple[str, ...]) -> CheckResult:
    completed = subprocess.run(
        ["uv", "run", "pytest", "-q", *paths],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    return CheckResult(
        f"stage0.{increment.lower()}",
        "PASS" if completed.returncode == 0 else "FAIL",
        f"{increment} implementation evidence passes its frozen test slice",
        {
            "command": ["uv", "run", "pytest", "-q", *paths],
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    )


def _check_stage0(root: Path) -> list[CheckResult]:
    _validate_stage0_registry()
    substrate = _check_fast(root)
    verify_r8_surfaces(root, R8_SURFACES)
    verify_offline_import_boundary(root)
    compatibility_digest = verify_r7_compatibility_ledger(evidence_root=root)
    current_surfaces = CheckResult(
        "V10.current-operation-surfaces",
        "PASS",
        "R8 executable surfaces match the inventory and contain no external adapter",
        {
            "generation": "R8_OPERATION_SURFACES_V1",
            "surfaces": [
                {**asdict(surface), "downstream": list(surface.downstream)}
                for surface in R8_SURFACES
            ],
            "exposure": "HOLD",
            "compatibility_ledger_digest": compatibility_digest,
        },
    )
    slices = {
        "R1": ("tests/conformance/test_canonicalization.py",),
        "R2": ("tests/architecture", "tests/integration/test_r1_r2_contract.py"),
        "R3": ("tests/platform",),
        "R4": ("tests/deployment_trust",),
        "R5": ("tests/capabilities", "tests/integration/test_r4_r5_convergence.py"),
        "R6": (
            "tests/contracts/test_r6_public_component.py",
            "tests/contracts/test_r6_trust_bridge.py",
            "tests/integration/test_r6_cli.py",
        ),
        "R7": ("tests/r7", "tests/contracts/test_r7_public_boundary.py"),
        "R8": ("tests/r8", "tests/contracts/test_r8_public_boundary.py"),
    }
    checks = [
        _run_test_slice(root, increment, slices[increment])
        for increment in STAGE0_INCREMENT_REGISTRY
    ]
    if tuple(check.check_id.removeprefix("stage0.").upper() for check in checks) != (
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
        "R6",
        "R7",
        "R8",
    ):
        raise RuntimeError("stage0 check aggregation is not the exact R1-R8 set")
    return [*substrate, current_surfaces, *checks]


def run_profile(root: Path, profile: str) -> tuple[dict[str, object], Path]:
    if profile not in CLOSED_PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    inputs = _input_identity(root)
    if profile not in IMPLEMENTED_PROFILES:
        result: dict[str, object] = {
            "schema_version": 1,
            "profile": profile,
            "status": "HOLD",
            "reason": "profile verifier manifest is not implemented",
            "checks": [],
            "input_identity": inputs,
            "eligibility": {
                "ready": False,
                "evaluation_authorized": False,
                "production_authorized": False,
                "adoption": "HOLD_ADOPTION",
            },
        }
        return result, write_artifact(root, result)
    checks = _check_fast(root) if profile == "fast" else _check_stage0(root)
    if not checks:
        raise RuntimeError("an implemented profile cannot contain zero checks")
    status = (
        "PASS"
        if all(check.status == "PASS" for check in checks)
        else "HOLD"
        if any(check.status == "HOLD" for check in checks)
        and not any(check.status == "FAIL" for check in checks)
        else "FAIL"
    )
    result = {
        "schema_version": 1,
        "profile": profile,
        "status": status,
        "claim": (
            "R0 verifier substrate and compile-only transcript contracts only"
            if profile == "fast"
            else "R0-R8 Stage-0 implementation evidence; all deployment eligibility remains HOLD"
        ),
        "checks": [check.to_dict() for check in checks],
        "input_identity": inputs,
        "eligibility": {
            "ready": False,
            "evaluation_authorized": False,
            "production_authorized": False,
            "adoption": "HOLD_ADOPTION",
        },
    }
    return result, write_artifact(root, result)


__all__ = ["CLOSED_PROFILES", "run_profile"]
