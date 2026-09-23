"""Version-six router preserving all version-five owner preparation routes."""

from . import _fan_out_process, _r14_calls_process

ROUTES = tuple(sorted((*_fan_out_process.ROUTES, *_r14_calls_process.ROUTES)))


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation == "agent_loop.prepare_captured_fan_out":
        return _fan_out_process.dispatch(operation, payload)
    return _r14_calls_process.dispatch(operation, payload)
