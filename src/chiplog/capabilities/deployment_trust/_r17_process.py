"""Current trust owner router; legacy graph keeps its original four-route module."""

from . import _ingress_process, _r7_process

ROUTES = tuple(sorted((*_r7_process.ROUTES, *_ingress_process.ROUTES)))


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation == _ingress_process.ROUTES[0][0]:
        return _ingress_process.dispatch(operation, payload)
    return _r7_process.dispatch(operation, payload)
