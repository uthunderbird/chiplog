"""Completion source decoding is deliberately narrower than terminal-work routing."""

import asyncio
import base64

import pytest
from pydantic import TypeAdapter, ValidationError
from tests.capabilities.agent_loop.test_execution_first_path_completion_contracts import (
    request as first_path_request,
)
from tests.support.completion_assembly import accepted_completion_fixture

from chiplog.capabilities.agent_loop import _execution_completion_process as process
from chiplog.capabilities.agent_loop import completion_terminal_work_sources as sources
from chiplog.capabilities.agent_loop.execution_completion_contracts import (
    ExecutionCompletionResult,
    PreparedExecutionCompletion,
)
from chiplog.capabilities.agent_loop.execution_first_path_completion_contracts import (
    first_path_inventory_fingerprint,
)
from chiplog.capabilities.agent_loop.rejected_completion_terminalization_contracts import (
    manifest_ref,
    run_ref,
)


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


def test_accepted_source_decodes_native_first_path_and_rejects_rehashed_splice() -> None:
    async def exercise() -> None:
        request = await first_path_request(canonical_response=True)
        reply = process.dispatch(process.FIRST_PATH_OPERATION, request.canonical_bytes())
        prepared = TypeAdapter(ExecutionCompletionResult).validate_json(
            base64.b64decode(reply["payload"])
        )
        assert isinstance(prepared, PreparedExecutionCompletion)
        source = sources.AcceptedCompletionWorkSourceV1(
            original_completion_request_bytes=request.canonical_bytes(),
            prepared_completion_bytes=prepared.canonical_bytes(),
            terminal_run=prepared.run,
            terminal_run_head=run_ref(prepared.run),
            terminal_manifest=prepared.terminal_manifest,
            terminal_manifest_head=manifest_ref(prepared.terminal_manifest),
            ordered_open_obligations=(),
        )
        assert sources.decode_completion_terminal_work_source(source.canonical_bytes()) == source

        cut = request.source.model_copy(update={"selected_capture": request.source.current_run})
        cut = cut.model_copy(
            update={"complete_inventory_fingerprint": first_path_inventory_fingerprint(cut)}
        )
        spliced = request.model_copy(update={"source": cut})
        wire = source.model_dump()
        wire["original_completion_request_bytes"] = spliced.canonical_bytes()
        with pytest.raises(ValidationError, match="unknown or noncanonical request"):
            sources.AcceptedCompletionWorkSourceV1.model_validate(wire)

    asyncio.run(exercise())
