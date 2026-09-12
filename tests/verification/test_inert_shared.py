from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from chiplog.verification.inert_shared import InertSharedViolation, verify_inert_shared_source
from tests.support.inert_shared import PACKAGE


@pytest.mark.parametrize(
    ("filename", "source", "message"),
    [
        ("extra.py", "VALUE = 1\n", "exact-set"),
        (
            "identity.py",
            "\ndef validate_record():\n    return True\n",
            "unregistered shared function",
        ),
        (
            "identity.py",
            "\nfrom enum import Enum\nclass State(Enum):\n    OPEN = 1\n",
            "shared import exact-set",
        ),
        ("identity.py", "\nPOLICY_REGISTRY = {}\n", "shared binding exact-set"),
        (
            "identity.py",
            "\nclass Lookup:\n    @property\n    def owner(self):\n        return 'planning'\n",
            "shared class exact-set",
        ),
        ("identity.py", "\nresolve = lambda value: value\n", "shared binding exact-set"),
        ("identity.py", "\nclass PlanningState:\n    OPEN = 1\n", "shared class exact-set"),
        (
            "identity.py",
            "\nfrom builtins import eval as check\n",
            "shared import exact-set",
        ),
    ],
)
def test_v2_executable_shared_mutants_are_rejected(
    tmp_path: Path, filename: str, source: str, message: str
) -> None:
    target = tmp_path / "domain_primitives"
    shutil.copytree(PACKAGE, target)
    path = target / filename
    if path.exists():
        path.write_text(path.read_text() + source)
    else:
        path.write_text(source)

    with pytest.raises(InertSharedViolation, match=message):
        verify_inert_shared_source(target)


def test_v2_substitution_after_initial_verification_is_detected(tmp_path: Path) -> None:
    target = tmp_path / "domain_primitives"
    shutil.copytree(PACKAGE, target)
    verify_inert_shared_source(target)
    identity = target / "identity.py"
    identity.write_text(identity.read_text() + "\ndef owner_validator(value):\n    return value\n")

    with pytest.raises(InertSharedViolation, match="unregistered shared function"):
        verify_inert_shared_source(target)


def test_v2_nested_plain_enum_bypass_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "domain_primitives"
    shutil.copytree(PACKAGE, target)
    codec = target / "codec.py"
    codec.write_text(
        codec.read_text().replace(
            '    """Encode one owner-produced record using its producing version bindings."""\n',
            (
                '    """Encode one owner-produced record using its producing version bindings."""\n'
                "    class PolicyState:\n"
                "        OPEN = 1\n"
            ),
            1,
        )
    )

    with pytest.raises(InertSharedViolation, match="shared class exact-set"):
        verify_inert_shared_source(target)


def test_v2_import_time_call_bypass_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "domain_primitives"
    shutil.copytree(PACKAGE, target)
    identity = target / "identity.py"
    identity.write_text(identity.read_text() + '\nprint("POLICY SIDE EFFECT")\n')

    with pytest.raises(InertSharedViolation, match="top-level statement is executable"):
        verify_inert_shared_source(target)


def test_v2_allowed_function_body_substitution_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "domain_primitives"
    shutil.copytree(PACKAGE, target)
    codec = target / "codec.py"
    codec.write_text(
        codec.read_text().replace(
            "    envelope = {\n",
            '    print("POLICY SIDE EFFECT")\n    envelope = {\n',
            1,
        )
    )

    with pytest.raises(InertSharedViolation, match="frozen source mismatch"):
        verify_inert_shared_source(target)
