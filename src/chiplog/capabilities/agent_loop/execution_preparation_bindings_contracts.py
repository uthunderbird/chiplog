"""Inert H1 preparation companion; locators are claims, never owner authority.

The closed registry names projections, not traversal expressions. A future reader
must resolve each anchor against selected durable bytes and owner-issued workspace
reads. Public construction, canonical decoding and consistency checks below do not
prove publication, issuance, completeness, currentness or authorization.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final, Literal, Self

from pydantic import model_validator

from .contracts import VisibilityMember
from .delivery_contracts import ExactHead
from .execution_contracts import ExecutionPromptArtifact
from .execution_history_contracts import ExecutionPromptArtifactV3
from .recovery_contracts import Identity, RecoveryDTO, UInt64

PREPARATION_BINDINGS_SCHEMA: Final = "chiplog.execution.preparation-bindings.v1"
H1_SOURCE_PROFILE: Final = "chiplog.execution.h1-cli-hermetic-source-profile.v1"

# These identifiers name registered projections; no generic JSON path interpreter.
SourceFieldPath = Literal[
    "initialization.admitted",
    "initialization.admitted.origin.recipient",
    "workspace.issued_read",
    "workspace.source.revision",
    "workspace.source.provenance",
    "workspace.source.label",
    "context.started_run",
    "context.contour",
    "preparation.manifest.artifact",
    "preparation.manifest.artifact.rendered",
    "preparation.manifest.artifact.generated_schema",
    "scope.disclosure_policy",
    "run.policy",
]


class PreparationSourceAnchorV1(RecoveryDTO):
    """Actual physical record and journal decision have separate fingerprints.

    Owner/schema strings identify the original surface, not a universal decoder.
    Unsupported owner/schema combinations must be rejected by the eventual reader.
    """

    tenant_id: Identity
    database_id: Identity
    owner_id: Identity
    schema_id: Identity
    record: ExactHead
    selected_operation_id: Identity
    selected_decision: ExactHead
    commit_sequence: UInt64

    @model_validator(mode="after")
    def physical_identity(self) -> Self:
        if self.record.identity != self.record.head:
            raise ValueError("physical record identity must equal actual record ID/head")
        if self.selected_decision.identity != self.selected_decision.head:
            raise ValueError("selected decision identity must equal actual decision ID/head")
        return self


class PreparationSourceLocatorV1(RecoveryDTO):
    anchor: PreparationSourceAnchorV1
    field_path: SourceFieldPath
    # Exact original string retained, never hashed to manufacture a source.
    original_head: Identity
    resolved_ref: ExactHead


class PreparationWorkspaceReadV1(RecoveryDTO):
    issued_read_locator: PreparationSourceLocatorV1
    ordered_source_locators: tuple[PreparationSourceLocatorV1, ...]

    @model_validator(mode="after")
    def roles(self) -> Self:
        _path(self.issued_read_locator, "workspace.issued_read")
        for locator in self.ordered_source_locators:
            if locator.field_path not in {
                "workspace.source.revision",
                "workspace.source.provenance",
                "workspace.source.label",
            }:
                raise ValueError("unregistered workspace source role")
        if len({x.canonical_bytes() for x in self.ordered_source_locators}) != len(
            self.ordered_source_locators
        ):
            raise ValueError("duplicate workspace source locator")
        return self


class PreparationVisibilityBindingV1(RecoveryDTO):
    member_index: UInt64
    record_id: Identity
    surface: Literal["workspace", "context", "prompt", "schema"]
    revision_source: PreparationSourceLocatorV1
    provenance_source: PreparationSourceLocatorV1
    label_source: PreparationSourceLocatorV1

    @model_validator(mode="after")
    def roles(self) -> Self:
        paths = {
            "workspace": (
                "workspace.source.revision",
                "workspace.source.provenance",
                "workspace.source.label",
            ),
            "context": ("context.started_run", "context.started_run", "context.contour"),
            "prompt": (
                "preparation.manifest.artifact.rendered",
                "preparation.manifest.artifact.rendered",
                "scope.disclosure_policy",
            ),
            "schema": (
                "preparation.manifest.artifact",
                "preparation.manifest.artifact",
                "scope.disclosure_policy",
            ),
        }[self.surface]
        for locator, path in zip(
            (self.revision_source, self.provenance_source, self.label_source), paths, strict=True
        ):
            _path(locator, path)
        return self


class PreparationPromptBindingV1(RecoveryDTO):
    artifact_locator: PreparationSourceLocatorV1
    rendered_text_locator: PreparationSourceLocatorV1
    prompt_member_index: Literal[2]

    @model_validator(mode="after")
    def roles(self) -> Self:
        _path(self.artifact_locator, "preparation.manifest.artifact")
        _path(self.rendered_text_locator, "preparation.manifest.artifact.rendered")
        return self


class PreparationGeneratedSchemaBindingV1(RecoveryDTO):
    artifact_locator: PreparationSourceLocatorV1
    schema_projection_locator: PreparationSourceLocatorV1
    schema_member_index: Literal[3]
    generator_version: Identity

    @model_validator(mode="after")
    def roles(self) -> Self:
        _path(self.artifact_locator, "preparation.manifest.artifact")
        _path(self.schema_projection_locator, "preparation.manifest.artifact.generated_schema")
        return self


def _path(locator: PreparationSourceLocatorV1, expected: str) -> None:
    if locator.field_path != expected:
        raise ValueError(f"source role requires {expected}")


class ExecutionPreparationBindingsV1(RecoveryDTO):
    schema_id: Literal["chiplog.execution.preparation-bindings.v1"] = PREPARATION_BINDINGS_SCHEMA
    kind: Literal["EXECUTION_PREPARATION_BINDINGS_V1"] = "EXECUTION_PREPARATION_BINDINGS_V1"
    tenant_id: Identity
    database_id: Identity
    run_id: Identity
    turn_id: Identity
    attempt_ordinal: Literal[0]
    profile: Literal["chiplog.execution.h1-cli-hermetic-source-profile.v1"] = H1_SOURCE_PROFILE
    preparation_command_ref: ExactHead
    prepared_run_ref: ExactHead
    authority_scope_ref: ExactHead
    admitted_input_locator: PreparationSourceLocatorV1
    ingress_ref: ExactHead
    workspace_read: PreparationWorkspaceReadV1
    visibility_bindings: tuple[PreparationVisibilityBindingV1, ...]
    prompt_binding: PreparationPromptBindingV1
    generated_schema_binding: PreparationGeneratedSchemaBindingV1
    ordered_tool_schema_refs: tuple[ExactHead, ...]
    registry_ref: ExactHead
    fan_out_bound_ref: ExactHead
    budget_policy_locator: PreparationSourceLocatorV1
    origin_recipient_locator: PreparationSourceLocatorV1
    endpoint_ref: ExactHead
    credential_binding_ref: ExactHead

    @model_validator(mode="after")
    def closed_profile(self) -> Self:
        _path(self.admitted_input_locator, "initialization.admitted")
        _path(self.budget_policy_locator, "run.policy")
        _path(self.origin_recipient_locator, "initialization.admitted.origin.recipient")
        if tuple(x.surface for x in self.visibility_bindings) != (
            "workspace",
            "context",
            "prompt",
            "schema",
        ) or tuple(x.member_index for x in self.visibility_bindings) != (0, 1, 2, 3):
            raise ValueError("H1 requires ordered workspace/context/prompt/schema members")
        if len({x.record_id for x in self.visibility_bindings}) != 4:
            raise ValueError("duplicate visibility record identity")
        prompt, schema = self.visibility_bindings[2:]
        if not (
            prompt.revision_source
            == prompt.provenance_source
            == self.prompt_binding.rendered_text_locator
            and schema.revision_source
            == schema.provenance_source
            == self.prompt_binding.artifact_locator
            == self.generated_schema_binding.artifact_locator
            and prompt.label_source == schema.label_source
        ):
            raise ValueError("prompt/schema source relationships differ")
        if not (
            self.prompt_binding.artifact_locator.anchor
            == self.prompt_binding.rendered_text_locator.anchor
            == self.generated_schema_binding.schema_projection_locator.anchor
        ):
            raise ValueError("artifact projections must retain the same selected anchor")
        context = self.visibility_bindings[1]
        if context.revision_source != context.provenance_source:
            raise ValueError("context revision/provenance must retain the same started Run")
        workspace = self.visibility_bindings[0]
        if any(
            x not in self.workspace_read.ordered_source_locators
            for x in (
                workspace.revision_source,
                workspace.provenance_source,
                workspace.label_source,
            )
        ):
            raise ValueError("workspace visibility source missing from retained read")
        anchors: dict[tuple[str, str], PreparationSourceAnchorV1] = {}
        projections: dict[tuple[str, str, str], PreparationSourceLocatorV1] = {}
        for locator in self.locators():
            anchor = locator.anchor
            if (anchor.tenant_id, anchor.database_id) != (self.tenant_id, self.database_id):
                raise ValueError("source tenant/database differs")
            key = (anchor.owner_id, anchor.record.identity)
            if key in anchors and anchors[key] != anchor:
                raise ValueError("conflicting physical source identity")
            anchors[key] = anchor
            projection = (*key, locator.field_path)
            if projection in projections and projections[projection] != locator:
                raise ValueError("conflicting source projection")
            projections[projection] = locator
        if len({x.identity for x in self.ordered_tool_schema_refs}) != len(
            self.ordered_tool_schema_refs
        ):
            raise ValueError("duplicate tool schema identity")
        return self

    def locators(self) -> tuple[PreparationSourceLocatorV1, ...]:
        return (
            self.admitted_input_locator,
            self.workspace_read.issued_read_locator,
            *self.workspace_read.ordered_source_locators,
            *(
                x
                for row in self.visibility_bindings
                for x in (row.revision_source, row.provenance_source, row.label_source)
            ),
            self.prompt_binding.artifact_locator,
            self.prompt_binding.rendered_text_locator,
            self.generated_schema_binding.artifact_locator,
            self.generated_schema_binding.schema_projection_locator,
            self.budget_policy_locator,
            self.origin_recipient_locator,
        )


def generated_schema_projection_bytes(generator_version: str, response_schema_json: str) -> bytes:
    """A derivation domain, deliberately distinct from the original artifact digest."""
    return json.dumps(
        {
            "schema_id": "chiplog.execution.generated-schema-binding.v1",
            "generator_version": generator_version,
            "response_schema_json": response_schema_json,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()


def decode_execution_preparation_bindings(raw: bytes) -> ExecutionPreparationBindingsV1:
    value = ExecutionPreparationBindingsV1.model_validate_json(raw)
    if value.canonical_bytes() != raw:
        raise ValueError("noncanonical preparation bindings")
    return value


def validate_preparation_manifest_sources(
    bindings: ExecutionPreparationBindingsV1,
    members: tuple[VisibilityMember, ...],
    artifact: ExecutionPromptArtifact | ExecutionPromptArtifactV3,
) -> None:
    """Consistency only; caller must independently resolve every selected source.

    Revalidate copies, compare original heads, and preserve artifact/rendered/schema
    hash domains. This function cannot authenticate a source or authorize execution.
    """
    value = decode_execution_preparation_bindings(bindings.canonical_bytes())
    if len(members) != len(value.visibility_bindings):
        raise ValueError("missing or additional visibility member")
    for member, binding in zip(members, value.visibility_bindings, strict=True):
        if (member.record_id, member.surface) != (binding.record_id, binding.surface):
            raise ValueError("visibility member order/identity differs")
        if (member.revision_head, member.provenance_head, member.label_head) != (
            binding.revision_source.original_head,
            binding.provenance_source.original_head,
            binding.label_source.original_head,
        ):
            raise ValueError("original visibility heads differ")
    prompt = value.prompt_binding
    schema = value.generated_schema_binding
    if (
        prompt.artifact_locator.resolved_ref.fingerprint != artifact.digest()
        or prompt.artifact_locator.original_head != artifact.digest()
        or prompt.rendered_text_locator.resolved_ref.fingerprint != artifact.content_hash
        or prompt.rendered_text_locator.original_head != artifact.content_hash
        or hashlib.sha256(artifact.rendered.encode()).hexdigest() != artifact.content_hash
        or schema.generator_version != artifact.generator_version
        or schema.schema_projection_locator.resolved_ref.fingerprint
        != hashlib.sha256(
            generated_schema_projection_bytes(
                artifact.generator_version, artifact.response_schema_json
            )
        ).hexdigest()
        or members[2].content != artifact.rendered
        or members[3].content != artifact.response_schema_json
    ):
        raise ValueError("artifact/rendered/schema derivation differs")
