from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from chiplog.capabilities.projections.r9_boundary import (
    BudgetPolicy,
)


def test_floor_n_minus_one_and_insufficient_total(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=31)
