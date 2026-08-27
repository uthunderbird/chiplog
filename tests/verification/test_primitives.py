from __future__ import annotations

import pytest

from chiplog.verification.primitives import FaultPoint, StateObservation, UnreachedFaultError


def test_fault_point_requires_observed_reachability() -> None:
    fault = FaultPoint("after-commit")

    with pytest.raises(UnreachedFaultError, match="after-commit"):
        fault.require_reached()

    fault.hit()
    fault.require_reached()


def test_state_observation_reports_change() -> None:
    assert StateObservation(before={"rows": 0}, after={"rows": 1}).changed
    assert not StateObservation(before={"rows": 1}, after={"rows": 1}).changed
