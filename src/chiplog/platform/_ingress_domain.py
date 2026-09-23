"""Pure broker-private ingress proposals. No I/O or authority issuance occurs here.

The registered broker handler must authenticate the command-bound source proof
and recompare the complete observed snapshot at its journal/writer CAS cut.
Successful evaluation alone is neither durable custody nor permission to ack.
"""

import json
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal

from chiplog.platform._ingress_contracts import (
    AdmissionBound,
    BlockedRebase,
    ClassReserve,
    CustodyState,
    DrainManifest,
    Head,
    PollMemberDisposition,
    PollPageManifest,
    QuarantineCustody,
    QuarantineResult,
    ReadyGeneration,
    ReceiptToken,
)

MAX_UINT64 = 2**64 - 1


class IngressHold(ValueError):
    """A complete proposal cannot be established; caller must retain custody."""


class IngressConflict(IngressHold):
    """An identity already names different immutable bytes or state."""


def _checked(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_UINT64:
        raise IngressHold("bounded integer overflow")
    return value


@dataclass(frozen=True)
class TokenEntry:
    token: ReceiptToken
    mode: Literal["LOSS_SLOT", "RETAINED_SOURCE"]
    retention_proof: Head | None
    state_head: Head
    staged_bytes: bytes | None = None
    staged_digest: str | None = None
    custody: CustodyState | None = None


@dataclass(frozen=True)
class CustodySnapshot:
    epoch_id: str
    epoch_head: str
    fence: int
    state: Literal["OPEN", "QUIESCING", "DRAINING", "CLOSED"]
    entries: tuple[TokenEntry, ...] = ()


def allocate_token(
    snapshot: CustodySnapshot,
    token: ReceiptToken,
    mode: Literal["LOSS_SLOT", "RETAINED_SOURCE"],
    retention_proof: Head | None = None,
) -> CustodySnapshot:
    """Propose durable allocation before broker issues the irreversible read."""
    if mode not in ("LOSS_SLOT", "RETAINED_SOURCE"):
        raise IngressHold("unknown acquisition mechanism")
    if (mode == "RETAINED_SOURCE") != (retention_proof is not None):
        raise IngressHold("retained-source mechanism requires its exact proof")
    fingerprint = sha256(
        json.dumps(
            [
                "chiplog.ingress.token-allocation.v1",
                token.model_dump(mode="json"),
                mode,
                retention_proof.model_dump(mode="json") if retention_proof is not None else None,
            ],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    state_head = Head(
        identity=token.token_id, head="token-allocation:" + fingerprint, fingerprint=fingerprint
    )
    candidate = TokenEntry(token, mode, retention_proof, state_head)
    matches = [entry for entry in snapshot.entries if entry.token.token_id == token.token_id]
    if matches:
        observed = (matches[0].token, matches[0].mode, matches[0].retention_proof)
        if len(matches) == 1 and observed == (token, mode, retention_proof):
            return snapshot
        raise IngressConflict("receipt identity already names different allocation")
    if snapshot.state != "OPEN":
        raise IngressHold("token allocation lost admission quiesce race")
    if (
        token.source.admission_epoch.identity,
        token.source.admission_epoch.head,
        token.source.admission_fence,
    ) != (snapshot.epoch_id, snapshot.epoch_head, snapshot.fence):
        raise IngressHold("stale admission epoch/fence")
    if any(entry.token.receive_slot == token.receive_slot for entry in snapshot.entries):
        raise IngressConflict("receive slot already allocated")
    return replace(snapshot, entries=(*snapshot.entries, candidate))


def _entry(snapshot: CustodySnapshot, token_id: str) -> tuple[int, TokenEntry]:
    matches = [
        (index, entry)
        for index, entry in enumerate(snapshot.entries)
        if entry.token.token_id == token_id
    ]
    if len(matches) != 1:
        raise IngressHold("token missing or duplicated in authoritative snapshot")
    return matches[0]


def _replace_entry(snapshot: CustodySnapshot, index: int, entry: TokenEntry) -> CustodySnapshot:
    entries = (*snapshot.entries[:index], entry, *snapshot.entries[index + 1 :])
    return replace(snapshot, entries=entries)


def stage_raw(snapshot: CustodySnapshot, token_id: str, raw: bytes) -> CustodySnapshot:
    index, entry = _entry(snapshot, token_id)
    if type(raw) is not bytes or len(raw) > entry.token.maximum_bytes:
        raise IngressHold("raw bytes exceed preallocated bounded receive")
    if entry.staged_bytes is not None:
        if entry.staged_bytes == raw and entry.staged_digest == sha256(raw).hexdigest():
            return snapshot
        raise IngressConflict("changed raw bytes for exact receipt")
    if entry.custody is not None or snapshot.state == "CLOSED":
        raise IngressConflict("raw staging cannot rewrite terminal custody")
    raw_digest = sha256(raw).hexdigest()
    fingerprint = sha256(
        json.dumps(
            ["chiplog.ingress.raw-stage.v1", entry.state_head.model_dump(mode="json"), raw_digest],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    head = Head(identity=token_id, head="token-raw-stage:" + fingerprint, fingerprint=fingerprint)
    return _replace_entry(
        snapshot,
        index,
        replace(
            entry,
            staged_bytes=raw,
            staged_digest=raw_digest,
            state_head=head,
        ),
    )


def publish_custody(
    snapshot: CustodySnapshot,
    token_id: str,
    custody: CustodyState,
) -> CustodySnapshot:
    """Broker verifies proof; this reducer enforces exact-byte/correlation closure."""
    index, entry = _entry(snapshot, token_id)
    if custody.token != entry.state_head:
        raise IngressConflict("custody successor does not match exact current token state head")
    if entry.custody is not None:
        if entry.custody == custody:
            return snapshot
        raise IngressConflict("second or changed custody successor")
    if custody.kind in ("ADMITTED_DURABLE", "QUARANTINED_RAW_EVIDENCE"):
        if entry.staged_bytes is None:
            raise IngressHold("local custody requires exact staged replayable bytes")
        if (
            custody.raw_bytes != entry.staged_bytes
            or custody.raw_digest != entry.staged_digest
            or custody.authentication.raw_digest != entry.staged_digest
            or custody.authentication.source != entry.token.source
        ):
            raise IngressConflict("raw/authentication/source binding substitution")
    elif custody.kind == "RETRY_WITH_SOURCE_CUSTODY":
        if entry.mode != "RETAINED_SOURCE" or (
            custody.continuing_retention_proof != entry.retention_proof
        ):
            raise IngressHold("retry requires continuing independently proven source custody")
    elif custody.kind == "LOSS_OBLIGATION" and custody.receive_slot != entry.token.receive_slot:
        raise IngressConflict("loss obligation names another receive slot")
    return _replace_entry(snapshot, index, replace(entry, custody=custody))


def quiesce(snapshot: CustodySnapshot) -> CustodySnapshot:
    if snapshot.state == "OPEN":
        return replace(snapshot, state="QUIESCING")
    if snapshot.state == "QUIESCING":
        return snapshot
    raise IngressHold("cannot reopen or requiesce drained epoch")


def begin_drain(snapshot: CustodySnapshot) -> CustodySnapshot:
    if snapshot.state not in ("QUIESCING", "DRAINING"):
        raise IngressHold("drain requires winning quiesce fence")
    if any(entry.custody is None for entry in snapshot.entries):
        raise IngressHold("winning receipt token still lacks enumerable successor")
    return replace(snapshot, state="DRAINING")


def selection_deadline(bound: AdmissionBound) -> tuple[int, int]:
    """Independent input is the complete writer-enumerated FIFO snapshot."""
    reserve = bound.reserve
    if (
        not bound.canonical_class_order
        or len(set(bound.canonical_class_order)) != len(bound.canonical_class_order)
        or reserve.source_class not in bound.canonical_class_order
    ):
        raise IngressHold("class order missing, duplicated or mismatched")
    if "ORDINARY" not in bound.canonical_class_order:
        raise IngressHold("ordinary work must participate in bounded fairness")
    if reserve.deficit_cap < reserve.maximum_item_bytes:
        raise IngressHold("deficit cap cannot serve largest admitted item")
    deficit = _checked(bound.initial_deficit)
    if deficit > reserve.deficit_cap:
        raise IngressHold("initial deficit exceeds cap")
    sequence = (*bound.complete_ordered_predecessors, bound.target)
    if len(sequence) > reserve.ready_depth_limit:
        raise IngressHold("ready depth exceeds atomic rebase capacity")
    keys = [(item.durable_admission_commit_seq, item.stable_tie_identity) for item in sequence]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise IngressHold("ready generation FIFO reordered or duplicated")
    visits = 0
    for item in sequence:
        if item.accounted_bytes > reserve.maximum_item_bytes:
            raise IngressHold("item exceeds maximum accounted bytes")
        needed = max(0, item.accounted_bytes - deficit)
        count = max(1, (needed + reserve.byte_quantum - 1) // reserve.byte_quantum)
        visits = _checked(visits + count)
        accrued = _checked(deficit + _checked(count * reserve.byte_quantum))
        deficit = min(reserve.deficit_cap, accrued)
        deficit -= item.accounted_bytes
    slots = _checked(len(bound.canonical_class_order) * visits)
    evaluation_slot = (
        bound.origin_scheduler_slot
        if bound.evaluation_scheduler_slot is None
        else bound.evaluation_scheduler_slot
    )
    if evaluation_slot < bound.origin_scheduler_slot:
        raise IngressHold("bound evaluation cut precedes immutable admission origin")
    deadline = _checked(evaluation_slot + slots)
    return deadline, deficit


def validate_reserve_accounting(
    reserve: ClassReserve,
    items: int,
    byte_count: int,
    quarantine_count: int,
) -> None:
    if (
        _checked(items) > reserve.physical_item_reserve
        or _checked(byte_count) > reserve.physical_byte_reserve
        or _checked(quarantine_count) > reserve.physical_quarantine_reserve
    ):
        raise IngressHold("class cannot borrow another class physical reserve")


def block_and_rebase(
    ready_fifo: tuple[ReadyGeneration, ...],
    complete_bounds: tuple[AdmissionBound, ...],
    blocked_head: Head,
    current_deficit: int,
    consumed_skip_slot: int,
) -> BlockedRebase:
    """Propose one complete head-block plus descendant rebase atomic batch.

    The writer supplies all ready heads/bounds, proves the head is no longer
    dependency-ready and that this is its exact next class visit. No subset may
    be materialized. Replacement origin/lineage/deadline remain immutable.
    """
    if not ready_fifo or len(ready_fifo) != len(complete_bounds):
        raise IngressHold("block rebase requires complete ready FIFO and bound inventory")
    first = complete_bounds[0]
    reserve = first.reserve
    if ready_fifo[0].exact_head != blocked_head:
        raise IngressConflict("blocking another generation or stale head")
    if len(ready_fifo) > reserve.ready_depth_limit:
        raise IngressHold("complete rebase exceeds admitted depth")
    keys = [(item.durable_admission_commit_seq, item.stable_tie_identity) for item in ready_fifo]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise IngressHold("noncanonical or duplicated ready FIFO")
    initial_bytes = sum(len(bound.model_dump_json().encode()) for bound in complete_bounds)
    if initial_bytes > reserve.descendant_snapshot_byte_limit:
        raise IngressHold("complete existing snapshots exceed rebase budget")
    deficit = _checked(current_deficit)
    if deficit > reserve.deficit_cap:
        raise IngressHold("current deficit exceeds cap")
    after_skip_deficit = min(reserve.deficit_cap, _checked(deficit + reserve.byte_quantum))
    after_skip_slot = _checked(_checked(consumed_skip_slot) + 1)
    replacements: list[AdmissionBound] = []
    closed: list[Head] = []
    seen_bounds: set[str] = set()
    for index, (generation, bound) in enumerate(zip(ready_fifo, complete_bounds, strict=True)):
        if (
            bound.target != generation
            or bound.complete_ordered_predecessors != ready_fifo[:index]
            or bound.reserve != reserve
            or bound.epoch != first.epoch
            or bound.canonical_class_order != first.canonical_class_order
            or bound.fairness_version != first.fairness_version
            or bound.formula_version != first.formula_version
            or bound.bound_id in seen_bounds
        ):
            raise IngressHold("bound inventory omitted, reordered, duplicated or stale")
        seen_bounds.add(bound.bound_id)
        old_fingerprint = sha256(bound.model_dump_json().encode()).hexdigest()
        closed.append(
            Head(
                identity=bound.bound_id,
                head=bound.bound_id,
                fingerprint=old_fingerprint,
            )
        )
        if index == 0:
            continue
        remaining = bound.model_copy(
            update={
                "complete_ordered_predecessors": ready_fifo[1:index],
                "initial_deficit": after_skip_deficit,
                "evaluation_scheduler_slot": after_skip_slot,
            }
        )
        remaining_deadline, _ = selection_deadline(remaining)
        if remaining_deadline > bound.absolute_selection_deadline_slot:
            raise IngressHold("blocked-prefix rebase would extend original absolute deadline")
        replacement_id = (
            "rebase:"
            + sha256(
                json.dumps(
                    [old_fingerprint, blocked_head.model_dump(mode="json"), consumed_skip_slot],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        replacements.append(remaining.model_copy(update={"bound_id": replacement_id}))
    output_bytes = sum(len(bound.model_dump_json().encode()) for bound in replacements)
    if output_bytes > reserve.descendant_snapshot_byte_limit:
        raise IngressHold("complete replacement snapshots exceed atomic rebase budget")
    fingerprint = sha256(
        json.dumps(
            [
                "chiplog.ingress.block-rebase.v1",
                blocked_head.model_dump(mode="json"),
                consumed_skip_slot,
                after_skip_deficit,
                [head.model_dump(mode="json") for head in closed],
                [bound.model_dump(mode="json") for bound in replacements],
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    proposal = BlockedRebase(
        blocked_generation=ready_fifo[0],
        blocked_head=blocked_head,
        consumed_skip_slot=consumed_skip_slot,
        resulting_deficit=after_skip_deficit,
        closed_descendant_bounds=tuple(closed),
        replacement_descendant_bounds=tuple(replacements),
        batch_fingerprint=fingerprint,
    )
    if len(proposal.model_dump_json().encode()) > reserve.descendant_snapshot_byte_limit:
        raise IngressHold("whole block-and-rebase proposal exceeds atomic byte budget")
    return proposal


def complete_poll_page(
    manifest: PollPageManifest,
    ordered_dispositions: tuple[PollMemberDisposition, ...],
) -> bytes:
    """Candidate cursor bytes only; authorization/application require writer CAS.

    The broker independently authenticates raw-page framing before constructing
    the manifest and verifies each durable member disposition's complete head.
    """
    if (
        manifest.authentication.kind != "PROVIDER_TRANSPORT"
        or manifest.authentication.source.source_class not in ("TELEGRAM_POLL", "PROVIDER_POLL")
    ):
        raise IngressHold("poll page requires its registered provider polling authentication")
    if manifest.raw_page_digest != manifest.authentication.raw_digest:
        raise IngressHold("raw page digest differs from its authentication binding")
    members = manifest.complete_ordered_members
    if len({member.identity for member in members}) != len(members):
        raise IngressHold("raw page manifest repeats a member identity")
    if tuple(item.member for item in ordered_dispositions) != members:
        raise IngressHold("poll cursor requires every exact ordered raw-page member disposition")
    return manifest.candidate_cursor


@dataclass(frozen=True)
class QuarantineState:
    custody_head: Head
    custody: QuarantineCustody
    current_head: Head
    selected_attempt: tuple[str, str, str] | None = None
    result: QuarantineResult | None = None
    last_completed_attempt: tuple[str, str, str] | None = None
    selected_retry_head: Head | None = None
    completed_attempt_ids: tuple[str, ...] = ()


def custody_successor_head(custody: CustodyState) -> Head:
    fingerprint = sha256(custody.model_dump_json().encode()).hexdigest()
    return Head(
        identity=custody.token.identity, head="custody:" + fingerprint, fingerprint=fingerprint
    )


def quarantine_genesis(custody_head: Head, custody: QuarantineCustody) -> QuarantineState:
    if custody_head != custody_successor_head(custody):
        raise IngressConflict("quarantine genesis does not bind exact custody successor")
    if sha256(custody.raw_bytes).hexdigest() != custody.raw_digest:
        raise IngressHold("quarantine raw bytes mismatch")
    if custody.authentication.raw_digest != custody.raw_digest:
        raise IngressHold("quarantine authentication bound to different raw bytes")
    fingerprint = sha256(
        json.dumps(
            [
                "chiplog.ingress.quarantine-genesis.v1",
                custody_head.model_dump(mode="json"),
                custody.model_dump(mode="json"),
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    head = Head(
        identity=custody_head.identity,
        head="quarantine-retry:" + fingerprint,
        fingerprint=fingerprint,
    )
    return QuarantineState(custody_head, custody, head)


def select_quarantine_parser(
    state: QuarantineState,
    expected_retry_head: Head,
    attempt_id: str,
    parser_id: str,
    parser_version: str,
) -> QuarantineState:
    if state.result is not None and state.result.kind != "RETRY_OR_HOLD":
        raise IngressConflict("quarantine terminal cannot start another parser")
    candidate = (attempt_id, parser_id, parser_version)
    if not all(candidate):
        raise IngressHold("parser attempt identity/version missing")
    if state.selected_attempt is not None:
        if state.selected_attempt == candidate and state.selected_retry_head == expected_retry_head:
            return state
        raise IngressConflict("rival parser already selected")
    if expected_retry_head != state.current_head:
        raise IngressConflict("parser selected from stale quarantine retry head")
    if attempt_id in state.completed_attempt_ids:
        raise IngressConflict("parser attempt identity cannot be reused")
    fingerprint = sha256(
        json.dumps(
            [
                "chiplog.ingress.quarantine-select.v1",
                expected_retry_head.model_dump(mode="json"),
                candidate,
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    selected_head = Head(
        identity=state.custody_head.identity,
        head="parser-selected:" + fingerprint,
        fingerprint=fingerprint,
    )
    return replace(
        state,
        selected_attempt=candidate,
        selected_retry_head=expected_retry_head,
        current_head=selected_head,
    )


def finish_quarantine_parser(
    state: QuarantineState,
    attempt: tuple[str, str, str],
    result: QuarantineResult,
) -> QuarantineState:
    """Broker checks registered parser/proof kind; unknown/exception must RETRY."""
    if state.selected_attempt is None:
        if state.last_completed_attempt == attempt and state.result == result:
            return state
        raise IngressConflict("parser result has no exact selected attempt")
    if state.selected_attempt != attempt or result.custody != state.custody_head:
        raise IngressConflict("parser attempt or original custody substitution")
    if result.kind == "MATERIALIZED":
        expected_inbox = (
            "quarantine-inbox:"
            + sha256(
                json.dumps(
                    [
                        "chiplog.ingress.quarantine-inbox.v1",
                        state.custody_head.model_dump(mode="json"),
                    ],
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
        )
        if (
            result.stable_inbox_id != expected_inbox
            or result.exact_inbox_head.identity != expected_inbox
        ):
            raise IngressConflict("parser fabricated a different stable inbox subject")
    fingerprint = sha256(
        json.dumps(
            [
                "chiplog.ingress.quarantine-result.v1",
                state.current_head.model_dump(mode="json"),
                attempt,
                result.model_dump(mode="json"),
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    head = Head(
        identity=state.custody_head.identity,
        head="quarantine-result:" + fingerprint,
        fingerprint=fingerprint,
    )
    return replace(
        state,
        current_head=head,
        selected_attempt=None,
        result=result,
        last_completed_attempt=attempt,
        selected_retry_head=None,
        completed_attempt_ids=(*state.completed_attempt_ids, attempt[0]),
    )


@dataclass(frozen=True)
class DrainQueueEntry:
    source_class: str
    generation: ReadyGeneration
    token_id: str | None
    blocked_head: Head | None = None


@dataclass(frozen=True)
class DrainDeficit:
    source_class: str
    head: Head
    value: int


@dataclass(frozen=True)
class DrainInventory:
    custody: CustodySnapshot
    epoch: Head
    canonical_reserves: tuple[ClassReserve, ...]
    ready: tuple[DrainQueueEntry, ...]
    blocked: tuple[DrainQueueEntry, ...]
    bounds: tuple[AdmissionBound, ...]
    deficits: tuple[DrainDeficit, ...]
    scheduler_trace: tuple[Head, ...]
    quarantines: tuple[QuarantineState, ...]
    # Each selected parser has its exact durable remainder and old producer fence.
    parser_remainder_fences: tuple[tuple[Head, Head], ...]
    # Terminal work dispositions, independently enumerated by the broker.
    settled_tokens: tuple[tuple[Head, Head], ...]
    physical_occupancy: tuple[Head, ...] = ()


@dataclass(frozen=True)
class DrainQuiescence:
    epoch: Head
    fence: int
    complete_inventory_fingerprint: str
    issued_proof: Head


def drain_inventory_fingerprint(inventory: DrainInventory) -> str:
    """Exact deterministic domain over all restart-relevant state, before proof."""
    custody = inventory.custody
    value = {
        "domain": "chiplog.ingress.complete-drain.v1",
        "epoch": inventory.epoch.model_dump(mode="json"),
        "custody_epoch": [custody.epoch_id, custody.epoch_head, custody.fence, custody.state],
        "tokens": [
            [
                entry.token.model_dump(mode="json"),
                entry.mode,
                entry.retention_proof.model_dump(mode="json") if entry.retention_proof else None,
                entry.state_head.model_dump(mode="json"),
                entry.staged_bytes.hex() if entry.staged_bytes is not None else None,
                entry.staged_digest,
                entry.custody.model_dump(mode="json") if entry.custody else None,
            ]
            for entry in custody.entries
        ],
        "reserves": [row.model_dump(mode="json") for row in inventory.canonical_reserves],
        "queues": [
            [
                [
                    row.source_class,
                    row.generation.model_dump(mode="json"),
                    row.token_id,
                    row.blocked_head.model_dump(mode="json") if row.blocked_head else None,
                ]
                for row in queue
            ]
            for queue in (inventory.ready, inventory.blocked)
        ],
        "bounds": [row.model_dump(mode="json") for row in inventory.bounds],
        "deficits": [
            [row.source_class, row.head.model_dump(mode="json"), row.value]
            for row in inventory.deficits
        ],
        "trace": [head.model_dump(mode="json") for head in inventory.scheduler_trace],
        "quarantines": [
            [
                row.custody_head.model_dump(mode="json"),
                row.custody.model_dump(mode="json"),
                row.current_head.model_dump(mode="json"),
                row.selected_attempt,
                row.result.model_dump(mode="json") if row.result else None,
                row.last_completed_attempt,
                row.selected_retry_head.model_dump(mode="json")
                if row.selected_retry_head
                else None,
                row.completed_attempt_ids,
            ]
            for row in inventory.quarantines
        ],
        "parser_fences": [
            [head.model_dump(mode="json"), fence.model_dump(mode="json")]
            for head, fence in inventory.parser_remainder_fences
        ],
        "settled": [
            [head.model_dump(mode="json"), result.model_dump(mode="json")]
            for head, result in inventory.settled_tokens
        ],
        "physical_occupancy": [
            head.model_dump(mode="json") for head in inventory.physical_occupancy
        ],
    }
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def close_drain(inventory: DrainInventory, quiescence: DrainQuiescence) -> DrainManifest:
    """Complete pure CLOSED proposal; broker must re-enumerate and authenticate.

    No caller inventory or proof reference itself establishes quiescence. Actual
    producer fencing, raw custody and storage equality are checked at writer CAS.
    """
    snapshot = inventory.custody
    fingerprint = drain_inventory_fingerprint(inventory)
    if snapshot.state != "DRAINING" or (inventory.epoch.identity, inventory.epoch.head) != (
        snapshot.epoch_id,
        snapshot.epoch_head,
    ):
        raise IngressHold("complete drain requires exact DRAINING epoch")
    if (
        quiescence.epoch != inventory.epoch
        or quiescence.fence != snapshot.fence
        or quiescence.complete_inventory_fingerprint != fingerprint
    ):
        raise IngressConflict("producer proof does not bind complete exact drain inventory")
    classes = tuple(row.source_class for row in inventory.canonical_reserves)
    expected_classes = {
        "TELEGRAM_PUSH",
        "TELEGRAM_POLL",
        "CLI",
        "PROVIDER_CALLBACK",
        "PROVIDER_POLL",
        "RECONCILIATION",
        "TOOL_RESULT",
        "ORDINARY",
    }
    if len(set(classes)) != len(classes) or set(classes) != expected_classes:
        raise IngressHold("drain omits or duplicates a registered class reserve")
    if tuple(row.source_class for row in inventory.deficits) != classes:
        raise IngressHold("drain deficit inventory differs from canonical class order")
    tokens = {row.token.token_id: row for row in snapshot.entries}
    if len(tokens) != len(snapshot.entries) or any(row.custody is None for row in tokens.values()):
        raise IngressHold("token successor inventory incomplete or duplicated")
    for row in tokens.values():
        if (
            row.token.source.admission_epoch != inventory.epoch
            or row.token.source.admission_fence != snapshot.fence
        ):
            raise IngressConflict("token belongs to another admission epoch or fence")
        if row.custody is None or row.custody.token != row.state_head:
            raise IngressConflict("custody successor token head mismatch")
        if (
            row.staged_bytes is not None
            and sha256(row.staged_bytes).hexdigest() != row.staged_digest
        ):
            raise IngressConflict("restart raw bytes differ from staged digest")
        if row.custody.kind in ("ADMITTED_DURABLE", "QUARANTINED_RAW_EVIDENCE") and (
            row.custody.raw_bytes != row.staged_bytes
            or row.custody.raw_digest != row.staged_digest
            or row.custody.authentication.raw_digest != row.staged_digest
            or row.custody.authentication.source != row.token.source
        ):
            raise IngressConflict("drain lost immutable local custody bytes or authentication")
    queue = (*inventory.ready, *inventory.blocked)
    generation_keys = [
        (row.generation.durable_admission_commit_seq, row.generation.stable_tie_identity)
        for row in queue
    ]
    if len(set(generation_keys)) != len(generation_keys):
        raise IngressHold("ready and blocked generations overlap or duplicate")
    if any(row.blocked_head is not None for row in inventory.ready) or any(
        row.blocked_head is None for row in inventory.blocked
    ):
        raise IngressHold("ready/blocked state encoded incorrectly")
    accounted: list[str] = []
    for queue_row in queue:
        if queue_row.source_class not in classes:
            raise IngressHold("unregistered queue class")
        if queue_row.source_class == "ORDINARY":
            if queue_row.token_id is not None:
                raise IngressHold("ordinary work cannot borrow an evidence token")
        else:
            token = tokens.get(queue_row.token_id or "")
            if token is None or token.token.source.source_class != queue_row.source_class:
                raise IngressHold("queue token missing or borrowed from another class")
            if token.custody is None or token.custody.kind != "ADMITTED_DURABLE":
                raise IngressHold("ready/blocked work lacks admitted durable custody")
            if queue_row.generation.exact_head != token.custody.inbox:
                raise IngressConflict("ready/blocked generation substitutes admitted inbox head")
            accounted.append(token.token.token_id)
    for head, _ in inventory.settled_tokens:
        token = tokens.get(head.identity)
        if token is None or head != token.state_head:
            raise IngressHold("settled token missing or bound to another state head")
        accounted.append(head.identity)
    parser_heads: list[Head] = []
    for state in inventory.quarantines:
        token = tokens.get(state.custody.token.identity)
        if (
            token is None
            or token.custody != state.custody
            or state.custody_head != custody_successor_head(state.custody)
        ):
            raise IngressHold("quarantine lineage lacks exact immutable token custody")
        accounted.append(token.token.token_id)
        if state.selected_attempt is not None:
            parser_heads.append(state.current_head)
    if tuple(head for head, _ in inventory.parser_remainder_fences) != tuple(parser_heads):
        raise IngressHold("selected parser remainder lacks exact durable producer fence")
    expected_attempts = tuple(
        state.selected_attempt[0]
        for state in inventory.quarantines
        if state.selected_attempt is not None
    )
    if tuple(fence.identity for _, fence in inventory.parser_remainder_fences) != expected_attempts:
        raise IngressHold("producer fence belongs to another selected parser attempt")
    if len(accounted) != len(tokens) or set(accounted) != set(tokens):
        raise IngressHold("drain work/token bidirectional join incomplete or duplicated")
    occupied_ids = [head.identity for head in inventory.physical_occupancy]
    if len(set(occupied_ids)) != len(occupied_ids):
        raise IngressHold("duplicate physical reserve occupancy")
    for head in inventory.physical_occupancy:
        if head.identity not in tokens or tokens[head.identity].state_head != head:
            raise IngressConflict("physical occupancy refers to another token state")
    active_ids = {item.token_id for item in queue if item.token_id is not None}
    active_ids.update(state.custody.token.identity for state in inventory.quarantines)
    if not active_ids.issubset(set(occupied_ids)):
        raise IngressHold("active durable evidence lacks physical reserve occupancy")
    bound_targets = tuple(bound.target for bound in inventory.bounds)
    if bound_targets != tuple(row.generation for row in inventory.ready):
        raise IngressHold("complete ready-bound join mismatched or blocked bound still running")
    for reserve, deficit in zip(inventory.canonical_reserves, inventory.deficits, strict=True):
        class_ready = tuple(
            row.generation for row in inventory.ready if row.source_class == reserve.source_class
        )
        keys = [(row.durable_admission_commit_seq, row.stable_tie_identity) for row in class_ready]
        if keys != sorted(keys) or _checked(deficit.value) > reserve.deficit_cap:
            raise IngressHold("noncanonical FIFO or deficit outside cap")
        class_tokens = [
            tokens[token_id]
            for token_id in occupied_ids
            if tokens[token_id].token.source.source_class == reserve.source_class
        ]
        ordinary = (
            [item for item in queue if item.source_class == "ORDINARY"]
            if (reserve.source_class == "ORDINARY")
            else []
        )
        validate_reserve_accounting(
            reserve,
            len(class_tokens) + len(ordinary),
            sum(len(row.staged_bytes or b"") for row in class_tokens)
            + sum(item.generation.accounted_bytes for item in ordinary),
            sum(
                row.custody is not None and row.custody.kind == "QUARANTINED_RAW_EVIDENCE"
                for row in class_tokens
            ),
        )
        for index, generation in enumerate(class_ready):
            bound = next(item for item in inventory.bounds if item.target == generation)
            if (
                bound.epoch != inventory.epoch
                or bound.reserve != reserve
                or bound.canonical_class_order != classes
                or bound.complete_ordered_predecessors != class_ready[:index]
            ):
                raise IngressHold("drain bound refers to stale prefix/epoch/reserve")
            remaining_deadline, _ = selection_deadline(bound)
            if remaining_deadline > bound.absolute_selection_deadline_slot:
                raise IngressHold("drain bound cannot meet its immutable absolute deadline")
    return DrainManifest(
        epoch=inventory.epoch,
        state="CLOSED",
        fence=snapshot.fence,
        canonical_reserves=inventory.canonical_reserves,
        scheduler_trace=inventory.scheduler_trace,
        all_tokens=tuple(row.state_head for row in snapshot.entries),
        all_successors=tuple(
            custody_successor_head(row.custody)
            for row in snapshot.entries
            if row.custody is not None
        ),
        ready_fifo=tuple(row.generation for row in inventory.ready),
        blocked_holds=tuple(
            row.blocked_head for row in inventory.blocked if row.blocked_head is not None
        ),
        bound_snapshots=inventory.bounds,
        deficit_heads=tuple(row.head for row in inventory.deficits),
        producer_quiescence=quiescence.issued_proof,
        complete_inventory_fingerprint=fingerprint,
        quarantine_heads=tuple(row.current_head for row in inventory.quarantines),
        parser_remainder_fences=inventory.parser_remainder_fences,
        settled_token_heads=inventory.settled_tokens,
        physical_occupancy=inventory.physical_occupancy,
        ready_token_ids=tuple(row.token_id for row in inventory.ready),
        blocked_token_ids=tuple(row.token_id for row in inventory.blocked),
    )
