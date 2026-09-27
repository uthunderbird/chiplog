"""Private H1 completion invocation source.

The installed H1 runtime currently has no broker/trust owner capable of
authenticating a completed B invocation.  This source consequently has no
positive path: accepting a completion request, an ``InvocationProofRef``, or
any reconstructed principal/trust bytes would turn caller evidence into
authority.  It records the exact owner extension required before a mounted
producer can be introduced.
"""

from __future__ import annotations

from dataclasses import dataclass


class H1LiveInvocationSourceUnavailable(RuntimeError):
    """The installed runtime cannot authenticate a live H1 completion invocation."""


@dataclass(frozen=True)
class H1InvocationOwnerExtension:
    """The smallest missing owner contract; this is documentation, never a grant."""

    operation: str
    reason: str
    required_observations: tuple[str, ...]
    requires_shared_gate_currentness: bool
    requires_one_use_unpredictable_issuer: bool


class H1LiveInvocationSource:
    """Deny H1 invocation issuance until its authenticating owner is installed.

    A future installed owner must retain the actual enrolled B session and
    completed completion exchange, authenticate its broker route, supply the
    canonical principal and immutable trust observation, then recheck all of
    them at the shared authority-gate cut.  This object intentionally accepts
    no owner callback or evidence constructor, so a DTO cannot supply any of
    those facts ahead of that extension.
    """

    _REQUIRED_OWNER_EXTENSION = H1InvocationOwnerExtension(
        operation="agent_loop.complete_acceptance.v2",
        reason="no installed H1 completion broker/trust proof owner",
        required_observations=(
            "exact enrolled B session and completion exchange",
            "canonical authenticated principal bytes",
            "immutable observed trust evidence",
            "broker epoch/session/runtime generation",
        ),
        requires_shared_gate_currentness=True,
        requires_one_use_unpredictable_issuer=True,
    )

    def __init__(self) -> None:
        self._issued_count = 0

    @property
    def required_owner_extension(self) -> H1InvocationOwnerExtension:
        return self._REQUIRED_OWNER_EXTENSION

    @property
    def issued_count(self) -> int:
        """No issuer identity exists before the broker/trust owner is installed."""
        return self._issued_count

    def capture_invocation(self, *, session: object, identity: object) -> None:
        """Refuse all candidate input before it can be mistaken for authority."""
        del session, identity
        raise H1LiveInvocationSourceUnavailable(
            "H1 completion broker/trust proof owner is not installed"
        )


__all__ = [
    "H1InvocationOwnerExtension",
    "H1LiveInvocationSource",
    "H1LiveInvocationSourceUnavailable",
]
