from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from chiplog.capabilities.planning._r8_authority import (
    AuthorityTraceViolation,
    PlanningAuthorityRecorder,
    require_reproduced_trace,
    validate_proposal_freshness,
)
from chiplog.capabilities.planning.r8_boundary import (
    AuthorityRead,
    AuthorityReadKind,
    AuthorityTrace,
    ProposalFreshnessBinding,
)
from chiplog.verification.r8_surface import (
    SURFACES,
    R8SurfaceViolation,
    verify_offline_import_boundary,
    verify_r8_surfaces,
)

ROOT = Path(__file__).parents[2]


class Source:
    def __init__(self, *, proposal: bool = False) -> None:
        registry = "adopted-proposal-create-v1" if proposal else "direct-principal-create-v1"
        sources: tuple[tuple[AuthorityReadKind, str], ...] = (
            ("TRUST", "deployment_trust.current"),
            ("PLANNING", "planning.committed"),
            ("REGISTRY", "planning.authority-registry"),
            ("ADOPTION", "planning.exact-adoption"),
        )
        self.rows: dict[AuthorityReadKind, AuthorityRead] = {
            kind: AuthorityRead(
                kind=kind,
                source_id=source,
                source_version="1",
                head="h",
                generation="g",
                frontier="f",
                valid_until_ns=100,
                canonical_value=(
                    json.dumps([["authority-row", registry]], separators=(",", ":")).encode()
                    if kind == "REGISTRY"
                    else b'{"value":1}'
                ),
            )
            for kind, source in sources
        }

    def read_authority(self, kind: AuthorityReadKind) -> AuthorityRead:
        return self.rows[kind]


def trace(source: Source, *, proposal: bool = False) -> AuthorityTrace:
    recorder = PlanningAuthorityRecorder(
        source, tenant_id="t", principal_id="p", now_ns=10, adopted_proposal=proposal
    )
    for kind in ("TRUST", "PLANNING", "REGISTRY"):
        recorder.read(kind)
    if proposal:
        recorder.read("ADOPTION")
    return recorder.finish()


def test_direct_principal_trace_requires_no_natural_language_adoption() -> None:
    observed = trace(Source())
    assert tuple(read.kind for read in observed.reads) == ("TRUST", "PLANNING", "REGISTRY")
    require_reproduced_trace(observed, trace(Source()))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reordered", "extra"])
def test_trace_rejects_incomplete_or_noncanonical_read_order(mutation: str) -> None:
    recorder = PlanningAuthorityRecorder(Source(), tenant_id="t", principal_id="p", now_ns=10)
    kinds = {
        "missing": ("TRUST",),
        "duplicate": ("TRUST", "TRUST"),
        "reordered": ("PLANNING", "TRUST", "REGISTRY"),
        "extra": ("TRUST", "PLANNING", "REGISTRY", "ADOPTION"),
    }[mutation]
    with pytest.raises(AuthorityTraceViolation):
        for kind in kinds:
            recorder.read(kind)  # type: ignore[arg-type]
        recorder.finish()


@pytest.mark.parametrize("kind", ["TRUST", "PLANNING", "REGISTRY"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "projection.cache"),
        ("source_version", "unknown"),
        ("generation", "new"),
        ("frontier", "lagging"),
        ("valid_until_ns", 10),
    ],
)
def test_source_substitution_freshness_and_mixed_frontier_reject(
    kind: AuthorityReadKind, field: str, value: object
) -> None:
    source = Source()
    source.rows[kind] = source.rows[kind].model_copy(update={field: value})
    with pytest.raises(AuthorityTraceViolation):
        trace(source)


@pytest.mark.parametrize("kind", ["TRUST", "PLANNING", "REGISTRY"])
@pytest.mark.parametrize("field,value", [("canonical_value", b'{"value":2}'), ("head", "h2")])
def test_each_discovered_authority_dependency_is_reproduced(
    kind: AuthorityReadKind, field: str, value: object
) -> None:
    source = Source()
    original = trace(source)
    source.rows[kind] = source.rows[kind].model_copy(update={field: value})
    with pytest.raises(AuthorityTraceViolation):
        require_reproduced_trace(original, trace(source))


def binding() -> ProposalFreshnessBinding:
    value = ProposalFreshnessBinding(
        proposal_id="proposal",
        display_digest=hashlib.sha256(b"display").hexdigest(),
        principal_id="p",
        adoption_act_id="adoption",
        ingress_id="ingress",
        interpretation_revision="interpretation",
        command_digest="command",
        proposed_result_digest="result",
        trace=trace(Source(proposal=True), proposal=True),
    )
    source = Source(proposal=True)
    source.rows["ADOPTION"] = source.rows["ADOPTION"].model_copy(
        update={
            "canonical_value": json.dumps(
                value.model_dump(exclude={"trace"}), sort_keys=True, separators=(",", ":")
            ).encode()
        }
    )
    return value.model_copy(update={"trace": trace(source, proposal=True)})


@pytest.mark.parametrize(
    "field",
    [
        "proposal_id",
        "display_digest",
        "principal_id",
        "adoption_act_id",
        "ingress_id",
        "interpretation_revision",
        "command_digest",
        "proposed_result_digest",
    ],
)
def test_freshness_is_whole_binding_not_subset(field: str) -> None:
    original = binding()
    validate_proposal_freshness(original, original, display_bytes=b"display", now_ns=1)
    with pytest.raises(AuthorityTraceViolation, match="new adoption"):
        validate_proposal_freshness(
            original,
            original.model_copy(update={field: "changed"}),
            display_bytes=b"display",
            now_ns=1,
        )


@pytest.mark.parametrize("field,value", [("valid_until_ns", 1), ("source_id", "unknown")])
def test_identical_proposal_bindings_still_require_valid_current_sources(
    field: str, value: object
) -> None:
    original = binding()
    reads = original.trace.reads
    invalid = original.model_copy(
        update={
            "trace": original.trace.model_copy(
                update={"reads": (reads[0].model_copy(update={field: value}), *reads[1:])}
            )
        }
    )
    with pytest.raises(AuthorityTraceViolation):
        validate_proposal_freshness(invalid, invalid, display_bytes=b"display", now_ns=1)


def test_surface_inventory_equals_actual_current_boundaries() -> None:
    verify_r8_surfaces(ROOT)
    verify_offline_import_boundary(ROOT)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "alias", "downstream"])
def test_surface_inventory_mutants_reject(mutation: str) -> None:
    changed = {
        "missing": SURFACES[:1],
        "extra": (*SURFACES, replace(SURFACES[0], surface_id="unknown")),
        "duplicate": (*SURFACES, SURFACES[0]),
        "alias": (replace(SURFACES[0], capability="other"), SURFACES[1]),
        "downstream": (replace(SURFACES[0], downstream=("cli._create",)), SURFACES[1]),
    }[mutation]
    with pytest.raises(R8SurfaceViolation):
        verify_r8_surfaces(ROOT, changed)


@pytest.mark.parametrize("mutant", ["old-runtime", "independent-replay", "external-provider"])
def test_reached_executable_mutants_reject(tmp_path: Path, mutant: str) -> None:
    source = tmp_path / "src/chiplog"
    shutil.copytree(ROOT / "src/chiplog", source)
    verify_r8_surfaces(tmp_path)
    verify_offline_import_boundary(tmp_path)
    cli = source / "cli.py"
    if mutant == "old-runtime":
        cli.write_text(
            cli.read_text().replace("chiplog.composition.r8", "chiplog.composition.r7_planning")
        )
    elif mutant == "independent-replay":
        cli.write_text(
            cli.read_text().replace(
                "outcome = await runtime.create(", "outcome = await runtime._create("
            )
        )
    else:
        (source / "new_adapter.py").write_text("import openai\n")
    with pytest.raises(R8SurfaceViolation):
        verify_r8_surfaces(tmp_path)
        verify_offline_import_boundary(tmp_path)


def test_real_cli_has_no_development_entitlement_bypass(tmp_path: Path) -> None:
    database = tmp_path / "cli.sqlite"
    prefix = [sys.executable, "-m", "chiplog.cli", "--database", str(database)]
    identity = ["--tenant", "t", "--principal", "p", "--credential", "c", "--session", "s"]
    environment = {**os.environ, "CHIPLOG_R6_OPERATOR_SECRET": "isolated-cli-fixture"}
    bootstrap = subprocess.run(
        [*prefix, "bootstrap", *identity, "--database-instance", "db", "--token", "token"],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    create = subprocess.run(
        [
            *prefix,
            "create",
            "synthetic purpose",
            *identity,
            "--command-id",
            "command",
            "--intention-id",
            "line",
            "--revision-id",
            "revision",
            "--authority-act",
            "act",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert create.returncode != 0 and "HOLD" in create.stderr
    assert create.stdout == ""
    show = subprocess.run(
        [*prefix, "show", *identity], capture_output=True, text=True, env=environment, check=False
    )
    assert show.returncode != 0 and "HOLD" in show.stderr and show.stdout == ""
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM records").fetchone() == (0,)


@pytest.mark.parametrize(
    "mutation", ["bootstrap-output", "main-output", "dead-gate", "plain-helper"]
)
def test_audited_implementation_identity_rejects_unreviewed_behavior(
    tmp_path: Path, mutation: str
) -> None:
    source = tmp_path / "src/chiplog"
    shutil.copytree(ROOT / "src/chiplog", source)
    verify_r8_surfaces(tmp_path)
    if mutation == "plain-helper":
        (source / "new_helper.py").write_text("def extra():\n    return 1\n")
    elif mutation == "dead-gate":
        path = source / "composition/r8.py"
        original = path.read_text()
        assert "result = self._gate.commit_handoff(" in original
        path.write_text(
            original.replace(
                "result = self._gate.commit_handoff(",
                "return None\n        result = self._gate.commit_handoff(",
                1,
            )
        )
    else:
        path = source / "cli.py"
        marker = (
            "async def _bootstrap(args: argparse.Namespace) -> None:"
            if mutation == "bootstrap-output"
            else "def main() -> None:"
        )
        original = path.read_text()
        assert marker in original
        path.write_text(
            original.replace(marker, marker + '\n    print("unexpected fixture output")', 1)
        )
    with pytest.raises(R8SurfaceViolation, match="implementation"):
        verify_r8_surfaces(tmp_path)
