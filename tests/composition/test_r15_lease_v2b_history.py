"""Actual pre-repair V2b bytes remain valid under the frozen complete evaluator."""

import ast
import gzip
import json
from pathlib import Path

import pytest

import chiplog.composition.r15_lease_publication as current
import chiplog.composition.r15_lease_publication_v2b as historical
import chiplog.composition.scheduler_source_registry as registry
from chiplog.platform._owner_publication_contracts import SingleOwnerBatch


def test_actual_v2b_selected_bytes_use_frozen_evaluator(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = Path(__file__).with_name("fixtures") / "r15_v2b_selected_batches.json.gz"
    batches = json.loads(gzip.decompress(fixture.read_bytes()))
    monkeypatch.setattr(current, "_EVIDENCE_SCHEMA", "deliberately-changed-current-v2c-schema")
    retained = [
        SingleOwnerBatch.model_validate_json(json.dumps(batch))
        for batch in batches
        if batch["operation"] in ("r15.adopt_lease_execution_policy", "scheduler.acquire")
    ]
    assert len(retained) == 2
    for batch in retained:
        assert current.validate_selected_lease(batch) == historical.validate_selected_lease(batch)


def test_frozen_v2b_validator_ast_and_dependency_closure(monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(historical.__file__)
    archive = source.with_name("scheduler_v2b_sources") / "r15_lease_publication.py.source"
    original = {
        node.name: ast.dump(node, include_attributes=False)
        for node in ast.parse(archive.read_bytes()).body
        if isinstance(node, ast.FunctionDef)
    }
    for node in ast.parse(source.read_bytes()).body:
        if isinstance(node, ast.FunctionDef):
            assert original[node.name] == ast.dump(node, include_attributes=False)
    from chiplog.composition import r15_lease_v2b_canonical as canonical

    helper_source = Path(canonical.__file__)
    helper_archive = source.with_name("scheduler_v2b_sources") / "r15_scheduler_registry.py.source"
    original_helpers = {
        node.name: ast.dump(node, include_attributes=False)
        for node in ast.parse(helper_archive.read_bytes()).body
        if isinstance(node, ast.FunctionDef)
    }
    for node in ast.parse(helper_source.read_bytes()).body:
        if isinstance(node, ast.FunctionDef):
            assert original_helpers[node.name] == ast.dump(node, include_attributes=False)
    registry._require_v2b_closure()
    closure = json.loads((source.with_name("scheduler_v2b_sources") / "closure.json").read_bytes())
    assert "chiplog.platform.r7_trust_durability" not in closure
    assert "chiplog.composition.r15_scheduler_registry" not in closure
    original_sha = registry._sha
    helper_bytes = helper_source.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            registry, "_sha", lambda raw: "0" * 64 if raw == helper_bytes else original_sha(raw)
        )
        with pytest.raises(
            ValueError, match=r"dependency changed: chiplog\.composition\.r15_lease_v2b_canonical"
        ):
            registry._require_v2b_closure()
    monkeypatch.setattr(registry, "_V2B_CLOSURE_SHA", "0" * 64)
    with pytest.raises(ValueError, match="closure manifest changed"):
        registry._require_v2b_closure()


def test_v2b_runtime_import_closure_contains_only_evaluator_dependencies() -> None:
    """Every retained import edge is pinned; annotation edges cannot pull live writers."""
    root = Path(historical.__file__).resolve().parents[3] / "src"
    pending = ["chiplog.composition.r15_lease_publication_v2b"]
    observed: set[str] = set()
    while pending:
        name = pending.pop()
        path = root / Path(*name.split(".")).with_suffix(".py")
        if name in observed or not path.is_file():
            continue
        observed.add(name)
        nodes: list[ast.AST] = list(ast.parse(path.read_bytes()).body)
        while nodes:
            node = nodes.pop()
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                nodes.extend(node.orelse)
                continue
            if isinstance(node, ast.ImportFrom):
                target = (
                    ".".join([*name.split(".")[: -node.level], node.module or ""])
                    if node.level
                    else node.module or ""
                )
                if target.startswith("chiplog."):
                    pending.append(target)
            elif isinstance(node, ast.Import):
                pending.extend(
                    alias.name for alias in node.names if alias.name.startswith("chiplog.")
                )
            nodes.extend(ast.iter_child_nodes(node))
    source = Path(historical.__file__)
    expected = json.loads((source.with_name("scheduler_v2b_sources") / "closure.json").read_bytes())
    assert observed == set(expected)
    assert not observed.intersection(
        {
            "chiplog.platform.owner_publications",
            "chiplog.platform._sqlite",
            "chiplog.platform.workspace_snapshot",
            "chiplog.platform.r7_trust_durability",
            "chiplog.composition.r15_scheduler_registry",
        }
    )


def test_actual_committed_v2c_selected_bytes_and_immutable_registration() -> None:
    from chiplog.composition.r15_scheduler_runtime import R15SchedulerRuntime

    _ = R15SchedulerRuntime
    profile, _, _ = registry._observe()
    old_registration = registry._historical_v2c_registration(profile)
    assert old_registration.reference.head.startswith("scheduler-source-registry-v2c:")
    assert profile["format"] == "chiplog.scheduler.source-profile.v2d"
    fixture = Path(__file__).with_name("fixtures") / "r15_v2c_selected_batches.json.gz"
    batches = json.loads(gzip.decompress(fixture.read_bytes()))
    retained = [
        SingleOwnerBatch.model_validate_json(json.dumps(batch))
        for batch in batches
        if batch["operation"] in ("r15.adopt_lease_execution_policy", "scheduler.acquire")
    ]
    assert len(retained) == 2
    for batch in retained:
        assert batch.expected.registry_head == old_registration.reference.head
        assert current.validate_selected_lease(batch) is not None


def test_v2c_equivalence_rejects_changes_beyond_annotation_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = json.loads(registry._MANIFEST_V2C.read_bytes())
    registry._require_v2c_evaluator_equivalent(old)
    original = Path.read_bytes

    def unrelated_runtime_change(path: Path) -> bytes:
        raw = original(path)
        return (
            raw + b"\nUNRELATED_RUNTIME_CHANGE = True\n"
            if path.name == "r15_lease_publication_v2b.py"
            else raw
        )

    monkeypatch.setattr(Path, "read_bytes", unrelated_runtime_change)
    with pytest.raises(ValueError, match="runtime AST changed"):
        registry._require_v2c_evaluator_equivalent(old)
