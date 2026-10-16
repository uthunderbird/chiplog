"""Inert complete genesis/version values; only authenticated startup can admit them."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DatabaseVersionTuple(BaseModel):
    """All members are explicit; representing a version does not register it."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    genesis_version: int = Field(ge=1)
    schema_version: int = Field(ge=1)
    journal_schema_version: int = Field(ge=1)
    canonicalization_version: int = Field(ge=1)
    registry_version: int = Field(ge=1)
    companion_manifest_version: int = Field(ge=1)


class DatabaseGenesisV2(DatabaseVersionTuple):
    """Distinct complete genesis shape; legacy V1 bytes are never defaulted here."""

    genesis_version: int = Field(ge=2, le=2)
    tenant_id: str = Field(min_length=1)
    database_instance_id: str = Field(min_length=1)


class UnsupportedSchemaVersion(RuntimeError):
    """Typed startup refusal; no replay, materialization or work is authorized."""

    @property
    def code(self) -> Literal["UNSUPPORTED_SCHEMA_VERSION"]:
        return "UNSUPPORTED_SCHEMA_VERSION"

    @property
    def adoption(self) -> Literal["HOLD_ADOPTION"]:
        return "HOLD_ADOPTION"
