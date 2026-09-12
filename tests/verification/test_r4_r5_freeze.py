from __future__ import annotations

from dataclasses import replace

import pytest

from chiplog.architecture.r4_r5_freeze import (
    R4_R5_EXPORTS,
    R4_R5_RECORDS,
)


def test_duplicate_freeze_member_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import chiplog.architecture.r4_r5_freeze as freeze

    monkeypatch.setattr(freeze, "R4_R5_EXPORTS", (*R4_R5_EXPORTS, R4_R5_EXPORTS[0]))
    with pytest.raises(ValueError, match="unique and canonically ordered"):
        freeze.verify_r4_r5_freeze()


def test_unknown_owner_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    import chiplog.architecture.r4_r5_freeze as freeze

    changed = replace(R4_R5_RECORDS[0], owner="storage")
    monkeypatch.setattr(freeze, "R4_R5_RECORDS", (changed, *R4_R5_RECORDS[1:]))
    with pytest.raises(ValueError, match="record owner"):
        freeze.verify_r4_r5_freeze()


@pytest.mark.parametrize(
    "name",
    [
        "R4_R5_PACKAGES",
        "R4_R5_CAPABILITIES",
        "R4_R5_EXPORTS",
        "R4_R5_RECORDS",
        "R4_R5_SURFACES",
        "R4_R5_DERIVATIVE_SINKS",
        "R4_R5_PROVIDERS",
    ],
)
def test_each_exact_set_rejects_an_omission(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    import chiplog.architecture.r4_r5_freeze as freeze

    values = getattr(freeze, name)
    monkeypatch.setattr(freeze, name, values[:-1])
    with pytest.raises(ValueError):
        freeze.verify_r4_r5_freeze()
