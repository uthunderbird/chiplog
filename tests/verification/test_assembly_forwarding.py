"""Registered helper-call mutations discriminate lexical assembly forwarding.

PostgreSQL chat factory construction is outside this helper-edge graph.
"""

from pathlib import Path

import pytest

from chiplog.verification.assembly_forwarding import (
    AssemblyEdge,
    AssemblySymbol,
    UnknownAssemblyForwarding,
    UnregisteredAssemblyMount,
    current_assembly_forwarding,
    inspect_assembly_forwarding,
    verify_assembly_forwarding,
)

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "chiplog/composition/"


def _sources():
    return tuple(
        (str(path.relative_to(ROOT / "src")), path.read_text())
        for path in sorted((ROOT / "src/chiplog/composition").glob("*.py"))
        if "_open_runtime" in path.read_text()
    )


def _mutate(sources, module, old, new):
    result = []
    for name, source in sources:
        if name == module:
            assert source.count(old) == 1
            source = source.replace(old, new)
        result.append((name, source))
    return tuple(result)


# Independent direct-call expectation, not generated from inspected rows.
EXPECTED = (
    ("r13_runtime.py", "open_r13_runtime", "R13PlanningRuntime", "R13_PRODUCTION_MANIFEST"),
    (
        "r16_denial_publication.py",
        "open_r16_runtime",
        "R16PlanningRuntime",
        "R16_PRODUCTION_MANIFEST",
    ),
    (
        "r17_ingress_runtime.py",
        "open_r17_runtime",
        "R17IngressRuntime",
        "R17_CUSTODY_PRODUCTION_MANIFEST",
    ),
    ("r7_planning.py", "open_r7_runtime", "R7PlanningRuntime", "R7_PRODUCTION_MANIFEST"),
    ("r8.py", "open_r8_runtime", "R8PlanningRuntime", "R8_PRODUCTION_MANIFEST"),
)


def _summary(observed):
    return tuple(
        (
            Path(row.module).name,
            row.caller,
            None if row.runtime is None else row.runtime.name,
            row.manifest.name,
        )
        for row in observed.edges
    )


def _expected_edges():
    edges = []
    for filename, caller, runtime, manifest in EXPECTED:
        module = "chiplog.composition." + filename.removesuffix(".py")
        runtime_symbol = (
            None
            if runtime is None
            else AssemblySymbol(
                runtime,
                "local_import" if runtime == "R13PlanningRuntime" else "symbol",
                ("chiplog.composition.r13_planning" if runtime == "R13PlanningRuntime" else module)
                + ":"
                + runtime,
            )
        )
        manifest_symbol = AssemblySymbol(
            manifest, "symbol", "chiplog.architecture.r7_runtime:" + manifest
        )
        callee = "chiplog.composition.r7_planning:_open_runtime"
        edges.append(
            AssemblyEdge(PREFIX + filename, caller, 1, callee, runtime_symbol, manifest_symbol)
        )
    return tuple(edges)


def test_actual_all_src_direct_mounts_match_independent_expected_rows_and_stay_open() -> None:
    observed = current_assembly_forwarding(ROOT)
    assert _summary(observed) == EXPECTED
    verify_assembly_forwarding(observed.edges, _expected_edges())
    assert all(row.ordinal == 1 for row in observed.edges)
    by_caller = {row.caller: row for row in observed.edges}
    assert by_caller["open_r8_runtime"].callee == "chiplog.composition.r7_planning:_open_runtime"
    assert by_caller["open_r7_runtime"].manifest.origin == (
        "chiplog.architecture.r7_runtime:R7_PRODUCTION_MANIFEST"
    )
    with pytest.raises(UnknownAssemblyForwarding, match="incomplete"):
        observed.require_closed()


@pytest.mark.parametrize("change", ["manifest", "runtime", "duplicate", "missing", "ghost"])
def test_changed_direct_edge_is_observed_before_bidirectional_refusal(change) -> None:
    sources = _sources()
    baseline = inspect_assembly_forwarding(sources)
    module = PREFIX + "r8.py"
    if change == "manifest":
        changed = _mutate(
            sources,
            module,
            "manifest=R8_PRODUCTION_MANIFEST,",
            "manifest=R7_PRODUCTION_MANIFEST,",
        )
        changed = _mutate(
            changed,
            module,
            "from chiplog.architecture.r7_runtime import R8_PRODUCTION_MANIFEST",
            "from chiplog.architecture.r7_runtime import "
            "R8_PRODUCTION_MANIFEST, R7_PRODUCTION_MANIFEST",
        )
    elif change == "runtime":
        changed = _mutate(
            sources,
            module,
            "runtime_type=R8PlanningRuntime,",
            "runtime_type=R7PlanningRuntime,",
        )
    elif change == "missing":
        changed = _mutate(
            sources, PREFIX + "r8.py", "async with _open_runtime(\n", "async with other(\n"
        )
    else:
        name = "open_extra" if change == "ghost" else "open_dispatch_runtime"
        addition = (
            f"\nasync def {name}(database):\n"
            "    return _open_runtime(database, runtime_type=R16DispatchRuntime, "
            "manifest=R16_DISPATCH_PRODUCTION_MANIFEST)\n"
        )
        if change == "duplicate":
            changed = _mutate(
                sources,
                PREFIX + "r8.py",
                "    async with _open_runtime(\n",
                "    unused = _open_runtime(database, runtime_type=R8PlanningRuntime, "
                "manifest=R8_PRODUCTION_MANIFEST)\n    async with _open_runtime(\n",
            )
        else:
            changed = (
                *sources,
                (
                    "sibling/extra.py",
                    (
                        "from chiplog.composition.r7_planning import _open_runtime\n"
                        "from chiplog.composition.r16_dispatch_runtime import R16DispatchRuntime\n"
                        "from chiplog.architecture.r7_runtime imp"
                        "ort R16_DISPATCH_PRODUCTION_MANIFEST\n" + addition
                    ),
                ),
            )
    observed = inspect_assembly_forwarding(changed)
    assert observed.edges != baseline.edges
    if change == "duplicate":
        assert [row.ordinal for row in observed.edges if row.caller == "open_r8_runtime"] == [1, 2]
    if change in {"runtime", "manifest"}:
        row = next(row for row in observed.edges if row.caller == "open_r8_runtime")
        assert (row.runtime.name if change == "runtime" else row.manifest.name) == (
            "R7PlanningRuntime" if change == "runtime" else "R7_PRODUCTION_MANIFEST"
        )
        assert (row.runtime.origin if change == "runtime" else row.manifest.origin) == (
            "chiplog.composition.r7_planning:R7PlanningRuntime"
            if change == "runtime"
            else "chiplog.architecture.r7_runtime:R7_PRODUCTION_MANIFEST"
        )
    with pytest.raises(UnregisteredAssemblyMount):
        verify_assembly_forwarding(observed.edges, baseline.edges)


@pytest.mark.parametrize("change", ["alias", "foreign_import", "shadow", "escape", "nested"])
def test_unknown_binding_or_frame_never_yields_resolved_identity(change) -> None:
    sources = _sources()
    module = PREFIX + "r8.py"
    if change == "alias":
        changed = _mutate(
            sources,
            module,
            "R7PlanningRuntime, _open_runtime",
            "R7PlanningRuntime, _open_runtime as another_open",
        )
    elif change == "foreign_import":
        changed = _mutate(sources, module, "from .r7_planning import", "from somewhere_else import")
    elif change == "shadow":
        changed = _mutate(
            sources, module, "runtime_type=R8PlanningRuntime,", "runtime_type=unknown_runtime,"
        )
    elif change == "escape":
        changed = tuple(
            (name, source + "\nescaped = _open_runtime\n") if name == module else (name, source)
            for name, source in sources
        )
    else:
        changed = tuple(
            (
                name,
                source
                + (
                    "\nasync def open_outer(database):\n"
                    "    async def inner():\n"
                    "        return _open_runtime(database, runtime_type=R8PlanningRuntime, "
                    "manifest=R8_PRODUCTION_MANIFEST)\n"
                ),
            )
            if name == module
            else (name, source)
            for name, source in sources
        )
    with pytest.raises(UnknownAssemblyForwarding):
        inspect_assembly_forwarding(changed)


def test_same_spelling_foreign_manifest_origin_is_observed_and_independently_rejected() -> None:
    changed = _mutate(
        _sources(),
        PREFIX + "r8.py",
        "from chiplog.architecture.r7_runtime import R8_PRODUCTION_MANIFEST",
        "from foreign_manifest import R8_PRODUCTION_MANIFEST",
    )
    observed = inspect_assembly_forwarding(changed)
    row = next(row for row in observed.edges if row.caller == "open_r8_runtime")
    assert row.manifest.origin == "foreign_manifest:R8_PRODUCTION_MANIFEST"
    with pytest.raises(UnregisteredAssemblyMount):
        verify_assembly_forwarding(observed.edges, _expected_edges())


@pytest.mark.parametrize(
    "statement",
    [
        "from foreign import manifest",
        "def manifest():\n        pass",
        "class manifest:\n        pass",
        "global manifest",
        "match resources:\n        case {'key': manifest}:\n            pass",
    ],
)
def test_forwarded_manifest_parameter_cannot_be_rebound_by_other_binding_syntax(statement) -> None:
    module = PREFIX + "r8.py"
    forwarded_call = (
        "    return _open_runtime(database, runtime_type=R8PlanningRuntime, manifest=manifest)\n"
    )
    addition = (
        "\nasync def forward_parameter(database, resources, *, manifest=R8_PRODUCTION_MANIFEST):\n"
        + forwarded_call
    )
    sources = tuple(
        (name, source + addition) if name == module else (name, source)
        for name, source in _sources()
    )
    baseline = inspect_assembly_forwarding(sources)
    forwarded = next(row for row in baseline.edges if row.caller == "forward_parameter")
    assert forwarded.manifest.kind == "parameter"
    changed = _mutate(sources, module, forwarded_call, "    " + statement + "\n" + forwarded_call)
    with pytest.raises(UnknownAssemblyForwarding, match=r"parameter.*(bound|shadow)"):
        inspect_assembly_forwarding(changed)
