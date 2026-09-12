from __future__ import annotations

import pytest
from pydantic import ValidationError

from chiplog.capabilities.planning.r8_boundary import AuthorityRead, AuthorityTrace
from chiplog.platform.deployment_gate import DeploymentGateGeneration, DeploymentGateResult


def test_authority_contract_preserves_bytes_and_rejects_untracked_fields() -> None:
    read = AuthorityRead(
        kind="PLANNING",
        source_id="planning",
        source_version="1",
        head="head",
        generation="generation",
        frontier="frontier",
        valid_until_ns=10,
        canonical_value=b'{"head":0}',
    )
    trace = AuthorityTrace(
        tenant_id="tenant",
        principal_id="principal",
        reads=(read,),
        registry_inputs=(("row", "direct-principal-create-v1"),),
    )
    assert trace.reads[0].canonical_value == b'{"head":0}'
    with pytest.raises(ValidationError):
        AuthorityTrace.model_validate({**trace.model_dump(), "cached_authority": b"bypass"})
    with pytest.raises(ValidationError):
        AuthorityRead.model_validate({**read.model_dump(), "kind": "UNKNOWN"})


def test_gate_contract_is_strict_and_has_no_third_permit_mode() -> None:
    with pytest.raises(ValidationError):
        DeploymentGateGeneration.model_validate(
            {"tenant_id": "tenant", "epoch": "epoch", "sequence": "1"}
        )
    with pytest.raises(ValidationError):
        DeploymentGateResult.model_validate(
            {"disposition": "PERMIT_DEVELOPMENT", "request_digest": "digest", "reason": "test"}
        )
