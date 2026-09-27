"""Installed H1 launch setup shared by installed-composition witnesses."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chiplog.composition.common_cli_execution_runtime import open_common_cli_execution_runtime
from chiplog.composition.h1_launch_enrollment import (
    ExpectedH1Genesis,
    InstalledH1Slot,
    _provision_h1_enrollment,
    _provision_h1_evidence_mount,
)
from chiplog.composition.h1_registration_custody import H1RegistrationCustodyV1
from chiplog.composition.r16_dispatch_registry import HermeticDispatchResources
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


async def prepare_installed_slot(
    slot: InstalledH1Slot, expected: ExpectedH1Genesis, tmp_path: Path
) -> None:
    resources = HermeticDispatchResources(
        scenarios=("CONFIRM",), cap=1, custody_path=tmp_path / "bootstrap-custody"
    )
    async with open_common_cli_execution_runtime(
        slot.database_path, resources=resources
    ) as runtime:
        observed = runtime._trust.verify()
        assert observed is not None and observed.phase == "ACTIVE"
        assert (observed.tenant_id, observed.database_instance_id, observed.genesis_head) == (
            TENANT,
            DATABASE,
            expected.digest,
        )
    slot._trust = ActiveTrust(expected.digest)
    registry = H1RegistrationCustodyV1.model_validate(
        {
            "schema_id": "chiplog.execution.h1-registration-custody.v1",
            "deployment_id": DEPLOYMENT,
            "database_id": DATABASE,
            "database_genesis_digest": expected.digest,
            "entries": [
                {
                    "tenant_id": TENANT,
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
    _provision_h1_enrollment(slot, expected, registry)
    _provision_h1_evidence_mount(slot, expected)
