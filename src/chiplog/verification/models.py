from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Status = Literal["PASS", "FAIL", "INCONCLUSIVE", "HOLD"]


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: Status
    claim: str
    observed: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FixtureRegistration:
    fixture_id: str
    scenario_id: str
    state: Literal["ACTIVE", "RESERVED"]
    relative_path: str | None


@dataclass(frozen=True)
class InvariantSource:
    number: int
    invariant_id: str
    normalized_text: str
    text_digest: str
