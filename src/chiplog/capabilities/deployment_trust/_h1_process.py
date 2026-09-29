"""H1 route registration plus the privately installed J7 owner evaluator."""

from collections.abc import Callable

from . import _cli_custody_process, _j7_process, _r7_process, operator_policy_command_verifier

ROUTES = tuple(
    sorted((*_cli_custody_process.ROUTES, *_r7_process._H1_ROUTES, *_j7_process.ROUTES))
)

_j7_dispatch: Callable[[str, bytes], dict[str, object]] = _j7_process.make_dispatch(None)


def install_j7_owner_evaluator(
    operator_policy_key_binding_bytes: bytes | None,
) -> Callable[[str, bytes], dict[str, object]]:
    """Install the frozen startup binding before the child audit hook starts."""
    global _j7_dispatch
    _j7_dispatch = _j7_process.make_dispatch(operator_policy_key_binding_bytes)
    operator_policy_command_verifier.preload_operator_policy_verifier()
    return dispatch


def dispatch(operation: str, payload: bytes) -> dict[str, object]:
    if operation == _j7_process.ROUTES[0][0]:
        return _j7_dispatch(operation, payload)
    if operation in {route[0] for route in _r7_process._H1_ROUTES}:
        return _r7_process.dispatch(operation, payload)
    return _cli_custody_process.dispatch(operation, payload)
