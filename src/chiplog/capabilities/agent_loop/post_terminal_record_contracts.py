"""Versioned, pure interpretation of post-terminal work companions.

These records describe a proposed complete batch.  Decoding or joining them does
not select a batch, authenticate a writer, or change any live work state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Annotated, Literal, cast

from pydantic import Field

from .post_terminal_contracts import (
    ClaimedWork,
    ClosedWork,
    PostTerminalWorkLeaseState,
    PostTerminalWorkRequest,
    PostTerminalWorkView,
    PreparedPostTerminalWork,
    PrepareTerminalWork,
    PrepareWorkClose,
    PrepareWorkLease,
    PrepareWorkRollover,
    UnleasedWork,
    WorkCanonicalMember,
)
from .recovery_contracts import (
    Absent,
    Digest,
    Identity,
    OriginalObligationBinding,
    Present,
    RecoveryDTO,
    RolloverPredecessor,
    UInt64,
    WorkEpochRolloverFence,
)
from .scheduler_rollover import IssuedRolloverObservation

OWNER: Literal["agent_loop"] = "agent_loop"
SUBJECT_SCHEMA: Literal["chiplog.post-terminal.subject.v1"] = "chiplog.post-terminal.subject.v1"
EPOCH_SCHEMA: Literal["chiplog.post-terminal.epoch.v1"] = "chiplog.post-terminal.epoch.v1"
SELECTOR_SCHEMA: Literal["chiplog.post-terminal.selector.v1"] = "chiplog.post-terminal.selector.v1"
LEASE_SCHEMA: Literal["chiplog.post-terminal.lease.v1"] = "chiplog.post-terminal.lease.v1"
ROLLOVER_SCHEMA: Literal["chiplog.post-terminal.rollover.v1"] = "chiplog.post-terminal.rollover.v1"
EDGE_SCHEMA: Literal["chiplog.post-terminal.edge.v1"] = "chiplog.post-terminal.edge.v1"


class _Record(RecoveryDTO):
    tenant_id: Identity
    command_id: Identity
    record_id: Identity
    owner: Literal["agent_loop"] = OWNER


class PostTerminalSubjectRecord(_Record):
    """The durable work subject; its envelope supplies its head and fingerprint."""

    kind: Literal["POST_TERMINAL_SUBJECT_V1"] = "POST_TERMINAL_SUBJECT_V1"
    record_kind: Literal["SUBJECT"] = "SUBJECT"
    schema_id: Literal["chiplog.post-terminal.subject.v1"] = SUBJECT_SCHEMA
    work_id: Identity
    terminal_run_head: Identity
    terminal_manifest_head: Identity
    terminal_manifest_member_fingerprint: Digest
    original_obligation: OriginalObligationBinding


class PostTerminalEpochRecord(_Record):
    kind: Literal["POST_TERMINAL_EPOCH_V1"] = "POST_TERMINAL_EPOCH_V1"
    record_kind: Literal["EPOCH"] = "EPOCH"
    schema_id: Literal["chiplog.post-terminal.epoch.v1"] = EPOCH_SCHEMA
    work_id: Identity
    epoch_id: Identity
    subject: Present
    predecessor_epoch: Annotated[Absent | Present, Field(discriminator="kind")]


class PostTerminalSelectorRecord(_Record):
    kind: Literal["POST_TERMINAL_SELECTOR_V1"] = "POST_TERMINAL_SELECTOR_V1"
    record_kind: Literal["SELECTOR"] = "SELECTOR"
    schema_id: Literal["chiplog.post-terminal.selector.v1"] = SELECTOR_SCHEMA
    work_id: Identity
    selector_id: Identity
    selector_version: UInt64
    selected_epoch_id: Identity
    selected_epoch: Present
    predecessor_selector: Annotated[Absent | Present, Field(discriminator="kind")]


class PostTerminalLeaseRecord(_Record):
    kind: Literal["POST_TERMINAL_LEASE_V1"] = "POST_TERMINAL_LEASE_V1"
    record_kind: Literal["LEASE"] = "LEASE"
    schema_id: Literal["chiplog.post-terminal.lease.v1"] = LEASE_SCHEMA
    work_id: Identity
    epoch: Present
    selector: Present
    predecessor_lease: Annotated[Absent | Present, Field(discriminator="kind")]
    lease: PostTerminalWorkLeaseState
    original_obligation: Present
    original_evidence: Annotated[Absent | Present, Field(discriminator="kind")]
    resolver_batch: Annotated[Absent | Present, Field(discriminator="kind")]


class PostTerminalRolloverRecord(_Record):
    kind: Literal["POST_TERMINAL_ROLLOVER_V1"] = "POST_TERMINAL_ROLLOVER_V1"
    record_kind: Literal["ROLLOVER"] = "ROLLOVER"
    schema_id: Literal["chiplog.post-terminal.rollover.v1"] = ROLLOVER_SCHEMA
    subject: Present
    old_epoch: Present
    new_epoch: Present
    old_selector: Present
    new_selector: Present
    old_lease: Present
    new_lease: Present
    fence: WorkEpochRolloverFence
    issued: IssuedRolloverObservation
    current_original_obligation: Present
    current_original_evidence: Annotated[Absent | Present, Field(discriminator="kind")]


class PostTerminalEdgeRecord(_Record):
    kind: Literal["POST_TERMINAL_EDGE_V1"] = "POST_TERMINAL_EDGE_V1"
    record_kind: Literal["EDGE"] = "EDGE"
    schema_id: Literal["chiplog.post-terminal.edge.v1"] = EDGE_SCHEMA
    rollover_decision: Present
    old_epoch: Present
    new_epoch: Present
    old_selector: Present
    new_selector: Present
    old_lease: Present
    new_lease: Present
    predecessor_rollover: Annotated[Absent | RolloverPredecessor, Field(discriminator="kind")]


type WorkDurableRecord = (
    PostTerminalSubjectRecord
    | PostTerminalEpochRecord
    | PostTerminalSelectorRecord
    | PostTerminalLeaseRecord
    | PostTerminalRolloverRecord
    | PostTerminalEdgeRecord
)

_DECODERS = {
    ("SUBJECT", SUBJECT_SCHEMA): PostTerminalSubjectRecord,
    ("EPOCH", EPOCH_SCHEMA): PostTerminalEpochRecord,
    ("SELECTOR", SELECTOR_SCHEMA): PostTerminalSelectorRecord,
    ("LEASE", LEASE_SCHEMA): PostTerminalLeaseRecord,
    ("ROLLOVER", ROLLOVER_SCHEMA): PostTerminalRolloverRecord,
    ("EDGE", EDGE_SCHEMA): PostTerminalEdgeRecord,
}


class PostTerminalRecordIntegrityError(ValueError):
    """A bounded failure while interpreting one immutable proposed member."""

    def __init__(
        self,
        reason: str,
        operation: str = "decode",
        tenant_id: str = "unknown",
        record_id: str = "unknown",
    ) -> None:
        super().__init__(
            f"post-terminal.{operation} tenant={tenant_id} record={record_id}: {reason}"
        )
        self.operation = operation
        self.tenant_id = tenant_id
        self.record_id = record_id
        self.reason = reason


@dataclass(frozen=True)
class WorkRecordContract:
    record_kind: str
    schema_id: str
    owner: Literal["agent_loop"]
    decoder: type[RecoveryDTO]


WORK_RECORD_CONTRACTS = (
    WorkRecordContract("SUBJECT", SUBJECT_SCHEMA, OWNER, PostTerminalSubjectRecord),
    WorkRecordContract("EPOCH", EPOCH_SCHEMA, OWNER, PostTerminalEpochRecord),
    WorkRecordContract("SELECTOR", SELECTOR_SCHEMA, OWNER, PostTerminalSelectorRecord),
    WorkRecordContract("LEASE", LEASE_SCHEMA, OWNER, PostTerminalLeaseRecord),
    WorkRecordContract("ROLLOVER", ROLLOVER_SCHEMA, OWNER, PostTerminalRolloverRecord),
    WorkRecordContract("EDGE", EDGE_SCHEMA, OWNER, PostTerminalEdgeRecord),
)


@dataclass(frozen=True)
class DecodedWorkCanonicalMember:
    member: WorkCanonicalMember
    record: WorkDurableRecord


def _reference(member: WorkCanonicalMember) -> Present:
    return Present(head=member.record_id, fingerprint=member.fingerprint)


def _member_error(member: WorkCanonicalMember, reason: str) -> PostTerminalRecordIntegrityError:
    return PostTerminalRecordIntegrityError(
        reason, operation="decode", tenant_id="untrusted", record_id=member.record_id
    )


def decode_work_canonical_member(member: WorkCanonicalMember) -> DecodedWorkCanonicalMember:
    """Decode exactly one canonical v1 member while retaining its supplied bytes."""
    decoder = _DECODERS.get((member.record_kind, member.schema_id))
    if decoder is None:
        raise _member_error(member, "unknown post-terminal record kind/schema")
    if hashlib.sha256(member.canonical_record_bytes).hexdigest() != member.fingerprint:
        raise _member_error(member, "member fingerprint differs from bytes")
    try:
        record = cast(
            WorkDurableRecord,
            cast(type[RecoveryDTO], decoder).model_validate_json(member.canonical_record_bytes),
        )
    except Exception as error:  # Pydantic's public error type is intentionally not leaked.
        raise _member_error(member, "malformed post-terminal record bytes") from error
    if (
        record.record_kind != member.record_kind
        or record.schema_id != member.schema_id
        or record.record_id != member.record_id
        or record.canonical_bytes() != member.canonical_record_bytes
    ):
        raise _member_error(member, "member envelope and canonical record differ")
    _reject_self_reference(record, member)
    return DecodedWorkCanonicalMember(member=member, record=record)


def _reject_self_reference(value: object, member: WorkCanonicalMember) -> None:
    if isinstance(value, Present) and (
        value.head == member.record_id or value.fingerprint == member.fingerprint
    ):
        raise PostTerminalRecordIntegrityError("record contains a self reference")
    if isinstance(value, RecoveryDTO):
        _reject_self_reference(value.model_dump(), member)
    elif isinstance(value, dict):
        if value.get("kind") == "PRESENT" and (
            value.get("head") == member.record_id or value.get("fingerprint") == member.fingerprint
        ):
            raise PostTerminalRecordIntegrityError("record contains a self reference")
        for child in value.values():
            _reject_self_reference(child, member)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_self_reference(child, member)


def _require(actual: object, expected: object, reason: str) -> None:
    if actual != expected:
        raise PostTerminalRecordIntegrityError(reason)


def _one(records: dict[str, DecodedWorkCanonicalMember], kind: str) -> DecodedWorkCanonicalMember:
    found = [item for item in records.values() if item.member.record_kind == kind]
    if len(found) != 1:
        raise PostTerminalRecordIntegrityError(f"expected exactly one {kind} companion")
    return found[0]


def _same_batch(actual: Present, member: DecodedWorkCanonicalMember, reason: str) -> None:
    _require(actual, _reference(member.member), reason)


def _same_head(actual: Present, expected: str, reason: str) -> None:
    _require(actual.head, expected, reason)


def validate_prepared_post_terminal_work(
    source_request: PostTerminalWorkRequest, result: PreparedPostTerminalWork
) -> tuple[DecodedWorkCanonicalMember, ...]:
    """Check the proposed companion graph against its supplied request, purely.

    This is representational equality only: no claim is made about selection, CAS,
    proof issuance, clocks, broker authority, or closure validity.
    """
    _require(
        result.source_request_fingerprint,
        hashlib.sha256(source_request.canonical_bytes()).hexdigest(),
        "source request fingerprint differs from exact request bytes",
    )
    decoded = tuple(decode_work_canonical_member(member) for member in result.complete_records)
    for item in decoded:
        _require(
            item.record.tenant_id, source_request.identity.tenant_id, "companion tenant differs"
        )
        _require(
            item.record.command_id, source_request.identity.command_id, "companion command differs"
        )
    ids = [item.member.record_id for item in decoded]
    pairs = [(item.member.record_kind, item.member.record_id) for item in decoded]
    if len(ids) != len(set(ids)) or len(pairs) != len(set(pairs)):
        raise PostTerminalRecordIntegrityError("duplicate companion identity")

    if isinstance(source_request, PrepareTerminalWork):
        _validate_genesis(source_request, result, decoded)
    elif isinstance(source_request, PrepareWorkLease):
        _validate_lease(source_request, result, decoded)
    elif isinstance(source_request, PrepareWorkRollover):
        _validate_rollover(source_request, result, decoded)
    elif isinstance(source_request, PrepareWorkClose):
        _validate_close(source_request, result, decoded)
    else:  # Future union additions must intentionally gain a companion contract.
        raise PostTerminalRecordIntegrityError("unsupported post-terminal request kind")
    return decoded


def _validate_subject(record: PostTerminalSubjectRecord, view: PostTerminalWorkView) -> None:
    subject = view.subject
    _require(record.work_id, subject.work_id, "subject work differs")
    _require(record.record_id, subject.work_subject_head, "subject head differs")
    _require(record.terminal_manifest_head, subject.terminal_manifest_head, "manifest head differs")
    _require(
        record.terminal_manifest_member_fingerprint,
        subject.terminal_manifest_member_fingerprint,
        "manifest fingerprint differs",
    )
    _require(record.original_obligation, subject.original_obligation, "subject obligation differs")


def _validate_genesis(
    request: PrepareTerminalWork,
    result: PreparedPostTerminalWork,
    decoded: tuple[DecodedWorkCanonicalMember, ...],
) -> None:
    if not request.ordered_open_obligations:
        _require(result.ordered_work, (), "empty terminal genesis has no work")
        _require(decoded, (), "empty terminal genesis has no companions")
        return
    _require(
        len(result.ordered_work),
        len(request.ordered_open_obligations),
        "genesis work cardinality differs",
    )
    _require(len(decoded), 4 * len(result.ordered_work), "genesis companion cardinality differs")
    for ordinal, (obligation, view) in enumerate(
        zip(request.ordered_open_obligations, result.ordered_work, strict=True)
    ):
        _require(view.subject.original_obligation, obligation, "genesis obligation order differs")
        group = decoded[ordinal * 4 : (ordinal + 1) * 4]
        bucket = {item.member.record_id: item for item in group}
        if len(bucket) != 4:
            raise PostTerminalRecordIntegrityError("genesis cross-work or missing companion")
        if any(getattr(item.record, "work_id", None) != view.subject.work_id for item in group):
            raise PostTerminalRecordIntegrityError("genesis companion work order differs")
        if tuple(item.member.record_kind for item in bucket.values()) != (
            "SUBJECT",
            "EPOCH",
            "SELECTOR",
            "LEASE",
        ):
            raise PostTerminalRecordIntegrityError("genesis companion order differs")
        subject = _one(bucket, "SUBJECT").record
        epoch_item = _one(bucket, "EPOCH")
        selector_item = _one(bucket, "SELECTOR")
        lease_item = _one(bucket, "LEASE")
        if not isinstance(subject, PostTerminalSubjectRecord):
            raise PostTerminalRecordIntegrityError("subject decoder mismatch")
        _validate_subject(subject, view)
        _require(
            view.subject.work_subject_fingerprint,
            _one(bucket, "SUBJECT").member.fingerprint,
            "subject fingerprint differs",
        )
        epoch = epoch_item.record
        selector = selector_item.record
        lease = lease_item.record
        if (
            not isinstance(epoch, PostTerminalEpochRecord)
            or not isinstance(selector, PostTerminalSelectorRecord)
            or not isinstance(lease, PostTerminalLeaseRecord)
        ):
            raise PostTerminalRecordIntegrityError("genesis record kind mismatch")
        _same_batch(epoch.subject, _one(bucket, "SUBJECT"), "epoch subject differs")
        _require(epoch.predecessor_epoch, Absent(), "genesis epoch has predecessor")
        _require(epoch.epoch_id, view.work_epoch.current_epoch_id, "genesis epoch id differs")
        _require(epoch.record_id, view.work_epoch.current_epoch_head, "genesis epoch head differs")
        _same_batch(selector.selected_epoch, epoch_item, "selector epoch differs")
        _require(selector.predecessor_selector, Absent(), "genesis selector has predecessor")
        _require(selector.selector_version, 0, "genesis selector version differs")
        _require(
            selector.selector_version,
            view.work_epoch.selector_version,
            "genesis selector view version differs",
        )
        _require(selector.selector_id, view.work_epoch.selector_id, "genesis selector id differs")
        _require(selector.record_id, view.work_epoch.selector_head, "genesis selector head differs")
        _require(selector.selected_epoch_id, epoch.epoch_id, "genesis selected epoch id differs")
        _same_batch(lease.epoch, epoch_item, "lease epoch differs")
        _same_batch(lease.selector, selector_item, "lease selector differs")
        _require(view.work_state_head, _reference(lease_item.member), "genesis work state differs")
        _require(lease.predecessor_lease, Absent(), "genesis lease has predecessor")
        _require(lease.lease, UnleasedWork(lease_head=lease.record_id), "genesis lease differs")
        _require(view.lease, lease.lease, "genesis view lease differs")
        _require(view.predecessor_rollover, Absent(), "genesis rollover predecessor differs")
        _require(
            lease.original_obligation.head,
            obligation.obligation_head,
            "genesis obligation head differs",
        )
        _require(lease.original_evidence, obligation.evidence_head, "genesis evidence differs")
        _require(lease.resolver_batch, Absent(), "genesis resolver batch differs")
        _require(subject.terminal_run_head, request.terminal_run.head, "terminal run differs")
        _require(
            subject.terminal_manifest_head,
            request.terminal_manifest.revision.head,
            "terminal manifest differs",
        )
        _require(
            subject.terminal_manifest_member_fingerprint,
            request.terminal_manifest.revision.fingerprint,
            "terminal manifest fingerprint differs",
        )


def _expected_view(
    request: PrepareWorkLease | PrepareWorkRollover | PrepareWorkClose,
    result: PreparedPostTerminalWork,
) -> PostTerminalWorkView:
    _require(len(result.ordered_work), 1, "non-genesis requires one resulting view")
    return result.ordered_work[0]


def _validate_lease(
    request: PrepareWorkLease,
    result: PreparedPostTerminalWork,
    decoded: tuple[DecodedWorkCanonicalMember, ...],
) -> None:
    view = _expected_view(request, result)
    _require(len(decoded), 1, "lease request publishes one companion")
    lease_item = decoded[0]
    record = lease_item.record
    if not isinstance(record, PostTerminalLeaseRecord):
        raise PostTerminalRecordIntegrityError("lease request requires lease companion")
    _require(record.work_id, view.subject.work_id, "lease work differs")
    _require(view.subject, request.expected.subject, "lease subject differs")
    _require(view.work_epoch, request.expected.work_epoch, "lease epoch view differs")
    _require(view.work_state_head, _reference(lease_item.member), "lease work state differs")
    _same_head(record.epoch, request.expected.work_epoch.current_epoch_head, "lease epoch differs")
    _same_head(record.selector, request.expected.work_epoch.selector_head, "lease selector differs")
    _require(
        record.original_obligation, request.current_original_obligation, "lease obligation differs"
    )
    _require(record.original_evidence, request.current_original_evidence, "lease evidence differs")
    _require(record.resolver_batch, Absent(), "lease resolver batch differs")
    if not isinstance(record.lease, ClaimedWork):
        raise PostTerminalRecordIntegrityError("lease request requires claimed state")
    _require(record.lease.binding.lease_head, record.record_id, "lease head differs")
    _require(record.lease.binding.lease_id, request.proposed_lease_id, "lease id differs")
    _require(
        record.lease.binding.generation, request.proposed_generation, "lease generation differs"
    )
    _require(record.lease.binding.holder_id, request.proposed_holder_id, "lease holder differs")
    _require(
        record.lease.binding.holder_session_id,
        request.proposed_holder_session_id,
        "lease session differs",
    )
    _require(record.lease.binding.trusted_expiry, request.proposed_expiry, "lease expiry differs")
    _require(
        record.predecessor_lease, request.expected.work_state_head, "lease predecessor differs"
    )
    _require(view.lease, record.lease, "lease view state differs")
    _require(
        view.predecessor_rollover,
        request.expected.predecessor_rollover,
        "lease rollover predecessor differs",
    )


def _validate_rollover(
    request: PrepareWorkRollover,
    result: PreparedPostTerminalWork,
    decoded: tuple[DecodedWorkCanonicalMember, ...],
) -> None:
    view = _expected_view(request, result)
    _require(len(decoded), 5, "rollover companion cardinality differs")
    records = {item.member.record_kind: item for item in decoded}
    if set(records) != {"EPOCH", "SELECTOR", "LEASE", "ROLLOVER", "EDGE"}:
        raise PostTerminalRecordIntegrityError("rollover companion kinds differ")
    if tuple(item.member.record_kind for item in decoded) != (
        "EPOCH",
        "SELECTOR",
        "LEASE",
        "ROLLOVER",
        "EDGE",
    ):
        raise PostTerminalRecordIntegrityError("rollover companion order differs")
    epoch, selector, lease, decision, edge = (
        records[k] for k in ("EPOCH", "SELECTOR", "LEASE", "ROLLOVER", "EDGE")
    )
    if not all(
        isinstance(item.record, expected)
        for item, expected in (
            (epoch, PostTerminalEpochRecord),
            (selector, PostTerminalSelectorRecord),
            (lease, PostTerminalLeaseRecord),
            (decision, PostTerminalRolloverRecord),
            (edge, PostTerminalEdgeRecord),
        )
    ):
        raise PostTerminalRecordIntegrityError("rollover decoder mismatch")
    epoch_record = epoch.record
    selector_record = selector.record
    lease_record = lease.record
    decision_record = decision.record
    edge_record = edge.record
    assert (
        isinstance(epoch_record, PostTerminalEpochRecord)
        and isinstance(selector_record, PostTerminalSelectorRecord)
        and isinstance(lease_record, PostTerminalLeaseRecord)
        and isinstance(decision_record, PostTerminalRolloverRecord)
        and isinstance(edge_record, PostTerminalEdgeRecord)
    )
    _require(epoch_record.work_id, view.subject.work_id, "rollover epoch work differs")
    _require(view.subject, request.expected.subject, "rollover subject view differs")
    _require(view.work_state_head, _reference(lease.member), "rollover work state differs")
    _same_head(
        epoch_record.subject, request.expected.subject.work_subject_head, "rollover subject differs"
    )
    if not isinstance(epoch_record.predecessor_epoch, Present):
        raise PostTerminalRecordIntegrityError("rollover epoch lacks predecessor")
    _same_head(
        epoch_record.predecessor_epoch,
        request.expected.work_epoch.current_epoch_head,
        "rollover epoch predecessor differs",
    )
    if not isinstance(selector_record.predecessor_selector, Present):
        raise PostTerminalRecordIntegrityError("rollover selector lacks predecessor")
    _same_head(
        selector_record.predecessor_selector,
        request.expected.work_epoch.selector_head,
        "rollover selector predecessor differs",
    )
    if not isinstance(lease_record.predecessor_lease, Present):
        raise PostTerminalRecordIntegrityError("rollover lease lacks predecessor")
    _require(
        lease_record.predecessor_lease,
        request.expected.work_state_head,
        "rollover lease predecessor differs",
    )
    _same_batch(selector_record.selected_epoch, epoch, "rollover selector epoch differs")
    _require(
        selector_record.selector_version,
        request.expected.work_epoch.selector_version + 1,
        "rollover selector version differs",
    )
    _require(
        selector_record.selector_id, view.work_epoch.selector_id, "rollover selector id differs"
    )
    _require(
        selector_record.record_id, view.work_epoch.selector_head, "rollover selector head differs"
    )
    _require(epoch_record.epoch_id, view.work_epoch.current_epoch_id, "rollover epoch id differs")
    _require(
        epoch_record.record_id, view.work_epoch.current_epoch_head, "rollover epoch head differs"
    )
    _same_batch(lease_record.epoch, epoch, "rollover lease epoch differs")
    _same_batch(lease_record.selector, selector, "rollover lease selector differs")
    _same_batch(decision_record.new_epoch, epoch, "rollover new epoch differs")
    _same_batch(decision_record.new_selector, selector, "rollover new selector differs")
    _same_batch(decision_record.new_lease, lease, "rollover new lease differs")
    _same_head(
        decision_record.subject,
        request.expected.subject.work_subject_head,
        "rollover decision subject differs",
    )
    _same_head(
        decision_record.old_epoch,
        request.expected.work_epoch.current_epoch_head,
        "rollover old epoch differs",
    )
    _same_head(
        decision_record.old_selector,
        request.expected.work_epoch.selector_head,
        "rollover old selector differs",
    )
    _require(
        decision_record.old_lease, request.expected.work_state_head, "rollover old lease differs"
    )
    _same_batch(edge_record.rollover_decision, decision, "edge decision differs")
    for name in ("old_epoch", "old_selector", "old_lease"):
        _require(
            getattr(decision_record, name), getattr(edge_record, name), "edge old reference differs"
        )
    for name, item in (("new_epoch", epoch), ("new_selector", selector), ("new_lease", lease)):
        _same_batch(getattr(edge_record, name), item, "edge new reference differs")
    _require(decision_record.fence, request.fence, "rollover fence differs")
    _require(decision_record.issued, request.issued, "rollover issuance differs")
    _require(
        decision_record.current_original_obligation,
        request.current_original_obligation,
        "rollover obligation differs",
    )
    _require(
        decision_record.current_original_evidence,
        request.current_original_evidence,
        "rollover evidence differs",
    )
    _require(
        lease_record.original_obligation,
        request.current_original_obligation,
        "rollover lease obligation differs",
    )
    _require(
        lease_record.original_evidence,
        request.current_original_evidence,
        "rollover lease evidence differs",
    )
    _require(
        edge_record.predecessor_rollover,
        request.expected.predecessor_rollover,
        "edge predecessor differs",
    )
    _require(view.lease, lease_record.lease, "rollover view lease differs")
    _require(
        lease_record.lease,
        UnleasedWork(lease_head=lease_record.record_id),
        "rollover lease differs",
    )
    _require(
        view.predecessor_rollover,
        RolloverPredecessor(decision=_reference(decision.member), edge=_reference(edge.member)),
        "rollover view predecessor differs",
    )


def _validate_close(
    request: PrepareWorkClose,
    result: PreparedPostTerminalWork,
    decoded: tuple[DecodedWorkCanonicalMember, ...],
) -> None:
    view = _expected_view(request, result)
    _require(len(decoded), 1, "close request publishes one companion")
    lease_item = decoded[0]
    record = lease_item.record
    if not isinstance(record, PostTerminalLeaseRecord) or not isinstance(record.lease, ClosedWork):
        raise PostTerminalRecordIntegrityError("close requires closed lease companion")
    _require(record.work_id, view.subject.work_id, "close work differs")
    _require(view.subject, request.expected.subject, "close subject differs")
    _require(view.work_epoch, request.expected.work_epoch, "close epoch differs")
    _require(view.work_state_head, _reference(lease_item.member), "close work state differs")
    _require(
        record.lease.exact_obligation_terminal_head,
        request.exact_obligation_terminal_head,
        "close terminal obligation differs",
    )
    _require(record.resolver_batch, request.original_resolver_batch, "close resolver batch differs")
    _require(
        record.original_obligation.head,
        view.subject.original_obligation.obligation_head,
        "close original obligation differs",
    )
    _require(
        record.original_evidence,
        view.subject.original_obligation.evidence_head,
        "close original evidence differs",
    )
    _require(
        record.predecessor_lease, request.expected.work_state_head, "close predecessor differs"
    )
    _require(view.lease, record.lease, "close view lease differs")
    _require(
        view.predecessor_rollover,
        request.expected.predecessor_rollover,
        "close rollover predecessor differs",
    )
