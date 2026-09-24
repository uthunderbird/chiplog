"""Fail-closed H1 assistant-conversation policy seam.

This module deliberately does not turn a policy DTO, a workspace closure DTO,
or an H1 wire DTO into authority.  The eventual broker-owned policy port must
capture those inputs and issue :class:`H1ConversationPolicyCapture` while the
canonical authority gate is held.  Until that port is mounted, public capture
refuses every request.

The pure projection is intentionally fed only by that non-serializable,
issuer-owned capability.  It is the one place where the eventual owner can
rederive the complete entry before conversation IPC and again before atomic
selection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, cast

from chiplog.capabilities.agent_loop.delivery_contracts import AcceptedDelivery, ExactHead
from chiplog.capabilities.agent_loop.execution_contracts import ExecutionRunRecord
from chiplog.capabilities.agent_loop.execution_history_contracts import ExecutionRunRecordV3
from chiplog.capabilities.projections.disclosure import join
from chiplog.capabilities.projections.provenance import ProvenanceBinding, ProvenanceSubject
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    DisclosureLabel,
    SourceReference,
)


class H1ConversationPolicyUnavailable(ValueError):
    """The runtime has not mounted the authenticated H1 policy source port."""


class H1ConversationPolicyViolation(ValueError):
    """A broker-held source cut cannot derive one deterministic entry."""


type NativeCapturedRun = ExecutionRunRecord | ExecutionRunRecordV3


@dataclass(frozen=True, slots=True)
class H1RegisteredConversationPolicy:
    """A broker-authenticated registration, never a caller authority token.

    The issuer that creates a capture must establish this exact binding from
    independently selected workspace policy/context evidence.  In particular,
    ``accepted_policy`` is a delivery-policy reference; it is not the workspace
    ``policy_head`` and neither value is inferred from the other.
    """

    tenant_id: str
    principal_id: str
    origin_recipient_id: str
    accepted_policy: ExactHead
    conversation_id: str
    origin_channel_id: str
    visible_channels: tuple[str, ...]
    workspace_policy_head: str
    contour_head: str
    deletion_fence_head: str
    disclosure_endpoint: str


@dataclass(frozen=True, slots=True)
class _CaptureMaterial:
    registration: H1RegisteredConversationPolicy
    run: NativeCapturedRun
    accepted_delivery: AcceptedDelivery
    authenticated_history: tuple[ConversationEntry, ...]
    narrowing: DisclosureLabel


class H1ConversationPolicyCapture:
    """Opaque, non-copyable capability issued only by a broker-private port."""

    __slots__ = ("__issuer", "__material")

    def __init__(self) -> None:
        raise TypeError("H1 conversation policy captures are issuer-held capabilities")

    def __copy__(self) -> H1ConversationPolicyCapture:
        raise TypeError("H1 conversation policy capture cannot be copied")

    def __deepcopy__(self, memo: dict[int, object]) -> H1ConversationPolicyCapture:
        del memo
        raise TypeError("H1 conversation policy capture cannot be copied")

    def __reduce__(self) -> str | tuple[object, ...]:
        raise TypeError("H1 conversation policy capture cannot be serialized")


class H1ConversationPolicySourcePort(Protocol):
    """Runtime-private port that owns authentic policy, closure, and source cuts.

    ``capture_current`` must run synchronously inside the runtime AuthorityGate;
    it must retain the first-path identity, selected native Run/attempt
    inventory, actual loop result, verified workspace closure, and database
    identity.  It may not await while the gate is held.  ``check_current`` must
    re-enumerate the entire cut and current context before writer admission.
    """

    def capture_current(self) -> H1ConversationPolicyCapture: ...

    def check_current(self, capture: H1ConversationPolicyCapture) -> bool: ...


def capture_policy_current(runtime: object) -> H1ConversationPolicyCapture:
    """Return one broker-issued capture or fail before any conversation IPC.

    No runtime currently mounts the required port.  A callable lookalike, DTO,
    or a capture made outside the port is rejected; this prevents fixture policy
    from silently becoming a positive H1 source path.
    """

    port = getattr(runtime, "_h1_conversation_policy_source_port", None)
    if port is None or not callable(getattr(port, "capture_current", None)):
        raise H1ConversationPolicyUnavailable(
            "H1 conversation lacks a registered authenticated policy source port"
        )
    capture = port.capture_current()
    if not isinstance(capture, H1ConversationPolicyCapture) or not _issued(capture, port):
        raise H1ConversationPolicyViolation(
            "H1 conversation policy port returned an unissued capture"
        )
    return capture


def check_policy_current(runtime: object, capture: object) -> bool:
    """Ask the mounted source owner to revalidate its exact retained cut."""

    port = getattr(runtime, "_h1_conversation_policy_source_port", None)
    if (
        not isinstance(capture, H1ConversationPolicyCapture)
        or not _issued(capture, port)
        or port is None
        or not callable(getattr(port, "check_current", None))
    ):
        return False
    return port.check_current(capture) is True


_issued_captures: dict[int, tuple[H1ConversationPolicyCapture, object]] = {}


def _issued(capture: H1ConversationPolicyCapture, issuer: object | None = None) -> bool:
    issued = _issued_captures.get(id(capture))
    return issued is not None and issued[0] is capture and (issuer is None or issued[1] is issuer)


def _issue_for_authenticated_port(
    material: _CaptureMaterial, *, issuer: object
) -> H1ConversationPolicyCapture:
    """Private construction hook for the future broker-owned source port.

    This is intentionally not exported.  The port must have authenticated
    ``material`` before calling it; construction itself establishes nothing.
    """

    capture = object.__new__(H1ConversationPolicyCapture)
    object.__setattr__(capture, "_H1ConversationPolicyCapture__issuer", issuer)
    object.__setattr__(capture, "_H1ConversationPolicyCapture__material", material)
    _issued_captures[id(capture)] = (capture, issuer)
    return capture


def _material(capture: H1ConversationPolicyCapture) -> _CaptureMaterial:
    if not _issued(capture):
        raise H1ConversationPolicyViolation("H1 conversation policy capture is unissued")
    issuer = object.__getattribute__(capture, "_H1ConversationPolicyCapture__issuer")
    issued = _issued_captures.get(id(capture))
    if issued is None or issued[1] is not issuer:
        raise H1ConversationPolicyViolation("H1 conversation policy capture issuer differs")
    return cast(
        "_CaptureMaterial",
        object.__getattribute__(capture, "_H1ConversationPolicyCapture__material"),
    )


def _require(value: bool, reason: str) -> None:
    if not value:
        raise H1ConversationPolicyViolation("H1 conversation policy " + reason)


def _source_references(run: NativeCapturedRun) -> tuple[SourceReference, ...]:
    by_identity: dict[tuple[str, str, str], SourceReference] = {}
    for turn in run.turns:
        for attempt in turn.attempts:
            for member in attempt.manifest.members:
                digest = hashlib.sha256(member.content.encode()).hexdigest()
                source = SourceReference(
                    tenant_id=run.tenant,
                    owner="agent_loop",
                    record_id=member.record_id,
                    record_version=member.revision_head + ":" + digest,
                    content_digest=digest,
                    label_head=member.label_head,
                    label=DisclosureLabel.model_validate(member.label.model_dump(mode="json")),
                )
                identity = (source.owner, source.record_id, source.record_version)
                previous = by_identity.get(identity)
                if previous is not None and previous != source:
                    raise H1ConversationPolicyViolation(
                        "has conflicting labels or source facts for one visibility identity"
                    )
                by_identity[identity] = source
    sources = tuple(by_identity[key] for key in sorted(by_identity))
    _require(bool(sources), "has no authentic visibility closure")
    return sources


def _validate_history(
    history: tuple[ConversationEntry, ...], registration: H1RegisteredConversationPolicy
) -> None:
    ordered = tuple(sorted(history, key=lambda entry: entry.sequence))
    _require(ordered == history, "history is not canonically ordered")
    _require(
        tuple(entry.sequence for entry in history) == tuple(range(1, len(history) + 1)),
        "history sequence is not contiguous",
    )
    _require(
        all(entry.tenant_id == registration.tenant_id for entry in history),
        "history crosses tenants",
    )
    _require(
        all(entry.conversation_id == registration.conversation_id for entry in history),
        "history has competing conversations",
    )
    _require(
        len({entry.entry_id for entry in history}) == len(history),
        "history entry identities duplicate",
    )


def _validate_registration(registration: H1RegisteredConversationPolicy) -> None:
    scalar_values = (
        registration.tenant_id,
        registration.principal_id,
        registration.origin_recipient_id,
        registration.conversation_id,
        registration.origin_channel_id,
        registration.workspace_policy_head,
        registration.contour_head,
        registration.deletion_fence_head,
        registration.disclosure_endpoint,
    )
    _require(
        all(isinstance(value, str) and bool(value) for value in scalar_values),
        "registered mapping has an empty scalar field",
    )
    _require(
        type(registration.accepted_policy) is ExactHead,
        "registered accepted-policy reference is unsupported",
    )


def derive_entry(capture: H1ConversationPolicyCapture) -> ConversationEntry:
    """Purely rederive the entry from one authenticated, sealed source cut."""

    material = _material(capture)
    registration, run, delivery = (
        material.registration,
        material.run,
        material.accepted_delivery,
    )
    _validate_registration(registration)
    _require(type(run) in (ExecutionRunRecord, ExecutionRunRecordV3), "run schema is unsupported")
    _require(run.tenant == registration.tenant_id, "run tenant differs from registration")
    _require(run.principal == registration.principal_id, "run principal differs from registration")
    _require(run.contour_head == registration.contour_head, "run contour differs from registration")
    _require(
        run.origin.recipient.recipient_id == registration.origin_recipient_id,
        "run origin recipient differs from registration",
    )
    _require(delivery.selection == run.origin, "accepted delivery origin differs from native run")
    _require(delivery.policy == registration.accepted_policy, "accepted delivery policy differs")
    _require(
        hashlib.sha256(delivery.rendered_bytes).hexdigest() == delivery.render_digest,
        "accepted delivery bytes/digest differ",
    )
    _require(
        registration.visible_channels == tuple(sorted(set(registration.visible_channels)))
        and registration.origin_channel_id in registration.visible_channels,
        "registered channels are not canonical or omit origin",
    )
    _validate_history(material.authenticated_history, registration)
    entry_id = run.run_id + "/accepted"
    _require(
        all(entry.entry_id != entry_id for entry in material.authenticated_history),
        "entry identity is already selected",
    )
    sources = _source_references(run)
    inherited = join(tuple(source.label for source in sources))
    effective_label = join((inherited, material.narrowing))
    envelope = DisclosureEnvelope(
        tenant_id=registration.tenant_id,
        content_digest=hashlib.sha256(delivery.rendered_bytes).hexdigest(),
        sources=sources,
        label=effective_label,
        policy_head=registration.workspace_policy_head,
        contour_head=registration.contour_head,
        deletion_fence_head=registration.deletion_fence_head,
    )
    return ConversationEntry(
        tenant_id=registration.tenant_id,
        conversation_id=registration.conversation_id,
        entry_id=entry_id,
        sequence=len(material.authenticated_history) + 1,
        origin_channel_id=registration.origin_channel_id,
        visible_channels=registration.visible_channels,
        role="assistant",
        accepted_bytes=delivery.rendered_bytes,
        envelope=envelope,
    )


def derive_provenance_binding(capture: H1ConversationPolicyCapture) -> ProvenanceBinding:
    """Build the subject-bound closure that admission must validate independently."""

    entry = derive_entry(capture)
    return ProvenanceBinding(
        subject=ProvenanceSubject(
            tenant_id=entry.tenant_id,
            producer="core.conversation",
            record_id=entry.entry_id,
            revision=str(entry.sequence),
        ),
        content_digest=entry.envelope.content_digest,
        sources=entry.envelope.sources,
    )


__all__ = [
    "H1ConversationPolicyCapture",
    "H1ConversationPolicySourcePort",
    "H1ConversationPolicyUnavailable",
    "H1ConversationPolicyViolation",
    "H1RegisteredConversationPolicy",
    "capture_policy_current",
    "check_policy_current",
    "derive_entry",
    "derive_provenance_binding",
]
