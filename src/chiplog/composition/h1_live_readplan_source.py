"""Fail-closed seam for the still-unowned live H1 completion read plan.

``agent_loop.complete_acceptance.v2`` cannot issue an authoritative read
manifest from a batch, a DTO, or an inventory decoded by its caller.  The
installed runtime has physical readers, but no installed owner that declares
the operation's complete ordered presence/absence read plan together with its
selected publication-registry revision.  Until that owner is mounted, this
module deliberately has no capture token and no manifest construction path.

This module is intentionally unwired.  Its future runtime integration must
replace this denial with one private owner that captures every field below
under one held authority gate and can replay currentness before issuance.
"""

from __future__ import annotations

from dataclasses import dataclass


class H1ReadPlanSourceUnavailable(ValueError):
    """No installed owner can independently authenticate the H1 read plan."""


@dataclass(frozen=True, slots=True)
class H1ReadPlanOwnerExtension:
    """Declarative contract for the one missing private installed owner.

    ``ordered_heads`` remains empty on purpose: no installed H1 completion
    registry names the canonical ordered head identifiers yet.  Filling it
    from command/batch DTOs would make the issued manifest self-authenticating.
    """

    operation: str
    ordered_heads: tuple[str, ...]  # Populated only by the future installed owner.
    reason: str
    requires_one_gate_cut: bool
    requires_presence_and_absence: bool
    required_physical_observations: tuple[str, ...]


_REQUIRED_OWNER_EXTENSION = H1ReadPlanOwnerExtension(
    operation="agent_loop.complete_acceptance.v2",
    ordered_heads=(),
    reason="no installed H1 completion read-plan/registry owner",
    requires_one_gate_cut=True,
    requires_presence_and_absence=True,
    required_physical_observations=(
        "tenant frontier",
        "materialization commitment",
        "no pending owner/loop/gate publication",
        "selected H1 publication registry revision/head/fingerprint",
    ),
)


class H1LiveReadPlanSource:
    """Non-integrated denial boundary for live H1 predecessor/manifest input.

    The methods accept an opaque value solely so callers cannot gain authority
    by changing argument shape.  They must reject before inspecting it: input
    values can be fabricated or hostile and are not installed owner evidence.
    """

    __slots__ = ()

    @property
    def required_owner_extension(self) -> H1ReadPlanOwnerExtension:
        """State exactly what must be mounted before positive issuance exists."""
        return _REQUIRED_OWNER_EXTENSION

    def capture_predecessor(self, *, evidence: object) -> None:
        """Deny because no atomic physical predecessor reader is installed."""
        del evidence
        raise H1ReadPlanSourceUnavailable(
            "live H1 completion read-plan/registry owner is not installed"
        )

    def issue_manifest(self, *, evidence: object) -> None:
        """Deny before any caller value can become an issuance manifest."""
        del evidence
        raise H1ReadPlanSourceUnavailable(
            "live H1 completion read-plan/registry owner is not installed"
        )
