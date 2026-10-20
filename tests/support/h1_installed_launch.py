"""Installed H1 launch setup shared by installed-composition witnesses."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chiplog.composition.h1_launch_enrollment import (
    ExpectedH1Genesis,
    InstalledH1Slot,
)
from chiplog.composition.h1_registration_custody import H1RegistrationCustodyV1
from chiplog.platform.r7_trust_durability import TrustDurabilityObservation

DEPLOYMENT = "installed-deployment"
TENANT = "hermetic-tenant"
DATABASE = "hermetic-database"
PRINCIPAL = "hermetic-principal"
CHANNEL = "hermetic-local"


class ActiveTrust:
    def __init__(self, digest: str) -> None:
        self._observation = TrustDurabilityObservation(
            TENANT, DATABASE, digest, "trust", "materialization", "ACTIVE"
        )

    def verify(self) -> TrustDurabilityObservation:
        return self._observation


def installed_slot(tmp_path: Path) -> tuple[InstalledH1Slot, ExpectedH1Genesis]:
    root = tmp_path / "installed"
    root.mkdir(mode=0o700)
    (root / "custody").mkdir(mode=0o700)
    database = root / "runtime.sqlite3"
    with sqlite3.connect(database):
        pass
    database.chmod(0o600)
    expected = ExpectedH1Genesis(DEPLOYMENT, TENANT, DATABASE)
    return (
        InstalledH1Slot(
            root,
            "custody",
            database,
            DEPLOYMENT,
            TENANT,
            DATABASE,
            ActiveTrust(expected.digest),
        ),
        expected,
    )


def active_registration_custody(expected: ExpectedH1Genesis) -> H1RegistrationCustodyV1:
    """One canonical active registry fixture for installed H1 composition tests."""
    return H1RegistrationCustodyV1.model_validate(
        {
            "schema_id": "chiplog.execution.h1-registration-custody.v1",
            "deployment_id": expected.deployment_id,
            "database_id": expected.database_id,
            "database_genesis_digest": expected.digest,
            "entries": [
                {
                    "tenant_id": expected.tenant_id,
                    "principal_id": PRINCIPAL,
                    "channel_id": CHANNEL,
                    "registration_id": "installed-registration",
                    "generation": 0,
                    "status": "ACTIVE",
                    "origin_recipient_id": PRINCIPAL,
                    "conversation_id": "installed-conversation",
                    "visible_channels": [CHANNEL],
                }
            ],
        }
    )
