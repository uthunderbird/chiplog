"""Composition-only H1 slot; no caller-provided recipient or authority bytes.

The verifier port is an implementation obligation, not a registered production
verifier. Canonical assembly must bind actual retained initialization selection,
R16 historical/current custody and the shared authority gate before issuance.
The combined binding must authenticate selected R17 and R16 together. Until it
exists the service unconditionally returns UNSUPPORTED.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead, ProviderRecipient

from . import TrustReference
from .hermetic_output_scope_contracts import SelectedHermeticResourceObservationRefV1


@dataclass(frozen=True, slots=True)
class VerifiedH1SelectedSources:
    """Internal source binding, never disclosure permission or an issuer result.

    Currentness is only meaningful inside the registered gate's ordering domain;
    retaining this value does not retain authority.
    """

    recipient: ProviderRecipient
    authenticated_cli_ref: TrustReference
    admitted_authentication_ref: ExactHead
    selected_resource_observation_ref: SelectedHermeticResourceObservationRefV1


class SelectedResourceVerifier(Protocol):
    def resolve_selected_current(
        self,
        resource_ref: SelectedHermeticResourceObservationRefV1,
        admitted_authentication_ref: ExactHead,
        authenticated_cli_ref: TrustReference,
    ) -> VerifiedH1SelectedSources | None:
        """Join selected R17 and current R16 sources under the shared gate.

        A copied source, matching tuple, or caller-supplied DTO proves nothing.
        Absence, stale resources or uncertain provenance must return None.
        """
        ...


@dataclass(frozen=True, slots=True)
class HermeticEffectsOriginSlotV1:
    resource_verifier: SelectedResourceVerifier
    slot_id: Literal["h1-cli-effects-origin"] = field(default="h1-cli-effects-origin", init=False)
    mandate_profile: Literal["h1-cli-effects-origin-zero-call-v1"] = field(
        default="h1-cli-effects-origin-zero-call-v1", init=False
    )
    ordered_mandates: tuple[()] = field(default=(), init=False)

    def __post_init__(self) -> None:
        if not callable(getattr(self.resource_verifier, "resolve_selected_current", None)):
            raise TypeError("H1 slot requires an owner-registered resource verifier port")


def configured_h1_effects_origin_slot(
    resource_verifier: SelectedResourceVerifier,
) -> HermeticEffectsOriginSlotV1:
    """Only canonical assembly supplies this verifier; this is not a wire DTO."""
    return HermeticEffectsOriginSlotV1(resource_verifier)
