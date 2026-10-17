"""Shared builders extracted without changing their behavior."""

from __future__ import annotations

from pathlib import Path

from chiplog.composition.h1_launch_enrollment import (
    ExpectedH1Genesis,
    InstalledH1Slot,
)
from chiplog.composition.h1_registration_custody import H1RegistrationCustodyV1
from chiplog.platform.r7_trust_durability import TrustDurabilityObservation

_DEPLOYMENT = "evidence-installed-deployment"


_TENANT = "evidence-installed-tenant"


_DATABASE = "evidence-installed-database"


class _ActiveTrust:
    def __init__(self, digest: str) -> None:
        self._observation = TrustDurabilityObservation(
            _TENANT, _DATABASE, digest, "trust", "materialization", "ACTIVE"
        )

    def verify(self) -> TrustDurabilityObservation:
        return self._observation


def _slot(tmp_path: Path) -> tuple[InstalledH1Slot, ExpectedH1Genesis, H1RegistrationCustodyV1]:
    root = tmp_path / "installed"
    root.mkdir(mode=0o700)
    (root / "custody").mkdir(mode=0o700)
    database = root / "runtime.sqlite3"
    database.write_bytes(b"sqlite placeholder")
    database.chmod(0o600)
    expected = ExpectedH1Genesis(_DEPLOYMENT, _TENANT, _DATABASE)
    slot = InstalledH1Slot(
        root, "custody", database, _DEPLOYMENT, _TENANT, _DATABASE, _ActiveTrust(expected.digest)
    )
    registry = H1RegistrationCustodyV1.model_validate(
        {
            "schema_id": "chiplog.execution.h1-registration-custody.v1",
            "deployment_id": _DEPLOYMENT,
            "database_id": _DATABASE,
            "database_genesis_digest": expected.digest,
            "entries": [
                {
                    "tenant_id": _TENANT,
                    "principal_id": "principal",
                    "channel_id": "channel",
                    "registration_id": "registration",
                    "generation": 0,
                    "status": "ACTIVE",
                    "origin_recipient_id": "recipient",
                    "conversation_id": "conversation",
                    "visible_channels": ["channel"],
                }
            ],
        }
    )
    return slot, expected, registry
