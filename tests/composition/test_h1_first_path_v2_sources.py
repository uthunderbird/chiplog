"""V2-specific fail-closed behavior for the first-path raw-source seam."""

from types import SimpleNamespace
from typing import cast

import pytest

from chiplog.capabilities.agent_loop.call_acceptance_contracts import CallSubjectHead
from chiplog.capabilities.agent_loop.recovery_contracts import Present
from chiplog.composition.common_execution_driver_contracts import DriverCommandIdentityV1
from chiplog.composition.h1_first_path_sources import (
    H1FirstPathSources,
    H1WorkspaceClosureResolver,
)
from chiplog.platform.ingress_transition_contracts import IngressCommandIdentity


def test_current_capture_never_relabels_a_selected_v1_registry_as_v2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A V1 physical registry cannot become a V2 cut by decoder choice."""
    reader = object.__new__(H1FirstPathSources)
    callback_calls: list[object] = []
    reader._workspace_closure_resolver = cast(
        H1WorkspaceClosureResolver, lambda **kwargs: callback_calls.append(kwargs)
    )
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
    assert callback_calls == []
