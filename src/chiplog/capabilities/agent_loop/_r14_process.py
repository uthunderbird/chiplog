"""Closed agent-loop owner router for transition and scheduler preparation."""

from . import _delivery_process, _r13_process, _scheduler_process

ROUTES = tuple(
    sorted((*_delivery_process.ROUTES, *_r13_process.ROUTES, *_scheduler_process.ROUTES))
)


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation in {route[0] for route in _delivery_process.ROUTES}:
        return _delivery_process.dispatch(operation, payload)
    if operation in {route[0] for route in _r13_process.ROUTES}:
        return _r13_process.dispatch(operation, payload)
    return _scheduler_process.dispatch(operation, payload)
