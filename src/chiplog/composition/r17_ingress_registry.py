"""Closed hermetic PRE_AUTH retained-file profile; no source authentication or ACK rights."""

from chiplog.platform.ingress_custody_records import CustodyProfile

RETAINED_CLI_READER_ID = "r17-retained-cli-reader.v1"


def retained_cli_profile(tenant_id: str, database_id: str) -> CustodyProfile:
    if (tenant_id, database_id) != ("hermetic-tenant", "hermetic-database"):
        raise ValueError("unregistered retained ingress deployment")
    return CustodyProfile(
        tenant_id=tenant_id,
        database_id=database_id,
        source_identity="hermetic-retained-cli",
        source_class="CLI",
        transport_version="chiplog.hermetic.cli-retained-file.v1",
        maximum_items=8,
        maximum_total_bytes=524288,
        maximum_item_bytes=65536,
    )


def require_registered_profile(profile: CustodyProfile) -> None:
    if profile != retained_cli_profile(profile.tenant_id, profile.database_id):
        raise ValueError("unregistered historical retained-source interpreter or profile")
