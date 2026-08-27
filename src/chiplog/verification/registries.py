from __future__ import annotations

from .models import FixtureRegistration

CLOSED_PROFILES = (
    "fast",
    "stage0",
    "stage1",
    "stage2-offline",
    "promotion",
    "release",
)

IMPLEMENTED_PROFILES = frozenset({"fast"})

FIXTURES = (
    FixtureRegistration(
        "T01",
        "calendar-proposal-confirmation",
        "ACTIVE",
        "design-docs/transcripts/calendar-proposal-confirmation.md",
    ),
    FixtureRegistration(
        "T02",
        "fact-claim-without-plan-change",
        "ACTIVE",
        "design-docs/transcripts/fact-claim-without-plan-change.md",
    ),
    FixtureRegistration(
        "T03",
        "unknown-calendar-outcome",
        "ACTIVE",
        "design-docs/transcripts/unknown-calendar-outcome.md",
    ),
    FixtureRegistration("T04", "stale-proposal-after-head-change", "RESERVED", None),
)

# No production surface is implemented in R0. This named empty generation prevents
# an empty list from being confused with a discovered-and-evidenced surface set.
SURFACE_REGISTRY_GENERATION = "R0_NO_PRODUCTION_SURFACES"
SURFACES: tuple[str, ...] = ()
