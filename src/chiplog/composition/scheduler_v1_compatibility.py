"""Bounded V1 historical evaluator compatibility, without executing archived code."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_V1 = Path(__file__).with_name("scheduler_source_manifest.json")
_V1_SHA = "8d8c28b7619703ff0e39d40e314de2427f361a6bc84050411b4dec1c11e3f7e7"
_ARCHIVE = Path(__file__).with_name("scheduler_v1_sources")
_CHANGED = ("scheduler_preparation", "scheduler_startup", "delivery_contracts")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _nodes(source: bytes) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }


def _same(old: ast.AST, current: ast.AST) -> bool:
    return ast.dump(old, include_attributes=False) == ast.dump(current, include_attributes=False)


def _compatible_preparation(old: bytes, current: bytes) -> None:
    original, live = _nodes(old), _nodes(current)
    if set(live) - set(original) != {
        "MandateIssuancePreparationRequest",
        "prepare_mandate_issuance",
    }:
        raise ValueError("V1 preparation top-level additions differ from reviewed extension")
    for name, expected in original.items():
        actual = copy.deepcopy(live[name])
        if name == "PreparedSchedulerBatch":
            assert isinstance(actual, ast.ClassDef)
            assert isinstance(expected, ast.ClassDef)
            extra = [
                node
                for node in actual.body
                if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "result"
            ]
            prior = [
                node
                for node in expected.body
                if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "result"
            ]
            if len(extra) != 1 or len(prior) != 1:
                raise ValueError("V1 prepared result slot differs")
            extra[0].annotation = copy.deepcopy(prior[0].annotation)
        elif name == "_batch":
            assert isinstance(actual, ast.FunctionDef)
            assert isinstance(expected, ast.FunctionDef)
            actual.args.args[1].annotation = copy.deepcopy(expected.args.args[1].annotation)
        if not _same(expected, actual):
            raise ValueError("V1 preparation semantics changed at " + name)


def _compatible_startup(old: bytes, current: bytes) -> None:
    original, live = _nodes(old), _nodes(current)
    if set(live) - set(original) != {"SelectedMandateGenesis", "_mandate_issuance"}:
        raise ValueError("V1 startup top-level additions differ from reviewed extension")
    for name, expected in original.items():
        actual = copy.deepcopy(live[name])
        if name == "SchedulerStartupIndex":
            assert isinstance(actual, ast.ClassDef)
            actual.body = [
                node
                for node in actual.body
                if not (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "mandates"
                )
            ]
        elif name == "validate_selected_scheduler_records":
            assert isinstance(actual, ast.FunctionDef)
            actual.body = [
                node
                for node in actual.body
                if not (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "mandates"
                )
            ]
            loops = [
                node
                for node in actual.body
                if isinstance(node, ast.For)
                and isinstance(node.target, ast.Name)
                and node.target.id == "batch"
            ]
            if len(loops) != 1:
                raise ValueError("V1 startup selected loop differs")
            branches = [
                (i, node)
                for i, node in enumerate(loops[0].body)
                if isinstance(node, ast.If)
                and ast.unparse(node.test) == "batch.operation == 'scheduler.issue_mandate'"
            ]
            if len(branches) != 1:
                raise ValueError("V1 startup mandate branch differs")
            index, branch = branches[0]
            if (
                len(branch.body) != 1
                or ast.unparse(branch.body[0]) != "_mandate_issuance(batch, mandates)"
                or len(branch.orelse) != 1
                or not isinstance(branch.orelse[0], ast.If)
            ):
                raise ValueError("V1 startup branch is not an additive prefix")
            loops[0].body[index] = branch.orelse[0]
            returns = [node for node in actual.body if isinstance(node, ast.Return)]
            if len(returns) != 1 or not isinstance(returns[0].value, ast.Call):
                raise ValueError("V1 startup return differs")
            result = returns[0].value
            added = [item for item in result.keywords if item.arg == "mandates"]
            if len(added) != 1 or ast.unparse(added[0].value) != (
                "tuple((value for _, value in sorted(mandates.items())))"
            ):
                raise ValueError("V1 startup index addition differs")
            result.keywords = [item for item in result.keywords if item.arg != "mandates"]
        if not _same(expected, actual):
            raise ValueError("V1 startup semantics changed at " + name)


def require_v1_compatible(current_profile: dict[str, object]) -> dict[str, object]:
    """Verify exact old artifacts and identical old operation AST before dispatch."""

    manifest = _V1.read_bytes()
    if _sha(manifest) != _V1_SHA:
        raise ValueError("immutable V1 scheduler manifest differs")
    expected = json.loads(manifest)
    if expected.get("format") != "chiplog.scheduler.source-profile.v1":
        raise ValueError("unknown V1 scheduler profile format")
    current_sources = current_profile["sources"]
    old_sources = expected["sources"]
    if not isinstance(current_sources, dict) or not isinstance(old_sources, dict):
        raise ValueError("scheduler source maps differ")
    if set(old_sources) - {f"chiplog.capabilities.agent_loop.{name}" for name in _CHANGED} > set(
        current_sources
    ):
        raise ValueError("V1 shared sources are missing")
    for name, old_hash in old_sources.items():
        short = name.rsplit(".", 1)[-1]
        if short in _CHANGED:
            archived = (_ARCHIVE / (short + ".py.source")).read_bytes()
            if _sha(archived) != old_hash:
                raise ValueError("V1 archived source differs: " + short)
            if short == "scheduler_preparation":
                _compatible_preparation(
                    archived,
                    (
                        _ROOT / "src/chiplog/capabilities/agent_loop/scheduler_preparation.py"
                    ).read_bytes(),
                )
            elif short == "scheduler_startup":
                _compatible_startup(
                    archived,
                    (
                        _ROOT / "src/chiplog/capabilities/agent_loop/scheduler_startup.py"
                    ).read_bytes(),
                )
        elif current_sources.get(name) != old_hash:
            raise ValueError("V1 shared source differs: " + name)
    if _sha((_ARCHIVE / "uv.lock.source").read_bytes()) != expected["lock_sha256"]:
        raise ValueError("V1 archived dependency lock differs")
    if (
        current_profile["runtime"] != expected["runtime"]
        or current_profile["packages"] != expected["packages"]
    ):
        raise ValueError("V1 runtime or serializer packages differ")
    return expected
