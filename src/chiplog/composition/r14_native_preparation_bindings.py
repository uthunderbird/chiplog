"""Build inert native Prepare bindings from an authenticated source projection."""

from __future__ import annotations

import hashlib

from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_preparation_bindings_contracts import (
    NativeExecutionPreparationBindingsV2,
)

from .r14_original_prepare_source import R14OriginalNativePrepareProjection


def bind_native_execution_preparation_v2(
    projection: R14OriginalNativePrepareProjection,
) -> NativeExecutionPreparationBindingsV2:
    """Retain exact Prepare facts without reading, issuing, or authorizing anything."""
    if type(projection) is not R14OriginalNativePrepareProjection:
        raise TypeError("native preparation V2 requires an original Prepare projection")
    artifact = projection.artifact
    members = projection.manifest.members
    try:
        run = ExecutionRunRecord.model_validate_json(projection.prepared_run_bytes)
    except ValueError as error:
        raise ValueError("original Prepare projection has malformed prepared Run bytes") from error
    if (
        len(members) != 4
        or tuple(member.surface for member in members)
        != ("workspace", "context", "prompt", "schema")
        or projection.selected_prepare_decision_id == ""
        or run.canonical_bytes() != projection.prepared_run_bytes
        or len(run.turns) != 1
        or len(run.turns[0].attempts) != 1
        or run.turns[0].selector != 0
        or projection.manifest != run.turns[0].attempts[0].manifest
        or hashlib.sha256(projection.selected_prepare_decision_bytes).hexdigest()
        != projection.selected_prepare_decision_fingerprint
        or projection.artifact_bytes != artifact.canonical_bytes()
        or projection.rendered_prompt_bytes != artifact.rendered.encode()
        or projection.generated_schema_bytes != artifact.response_schema_json.encode()
        or projection.ordered_tool_spec_bytes
        != tuple(tool.canonical_bytes() for tool in artifact.tools)
        or members[2].content != artifact.rendered
        or members[2].revision_head != artifact.content_hash
        or members[3].content != artifact.response_schema_json
        or members[3].revision_head != artifact.digest()
    ):
        raise ValueError("original Prepare projection differs from native binding facts")
    return NativeExecutionPreparationBindingsV2(
        tenant_id=projection.tenant_id,
        database_identity=projection.database_identity,
        selected_response_seal=projection.selected_response_seal,
        selected_prepare_decision_id=projection.selected_prepare_decision_id,
        selected_prepare_decision_bytes=projection.selected_prepare_decision_bytes,
        selected_prepare_decision_fingerprint=projection.selected_prepare_decision_fingerprint,
        prepared_run=projection.prepared_run,
        prepared_run_bytes=projection.prepared_run_bytes,
        artifact_bytes=projection.artifact_bytes,
        artifact_digest=artifact.digest(),
        rendered_prompt_bytes=projection.rendered_prompt_bytes,
        generated_schema_bytes=projection.generated_schema_bytes,
        ordered_tool_spec_bytes=projection.ordered_tool_spec_bytes,
        policy_bytes=projection.policy_bytes,
        reported_template_digest=artifact.content_hash,
        rendered_prompt_sha256=hashlib.sha256(projection.rendered_prompt_bytes).hexdigest(),
    )


__all__ = ["bind_native_execution_preparation_v2"]
