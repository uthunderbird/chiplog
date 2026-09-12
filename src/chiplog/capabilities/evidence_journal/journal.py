"""Owner-authoritative journal seed. No Plan port or provider authority is available."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import Literal

from .commands import (
    Claim,
    Command,
    ConfirmationIngress,
    Display,
    IdentityPort,
    JournalPersistence,
    JournalQuery,
    JournalRecord,
    JournalRow,
    JournalView,
    Outcome,
    Snapshot,
    TrustedIngress,
)


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def claim_digest(claim: Claim) -> str:
    return digest(claim.model_dump(mode="json", exclude={"envelope"}))


def display_digest(display: Display) -> str:
    return digest(display.model_dump(mode="json", exclude={"digest"}))


_PROOFS = (
    "authenticated_speaker",
    "principal_endorser",
    "single_complete_span",
    "declarative",
    "affirmative",
    "actual",
    "not_question",
    "not_request",
    "not_command",
    "not_wish",
    "not_doubt",
    "not_conditional",
    "not_hypothetical",
    "not_quote_or_mention",
    "not_third_party",
    "not_correction_or_retraction",
    "not_negation",
    "exact_subject",
    "exact_payload",
    "exact_provenance",
    "no_inference_split_merge_or_consequence",
)


def positive_proof(claim: Claim, identity: TrustedIngress) -> tuple[tuple[str, bool], ...]:
    """Closed whole-span parser: unsupported syntax makes every predicate false."""
    valid = False
    if len(claim.provenance) == 1:
        item = claim.provenance[0]
        match = re.fullmatch(
            r"Я выполнил occurrence:([A-Za-z0-9_-]+) (\d{4}-\d{2}-\d{2})\.", item.utterance
        )
        if match is not None:
            try:
                date.fromisoformat(match[2])
                valid = (
                    item in identity.items
                    and item.source == "principal"
                    and item.digest == hashlib.sha256(item.utterance.encode()).hexdigest()
                    and claim.subject.identity == match[1]
                    and claim.payload.date == match[2]
                    and claim.payload.outcome == "completed"
                )
            except ValueError:
                pass
    return tuple((predicate, valid) for predicate in _PROOFS)


def _disclosed(claim: Claim, identity: TrustedIngress) -> bool:
    envelope = claim.envelope
    sources = envelope.sources
    if (
        envelope.tenant_id != identity.tenant
        or envelope.content_digest != claim_digest(claim)
        or envelope.policy_head != identity.heads.policy
        or envelope.contour_head != identity.heads.contour
        or envelope.deletion_fence_head != identity.heads.deletion
        or not claim.provenance
        or not sources
        or len(sources) != len(set(source.record_id for source in sources))
        or tuple(sorted(sources, key=lambda x: x.record_id)) != sources
        or {p.ingress_id for p in claim.provenance} != {s.record_id for s in sources}
    ):
        return False
    allowed: set[str] | None = None
    denied = False
    for source in sources:
        if source not in identity.sources or source.tenant_id != identity.tenant:
            return False
        matching = [p for p in claim.provenance if p.ingress_id == source.record_id]
        if len(matching) != 1 or matching[0] not in identity.items:
            return False
        if source.content_digest != matching[0].digest or source.record_version != str(
            matching[0].ingress_version
        ):
            return False
        label = source.label
        if tuple(sorted(set(label.allowed_endpoints))) != label.allowed_endpoints:
            return False
        if label.value == "DENY_ALL":
            denied = True
        elif label.value == "ENDPOINT_RESTRICTED":
            allowed = (
                set(label.allowed_endpoints)
                if allowed is None
                else allowed & set(label.allowed_endpoints)
            )
        elif label.allowed_endpoints:
            return False
    expected = (
        "DENY_ALL"
        if denied or allowed == set()
        else "UNRESTRICTED"
        if allowed is None
        else "ENDPOINT_RESTRICTED"
    )
    if envelope.label.value != expected or envelope.label.allowed_endpoints != tuple(
        sorted(allowed or ())
    ):
        return False
    return expected != "DENY_ALL" and (allowed is None or identity.endpoint in allowed)


class Journal:
    def __init__(self, persistence: JournalPersistence, identities: IdentityPort) -> None:
        self._store = persistence
        self._identities = identities

    def _identity(self, peer: str, command: Command) -> TrustedIngress | None:
        identity = self._identities.authenticate(peer)
        if identity is None or (identity.tenant, identity.principal) != (
            command.tenant,
            command.principal,
        ):
            return None
        return identity

    def _guard(
        self, peer: str, command: Command
    ) -> Literal["DENIED", "STALE", "INDETERMINATE"] | None:
        identity = self._identity(peer, command)
        if identity is None:
            return "DENIED"
        if identity.heads != command.heads:
            return "STALE"
        if not _disclosed(command.claim, identity):
            return "DENIED"
        if (
            command.action == "confirm_candidate"
            and ConfirmationIngress(
                confirmation_id=command.command_id,
                binding_digest=digest(command.model_dump(mode="json")),
            )
            not in identity.confirmations
        ):
            return "DENIED"
        return None

    @staticmethod
    def _replay(snapshot: Snapshot, command: Command) -> Outcome | None:
        found = next((r for r in snapshot.records if r.command_id == command.command_id), None)
        if found is None:
            return None
        if found.fingerprint != digest(command.model_dump(mode="json")):
            return Outcome(disposition="CONFLICT")
        return Outcome(disposition="REPLAY", record=found)

    @staticmethod
    def _target(snapshot: Snapshot, command: Command) -> JournalRecord | None:
        target = next(
            (
                r
                for r in snapshot.records
                if r.record_id == command.predecessor
                and r.kind != "CandidateEvidence"
                and r.principal == command.principal
            ),
            None,
        )
        if target is None:
            return None
        if any(
            r.predecessor == target.record_id and r.kind == "ClaimDisposition"
            for r in snapshot.records
        ):
            return None
        return target

    async def prepare(self, peer: str, command: Command) -> Outcome:
        try:
            command = Command.model_validate_json(command.model_dump_json(warnings=False))
        except ValueError, TypeError:
            return Outcome(disposition="DENIED", reason="invalid command schema")
        identity = self._identity(peer, command)
        if identity is None or not _disclosed(command.claim, identity):
            return Outcome(disposition="DENIED")
        snapshot = self._store.snapshot(command.tenant)
        replay = self._replay(snapshot, command)
        if replay is not None:
            return replay
        guard = self._guard(peer, command)
        if guard is not None:
            return Outcome(disposition=guard)
        if snapshot.head != command.heads.journal:
            return Outcome(disposition="STALE")
        if command.display is not None or command.action == "confirm_candidate":
            return Outcome(disposition="DENIED")
        if not self._valid_supersession(snapshot, command):
            return Outcome(disposition="DENIED", reason="foreign or invalid candidate supersession")
        target = self._target(snapshot, command)
        if command.action != "record_fact" and target is None:
            return Outcome(disposition="STALE")
        if target is not None and target.claim.subject != command.claim.subject:
            return Outcome(disposition="DENIED", reason="correction cannot replace subject")
        operation: Literal["RECORD", "CORRECT", "RETRACT"] = (
            "CORRECT"
            if command.action == "correct_claim"
            else "RETRACT"
            if command.action == "retract_claim"
            else "RECORD"
        )
        candidate_id = "journal:candidate:" + digest([command.tenant, command.command_id])
        # Candidate publication advances the complete tenant head by exactly one.
        heads = command.heads.model_copy(update={"journal": snapshot.head + 1})
        display = Display(
            tenant=command.tenant,
            principal=command.principal,
            candidate_id=candidate_id,
            candidate_version=1,
            interpretation=canonical(command.claim.model_dump(mode="json")).decode(),
            claim=command.claim,
            consequence="Journal " + operation + "; Plan unchanged",
            heads=heads,
            predecessor=command.predecessor,
            operation=operation,
            digest="",
            supersedes=command.supersedes,
        )
        display = display.model_copy(update={"digest": display_digest(display)})
        record = JournalRecord(
            record_id=candidate_id,
            tenant=command.tenant,
            principal=command.principal,
            kind="CandidateEvidence",
            family=target.family if target else candidate_id,
            predecessor=command.predecessor,
            operation="CANDIDATE",
            claim=command.claim,
            display=display,
            command_id=command.command_id,
            fingerprint=digest(command.model_dump(mode="json")),
            positive_proof=positive_proof(command.claim, identity)
            if command.action == "record_fact"
            else (),
        )
        result = await self._store.publish(
            record, snapshot.head, lambda: self._guard(peer, command)
        )
        return Outcome(
            disposition=result, record=record if result in {"COMMITTED", "REPLAY"} else None
        )

    @staticmethod
    def _valid_supersession(snapshot: Snapshot, command: Command) -> bool:
        if tuple(sorted(set(command.supersedes))) != command.supersedes:
            return False
        eligible = {
            record.record_id
            for record in snapshot.records
            if record.kind == "CandidateEvidence"
            and record.principal == command.principal
            and record.claim.subject == command.claim.subject
            and record.predecessor == command.predecessor
        }
        return set(command.supersedes) <= eligible

    async def execute(self, peer: str, command: Command) -> Outcome:
        try:
            command = Command.model_validate_json(command.model_dump_json(warnings=False))
        except ValueError, TypeError:
            return Outcome(disposition="DENIED", reason="invalid command schema")
        identity = self._identity(peer, command)
        if identity is None or not _disclosed(command.claim, identity):
            return Outcome(disposition="DENIED")
        snapshot = self._store.snapshot(command.tenant)
        replay = self._replay(snapshot, command)
        if replay is not None:
            return replay
        guard = self._guard(peer, command)
        if guard is not None:
            return Outcome(disposition=guard)
        if snapshot.head != command.heads.journal:
            return Outcome(disposition="STALE")
        if not self._valid_supersession(snapshot, command):
            return Outcome(disposition="DENIED", reason="foreign or invalid candidate supersession")
        target = self._target(snapshot, command)
        operation: Literal["RECORD", "CORRECT", "RETRACT"] = "RECORD"
        if (
            command.action == "record_fact"
            and command.display is None
            and command.predecessor is None
        ):
            if not all(value for _, value in positive_proof(command.claim, identity)):
                return Outcome(disposition="NEEDS_CONFIRMATION")
        elif command.action == "confirm_candidate":
            display = command.display
            if display is None or display.digest != display_digest(display):
                return Outcome(disposition="DENIED")
            candidate = next(
                (
                    r
                    for r in snapshot.records
                    if r.record_id == display.candidate_id and r.kind == "CandidateEvidence"
                ),
                None,
            )
            if (
                candidate is None
                or candidate.principal != command.principal
                or candidate.display != display
                or display.claim != command.claim
                or display.heads != command.heads
                or display.predecessor != command.predecessor
                or (display.tenant, display.principal) != (command.tenant, command.principal)
            ):
                return Outcome(disposition="STALE")
            rivals = [
                r
                for r in snapshot.records
                if r.kind == "CandidateEvidence"
                and r.principal == command.principal
                and r.predecessor == candidate.predecessor
                and r.claim.subject == candidate.claim.subject
            ]
            consumed = {
                r.display.candidate_id
                for r in snapshot.records
                if r.principal == command.principal
                and r.kind != "CandidateEvidence"
                and r.display is not None
            }
            consumed.update(
                candidate_id
                for r in snapshot.records
                if r.principal == command.principal
                and r.kind != "CandidateEvidence"
                and r.display is not None
                for candidate_id in r.display.supersedes
            )
            rivals = [r for r in rivals if r.record_id not in consumed]
            other_ids = {r.record_id for r in rivals if r.record_id != candidate.record_id}
            if (
                tuple(sorted(set(display.supersedes))) != display.supersedes
                or command.supersedes != display.supersedes
                or set(display.supersedes) != other_ids
            ):
                return Outcome(disposition="UNRESOLVED")
            operation = display.operation
        else:
            return Outcome(disposition="NEEDS_CONFIRMATION")
        if operation != "RECORD":
            if target is None:
                return Outcome(disposition="STALE")
            if target.claim.subject != command.claim.subject:
                return Outcome(disposition="DENIED")
        elif command.predecessor is not None:
            return Outcome(disposition="DENIED")
        record_id = "journal:claim:" + digest([command.tenant, command.command_id])
        record = JournalRecord(
            record_id=record_id,
            tenant=command.tenant,
            principal=command.principal,
            kind="FactClaim" if operation == "RECORD" else "ClaimDisposition",
            family=target.family if target else record_id,
            predecessor=command.predecessor,
            operation=operation,
            claim=command.claim,
            display=command.display,
            command_id=command.command_id,
            fingerprint=digest(command.model_dump(mode="json")),
            positive_proof=positive_proof(command.claim, identity)
            if command.action == "record_fact"
            else (),
        )
        result = await self._store.publish(
            record, snapshot.head, lambda: self._guard(peer, command)
        )
        return Outcome(
            disposition=result, record=record if result in {"COMMITTED", "REPLAY"} else None
        )

    @staticmethod
    def _closure(records: tuple[JournalRecord, ...], family: str) -> tuple[JournalRecord, ...]:
        chosen = {r.record_id for r in records if r.family == family}
        while True:
            prior = set(chosen)
            for record in records:
                if record.record_id in chosen and record.display is not None:
                    chosen.add(record.display.candidate_id)
                    chosen.update(record.display.supersedes)
            if chosen == prior:
                break
        return tuple(r for r in records if r.record_id in chosen)

    def lineage(self, peer: str, tenant: str, family: str) -> tuple[JournalRecord, ...]:
        identity = self._identities.authenticate(peer)
        if identity is None or identity.tenant != tenant:
            return ()
        records = self._closure(self._store.snapshot(tenant).records, family)
        if not all(
            r.principal == identity.principal and _disclosed(r.claim, identity) for r in records
        ):
            return ()
        if self._identities.authenticate(peer) != identity:
            return ()
        return records

    def project(self, peer: str, query: JournalQuery) -> JournalView:
        try:
            query = JournalQuery.model_validate_json(query.model_dump_json(warnings=False))
        except ValueError, TypeError:
            return JournalView(disposition="DENIED", head=0)
        identity = self._identities.authenticate(peer)
        if identity is None or identity.tenant != query.tenant:
            return JournalView(disposition="DENIED", head=0)
        snapshot = self._store.snapshot(query.tenant)
        if identity.heads != query.expected_heads or snapshot.head != query.expected_heads.journal:
            return JournalView(disposition="STALE", head=snapshot.head)
        records = snapshot.records
        consumed = {
            r.display.candidate_id
            for r in records
            if r.principal == identity.principal
            and r.kind != "CandidateEvidence"
            and r.display is not None
        }
        consumed.update(
            candidate_id
            for r in records
            if r.principal == identity.principal
            and r.kind != "CandidateEvidence"
            and r.display is not None
            for candidate_id in r.display.supersedes
        )
        superseded = {
            r.predecessor
            for r in records
            if r.principal == identity.principal and r.kind == "ClaimDisposition"
        }
        selected = [
            r
            for r in records
            if r.principal == identity.principal
            and r.record_id not in superseded
            and r.record_id not in consumed
            and (r.kind != "CandidateEvidence" or r.predecessor not in superseded)
            and (query.subject is None or r.claim.subject == query.subject)
        ]
        selected.sort(key=lambda r: r.record_id)
        current = tuple(selected)
        if query.after_id is not None:
            if query.after_id not in {r.record_id for r in selected}:
                return JournalView(disposition="INDETERMINATE", head=snapshot.head)
            selected = [r for r in selected if r.record_id > query.after_id]
        result: list[JournalRow] = []
        for record in selected[: query.max_rows]:
            lineage = self._closure(records, record.family)
            # The numeric bound covers the full content closure, not only roots.
            if sum(len(row.lineage) for row in result) + len(lineage) > query.max_rows:
                return JournalView(disposition="INDETERMINATE", head=snapshot.head)
            if not all(
                r.principal == identity.principal and _disclosed(r.claim, identity) for r in lineage
            ):
                return JournalView(disposition="DENIED", head=snapshot.head)
            rivals = [
                r
                for r in current
                if r.claim.subject == record.claim.subject
                and (r.kind == "CandidateEvidence") == (record.kind == "CandidateEvidence")
            ]
            status: Literal[
                "user reported",
                "provider observed",
                "user confirmed provider observation",
                "disputed",
                "corrected",
                "retracted",
                "unknown",
            ]
            if len(rivals) > 1:
                status = "disputed"
            elif record.operation == "RETRACT":
                status = "retracted"
            elif record.operation == "CORRECT":
                status = "corrected"
            elif record.kind == "CandidateEvidence":
                status = (
                    "provider observed"
                    if any(p.source == "provider" for p in record.claim.provenance)
                    else "unknown"
                )
            elif any(p.source == "provider" for p in record.claim.provenance):
                status = "user confirmed provider observation"
            else:
                status = "user reported"
            result.append(
                JournalRow(
                    record=record,
                    status=status,
                    current_positive=record.kind != "CandidateEvidence"
                    and record.operation != "RETRACT"
                    and status != "disputed",
                    lineage=lineage,
                )
            )
        # Release rechecks independently owned trust and durable head.
        if (
            self._identities.authenticate(peer) != identity
            or self._store.snapshot(query.tenant).head != snapshot.head
        ):
            return JournalView(disposition="STALE", head=snapshot.head)
        cursor = result[-1].record.record_id if result and len(selected) > len(result) else None
        return JournalView(
            disposition="CURRENT", head=snapshot.head, rows=tuple(result), next_cursor=cursor
        )
