"""Contract-only fixtures do not represent issued or selected owner sources."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from chiplog.capabilities.agent_loop import execution_preparation_bindings_contracts as c
from chiplog.capabilities.agent_loop.contracts import DisclosureLabel, ToolSpec, VisibilityMember
from chiplog.capabilities.agent_loop.delivery_contracts import ExactHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionPromptArtifact


def ref(name: str, raw: bytes = b"fixture") -> ExactHead:
    return ExactHead(identity=name, head=name, fingerprint=hashlib.sha256(raw).hexdigest())


def locator(path: c.SourceFieldPath, raw: bytes = b"fixture") -> c.PreparationSourceLocatorV1:
    return c.PreparationSourceLocatorV1(
        anchor=c.PreparationSourceAnchorV1(
            tenant_id="tenant",
            database_id="database",
            owner_id="fixture-owner",
            schema_id="fixture-schema",
            record=ref("physical"),
            selected_operation_id="operation",
            selected_decision=ref("decision", b"different decision bytes"),
            commit_sequence=1,
        ),
        field_path=path,
        original_head=hashlib.sha256(raw).hexdigest(),
        resolved_ref=ref(path, raw),
    )


def fixture() -> tuple[
    c.ExecutionPreparationBindingsV1, tuple[VisibilityMember, ...], ExecutionPromptArtifact
]:
    artifact = ExecutionPromptArtifact(
        content_hash=hashlib.sha256(b"rendered").hexdigest(),
        library_version="fixture",
        tools=(ToolSpec(name="propose_planning", schema_id="chiplog.propose-planning.v1"),),
        response_schema_json='{"type":"object"}',
        rendered="rendered",
    )
    artifact_locator = locator("preparation.manifest.artifact", artifact.canonical_bytes())
    rendered = locator("preparation.manifest.artifact.rendered", b"rendered")
    policy = locator("scope.disclosure_policy")
    sources = (
        (
            locator("workspace.source.revision"),
            locator("workspace.source.provenance"),
            locator("workspace.source.label"),
        ),
        (
            locator("context.started_run"),
            locator("context.started_run"),
            locator("context.contour"),
        ),
        (rendered, rendered, policy),
        (artifact_locator, artifact_locator, policy),
    )
    surfaces = ("workspace", "context", "prompt", "schema")
    bindings = tuple(
        c.PreparationVisibilityBindingV1.model_validate(
            {
                "member_index": index,
                "record_id": surface,
                "surface": surface,
                "revision_source": source[0],
                "provenance_source": source[1],
                "label_source": source[2],
            }
        )
        for index, (surface, source) in enumerate(zip(surfaces, sources, strict=True))
    )
    members = tuple(
        VisibilityMember(
            record_id=row.record_id,
            surface=row.surface,
            producer="fixture",
            revision_head=row.revision_source.original_head,
            provenance_head=row.provenance_source.original_head,
            label_head=row.label_source.original_head,
            label=DisclosureLabel(value="UNRESTRICTED", allowed_endpoints=()),
            content=artifact.rendered
            if index == 2
            else artifact.response_schema_json
            if index == 3
            else "fixture",
        )
        for index, row in enumerate(bindings)
    )
    value = c.ExecutionPreparationBindingsV1(
        tenant_id="tenant",
        database_id="database",
        run_id="run",
        turn_id="turn",
        attempt_ordinal=0,
        preparation_command_ref=ref("prepare"),
        prepared_run_ref=ref("prepared-run"),
        authority_scope_ref=ref("scope"),
        admitted_input_locator=locator("initialization.admitted"),
        ingress_ref=ref("ingress"),
        workspace_read=c.PreparationWorkspaceReadV1(
            issued_read_locator=locator("workspace.issued_read"),
            ordered_source_locators=sources[0],
        ),
        visibility_bindings=bindings,
        prompt_binding=c.PreparationPromptBindingV1(
            artifact_locator=artifact_locator,
            rendered_text_locator=rendered,
            prompt_member_index=2,
        ),
        generated_schema_binding=c.PreparationGeneratedSchemaBindingV1(
            artifact_locator=artifact_locator,
            schema_member_index=3,
            generator_version=artifact.generator_version,
            schema_projection_locator=locator(
                "preparation.manifest.artifact.generated_schema",
                c.generated_schema_projection_bytes(
                    artifact.generator_version, artifact.response_schema_json
                ),
            ),
        ),
        ordered_tool_schema_refs=(ref("tool-a"), ref("tool-b")),
        registry_ref=ref("registry"),
        fan_out_bound_ref=ref("fanout"),
        budget_policy_locator=locator("run.policy"),
        origin_recipient_locator=locator("initialization.admitted.origin.recipient"),
        endpoint_ref=ref("endpoint"),
        credential_binding_ref=ref("credential"),
    )
    return value, members, artifact


def test_canonical_roundtrip_and_distinct_domains() -> None:
    value, members, artifact = fixture()
    assert c.decode_execution_preparation_bindings(value.canonical_bytes()) == value
    c.validate_preparation_manifest_sources(value, members, artifact)
    assert value.prompt_binding.artifact_locator.resolved_ref.fingerprint != artifact.content_hash
    assert value.admitted_input_locator.anchor.record.fingerprint != (
        value.admitted_input_locator.anchor.selected_decision.fingerprint
    )
    reversed_tools = value.model_copy(
        update={"ordered_tool_schema_refs": tuple(reversed(value.ordered_tool_schema_refs))}
    )
    assert reversed_tools.canonical_bytes() != value.canonical_bytes()
    with pytest.raises(ValueError, match="noncanonical"):
        c.decode_execution_preparation_bindings(
            json.dumps(json.loads(value.canonical_bytes())).encode()
        )


@pytest.mark.parametrize(
    "field",
    [
        "workspace_read",
        "origin_recipient_locator",
        "ingress_ref",
        "ordered_tool_schema_refs",
        "budget_policy_locator",
    ],
)
def test_missing_required_source_rejected(field: str) -> None:
    value, _, _ = fixture()
    raw = value.model_dump(mode="json")
    del raw[field]
    with pytest.raises(ValidationError):
        c.ExecutionPreparationBindingsV1.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(
    "change", ["order", "role", "workspace", "tenant", "profile", "path", "attempt", "conflict"]
)
def test_closed_profile_rejects_substitution(change: str) -> None:
    value, _, _ = fixture()
    raw = value.model_dump(mode="json")
    if change == "order":
        raw["visibility_bindings"].reverse()
    elif change == "role":
        raw["prompt_binding"]["rendered_text_locator"] = raw["prompt_binding"]["artifact_locator"]
    elif change == "workspace":
        raw["workspace_read"]["ordered_source_locators"] = []
    elif change == "tenant":
        raw["tenant_id"] = "other"
    elif change == "profile":
        raw["profile"] = "unknown"
    elif change == "path":
        raw["budget_policy_locator"]["field_path"] = "run.arbitrary"
    elif change == "attempt":
        raw["attempt_ordinal"] = 1
    else:
        raw["budget_policy_locator"]["anchor"]["record"]["fingerprint"] = "0" * 64
    with pytest.raises(ValidationError):
        c.ExecutionPreparationBindingsV1.model_validate_json(json.dumps(raw))


def test_manifest_original_heads_order_and_schema_roles() -> None:
    value, members, artifact = fixture()
    for bad in (
        members[:-1],
        tuple(reversed(members)),
        (members[0].model_copy(update={"revision_head": "invented"}), *members[1:]),
    ):
        with pytest.raises(ValueError):
            c.validate_preparation_manifest_sources(value, bad, artifact)
    wrong = value.generated_schema_binding.schema_projection_locator.model_copy(
        update={"resolved_ref": value.prompt_binding.artifact_locator.resolved_ref}
    )
    copied = value.model_copy(
        update={
            "generated_schema_binding": value.generated_schema_binding.model_copy(
                update={"schema_projection_locator": wrong}
            )
        }
    )
    with pytest.raises(ValueError, match="derivation"):
        c.validate_preparation_manifest_sources(copied, members, artifact)
