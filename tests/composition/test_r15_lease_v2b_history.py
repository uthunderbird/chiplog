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
