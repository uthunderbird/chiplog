"""V2 H1 frontier extraction fails closed before a broker proves closure."""

import pytest

from chiplog.capabilities.agent_loop.execution_h1_frontier_profile_v2 import (
    H1FrontierProfileV2Error,
    derive_h1_frontier_profile_v2_members,
)


def test_v2_extractor_requires_original_workspace_closure_before_carrier_use() -> None:
    with pytest.raises(H1FrontierProfileV2Error) as raised:
        derive_h1_frontier_profile_v2_members(
            final_run=object(),  # type: ignore[arg-type]
            selected_admitted_input=object(),  # type: ignore[arg-type]
            seal=object(),  # type: ignore[arg-type]
            verified_workspace=None,
        )

    assert raised.value.code == "H1_WORKSPACE_ORIGINAL_READ_UNPROVEN"
