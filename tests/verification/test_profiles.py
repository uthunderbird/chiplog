from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from chiplog.verification import CLOSED_PROFILES, run_profile
from chiplog.verification.artifacts import write_artifact
from chiplog.verification.models import FixtureRegistration
from chiplog.verification.registries import FIXTURES
from tests.support.stage0_delegation import confirm_reports, prepare_reports

ROOT = Path(__file__).resolve().parents[2]


def test_fast_profile_pass_is_narrow_and_holds_all_eligibility() -> None:
    result, artifact = run_profile(ROOT, "fast")

    assert result["status"] == "PASS"
    assert result["claim"] == "R0 verifier substrate and compile-only transcript contracts only"
    assert result["eligibility"] == {
        "ready": False,
        "evaluation_authorized": False,
        "production_authorized": False,
        "adoption": "HOLD_ADOPTION",
    }
    checks = cast(list[dict[str, object]], result["checks"])
    invariant_check = checks[0]
    observed = cast(dict[str, object], invariant_check["observed"])
    assert observed["evidenced_invariants"] == []
    entries = cast(list[dict[str, object]], observed["entries"])
    assert [entry["invariant_id"] for entry in entries] == [
        f"A{number:02d}" for number in range(1, 109)
    ]
    assert json.loads(artifact.read_text()) == result


@pytest.mark.parametrize("profile", sorted(set(CLOSED_PROFILES) - {"fast", "stage0", "stage1"}))
def test_unimplemented_profile_is_nonpassing_hold(profile: str) -> None:
    result, _ = run_profile(ROOT, profile)

    assert result["status"] == "HOLD"
    assert result["checks"] == []
    eligibility = cast(dict[str, object], result["eligibility"])
    assert eligibility["adoption"] == "HOLD_ADOPTION"


def test_stage0_evidences_r0_through_r8_and_holds_deployment_eligibility(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reports = tmp_path / "stage0-children"
    prepare_reports(request.config, monkeypatch, reports)
    result, artifact = run_profile(ROOT, "stage0")

    assert result["status"] == "PASS"
    checks = cast(list[dict[str, object]], result["checks"])
    assert [check["check_id"] for check in checks[:4]] == [
        "V0.invariant-source-exact-set",
        "V8.transcript-compile",
        "V0.surface-registry-generation",
        "V10.current-operation-surfaces",
    ]
    assert [check["check_id"] for check in checks[4:]] == [
        f"stage0.r{number}" for number in range(1, 9)
    ]
    assert all(check["status"] == "PASS" for check in checks)
    assert result["eligibility"] == {
        "ready": False,
        "evaluation_authorized": False,
        "production_authorized": False,
        "adoption": "HOLD_ADOPTION",
    }
    assert json.loads(artifact.read_text()) == result
    confirm_reports(request.config, result, reports)


@pytest.mark.parametrize(
    ("increments", "evidenced"),
    [
        ((), frozenset()),
        (
            ("R1", "R2", "R3", "R4", "R5", "R6", "R7"),
            frozenset({"R1", "R2", "R3", "R4", "R5", "R6", "R7"}),
        ),
        (
            ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R8"),
            frozenset({"R1", "R2", "R3", "R4", "R5", "R6", "R7"}),
        ),
        (
            ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"),
            frozenset({"R1", "R2", "UNKNOWN"}),
        ),
    ],
)
def test_stage0_rejects_empty_missing_duplicate_or_unknown_check_sets(
    increments: tuple[str, ...], evidenced: frozenset[str]
) -> None:
    from chiplog.verification import runner

    with pytest.raises(ValueError, match="stage0"):
        runner._validate_stage0_registry(increments, evidenced)


def test_unknown_profile_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown profile"):
        run_profile(ROOT, "unknown")


@pytest.mark.parametrize("failed_increment", [f"R{number}" for number in range(1, 9)])
def test_stage0_cannot_promote_with_any_failed_increment(
    failed_increment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chiplog.verification import runner
    from chiplog.verification.models import CheckResult

    reached: dict[str, tuple[str, ...]] = {}

    def fake_slice(root: Path, increment: str, paths: tuple[str, ...]) -> CheckResult:
        reached[increment] = paths
        return CheckResult(
            f"stage0.{increment.lower()}",
            "FAIL" if increment == failed_increment else "PASS",
            "controlled test outcome",
            {},
        )

    monkeypatch.setattr(runner, "_run_test_slice", fake_slice)
    result, artifact = run_profile(ROOT, "stage0")
    assert result["status"] == "FAIL"
    assert json.loads(artifact.read_text()) == result
    assert tuple(reached) == tuple(f"R{number}" for number in range(1, 9))
    assert reached["R8"] == (
        "tests/architecture/test_r8_current_surfaces.py",
        "tests/capabilities/planning/test_r8_authority.py",
        "tests/capabilities/planning/test_r8_authority_contract.py",
        "tests/cli/test_corrupt_store.py",
        "tests/cli/test_r8_default_hold.py",
        "tests/composition/test_planning_journeys.py",
        "tests/composition/test_r8_runtime.py",
        "tests/platform/test_r8_deployment_gate.py",
        "tests/platform/test_r8_gate_contract.py",
        "tests/verification/test_r8_surface.py",
    )


def test_stage0_rejects_missing_current_surface(monkeypatch: pytest.MonkeyPatch) -> None:
    from chiplog.verification import runner

    monkeypatch.setattr(runner, "R8_SURFACES", ())
    with pytest.raises(ValueError, match="surface inventory"):
        runner._check_stage0(ROOT)


def test_artifact_identity_changes_with_bound_input() -> None:
    from chiplog.verification import runner

    first = runner._input_identity(ROOT)
    original = runner.BOUND_INPUTS
    second = runner._input_identity(ROOT, bound_inputs=(*original, "README.md"))

    assert first["digest"] != second["digest"]


def test_artifact_is_content_addressed_and_never_overwritten() -> None:
    first_result, first_path = run_profile(ROOT, "fast")
    second_result, second_path = run_profile(ROOT, "fast")

    assert first_result == second_result
    assert first_path == second_path
    assert first_path.name == f"{first_path.stem}.json"
    assert not list(first_path.parent.glob(f".{first_path.stem}.*"))


@pytest.mark.parametrize(
    "addition",
    [
        lambda fixtures: (*fixtures, fixtures[0]),
        lambda fixtures: (*fixtures, FixtureRegistration("T05", "orphan", "RESERVED", None)),
        lambda fixtures: (
            *fixtures,
            FixtureRegistration("T01", "reserved-duplicate", "RESERVED", None),
        ),
    ],
)
def test_fast_rejects_fixture_registry_dilution(
    addition: Callable[[tuple[FixtureRegistration, ...]], tuple[FixtureRegistration, ...]],
) -> None:
    from chiplog.verification import runner

    with pytest.raises(ValueError, match=r"fixture|duplicate"):
        runner._validate_fixture_registry(addition(FIXTURES))


def test_input_identity_binds_dirty_file_bytes(tmp_path: Path) -> None:
    from chiplog.verification import runner

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    path = tmp_path / "unbound.txt"
    path.write_text("base")
    subprocess.run(["git", "add", "unbound.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    path.write_text("first dirty value")
    first = runner._input_identity(tmp_path, bound_inputs=(), fixtures=())
    path.write_text("second dirty value")
    second = runner._input_identity(tmp_path, bound_inputs=(), fixtures=())

    assert first["digest"] != second["digest"]


@pytest.mark.parametrize(
    ("generation", "surfaces"),
    [
        ("UNKNOWN", ()),
        ("R0_NO_PRODUCTION_SURFACES", ("unregistered-production-surface",)),
    ],
)
def test_fast_rejects_unknown_surface_registry(generation: str, surfaces: tuple[str, ...]) -> None:
    from chiplog.verification import runner

    with pytest.raises(ValueError, match=r"surface registry|production surface"):
        runner._validate_surface_registry(generation, surfaces)


def test_fast_path_rejects_substituted_surface_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.verification import runner

    monkeypatch.setattr(runner, "SURFACES", ("unregistered-production-surface",))

    with pytest.raises(ValueError, match="production surface"):
        runner._check_fast(ROOT)


def test_artifact_publish_never_overwrites_racing_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_link = os.link

    def racing_link(source: str | Path, target: str | Path) -> None:
        Path(target).write_bytes(b"conflicting partial bytes")
        real_link(source, target)

    monkeypatch.setattr(os, "link", racing_link)

    with pytest.raises(RuntimeError, match="artifact identity collision"):
        write_artifact(tmp_path, {"status": "PASS"})
