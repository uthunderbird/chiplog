from __future__ import annotations

import ast
import hashlib
from pathlib import Path

ALLOWED_FUNCTIONS: dict[str, set[str]] = {
    "codec.py": {
        "admit_exact_version",
        "canonical_record_bytes",
        "fingerprint",
        "verify_preserved_bytes",
    }
}
FORBIDDEN_ROLE_MARKERS = ("validator", "registry", "factory", "hook", "service_lookup")
ALLOWED_CLASSES: dict[str, dict[str, tuple[str, ...]]] = {
    "canonical.py": {
        "CanonicalBytes": ("payload", "producing_versions"),
        "PreservedBytes": ("original", "producing_versions"),
    },
    "codec.py": {"AdmissionResult": ("status", "reason", "preserved", "fingerprint")},
    "identity.py": {
        "RecordId": ("tenant_id", "value"),
        "RecordTypeId": ("namespace", "name"),
        "SchemaId": ("namespace", "name", "version"),
    },
    "principal.py": {"PrincipalId": ("value",), "PermissionScope": ("value",)},
    "tenant.py": {"TenantId": ("value",)},
    "versions.py": {
        "CodecVersion": ("value",),
        "CanonicalizationVersion": ("value",),
        "OwnerTag": ("value",),
        "Fingerprint": ("algorithm", "digest"),
        "ProducingVersions": ("schema_id", "codec", "canonicalization"),
    },
}
ALLOWED_IMPORTS: dict[str, set[str]] = {
    "__init__.py": {
        "from .canonical import CanonicalBytes, PreservedBytes",
        "from .identity import RecordId, RecordTypeId, SchemaId",
        "from .principal import PermissionScope, PrincipalId",
        "from .tenant import TenantId",
        (
            "from .versions import CanonicalizationVersion, CodecVersion, Fingerprint, "
            "OwnerTag, ProducingVersions"
        ),
    },
    "canonical.py": {
        "from __future__ import annotations",
        "from dataclasses import dataclass",
        "from .versions import ProducingVersions",
    },
    "codec.py": {
        "from __future__ import annotations",
        "import hashlib",
        "import json",
        "from collections.abc import Callable, Mapping",
        "from dataclasses import dataclass",
        "from typing import Literal",
        "from .canonical import CanonicalBytes, PreservedBytes",
        "from .identity import RecordId, RecordTypeId, SchemaId",
        "from .versions import Fingerprint, OwnerTag, ProducingVersions",
    },
    "identity.py": {
        "from __future__ import annotations",
        "from dataclasses import dataclass",
        "from .tenant import TenantId",
    },
    "principal.py": {"from __future__ import annotations", "from dataclasses import dataclass"},
    "tenant.py": {"from __future__ import annotations", "from dataclasses import dataclass"},
    "versions.py": {
        "from __future__ import annotations",
        "from dataclasses import dataclass",
        "from .identity import SchemaId",
    },
}
ALLOWED_BINDINGS: dict[str, set[str]] = {
    "__init__.py": {"__all__"},
    "codec.py": {"FINGERPRINT_ALGORITHM", "FINGERPRINT_DOMAIN"},
}
ALLOWED_SOURCE_DIGESTS = {
    "__init__.py": "53c93ff53644262f31d50b2732ef0f453f8c8c65360078c80eb2b00aa77da977",
    "canonical.py": "afc797f551f952854082bffe2833cd523c44376759ed3acc617382df9b518918",
    "codec.py": "938a79819c30b7ee693a1547632202efd170889e07e34f6340479f856bd25d54",
    "identity.py": "4809a9e4667e544bfd29d4ec01babd7b930bc6768c194e5ff974b45047e0f2e9",
    "principal.py": "7a939a76f39bd24550a0bd55ba6adece7bb192464e72e8e07f6e685c846e512e",
    "tenant.py": "31c06b90e43fb4df7238ff3f1f3dc92ab08a5622ca08286ea6e1d8cb847206b0",
    "versions.py": "241b8f14f403571c5c16a63e26ca72d126daad185651f2d7cf06b3f92ce6909a",
}


class InertSharedViolation(ValueError):
    pass


def verify_inert_shared_source(package: Path) -> None:
    """Reject executable policy extension points in the shared primitives package."""
    discovered = {path.name for path in package.glob("*.py")}
    expected = {
        "__init__.py",
        "canonical.py",
        "codec.py",
        "identity.py",
        "principal.py",
        "tenant.py",
        "versions.py",
    }
    if discovered != expected:
        missing = expected - discovered
        extra = discovered - expected
        raise InertSharedViolation(
            f"shared module exact-set mismatch: missing={missing}, extra={extra}"
        )
    for path in sorted(package.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for index, statement in enumerate(tree.body):
            allowed_statement = isinstance(
                statement,
                (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef, ast.Assign),
            ) or (
                index == 0
                and isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and isinstance(statement.value.value, str)
            )
            if not allowed_statement:
                raise InertSharedViolation(
                    f"shared top-level statement is executable in {path.name}"
                )
        allowed_functions = ALLOWED_FUNCTIONS.get(path.name, set())
        imports = {
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        if imports != ALLOWED_IMPORTS.get(path.name, set()):
            raise InertSharedViolation(f"shared import exact-set mismatch in {path.name}")
        all_classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        expected_classes = ALLOWED_CLASSES.get(path.name, {})
        if len(all_classes) != len(expected_classes) or {node.name for node in all_classes} != set(
            expected_classes
        ):
            raise InertSharedViolation(f"shared class exact-set mismatch in {path.name}")
        classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
        if set(classes) != set(expected_classes):
            raise InertSharedViolation(f"shared class placement mismatch in {path.name}")
        for name, expected_fields in expected_classes.items():
            _verify_data_class(path.name, classes[name], expected_fields)
        bindings = {
            target.id
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in _assignment_targets(node)
            if isinstance(target, ast.Name)
        }
        if bindings != ALLOWED_BINDINGS.get(path.name, set()):
            raise InertSharedViolation(f"shared binding exact-set mismatch in {path.name}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef, ast.Lambda)):
                raise InertSharedViolation(f"executable shared callback in {path.name}")
            if isinstance(node, ast.FunctionDef) and node.name not in allowed_functions:
                raise InertSharedViolation(f"unregistered shared function {path.name}:{node.name}")
            if isinstance(node, ast.ClassDef):
                bases = {_qualified_name(base) for base in node.bases}
                if any(name.endswith("Enum") for name in bases):
                    raise InertSharedViolation(f"shared enum {path.name}:{node.name}")
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        raise InertSharedViolation(
                            f"executable shared descriptor {path.name}:{node.name}.{member.name}"
                        )
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Name)):
                name = node.name if hasattr(node, "name") else node.id
                normalized = name.lower()
                if any(marker in normalized for marker in FORBIDDEN_ROLE_MARKERS):
                    raise InertSharedViolation(f"forbidden shared role {path.name}:{name}")
            if isinstance(node, ast.FunctionDef):
                decorators = {_qualified_name(item) for item in node.decorator_list}
                if decorators & {"property", "classmethod", "staticmethod"}:
                    raise InertSharedViolation(f"executable descriptor in {path.name}:{node.name}")
        if any(isinstance(node, ast.FunctionDef) for node in ast.walk(tree)):
            actual = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
            if actual != allowed_functions:
                raise InertSharedViolation(
                    f"shared function exact-set mismatch in {path.name}: {actual}"
                )
        source_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if source_digest != ALLOWED_SOURCE_DIGESTS[path.name]:
            raise InertSharedViolation(f"shared frozen source mismatch in {path.name}")


def _qualified_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_qualified_name(node.value)}.{node.attr}"
    return ""


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        return node.targets
    return [node.target]


def _verify_data_class(filename: str, node: ast.ClassDef, expected_fields: tuple[str, ...]) -> None:
    if node.bases or len(node.decorator_list) != 1:
        raise InertSharedViolation(f"shared data class shape mismatch {filename}:{node.name}")
    decorator = node.decorator_list[0]
    if not (
        isinstance(decorator, ast.Call)
        and _qualified_name(decorator.func) == "dataclass"
        and not decorator.args
        and len(decorator.keywords) == 1
        and decorator.keywords[0].arg == "frozen"
        and isinstance(decorator.keywords[0].value, ast.Constant)
        and decorator.keywords[0].value.value is True
    ):
        raise InertSharedViolation(f"shared data class must be frozen {filename}:{node.name}")
    fields = tuple(
        item.target.id
        for item in node.body
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
    )
    if fields != expected_fields or len(node.body) != len(fields):
        raise InertSharedViolation(f"shared data fields mismatch {filename}:{node.name}")
