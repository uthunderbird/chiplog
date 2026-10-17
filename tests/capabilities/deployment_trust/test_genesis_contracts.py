"""Public representation only; these values grant no startup admission."""

import pytest
from pydantic import ValidationError

from chiplog.capabilities.deployment_trust import (
    DatabaseGenesisV2,
    DatabaseVersionTuple,
    UnsupportedSchemaVersion,
)

VERSIONS = {
    "genesis_version": 2,
    "schema_version": 1,
    "journal_schema_version": 1,
    "canonicalization_version": 1,
    "registry_version": 1,
    "companion_manifest_version": 1,
}


def test_public_complete_genesis_retains_every_explicit_member_and_is_immutable() -> None:
    value = VERSIONS | {"tenant_id": "tenant", "database_instance_id": "database"}
    genesis = DatabaseGenesisV2(**value)
    assert genesis.model_dump() == value
    assert DatabaseGenesisV2.model_validate_json(genesis.model_dump_json()) == genesis
    with pytest.raises(ValidationError, match="frozen"):
        genesis.registry_version = 999
    with pytest.raises(ValidationError, match="extra_forbidden"):
        DatabaseGenesisV2(**(value | {"invented_version": 1}))


@pytest.mark.parametrize("member", tuple(VERSIONS))
def test_complete_tuple_has_no_missing_member_defaults(member):
    incomplete = {name: version for name, version in VERSIONS.items() if name != member}
    with pytest.raises(ValidationError, match=member):
        DatabaseVersionTuple(**incomplete)
    with pytest.raises(ValidationError, match=member):
        DatabaseGenesisV2(tenant_id="tenant", database_instance_id="database", **incomplete)


@pytest.mark.parametrize("version", (True, "2", 2.0, 0, 1, 3))
def test_v2_genesis_does_not_coerce_or_reinterpret_another_genesis_version(version):
    with pytest.raises(ValidationError):
        DatabaseGenesisV2(
            **(
                VERSIONS
                | {
                    "genesis_version": version,
                    "tenant_id": "tenant",
                    "database_instance_id": "db",
                }
            )
        )


def test_tuple_can_represent_unknown_positive_version_without_admitting_it() -> None:
    observed = DatabaseVersionTuple(**(VERSIONS | {"registry_version": 999}))
    assert observed.registry_version == 999
    # No initializer, parser, registered running tuple or admission result is
    # inferred from this observation; the startup implementation is still absent.


def test_unsupported_failure_has_fixed_typed_code_and_adoption_status() -> None:
    failure = UnsupportedSchemaVersion("registry version differs")
    assert str(failure) == "registry version differs"
    assert failure.code == "UNSUPPORTED_SCHEMA_VERSION"
    assert failure.adoption == "HOLD_ADOPTION"
    with pytest.raises(AttributeError):
        failure.code = "VALID"
    with pytest.raises(AttributeError):
        failure.adoption = "ADMITTED"
    with pytest.raises(TypeError):
        UnsupportedSchemaVersion("differs", code="VALID")
