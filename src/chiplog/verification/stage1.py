"""Closed Stage-1 component evidence; this is not a production-loop evaluator."""

from pathlib import Path

STAGE1_INCREMENTS = ("R9", "R10", "R11", "R12")
STAGE1_SLICES = {
    "R9": (
        "tests/composition/test_conversation_workspace.py",
        "tests/composition/test_workspace_surfaces.py",
        "tests/capabilities/projections/test_disclosure_provenance.py",
        "tests/capabilities/projections/test_workspace.py",
        "tests/capabilities/projections/test_planning_workspace_bridge.py",
        "tests/capabilities/projections/test_budget_policy.py",
    ),
    "R10": (
        "tests/capabilities/evidence_journal",
        "tests/composition/test_journal_restart_and_plan.py",
    ),
    "R11": (
        "tests/architecture/test_r9_r11_contract.py",
        "tests/composition/test_calendar_acquisition.py",
        "tests/platform/test_calendar_reads.py",
    ),
    "R12": (
        "tests/architecture/test_workspace_batch_contract.py",
        "tests/composition/test_workspace_batch.py",
        "tests/composition/test_workspace_journal.py",
        "tests/platform/test_workspace_snapshot.py",
        "tests/capabilities/projections/test_journal_workspace_bridge.py",
    ),
}

_EXPECTED_STAGE1_SLICES = tuple(STAGE1_SLICES.items())


def validate_stage1_registry(
    root: Path, increments: tuple[str, ...], slices: dict[str, tuple[str, ...]]
) -> None:
    if increments != ("R9", "R10", "R11", "R12") or tuple(slices) != increments:
        raise ValueError("Stage1 must contain exactly ordered R9-R12 evidence")
    if tuple(slices.items()) != _EXPECTED_STAGE1_SLICES:
        raise ValueError("Stage1 cannot substitute or omit registered component evidence")
    for increment, paths in slices.items():
        if not paths or len(paths) != len(set(paths)):
            raise ValueError(f"Stage1 {increment} has empty or duplicate evidence")
        if any(not (root / path).exists() for path in paths):
            raise ValueError(f"Stage1 {increment} evidence selector is absent")
    required = (
        "tests/architecture/test_workspace_batch_contract.py",
        "tests/composition/test_workspace_batch.py",
        "tests/composition/test_workspace_journal.py",
        "tests/platform/test_workspace_snapshot.py",
        "tests/capabilities/projections/test_journal_workspace_bridge.py",
    )
    if slices["R12"] != required:
        raise ValueError("Stage1 composed contract cannot substitute component-only evidence")


__all__ = ["STAGE1_INCREMENTS", "STAGE1_SLICES", "validate_stage1_registry"]
