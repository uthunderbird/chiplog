"""Completion source decoding is deliberately narrower than terminal-work routing."""

import asyncio

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.support.completion_assembly import accepted_completion_fixture

from chiplog.capabilities.agent_loop import completion_terminal_work_sources as sources
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import run_ref


def test_completion_source_union_rejects_other_terminalization_bytes() -> None:
    adapter: TypeAdapter[sources.CompletionTerminalWorkSourceV1] = TypeAdapter(
        sources.CompletionTerminalWorkSourceV1
    )
    assert set(adapter.json_schema()["discriminator"]["mapping"]) == {
        "ACCEPTED_COMPLETION_WORK_SOURCE_V1",
        "REJECTED_COMPLETION_WORK_SOURCE_V1",
    }
    with pytest.raises(ValueError, match="unknown completion"):
        sources.decode_completion_terminal_work_source(b'{"kind":"PREPARE_WORK_CLOSE_V1"}')
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "ACCEPTED_COMPLETION_WORK_SOURCE_V1"})


def test_accepted_source_rejects_rehashed_terminal_with_foreign_predecessor() -> None:
    async def exercise() -> None:
        fixture = await accepted_completion_fixture("v3", "nonempty")
        source = fixture.assembly.work_source
        assert sources.decode_completion_terminal_work_source(source.canonical_bytes()) == source
        pending = source.terminal_run.model_copy(
            update={"predecessor": "foreign-original-run", "head": "pending"}
        )
        foreign = pending.model_copy(update={"head": "loop:" + pending.digest()})
        prepared = fixture.assembly.prepared_completion.model_copy(update={"run": foreign})
        wire = source.model_dump()
        wire.update(
            terminal_run=foreign,
            terminal_run_head=run_ref(foreign),
            prepared_completion_bytes=prepared.canonical_bytes(),
        )
        with pytest.raises(ValidationError, match="accepted work source"):
            sources.AcceptedCompletionWorkSourceV1.model_validate(wire)

    asyncio.run(exercise())
