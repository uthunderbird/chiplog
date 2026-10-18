"""Independent executable-boundary inventory for the canonical R8 entrypoint."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DeploymentSurface:
    surface_id: str
    capability: str
    boundary: str
    downstream: tuple[str, ...]


SURFACES = (
    DeploymentSurface(
        "cli.render", "planning.read", "PostgresPlanningRuntime.render", ("cli._render",)
    ),
    DeploymentSurface(
        "planning.create",
        "planning.create_intention_line",
        "PostgresPlanningRuntime.create",
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
    cli = ast.parse((root / "src/chiplog/cli.py").read_text())
    imports = {
        (node.module, alias.name)
        for node in ast.walk(cli)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    if ("chiplog.composition.postgres_planning", "open_postgres_planning_runtime") not in imports:
        raise R8SurfaceViolation("canonical entrypoint bypasses PostgreSQL deployment boundary")
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
            "PostgresPlanningRuntime.create",
        ),
        ("_render", "render", "cli.render", "planning.read", "PostgresPlanningRuntime.render"),
    ):
        calls = _calls(cli_functions[name])
        if operation not in calls or "open_postgres_planning_runtime" not in calls:
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
                "RecordId",
                "SystemExit",
                "TenantId",
                "_operator_secret",
                "create",
                "getattr",
                "open_postgres_planning_runtime",
                "print",
            },
            "_render": {
                "SystemExit",
                "_operator_secret",
                "join",
                "open_postgres_planning_runtime",
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
    runtime = ast.parse((root / "src/chiplog/composition/postgres_planning.py").read_text())
    runtime_class = next(
        node
        for node in runtime.body
        if isinstance(node, ast.ClassDef) and node.name == "PostgresPlanningRuntime"
    )
    methods = {
        node.name: node
        for node in runtime_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if not {"_transaction", "publish_plan_in_transaction"} <= _calls(methods["create"]):
        raise R8SurfaceViolation("planning create lost its transaction-local handoff")
    for boundary, required in (
        ("publish_plan_in_transaction", {"require", "crossed", "_context", "execute"}),
        ("render", {"require", "crossed", "_context", "_transaction", "rebuild"}),
    ):
        if boundary not in methods or not required <= _calls(methods[boundary]):
            raise R8SurfaceViolation("real handoff has no final deployment gate")


def _implementation_catalog(root: Path) -> dict[str, str]:
    path = root / "src/chiplog/inert_shared/r8-implementation-v1.json"

    def reject_duplicate_keys(pairs: list[tuple[object, object]]) -> dict[object, object]:
        result: dict[object, object] = {}
        for key, value in pairs:
            if key in result:
                raise R8SurfaceViolation("audited implementation catalog has duplicate keys")
            result[key] = value
        return result

    try:
        raw: object = json.loads(path.read_bytes(), object_pairs_hook=reject_duplicate_keys)
    except (OSError, json.JSONDecodeError) as error:
        raise R8SurfaceViolation("audited implementation catalog is unreadable") from error
    if not isinstance(raw, dict) or not raw:
        raise R8SurfaceViolation("audited implementation catalog is not a nonempty object")
    catalog: dict[str, str] = {}
    for relative, digest in raw.items():
        if (
            not isinstance(relative, str)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or Path(relative).is_absolute()
            or "\\" in relative
            or relative != Path(relative).as_posix()
            or ".." in Path(relative).parts
            or relative in catalog
        ):
            raise R8SurfaceViolation("audited implementation catalog is malformed")
        catalog[relative] = digest
    if list(catalog) != sorted(catalog):
        raise R8SurfaceViolation("audited implementation catalog is not canonical")
    return catalog


def validated_catalog_broker_version(root: Path, observed: dict[str, str]) -> str:
    catalog = _implementation_catalog(root)
    if set(catalog) != set(observed):
        raise R8SurfaceViolation("audited implementation inventory is incomplete or unknown")
    if any(observed[relative] != digest for relative, digest in catalog.items()):
        raise R8SurfaceViolation("implementation requires renewed boundary evidence")
    return (
        "r8-broker-v1:" + hashlib.sha256(json.dumps(catalog, sort_keys=True).encode()).hexdigest()
    )


def verify_implementation_identity(root: Path) -> None:
    observed: dict[str, str] = {}
    for path in sorted((root / "src/chiplog").rglob("*.py")):
        try:
            observed[path.relative_to(root / "src/chiplog").as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except OSError as error:
            raise R8SurfaceViolation("audited implementation is missing") from error
    validated_catalog_broker_version(root, observed)


def verify_offline_import_boundary(root: Path) -> None:
    """Register the exact reviewed storage, provider and transport leaves.

    This is a current executable-source check, not permission for a future adapter.
    A newly reachable SDK/transport requires new inventory and gate evidence.
    """
    forbidden = {"openai", "httpx", "requests", "urllib", "aiohttp", "telegram", "google", "grpc"}
    # R9 uses only the reviewed stateless renderer/DTO facade, not the optional
    # hub or TUI. This registration grants no additional deployment surface.
    dashboard_imports = {
        "chiplog/composition/r9.py": {"render_screen", "screen_from_dict", "screen_to_dict"},
        "chiplog/capabilities/projections/workspace.py": {
            "DashboardScreen",
            "render_screen",
            "screen_from_dict",
            "screen_to_dict",
        },
    }
    for path in (root / "src/chiplog").rglob("*.py"):
        relative = path.relative_to(root / "src").as_posix()
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                modules = (node.module,)
            if (
                relative
                in {
                    "chiplog/composition/r13_runtime.py",
                    "chiplog/adapters/driven/deployment_trust/_journal.py",
                    "chiplog/composition/r14_loop_history.py",
                }
                and isinstance(node, ast.Import)
                and tuple(alias.name for alias in node.names) == ("orjson",)
            ):
                # Native JSON codec only; this adds no transport or adapter surface.
                continue
            if relative == "chiplog/platform/postgres.py" and (
                (isinstance(node, ast.Import) and modules == ("psycopg",))
                or (
                    isinstance(node, ast.ImportFrom)
                    and node.module == "psycopg"
                    and {alias.name for alias in node.names} == {"sql"}
                )
            ):
                # User-authorized PostgreSQL substrate; no arbitrary new driver
                # imports or direct storage access from capability owners.
                continue
            if (
                relative
                in {
                    "chiplog/capabilities/deployment_trust/operator_policy_command_verifier.py",
                    "chiplog/composition/postgres_native_cli.py",
                }
                and isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module == "cryptography.hazmat.primitives.asymmetric.ed25519"
                and len(node.names) == 1
                and node.names[0].name == "Ed25519PublicKey"
                and node.names[0].asname is None
            ):
                continue
            if (
                isinstance(node, ast.ImportFrom)
                and node.module == "cryptography.hazmat.primitives.asymmetric.ed25519"
            ):
                raise R8SurfaceViolation("offline graph acquired an unregistered dependency")
            # Registered opt-in auth/model leaves, not part of the hermetic assembly.
            if relative == "chiplog/adapters/driven/codex_auth.py" and all(
                module in {"oauth_cli_kit", "oauth_cli_kit.models"} for module in modules
            ):
                continue
            if relative == "chiplog/adapters/driven/codex_model.py" and modules == ("httpx",):
                continue
            if any(module.split(".")[0] in forbidden for module in modules):
                raise R8SurfaceViolation(
                    "offline graph acquired an external provider/channel import"
                )
            for module in modules:
                top = module.split(".")[0]
                if module == "yaml" and relative == "chiplog/verification/transcript_yaml.py":
                    # SafeLoader-only authored contract parsing; no adapter/transport surface.
                    continue
                if (
                    relative == "chiplog/composition/scheduler_source_registry.py"
                    and isinstance(node, ast.ImportFrom)
                    and module == "importlib.metadata"
                    and {alias.name for alias in node.names} == {"version"}
                ):
                    # Read the installed serializer version; dynamic loading and
                    # transport imports retain their ordinary rejection below.
                    continue
                if relative in {
                    "chiplog/adapters/driven/loop_prompts.py",
                    "chiplog/adapters/driven/execution_prompts.py",
                } and isinstance(node, ast.ImportFrom):
                    names = {alias.name for alias in node.names}
                    if (
                        module == "promptstrings" and names <= {"PromptContext", "promptstring"}
                    ) or (module == "importlib.metadata" and names == {"version"}):
                        continue
                if top == "agent_dashboard":
                    if (
                        module != "agent_dashboard"
                        or not isinstance(node, ast.ImportFrom)
                        or not {alias.name for alias in node.names}
                        <= dashboard_imports.get(relative, set())
                    ):
                        raise R8SurfaceViolation("unregistered dashboard import surface")
                    continue
                if top not in sys.stdlib_module_names | {"chiplog", "dishka", "pydantic"}:
                    raise R8SurfaceViolation("offline graph acquired an unregistered dependency")
                if (
                    top in {"socket", "ssl", "ctypes", "importlib"}
                    and relative != "chiplog/platform/r7_runtime.py"
                    and not (
                        relative
                        in {
                            "chiplog/adapters/driven/cli_custody_socket.py",
                            "chiplog/adapters/driven/native_ingress_cli.py",
                        }
                        and module in {"socket", "ctypes"}
                        and isinstance(node, ast.Import)
                    )
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
    "validated_catalog_broker_version",
    "verify_implementation_identity",
    "verify_offline_import_boundary",
    "verify_r8_surfaces",
]
