from pathlib import Path

import pytest

from chiplog.verification.stage1 import (
    STAGE1_INCREMENTS,
    STAGE1_SLICES,
    validate_stage1_registry,
)

ROOT = Path(__file__).resolve().parents[2]


def test_stage1_requires_composed_evidence_and_all_predecessor_components() -> None:
    validate_stage1_registry(ROOT, STAGE1_INCREMENTS, STAGE1_SLICES)
    for increment in STAGE1_INCREMENTS:
        missing = {key: value for key, value in STAGE1_SLICES.items() if key != increment}
        with pytest.raises(ValueError):
            validate_stage1_registry(ROOT, STAGE1_INCREMENTS, missing)
    with pytest.raises(ValueError):
        validate_stage1_registry(ROOT, tuple(reversed(STAGE1_INCREMENTS)), STAGE1_SLICES)


@pytest.mark.parametrize("paths", [(), ("absent",), STAGE1_SLICES["R10"], STAGE1_SLICES["R12"] * 2])
def test_stage1_rejects_missing_aliased_duplicate_or_substituted_composed_slice(
    paths: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        validate_stage1_registry(ROOT, STAGE1_INCREMENTS, {**STAGE1_SLICES, "R12": paths})


@pytest.mark.parametrize("failed", STAGE1_INCREMENTS)
def test_stage1_failure_cannot_be_hidden_by_passing_predecessors(
    failed: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chiplog.verification import runner
    from chiplog.verification.models import CheckResult

    monkeypatch.setattr(
        runner, "_check_stage0", lambda root: [CheckResult("stage0", "PASS", "fixture", {})]
    )
    reached = []

    def run_slice(
        root: Path, increment: str, paths: tuple[str, ...], *, stage: str = "stage0"
    ) -> CheckResult:
        reached.append(increment)
        return CheckResult(
            f"{stage}.{increment.lower()}",
            "FAIL" if increment == failed else "PASS",
            "controlled component outcome",
            {},
        )

    monkeypatch.setattr(runner, "_run_test_slice", run_slice)
    result, _ = runner.run_profile(ROOT, "stage1")
    assert result["status"] == "FAIL"
    assert tuple(reached) == STAGE1_INCREMENTS
    assert result["eligibility"] == {
        "ready": False,
        "evaluation_authorized": False,
        "production_authorized": False,
        "adoption": "HOLD_ADOPTION",
    }
