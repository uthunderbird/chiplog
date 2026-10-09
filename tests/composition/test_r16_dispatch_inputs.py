"""Origin-specific planning projections for R16 source capture."""

from types import SimpleNamespace

from chiplog.capabilities.effects.dispatch_v2 import canonical
from chiplog.composition.r16_dispatch_inputs import planning_scope


def test_call_origin_uses_empty_scope_and_plan_origin_keeps_inventory() -> None:
    captured = SimpleNamespace(
        planning_records=(("plan-record", b"selected planning bytes"),),
        own_planning_records=(),
    )

    assert planning_scope(captured, None, call_origin=True) == canonical([])
    assert planning_scope(captured, None) == canonical(
        [["plan-record", b"selected planning bytes".hex()]]
    )
