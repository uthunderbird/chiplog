"""Version-five router preserving the version-four owner preparation partition."""

from . import _call_process, _r14_process

ROUTES = tuple(sorted((*_call_process.ROUTES, *_r14_process.ROUTES)))


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation in {route[0] for route in _call_process.ROUTES}:
        return _call_process.dispatch(operation, payload)
    return _r14_process.dispatch(operation, payload)
