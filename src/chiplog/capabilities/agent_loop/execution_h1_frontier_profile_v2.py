"""Pure carrier-to-member extraction for the H1 zero-call frontier V2.

The values here are consistency commitments only.  In particular,
``H1VerifiedWorkspaceClosure`` is an inert carrier for bytes produced by the
broker-private verifier; constructing it does not authenticate a workspace.
The composition broker must authenticate the original issuance, source closure,
and negative inventories before it uses this output to select a cut.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from .call_acceptance_contracts import SealedResponseRecord
from .execution_contracts import ExecutionRunRecord
from .execution_initialization_contracts import SelectedAdmittedRunInput
from .recovery_contracts import Absent, NotApplicable, Present
from .recovery_frontier_contracts import FrontierMember

__all__ = [
    "H1FrontierProfileV2Error",
    "H1VerifiedWorkspaceClosure",
    "H1WorkspaceClosure",
    "derive_h1_frontier_profile_v2_members",
]


class H1FrontierProfileV2Error(ValueError):
    """A closed V2 carrier profile could not be derived."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class H1WorkspaceClosure(Protocol):
    """Structural input supplied by a broker-private closure verifier."""

    @property
    def proposal_context_bytes(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class H1VerifiedWorkspaceClosure:
    """Exact original ProposalContext UTF-8 bytes from the broker verifier.

    This intentionally carries no capability or verification flag.  Its type
    keeps the agent-loop helper independent from composition's private reader.
    """

    proposal_context_bytes: bytes


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _binding(family: str, subject: str, value: object) -> Present:
    raw = _json(
        {
            "domain": "chiplog.execution.h1-frontier-binding.v2",
            "family": family,
            "subject": subject,
            "value": value,
        }
    )
    digest = _hash(raw)
    return Present(head="h1-binding:" + digest, fingerprint=digest)


def _key(role: str, *parts: str) -> str:
    return role + ":" + _hash(_json(list(parts)))


def _member(family: str, subject: str, branch: str, heads: tuple[object, ...]) -> FrontierMember:
    member = FrontierMember(
        family=family,
        subject=subject,
        branch=branch,
        ordered_heads=heads,  # type: ignore[arg-type]
        fingerprint="0" * 64,
    )
    # Kept local to avoid a module cycle with the DTO validator.
    payload = member.model_dump(mode="json")
    payload.pop("fingerprint")
    fingerprint = _hash(
        _json({"domain": "chiplog.execution.first-path-frontier-member.v2", "content": payload})
    )
    return member.model_copy(update={"fingerprint": fingerprint})


def _native(value: object, head: str) -> Present:
    canonical = value.canonical_bytes()  # type: ignore[attr-defined]
    return Present(head=head, fingerprint=_hash(canonical))


def _workspace_error() -> H1FrontierProfileV2Error:
    return H1FrontierProfileV2Error("H1_WORKSPACE_ORIGINAL_READ_UNPROVEN")


def _context(value: dict[str, Any], role: str) -> dict[str, Any]:
    context = value.get("context")
    if not isinstance(context, dict):
        raise _workspace_error()
    required = {
        "tenant_id",
        "database_instance_id",
        "principal_id",
        "principal_contour_head",
        "channel_id",
        "endpoint_binding_head",
        "policy_head",
        "deletion_fence_head",
    }
    if not required <= set(context):
        raise _workspace_error()
    return context


def derive_h1_frontier_profile_v2_members(
    *,
    final_run: ExecutionRunRecord,
    selected_admitted_input: SelectedAdmittedRunInput,
    seal: SealedResponseRecord,
    verified_workspace: H1WorkspaceClosure | None,
) -> tuple[FrontierMember, ...]:
    """Derive every V2 member from native selected carriers and exact closure bytes.

    The function fail-closes on any carrier shape it does not recognize.  It does
    not enumerate physical streams and therefore cannot establish any EMPTY
    predicate for a broker; its EMPTY members describe only this closed native
    carrier profile.
    """

    if verified_workspace is None:
        raise _workspace_error()
    try:
        context_value = json.loads(verified_workspace.proposal_context_bytes)
    except UnicodeDecodeError, json.JSONDecodeError:
        raise _workspace_error() from None
    if (
        not isinstance(context_value, dict)
        or context_value.get("kind") != "WORKSPACE_EVIDENCE_ONLY"
    ):
        raise _workspace_error()

    if not (
        final_run.state == "ACTIVE"
        and final_run.event == "ModelCompletionPrepared"
        and final_run.root_binding == "NOT_APPLICABLE"
        and final_run.delivery_acceptance is None
        and not final_run.original_obligations
        and not final_run.no_retry_references
        and len(final_run.turns) == 1
        and selected_admitted_input.source_class == "CLI"
        and selected_admitted_input.normalized_prompt == final_run.prompt
        and selected_admitted_input.principal_id == final_run.principal
        and selected_admitted_input.contour_head == final_run.contour_head
    ):
        raise H1FrontierProfileV2Error("H1_FRONTIER_PROFILE_UNSUPPORTED")
    turn = final_run.turns[0]
    if not (
        turn.state == "RESPONSE_AVAILABLE"
        and turn.initialized_calls == ()
        and len(turn.attempts) == 1
    ):
        raise H1FrontierProfileV2Error("H1_FRONTIER_PROFILE_UNSUPPORTED")
    attempt = turn.attempts[0]
    manifest = attempt.manifest
    if not (
        attempt.state == "RESPONSE_CAPTURED"
        and attempt.generation == 0
        and attempt.provider_contract == "hermetic-model.v1"
        and attempt.recipient == "hermetic-model"
        and attempt.live_model is None
        and seal.complete_ordered_initialized == ()
        and (seal.tenant_id, seal.original_run_id, seal.original_turn_id)
        == (final_run.tenant, final_run.run_id, turn.turn_id)
    ):
        raise H1FrontierProfileV2Error("H1_FRONTIER_PROFILE_UNSUPPORTED")
    workspace_members = tuple(
        item
        for item in manifest.members
        if item.producer == "projections" and item.surface == "workspace"
    )
    if (
        len(workspace_members) != 1
        or workspace_members[0].content.encode() != verified_workspace.proposal_context_bytes
    ):
        raise _workspace_error()
    if (
        manifest.tenant,
        manifest.principal,
        manifest.run_id,
        manifest.turn_id,
        manifest.generation,
    ) != (
        final_run.tenant,
        final_run.principal,
        final_run.run_id,
        turn.turn_id,
        0,
    ) or manifest.contour_head != final_run.contour_head:
        raise H1FrontierProfileV2Error("H1_FRONTIER_PROFILE_UNSUPPORTED")

    batch = context_value.get("batch")
    if not isinstance(batch, dict) or batch.get("history_complete") is not True:
        raise _workspace_error()
    contexts = {
        "batch": _context(batch, "batch"),
        "external": batch.get("external_context"),
        "history": _context(batch.get("history", {}), "history"),
        "planning": _context(batch.get("planning", {}), "planning"),
        "journal": _context(batch.get("journal", {}), "journal"),
        "calendar": _context(batch.get("calendar", {}), "calendar"),
    }
    typed_contexts: dict[str, dict[str, Any]] = {}
    for role, item in contexts.items():
        if not isinstance(item, dict):
            raise _workspace_error()
        typed_contexts[role] = item
    for item in typed_contexts.values():
        if (item["tenant_id"], item["principal_id"], item["database_instance_id"]) != (
            final_run.tenant,
            final_run.principal,
            selected_admitted_input.database_id,
        ):
            raise _workspace_error()

    run_head = _native(final_run, final_run.head)
    turn_head = _native(turn, turn.head)
    artifact = manifest.artifact
    members: list[FrontierMember] = [
        _member("RUN", final_run.run_id, "PRESENT", (run_head,)),
        _member("TURN", turn.turn_id, "PRESENT", (turn_head,)),
        _member(
            "SEALED_RESPONSE",
            seal.response_seal_id,
            "PRESENT",
            (_native(seal, "record:" + _hash(seal.canonical_bytes())),),
        ),
        _member("CALL", "CALL", "EMPTY", (Absent(),)),
        _member("EFFECT", "EFFECT", "EMPTY", (Absent(),)),
        _member("DELIVERY", "DELIVERY", "EMPTY", (Absent(),)),
        _member("MANDATE", "MANDATE", "NOT_APPLICABLE", (NotApplicable(),)),
        _member("EXECUTION_LINEAGE", "EXECUTION_LINEAGE", "NOT_APPLICABLE", (NotApplicable(),)),
        _member("OBLIGATION", "OBLIGATION", "EMPTY", (Absent(),)),
        _member("SEMANTIC_REDUCTION", "SEMANTIC_REDUCTION", "EMPTY", (Absent(),)),
    ]
    workspace = workspace_members[0]
    members.append(
        _member(
            "EVIDENCE",
            "workspace-batch",
            "PRESENT",
            (
                _binding(
                    "EVIDENCE",
                    "workspace-batch",
                    {"content": workspace.content, "context": context_value},
                ),
            ),
        )
    )
    for slot in ("history", "planning", "journal", "calendar"):
        result = batch[slot]
        if not isinstance(result, dict) or not isinstance(result.get("rows"), list):
            raise _workspace_error()
        for row in result["rows"]:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("row_id"), str)
                or not isinstance(row.get("row_version"), str)
            ):
                raise _workspace_error()
            subject = _key("workspace-row", slot, row["row_id"], row["row_version"])
            members.append(
                _member(
                    "EVIDENCE",
                    subject,
                    "PRESENT",
                    (_binding("EVIDENCE", subject, {"slot": slot, "row": row}),),
                )
            )
            envelope = row.get("envelope")
            if not isinstance(envelope, dict) or not {
                "policy_head",
                "deletion_fence_head",
            } <= set(envelope):
                raise _workspace_error()
            policy_subject = _key("workspace-row-policy", slot, row["row_id"], row["row_version"])
            members.append(
                _member(
                    "POLICY",
                    policy_subject,
                    "PRESENT",
                    (
                        _binding(
                            "POLICY",
                            policy_subject,
                            {
                                "policy_head": envelope["policy_head"],
                                "deletion_fence_head": envelope["deletion_fence_head"],
                            },
                        ),
                    ),
                )
            )
    members.extend(
        [
            _member(
                "AUTHORITY",
                "run-contour",
                "PRESENT",
                (
                    _binding(
                        "AUTHORITY",
                        "run-contour",
                        {
                            "tenant": final_run.tenant,
                            "principal": final_run.principal,
                            "contour_head": final_run.contour_head,
                        },
                    ),
                ),
            ),
            _member(
                "AUTHORITY",
                "admitted-authentication",
                "PRESENT",
                (selected_admitted_input.source_authentication.revision,),
            ),
            _member(
                "AUTHORITY",
                "origin-ingress",
                "PRESENT",
                (
                    Present(
                        head=final_run.origin.ingress_binding.head,
                        fingerprint=final_run.origin.ingress_binding.fingerprint,
                    ),
                ),
            ),
            _member(
                "POLICY",
                "run-budget",
                "PRESENT",
                (
                    _binding(
                        "POLICY",
                        "run-budget",
                        {
                            "policy_head": final_run.policy_head,
                            "policy": final_run.policy.model_dump(mode="json"),
                        },
                    ),
                ),
            ),
            _member(
                "POLICY",
                "workspace-binding",
                "PRESENT",
                (
                    _binding(
                        "POLICY",
                        "workspace-binding",
                        {"policy_binding_digest": batch.get("policy_binding_digest")},
                    ),
                ),
            ),
            _member(
                "PROMPT",
                "input",
                "PRESENT",
                (
                    _binding(
                        "PROMPT",
                        "input",
                        {
                            "normalized_prompt": selected_admitted_input.normalized_prompt,
                            "normalization": selected_admitted_input.normalization.model_dump(
                                mode="json"
                            ),
                            "run_prompt": final_run.prompt,
                        },
                    ),
                ),
            ),
            _member(
                "PROMPT",
                _key("artifact", artifact.prompt_id, artifact.version),
                "PRESENT",
                (Present(head="record:" + artifact.digest(), fingerprint=artifact.digest()),),
            ),
            _member(
                "TOOL_SCHEMA",
                "generated-response-schema",
                "PRESENT",
                (
                    _binding(
                        "TOOL_SCHEMA",
                        "generated-response-schema",
                        {
                            "generator_version": artifact.generator_version,
                            "response_schema_json": artifact.response_schema_json,
                            "ordered_tools": [
                                tool.model_dump(mode="json") for tool in artifact.tools
                            ],
                        },
                    ),
                ),
            ),
            _member(
                "RECIPIENT",
                "model",
                "PRESENT",
                (
                    _binding(
                        "RECIPIENT",
                        "model",
                        {
                            "provider_contract": attempt.provider_contract,
                            "recipient": attempt.recipient,
                            "live_model": None,
                        },
                    ),
                ),
            ),
            _member(
                "SEMANTIC_BINDING",
                "execution-input",
                "PRESENT",
                (
                    _binding(
                        "SEMANTIC_BINDING",
                        "execution-input",
                        {
                            "prompt": final_run.prompt,
                            "origin": final_run.origin.model_dump(mode="json"),
                            "policy_head": final_run.policy_head,
                            "contour_head": final_run.contour_head,
                            "worker_session": final_run.worker_session,
                        },
                    ),
                ),
            ),
            _member(
                "SEMANTIC_BINDING",
                "visibility",
                "PRESENT",
                (_binding("SEMANTIC_BINDING", "visibility", manifest.model_dump(mode="json")),),
            ),
            _member(
                "SEMANTIC_BINDING",
                "workspace-proposal-context",
                "PRESENT",
                (_binding("SEMANTIC_BINDING", "workspace-proposal-context", context_value),),
            ),
        ]
    )
    for role, context in typed_contexts.items():
        members.extend(
            [
                _member(
                    "AUTHORITY",
                    "workspace-context:" + role,
                    "PRESENT",
                    (_binding("AUTHORITY", "workspace-context:" + role, context),),
                ),
                _member(
                    "POLICY",
                    "workspace-policy:" + role,
                    "PRESENT",
                    (
                        _binding(
                            "POLICY",
                            "workspace-policy:" + role,
                            {
                                "policy_head": context["policy_head"],
                                "deletion_fence_head": context["deletion_fence_head"],
                            },
                        ),
                    ),
                ),
                _member(
                    "RECIPIENT",
                    "workspace-endpoint:" + role,
                    "PRESENT",
                    (
                        _binding(
                            "RECIPIENT",
                            "workspace-endpoint:" + role,
                            {
                                "channel_id": context["channel_id"],
                                "endpoint_binding_head": context["endpoint_binding_head"],
                            },
                        ),
                    ),
                ),
            ]
        )
    for ordinal, tool in enumerate(artifact.tools):
        subject = _key("tool", str(ordinal), tool.name, tool.version, tool.schema_id)
        members.append(
            _member(
                "TOOL_SCHEMA",
                subject,
                "PRESENT",
                (Present(head="record:" + tool.digest(), fingerprint=tool.digest()),),
            )
        )
    origin = final_run.origin.recipient
    members.append(
        _member(
            "RECIPIENT",
            "output-origin",
            "PRESENT",
            (
                Present(head=origin.endpoint.head, fingerprint=origin.endpoint.fingerprint),
                Present(
                    head=origin.credential_binding.head,
                    fingerprint=origin.credential_binding.fingerprint,
                ),
                _binding(
                    "RECIPIENT", "output-origin", final_run.origin.recipient.model_dump(mode="json")
                ),
            ),
        )
    )
    for visibility in manifest.members:
        subject = _key("visibility-member", visibility.record_id)
        members.append(
            _member(
                "SEMANTIC_BINDING",
                subject,
                "PRESENT",
                (_binding("SEMANTIC_BINDING", subject, visibility.model_dump(mode="json")),),
            )
        )
    family_order = (
        "RUN",
        "TURN",
        "SEALED_RESPONSE",
        "CALL",
        "EFFECT",
        "EVIDENCE",
        "DELIVERY",
        "AUTHORITY",
        "MANDATE",
        "POLICY",
        "PROMPT",
        "TOOL_SCHEMA",
        "RECIPIENT",
        "SEMANTIC_BINDING",
        "EXECUTION_LINEAGE",
        "OBLIGATION",
        "SEMANTIC_REDUCTION",
    )
    if len({(item.family, item.subject) for item in members}) != len(members):
        raise H1FrontierProfileV2Error("H1_FRONTIER_DUPLICATE_SUBJECT")
    return tuple(
        sorted(members, key=lambda item: (family_order.index(item.family), item.subject.encode()))
    )
