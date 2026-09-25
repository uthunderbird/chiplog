"""V2-specific fail-closed behavior for the first-path raw-source seam."""

from types import SimpleNamespace

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_first_path_sources import (
    H1FirstPathSources,
)
from chiplog.composition.h1_preseal_contracts import H1OwnerAsOfV1
from chiplog.platform.ingress_transition_contracts import IngressCommandIdentity


def test_current_capture_never_relabels_a_selected_v1_registry_as_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A V1 physical registry cannot become a V2 cut by decoder choice."""
    reader = object.__new__(H1FirstPathSources)
    monkeypatch.setattr(
        H1FirstPathSources,
        "_read_selected_cut",
        lambda *_args, **_kwargs: SimpleNamespace(registry_is_v2=False),
    )

    with pytest.raises(ValueError, match="genuinely selected V2 registry"):
        reader.capture_current(
            original_identity=DriverCommandIdentityV1(
                tenant_id="tenant",
                database_id="database",
                driver_command_id="driver:command",
                original_ingress_identity=IngressCommandIdentity(
                    tenant_id="tenant", database_id="database", command_id="ingress:command"
                ),
                original_ingress_request_fingerprint="a" * 64,
            ),
            original_fingerprint="b" * 64,
            selected_seal=CallSubjectHead(
                subject_id="seal",
                revision=Present(head="record:" + "c" * 64, fingerprint="c" * 64),
            ),
        )


def test_historical_owner_asof_decodes_from_the_exact_selected_decision() -> None:
    locator = H1OwnerAsOfV1(tenant_id="tenant", owner_head=None)
    decision = b'{"h1_owner_asof":' + locator.canonical_bytes() + b',"kind":"DECIDED"}'

    assert H1FirstPathSources._decode_h1_owner_asof(decision, tenant_id="tenant") == locator


@pytest.mark.parametrize(
    ("decision", "tenant", "match"),
    [
        (b'{"kind":"DECIDED"}', "tenant", "lacks owner-as-of"),
        (b'{"h1_owner_asof":null}', "tenant", "owner-as-of field is invalid"),
        (
            b'{"h1_owner_asof":{"kind":"H1_OWNER_AS_OF_V1","owner_head":null,"tenant_id":"other"}}',
            "tenant",
            "owner-as-of tenant differs",
        ),
        (
            b'{"h1_owner_asof":{"kind":"H1_OWNER_AS_OF_V1","owner_head":null,"tenant_id":"tenant"},"h1_owner_asof":{"kind":"H1_OWNER_AS_OF_V1","owner_head":null,"tenant_id":"tenant"}}',
            "tenant",
            "duplicate owner-as-of",
        ),
    ],
)
def test_historical_owner_asof_rejects_unselected_or_foreign_locator(
    decision: bytes, tenant: str, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        H1FirstPathSources._decode_h1_owner_asof(decision, tenant_id=tenant)
