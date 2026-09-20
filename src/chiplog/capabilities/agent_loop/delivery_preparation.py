"""Pure delivery proposals; broker reproduces every observation before publication.

These owner-local records do not authenticate a query or replace Run acceptance.
The canonical broker must commit loop/history and effects members atomically.
"""

from __future__ import annotations

import base64
import hashlib
import json
from types import GenericAlias
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, create_model

from .contracts import (
    Continue,
    DeliveryAcceptanceReference,
    DisclosureLabel,
    LoopRejected,
    PromptArtifact,
    RunRecord,
    ToolCall,
    ToolSpec,
)
from .delivery_contracts import (
    AcceptedDelivery,
    DeliveryDTO,
    DeliveryManifest,
    Digest,
    EndpointSelection,
    ExactHead,
    Identity,
    OriginSelection,
    ProviderRecipient,
)

Frontier = Annotated[int, Field(ge=0, le=2**64 - 1)]


class QueryBase(DeliveryDTO):
    tenant: Identity
    subject: ExactHead
    correlation: ExactHead
    query: ExactHead
    records: tuple[ExactHead, ...] = Field(min_length=1)
    source_frontier: Frontier


class PlanQuery(QueryBase):
    kind: Literal["PLAN"] = "PLAN"
    state: Literal["ACTIVE", "COMPLETED", "CANCELLED"]
    planning_head: ExactHead


class FactQuery(QueryBase):
    kind: Literal["FACT"] = "FACT"
    state: Literal["OWNER_ASSERTED", "RETRACTED"]
    claim_head: ExactHead
    attribution: Identity


class CandidateQuery(QueryBase):
    kind: Literal["CANDIDATE"] = "CANDIDATE"
    candidate_version: ExactHead
    disposition: ExactHead
    state: Literal["PENDING", "ACCEPTED", "REJECTED"]
    classification: Literal["NONTERMINAL", "TERMINAL"]
    attribution: Identity


class ProviderQuery(QueryBase):
    kind: Literal["PROVIDER"] = "PROVIDER"
    evidence_kind: Literal["AUTHENTICATED_RECEIPT", "COMPLETED_RECONCILIATION"]
    evidence: ExactHead
    outcome: Literal["CONFIRMED_SUCCESS", "CONFIRMED_FAILURE", "UNKNOWN"]


CurrentQuery = Annotated[
    PlanQuery | FactQuery | CandidateQuery | ProviderQuery, Field(discriminator="kind")
]
AssertionCode = Literal[
    "PLAN_ACTIVE",
    "PLAN_COMPLETED",
    "PLAN_CANCELLED",
    "FACT_OWNER_ASSERTED",
    "FACT_RETRACTED",
    "CANDIDATE_PENDING",
    "CANDIDATE_ACCEPTED",
    "CANDIDATE_REJECTED",
    "PROVIDER_CONFIRMED_SUCCESS",
    "PROVIDER_CONFIRMED_FAILURE",
    "PROVIDER_UNKNOWN",
]


class Commentary(DeliveryDTO):
    kind: Literal["NonAuthoritativeText"] = "NonAuthoritativeText"
    text: str = Field(min_length=1)


class Assertion(DeliveryDTO):
    kind: Literal["DeliveryAssertion"] = "DeliveryAssertion"
    assertion_kind: Literal["PLAN", "FACT", "CANDIDATE", "PROVIDER"]
    subject: ExactHead
    correlation: ExactHead
    query: ExactHead
    records: tuple[ExactHead, ...] = Field(min_length=1)
    source_frontier: Frontier
    candidate_disposition: ExactHead | None = None
    codes: tuple[AssertionCode, ...] = Field(min_length=1)
    renderer_version: Literal["chiplog.assertion-renderer.v1"] = "chiplog.assertion-renderer.v1"


Payload = Annotated[Commentary | Assertion, Field(discriminator="kind")]


class ProposedDelivery(DeliveryDTO):
    selection: EndpointSelection | None = None
    payload: tuple[Payload, ...] = Field(min_length=1)


class DeliveryCompletion(DeliveryDTO):
    kind: Literal["Complete"] = "Complete"
    tenant: Identity
    run_id: Identity
    turn_id: Identity
    deliveries: tuple[ProposedDelivery, ...] = Field(min_length=1)


class HistoricalEnvelope(DeliveryDTO):
    """Exact original revision; later source narrowing cannot replace this label."""

    content: ExactHead
    visibility: ExactHead
    provenance: ExactHead
    disclosure: ExactHead
    label: DisclosureLabel
    narrowing: tuple[ExactHead, ...]


class DeliveryObservation(DeliveryDTO):
    tenant: Identity
    run: ExactHead
    turn_id: Identity
    captured_response: bytes
    source_frontier: Frontier
    origin: OriginSelection
    recipients: tuple[ProviderRecipient, ...] = Field(min_length=1)
    history: tuple[HistoricalEnvelope, ...] = Field(min_length=1)
    queries: tuple[CurrentQuery, ...]
    policy: ExactHead
    worker_fence: ExactHead


class DeliveryAcceptanceProposal(DeliveryDTO):
    """All-or-none inert proposal, not a committed Run or ConversationHistory row."""

    source_command_digest: Digest
    source_snapshot_digest: Digest
    acceptance: ExactHead
    manifest: DeliveryManifest
    history_rendered_bytes: tuple[bytes, ...]
    proposal_digest: Digest


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise LoopRejected(reason)


def _render_assertion(assertion: Assertion, snapshot: DeliveryObservation) -> bytes:
    matches = [query for query in snapshot.queries if query.query == assertion.query]
    _require(len(matches) == 1, "assertion has no unique current admissible query")
    query = matches[0]
    _require(
        query.tenant == snapshot.tenant
        and query.subject == assertion.subject
        and query.correlation == assertion.correlation
        and query.kind == assertion.assertion_kind
        and query.records == assertion.records
        and query.source_frontier == assertion.source_frontier == snapshot.source_frontier,
        "assertion source tenant/subject/correlation/frontier differs",
    )
    _require(len(set(assertion.codes)) == len(assertion.codes), "duplicate assertion code")
    _require(len(set(query.records)) == len(query.records), "duplicate assertion evidence")
    if isinstance(query, PlanQuery):
        _require(query.planning_head in query.records, "planning head absent from query evidence")
        code = "PLAN_" + query.state
        text = f"Plan state: {query.state}."
    elif isinstance(query, FactQuery):
        _require(query.claim_head in query.records, "claim head absent from query evidence")
        code = "FACT_" + query.state
        text = (
            f"Owner-authored fact claim: {query.state}; "
            f"attributed to {_json(query.attribution).decode()}."
        )
    elif isinstance(query, CandidateQuery):
        _require(
            query.disposition == assertion.candidate_disposition
            and query.disposition in query.records
            and query.candidate_version in query.records
            and query.classification == ("NONTERMINAL" if query.state == "PENDING" else "TERMINAL"),
            "candidate version/disposition or terminal classification differs",
        )
        code = "CANDIDATE_" + query.state
        text = (
            f"Candidate status: {query.state}; attributed to {_json(query.attribution).decode()}."
        )
    else:
        _require(query.evidence in query.records, "provider receipt/reconciliation absent")
        code = "PROVIDER_" + query.outcome
        text = f"Provider outcome: {query.outcome}; evidence: {query.evidence_kind}."
    _require(
        isinstance(query, CandidateQuery) or assertion.candidate_disposition is None,
        "noncandidate assertion carries candidate disposition",
    )
    _require(assertion.codes == (code,), "assertion code not entailed by typed current record")
    # JSON quoting makes record-sourced attribution and identity inert text fields.
    return (text + " Subject: " + _json(query.subject.identity).decode() + ".").encode()


def _recipient_key(recipient: ProviderRecipient) -> tuple[str, str, str]:
    # Endpoint aliases and differing address bytes never license a second send to
    # the same authenticated provider/account/recipient identity.
    return recipient.provider_id, recipient.account_id, recipient.recipient_id


def prepare_completion(
    completion: DeliveryCompletion, snapshot: DeliveryObservation
) -> DeliveryAcceptanceProposal:
    _require(
        completion.canonical_bytes() == snapshot.captured_response
        and completion.tenant == snapshot.tenant
        and completion.run_id == snapshot.run.identity
        and completion.turn_id == snapshot.turn_id,
        "completion differs from exact captured Run/Turn response",
    )
    _require(
        len({(entry.content, entry.visibility) for entry in snapshot.history})
        == len(snapshot.history),
        "duplicate historical revision",
    )
    _require(
        len({query.query for query in snapshot.queries}) == len(snapshot.queries),
        "duplicate current query",
    )
    _require(
        len({(query.kind, query.subject.identity) for query in snapshot.queries})
        == len(snapshot.queries),
        "rival current subject queries",
    )
    prepared: list[tuple[EndpointSelection, bytes]] = []
    recipient_keys: set[tuple[str, str, str]] = set()
    for delivery in completion.deliveries:
        selection = delivery.selection or snapshot.origin
        _require(
            not isinstance(selection, OriginSelection) or selection == snapshot.origin,
            "origin differs from authenticated ingress binding",
        )
        _require(
            sum(recipient == selection.recipient for recipient in snapshot.recipients) == 1,
            "endpoint tuple is not uniquely registered and current",
        )
        key = _recipient_key(selection.recipient)
        _require(key not in recipient_keys, "duplicate authenticated provider recipient")
        recipient_keys.add(key)
        for envelope in snapshot.history:
            label = envelope.label
            _require(
                (label.value == "UNRESTRICTED" and not label.allowed_endpoints)
                or (
                    label.value == "ENDPOINT_RESTRICTED"
                    and selection.recipient.endpoint.identity in label.allowed_endpoints
                ),
                "historical disclosure forbids endpoint",
            )
        rendered = []
        for payload in delivery.payload:
            if isinstance(payload, Commentary):
                # JSON quoting prevents embedded newlines/control bytes from
                # escaping the visibly marked commentary field in plain text.
                rendered.append(
                    b"UNVERIFIED MODEL COMMENTARY\n"
                    + _json(payload.text)
                    + b"\nEND UNVERIFIED MODEL COMMENTARY"
                )
            else:
                rendered.append(_render_assertion(payload, snapshot))
        prepared.append((selection, b"\n\n".join(rendered)))
    # No child/output digest participates in its own preimage. All accepted
    # semantic inputs are bound first; manifest/effects children reference it.
    primitive = _json(
        {
            "domain": "chiplog.delivery-acceptance-primitive.v1",
            "command": completion.model_dump(mode="json"),
            "observation": snapshot.model_dump(mode="json"),
        }
    )
    fingerprint = _sha(primitive)
    acceptance = ExactHead(
        identity=f"{completion.run_id}/{completion.turn_id}/complete",
        head="delivery-acceptance:" + fingerprint,
        fingerprint=fingerprint,
    )
    deliveries = []
    for index, (selection, rendered_bytes) in enumerate(prepared):
        manifest_digest = _sha(
            _json(
                {
                    "selection": selection.model_dump(mode="json"),
                    "render_digest": _sha(rendered_bytes),
                    "history": [entry.model_dump(mode="json") for entry in snapshot.history],
                    "policy": snapshot.policy.model_dump(mode="json"),
                }
            )
        )
        deliveries.append(
            AcceptedDelivery(
                delivery_id=f"{acceptance.identity}/delivery/{index}",
                acceptance=acceptance,
                selection=selection,
                rendered_bytes=rendered_bytes,
                render_digest=_sha(rendered_bytes),
                manifest_digest=manifest_digest,
                visibility=tuple(entry.visibility for entry in snapshot.history),
                provenance=tuple(entry.provenance for entry in snapshot.history),
                disclosure=tuple(entry.disclosure for entry in snapshot.history),
                narrowing=tuple(head for entry in snapshot.history for head in entry.narrowing),
                policy=snapshot.policy,
            )
        )
    manifest = DeliveryManifest(
        manifest_id=acceptance.identity + "/manifest",
        run_id=completion.run_id,
        turn_id=completion.turn_id,
        complete_acceptance=acceptance,
        authenticated_worker_fence=snapshot.worker_fence,
        ordered_deliveries=tuple(deliveries),
    )
    return DeliveryAcceptanceProposal(
        source_command_digest=_sha(completion.canonical_bytes()),
        source_snapshot_digest=_sha(snapshot.canonical_bytes()),
        acceptance=acceptance,
        manifest=manifest,
        history_rendered_bytes=tuple(raw for _, raw in prepared),
        proposal_digest=_sha(manifest.canonical_bytes()),
    )


def revalidate_delivery(
    accepted: DeliveryAcceptanceProposal,
    completion: DeliveryCompletion,
    current: DeliveryObservation,
) -> None:
    """Conservative exact-cut proposal validation, awaiting broker SEND_COMMITTED."""

    _require(prepare_completion(completion, current) == accepted, "accepted delivery cut changed")


DELIVERY_GENERATOR = "chiplog.turn-schema.delivery.v1"
DELIVERY_TOOLS = (
    ToolSpec(name="propose_planning", schema_id="chiplog.propose-planning.v1"),
    ToolSpec(name="propose_intent", schema_id="chiplog.propose-intent.v1"),
)


def delivery_response_adapter(
    tools: tuple[ToolSpec, ...],
) -> TypeAdapter[Continue | DeliveryCompletion]:
    _require(bool(tools) and len(set(tools)) == len(tools), "empty/duplicate delivery tools")
    _require(
        tools == tuple(tool for tool in DELIVERY_TOOLS if tool in tools),
        "unknown or noncanonical delivery tool identities",
    )
    names = tuple(tool.name for tool in tools)
    call = create_model("DeliveryToolCall", __base__=ToolCall, tool=(Literal[names], ...))
    continuation = create_model(
        "DeliveryContinue",
        __base__=Continue,
        tool_calls=(GenericAlias(tuple, (call, Ellipsis)), Field(min_length=1)),
    )
    return TypeAdapter(continuation | DeliveryCompletion)


def parse_delivery_response(raw: bytes, artifact: PromptArtifact) -> Continue | DeliveryCompletion:
    _require(artifact.generator_version == DELIVERY_GENERATOR, "wrong captured delivery generator")
    adapter = delivery_response_adapter(artifact.tools)
    _require(
        artifact.response_schema_json == _json(adapter.json_schema()).decode(),
        "wrong captured delivery response schema",
    )
    response = adapter.validate_json(raw)
    _require(response.canonical_bytes() == raw, "noncanonical delivery response bytes")
    if isinstance(response, Continue):
        _require(
            len({call.call_id for call in response.tool_calls}) == len(response.tool_calls),
            "duplicate delivery tool call",
        )
        return Continue.model_validate_json(raw)
    return response


def complete_delivery(run: RunRecord, observation: DeliveryObservation) -> RunRecord:
    from . import domain

    attempt = domain.current_attempt(run, "RESPONSE_CAPTURED")
    domain.continuation(run.model_copy(update={"turns": run.turns[:-1]}))
    _require(run.accepted_delivery_binding == "LEGACY_R13", "Run already has accepted delivery")
    _require(attempt.response_base64 is not None, "missing captured response bytes")
    assert attempt.response_base64 is not None
    raw = base64.b64decode(attempt.response_base64, validate=True)
    response = parse_delivery_response(raw, attempt.manifest.artifact)
    _require(
        isinstance(response, DeliveryCompletion), "captured response is not delivery completion"
    )
    assert isinstance(response, DeliveryCompletion)
    _require(
        run.head == "loop:" + run.model_copy(update={"head": "pending"}).digest(),
        "current captured Run head is not canonical",
    )
    _require(
        observation.run == ExactHead(identity=run.run_id, head=run.head, fingerprint=run.digest())
        and observation.tenant == run.tenant
        and observation.turn_id == run.turns[-1].turn_id
        and observation.captured_response == raw,
        "delivery observation differs from exact selected captured Run/Turn",
    )
    expected_history = [
        (historical, member)
        for turn in run.turns
        for historical in turn.attempts
        for member in historical.manifest.members
    ]
    _require(len(expected_history) == len(observation.history), "incomplete historical disclosure")
    for (historical, member), envelope in zip(expected_history, observation.history, strict=True):
        manifest_digest = historical.manifest.digest()
        _require(
            envelope.content
            == ExactHead(
                identity=member.record_id, head=member.revision_head, fingerprint=member.digest()
            )
            and envelope.visibility
            == ExactHead(
                identity=historical.attempt_id, head=manifest_digest, fingerprint=manifest_digest
            )
            and envelope.provenance.head == member.provenance_head
            and envelope.disclosure.head == member.label_head
            and envelope.label == member.label,
            "historical envelope differs from exact captured visibility member",
        )
    proposal = prepare_completion(response, observation)
    reference = DeliveryAcceptanceReference(
        acceptance_identity=proposal.acceptance.identity,
        acceptance_head=proposal.acceptance.head,
        acceptance_fingerprint=proposal.acceptance.fingerprint,
        proposal_canonical_base64=base64.b64encode(proposal.canonical_bytes()).decode(),
    )
    accepted = domain.with_attempt(
        run, attempt.model_copy(update={"state": "TERMINAL_ACCEPTED"}), "CompleteAcceptance"
    )
    turn = domain.current_turn(accepted).model_copy(
        update={"state": "ACCEPTED", "sealed_calls": ()}
    )
    return domain.successor(
        run,
        "CompleteAcceptance",
        state="SUCCEEDED",
        turns=(*accepted.turns[:-1], turn),
        deliveries=(),
        accepted_text=tuple(raw.decode() for raw in proposal.history_rendered_bytes),
        accepted_delivery_binding=reference,
    )


class DeliveryPrepareRequest(DeliveryDTO):
    previous: RunRecord
    observation: DeliveryObservation


class DeliveryValidateRequest(DeliveryPrepareRequest):
    proposed: RunRecord
