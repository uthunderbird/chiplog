"""R13 binds an installed H1 workspace only after its authenticated inventory."""

from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import BudgetPolicy, LoopRejected
from chiplog.capabilities.projections.provenance import ProvenanceBinding
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.capabilities.projections.workspace_boundary import SourceReference
from chiplog.composition.r13_runtime import R13Runtime
from chiplog.composition.r13_workspace import R13Workspace
from chiplog.composition.r13_workspace_provenance import conversation_bindings
from chiplog.composition.r14_execution_runtime import open_execution_runtime
from chiplog.composition.r14_h1_workspace_issuance_contracts import H1OriginalWorkspaceIssuanceV1


@pytest.mark.asyncio
async def test_unsupported_h1_selection_refuses_before_authenticated_enumeration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "execution.sqlite"
    async with open_execution_runtime(database) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        enumerated = False

        def track_enumeration(
            source_runtime: R13Runtime,
            history: tuple[ConversationEntry, ...],
            extra: tuple[SourceReference, ...],
            *,
            channel: str,
            contour: str,
        ) -> tuple[ProvenanceBinding, ...]:
            nonlocal enumerated
            enumerated = True
            return conversation_bindings(
                source_runtime, history, extra, channel=channel, contour=contour
            )

        monkeypatch.setattr(
            "chiplog.composition.r13_workspace.conversation_bindings", track_enumeration
        )

        with pytest.raises(LoopRejected, match="workspace binding mount is unavailable"):
            await R13Workspace(runtime).context(started, h1_preissuance_selection=object())

        assert not enumerated


@pytest.mark.asyncio
async def test_context_without_installed_selection_keeps_v1_original_issuance(
    tmp_path: Path,
) -> None:
    database = tmp_path / "execution.sqlite"
    async with open_execution_runtime(database) as runtime:
        created = await runtime.create_execution("hermetic-ingress", "run", "Plan", BudgetPolicy())
        started = await runtime.begin_execution("hermetic-ingress", "run", created.head)
        workspace = R13Workspace(runtime)

        await workspace.context(started)

        reference = workspace._last_h1_workspace_issuance
        assert reference is not None
        issuance = workspace.open_h1_workspace_issuance().load(reference)
        assert isinstance(issuance, H1OriginalWorkspaceIssuanceV1)
