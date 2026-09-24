"""H1-only route registration; decisions remain in the existing trust owner."""

from . import _cli_custody_process, _r7_process

ROUTES = tuple(sorted((*_cli_custody_process.ROUTES, *_r7_process._H1_ROUTES)))


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    return _cli_custody_process.dispatch(operation, payload)
