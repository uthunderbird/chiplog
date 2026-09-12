"""Independent executable-boundary inventory for the canonical R8 entrypoint."""

from __future__ import annotations

import ast
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

from chiplog.architecture.r8_implementation import R8_IMPLEMENTATION_FILES


@dataclass(frozen=True)
class DeploymentSurface:
    surface_id: str
    capability: str
    boundary: str
    downstream: tuple[str, ...]


SURFACES = (
    DeploymentSurface("cli.render", "planning.read", "R8PlanningRuntime.render", ("cli._render",)),
    DeploymentSurface(
        "planning.create",
        "planning.create_intention_line",
        "R8PlanningRuntime._publication_decision_guard",
        ("cli._create",),
    ),
)


class R8SurfaceViolation(ValueError):
    pass


def _calls(node: ast.AST) -> set[str]:
    result: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name):
                result.add(child.func.id)
            elif isinstance(child.func, ast.Attribute):
                result.add(child.func.attr)
    return result


def verify_r8_surfaces(root: Path, surfaces: tuple[DeploymentSurface, ...] = SURFACES) -> None:
    verify_implementation_identity(root)
    cli = ast.parse((root / "src/chiplog/cli.py").read_text())
    imports = {
        (node.module, alias.name)
        for node in ast.walk(cli)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    if ("chiplog.composition.r8", "open_r8_runtime") not in imports:
        raise R8SurfaceViolation("canonical entrypoint bypasses R8")
    if any(
        module and ("r7" in module or "r6" in module or ".platform" in module)
        for module, _ in imports
    ):
        raise R8SurfaceViolation("CLI has an independent raw or historical route")
    cli_functions = {node.name: node for node in cli.body if isinstance(node, ast.AsyncFunctionDef)}
    if set(cli_functions) != {"_bootstrap", "_create", "_render"}:
        raise R8SurfaceViolation("unknown CLI start/replay/fan-out entry")
    actual: list[DeploymentSurface] = []
    for name, operation, surface_id, capability, boundary in (
        (
            "_create",
            "create",
            "planning.create",
            "planning.create_intention_line",
            "R8PlanningRuntime._publication_decision_guard",
        ),
        ("_render", "render", "cli.render", "planning.read", "R8PlanningRuntime.render"),
    ):
        calls = _calls(cli_functions[name])
        if operation not in calls or "open_r8_runtime" not in calls:
            raise R8SurfaceViolation("CLI operation lost its checked runtime boundary")
        runtime_calls = [
            node.func.attr
            for node in ast.walk(cli_functions[name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "runtime"
        ]
        if runtime_calls != [operation]:
            raise R8SurfaceViolation("downstream CLI acquired an independent start/replay route")
        parents = {
            child: parent
            for parent in ast.walk(cli_functions[name])
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(cli_functions[name]):
            if (
                isinstance(node, ast.Name)
                and node.id == "runtime"
                and isinstance(node.ctx, ast.Load)
            ):
                parent = parents[node]
                if not isinstance(parent, ast.Attribute) or parent.attr != operation:
                    raise R8SurfaceViolation("runtime alias or reflective downstream bypass")
        allowed_calls = {
            "_create": {
                "CreateIntentionLine",
                "Path",
                "RecordId",
                "SystemExit",
                "TenantId",
                "_operator_secret",
                "create",
                "getattr",
                "open_r8_runtime",
                "print",
            },
            "_render": {
                "Path",
                "SystemExit",
                "_operator_secret",
                "join",
                "open_r8_runtime",
                "print",
                "render",
                "str",
            },
        }
        if calls != allowed_calls[name]:
            raise R8SurfaceViolation("unregistered downstream helper or adapter path")
        actual.append(DeploymentSurface(surface_id, capability, boundary, (f"cli.{name}",)))
    if tuple(sorted(actual, key=lambda item: item.surface_id)) != surfaces:
        raise R8SurfaceViolation(
            "deployment surface inventory does not equal executable boundaries"
        )
    runtime = ast.parse((root / "src/chiplog/composition/r8.py").read_text())
    runtime_class = next(
        node
        for node in runtime.body
        if isinstance(node, ast.ClassDef) and node.name == "R8PlanningRuntime"
    )
    methods = {
        node.name: node
        for node in runtime_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for boundary in ("_publication_decision_guard", "render"):
        if "commit_handoff" not in _calls(methods[boundary]):
            raise R8SurfaceViolation("real handoff has no final deployment gate")
    if "_trace" not in _calls(methods["_publication_authority_guard"]):
        raise R8SurfaceViolation("publication bypasses authority-trace reproduction")
    declaration = next(
        node
        for node in runtime.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "R8_SURFACES" for target in node.targets
        )
    )
    if ast.literal_eval(declaration.value) != tuple(
        (row.surface_id, row.capability) for row in surfaces
    ):
        raise R8SurfaceViolation("broker registrations differ from independently observed surfaces")


def verify_implementation_identity(root: Path) -> None:
    expected = {
        path.relative_to(root / "src/chiplog").as_posix()
        for path in (root / "src/chiplog").rglob("*.py")
    }
    if set(R8_IMPLEMENTATION_FILES) != expected:
        raise R8SurfaceViolation("audited implementation inventory is incomplete or unknown")
    for relative, digest in R8_IMPLEMENTATION_FILES.items():
        try:
            actual = hashlib.sha256((root / "src/chiplog" / relative).read_bytes()).hexdigest()
        except OSError as error:
            raise R8SurfaceViolation("audited implementation is missing") from error
        if actual != digest:
            raise R8SurfaceViolation(
                f"implementation requires renewed boundary evidence: {relative}"
            )


def verify_offline_import_boundary(root: Path) -> None:
    """R8 contains local SQLite and authenticated Unix IPC, no external adapter.

    This is a current executable-source check, not permission for a future adapter.
    A newly reachable SDK/transport requires new inventory and gate evidence.
    """
    forbidden = {"openai", "httpx", "requests", "urllib", "aiohttp", "telegram", "google", "grpc"}
    for path in (root / "src/chiplog").rglob("*.py"):
        relative = path.relative_to(root / "src").as_posix()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules = (node.module,)
            if any(module.split(".")[0] in forbidden for module in modules):
                raise R8SurfaceViolation(
                    "offline graph acquired an external provider/channel import"
                )
            for module in modules:
                top = module.split(".")[0]
                if top not in sys.stdlib_module_names | {"chiplog", "dishka", "pydantic"}:
                    raise R8SurfaceViolation("offline graph acquired an unregistered dependency")
                if (
                    top in {"socket", "ssl", "ctypes", "importlib"}
                    and relative != "chiplog/platform/r7_runtime.py"
                ):
                    raise R8SurfaceViolation("unregistered raw transport or dynamic adapter")
                if top == "subprocess" and relative not in {
                    "chiplog/verification/runner.py",
                    "chiplog/verification/r1_lane.py",
                }:
                    raise R8SurfaceViolation("unregistered process transport")


__all__ = [
    "SURFACES",
    "DeploymentSurface",
    "R8SurfaceViolation",
    "verify_offline_import_boundary",
    "verify_r8_surfaces",
]
