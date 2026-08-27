"""Deployment-trust state machine; contains no adapter imports."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, replace
from typing import Literal

from chiplog.domain_primitives import PrincipalId, TenantId

from . import (
    AuthenticationRequest,
    TenantDecisionJournalPort,
    TrustDecision,
    TrustMaterializationPort,
    TrustReference,
    TrustReferenceRevalidation,
)
from ._records import KIND_RECORD_TYPES, SCHEMA_ID


class TrustInvariantError(RuntimeError):
    """A protected lineage or identity invariant failed closed."""


class RecoveryRequired(TrustInvariantError):
    """Only the operator recovery contour may proceed."""


@dataclass(frozen=True)
class DatabaseGenesis:
    tenant_id: str
    database_instance_id: str
    genesis_version: int = 1


@dataclass(frozen=True)
class DeploymentTenantBinding:
    tenant_id: str
    database_instance_id: str
    genesis_digest: str
    journal_identity: str
    journal_epoch: int
    operator_key_id: str
    predecessor: str | None
    signature: str


@dataclass(frozen=True)
class CurrentDatabaseState:
    tenant_id: str
    database_instance_id: str
    trust_head: str
    materialization_head: str
    generation: int
    phase: Literal["BOOTSTRAP_REQUIRED", "ACTIVE", "PREPARED", "READY", "RECOVERY_REQUIRED", "HOLD"]


@dataclass(frozen=True)
class PendingTrustTransition:
    transition_id: str
    kind: Literal["OPERATOR_BINDING_KEY_ROTATION", "JOURNAL_ROOT_ROTATION"]
    predecessor_trust_head: str
    prepared_journal_head: str
    proposed_key_id: str | None
    proposed_epoch: int | None
    phase: Literal["PREPARED", "READY"]


@dataclass(frozen=True)
class TransportOriginWitness:
    witness_id: str
    kind: Literal["TELEGRAM_POLLING", "TELEGRAM_WEBHOOK"]
    tenant_id: str
    candidate_id: str
    bot_account: str
    endpoint: str
    credential_head: str
    session_head: str
    trust_head: str
    materialization_head: str
    raw_digest: str
    replay_identity: str
    broker_signature: str


@dataclass(frozen=True)
class CredentialState:
    credential_id: str
    head: str
    session_id: str
    session_head: str
    revoked: bool = False


@dataclass(frozen=True)
class EvidenceSourceState:
    source_id: str
    head: str
    authenticated_late: bool = False


@dataclass(frozen=True)
class PollCursorState:
    source_id: str
    authorized_cursor: str | None = None
    authorized_page_id: str | None = None
    authorized_sequence: int = 0
    authorization_decision_id: str | None = None
    applied_cursor: str | None = None
    application_decision_id: str | None = None


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _decision_digest(decision: bytes, predecessor: str | None) -> str:
    return _digest((predecessor or "GENESIS").encode() + b"\x00" + decision)


class DeploymentTrustService:
    """Closed single-tenant trust aggregate over two independent durability ports."""

    def __init__(
        self,
        journal: TenantDecisionJournalPort,
        materializer: TrustMaterializationPort,
        *,
        operator_key_id: str,
        operator_secret: bytes,
        broker_secret: bytes,
        journal_identity: str = "tenant-journal-v1",
    ) -> None:
        self._journal = journal
        self._materializer = materializer
        self._operator_key_id = operator_key_id
        self._operator_secret = operator_secret
        self._broker_secret = broker_secret
        self._journal_identity = journal_identity
        self._state: CurrentDatabaseState | None = None
        self._genesis: DatabaseGenesis | None = None
        self._binding: DeploymentTenantBinding | None = None
        self._principal: PrincipalId | None = None
        self._credential: CredentialState | None = None
        self._sources: dict[str, EvidenceSourceState] = {}
        self._witnesses: dict[str, TransportOriginWitness] = {}
        self._witness_decisions: dict[str, str] = {}
        self._consumed_replays: set[str] = set()
        self._poll: dict[str, PollCursorState] = {}
        self._poll_pages: dict[tuple[str, str], tuple[str, tuple[tuple[str, str], ...], str]] = {}
        self._freshness = 0
        self._bootstrap_fingerprint: str | None = None
        self._pending: PendingTrustTransition | None = None

    @property
    def state(self) -> CurrentDatabaseState | None:
        return self._state

    @property
    def genesis(self) -> DatabaseGenesis | None:
        return self._genesis

    @property
    def binding(self) -> DeploymentTenantBinding | None:
        return self._binding

    def _sign(self, payload: bytes, secret: bytes | None = None) -> str:
        return hmac.new(secret or self._operator_secret, payload, hashlib.sha256).hexdigest()

    def _commit(self, kind: str, payload: dict[str, object], *, fault: str = "none") -> str:
        predecessor = self._journal.entries()[-1][0] if self._journal.entries() else None
        envelope = _canonical({"kind": kind, "payload": payload, "predecessor": predecessor})
        if fault == "before_decision":
            raise RuntimeError("fault before decision")
        decision_id = self._journal.append(envelope, predecessor)
        if fault == "after_decision":
            raise RecoveryRequired("decided commit requires replay")
        record_types = KIND_RECORD_TYPES.get(kind, ())
        if not record_types:
            record_types = ("chiplog.deployment_trust.tenant_decision",)
        records = tuple(
            _canonical(
                {
                    "decision_id": decision_id,
                    "operation_kind": kind,
                    "record_type_id": record_type,
                    "schema_id": SCHEMA_ID,
                    **payload,
                }
            )
            for record_type in ("chiplog.deployment_trust.tenant_decision", *record_types)
        )
        self._materializer.materialize(decision_id, records)
        if self._state is not None:
            self._state = replace(self._state, materialization_head=decision_id)
        if fault == "after_materialization":
            raise RecoveryRequired("materialized decision acknowledgement unknown")
        return decision_id

    def initialize(self, tenant_id: str, database_instance_id: str) -> CurrentDatabaseState:
        if self._state is not None:
            raise TrustInvariantError("database already initialized")
        genesis = DatabaseGenesis(tenant_id, database_instance_id)
        genesis_digest = _digest(_canonical(asdict(genesis)))
        unsigned = {
            "tenant_id": tenant_id,
            "database_instance_id": database_instance_id,
            "genesis_digest": genesis_digest,
            "journal_identity": self._journal_identity,
            "journal_epoch": 1,
            "operator_key_id": self._operator_key_id,
            "predecessor": None,
        }
        binding = DeploymentTenantBinding(
            tenant_id,
            database_instance_id,
            genesis_digest,
            self._journal_identity,
            1,
            self._operator_key_id,
            None,
            self._sign(_canonical(unsigned)),
        )
        decision = self._commit(
            "INITIALIZE",
            {"genesis": asdict(genesis), "binding": asdict(binding)},
        )
        self._genesis = genesis
        self._binding = binding
        self._state = CurrentDatabaseState(
            tenant_id,
            database_instance_id,
            _digest(_canonical(asdict(binding))),
            decision,
            1,
            "BOOTSTRAP_REQUIRED",
        )
        return self._state

    def bootstrap(
        self,
        *,
        token_fingerprint: str,
        peer: str,
        expected_peer: str,
        principal_id: PrincipalId,
        credential_id: str,
        session_id: str,
        recovery_verifier: str,
        expires_at: int | None = None,
        now: int | None = None,
    ) -> str:
        if self._bootstrap_fingerprint is not None:
            if self._bootstrap_fingerprint == token_fingerprint:
                assert self._state is not None
                return self._state.materialization_head
            raise TrustInvariantError("bootstrap token conflict")
        if self._state is None or self._state.phase != "BOOTSTRAP_REQUIRED":
            raise TrustInvariantError("bootstrap is not available")
        if peer != expected_peer:
            raise TrustInvariantError("bootstrap peer denied")
        if expires_at is not None and (now is None or now > expires_at):
            raise TrustInvariantError("bootstrap token expired")
        if self._principal is not None:
            raise TrustInvariantError("second principal denied")
        credential = CredentialState(credential_id, "credential:1", session_id, "session:1")
        decision = self._commit(
            "BOOTSTRAP",
            {
                "principal_id": principal_id.value,
                "credential": asdict(credential),
                "recovery_verifier": recovery_verifier,
                "token_fingerprint": token_fingerprint,
            },
        )
        self._principal = principal_id
        self._credential = credential
        self._bootstrap_fingerprint = token_fingerprint
        self._freshness += 1
        self._state = replace(
            self._state, materialization_head=decision, generation=2, phase="ACTIVE"
        )
        return decision

    def register_evidence_source(self, source_id: str) -> EvidenceSourceState:
        self._require_active()
        if source_id in self._sources:
            raise TrustInvariantError("shared or duplicate endpoint denied")
        source = EvidenceSourceState(source_id, f"source:{len(self._sources) + 1}")
        decision = self._commit("REGISTER_EVIDENCE_SOURCE", asdict(source))
        self._sources[source_id] = source
        assert self._state is not None
        self._state = replace(self._state, materialization_head=decision)
        return source

    def rotate_credential(self, expected_head: str, new_credential_id: str) -> CredentialState:
        self._require_active()
        current = self._credential
        if current is None or current.revoked or current.head != expected_head:
            raise TrustInvariantError("stale credential head")
        replacement = CredentialState(
            new_credential_id,
            f"credential:{self._freshness + 1}",
            "",
            f"session:{self._freshness + 1}",
        )
        decision = self._commit("ROTATE_CREDENTIAL", asdict(replacement))
        self._credential = replacement
        self._freshness += 1
        assert self._state is not None
        self._state = replace(self._state, materialization_head=decision)
        return replacement

    def revoke(self, expected_head: str) -> None:
        current = self._credential
        if current is None or current.head != expected_head:
            raise TrustInvariantError("stale credential head")
        decision = self._commit("REVOKE_CREDENTIAL", {"head": expected_head})
        self._credential = replace(current, revoked=True)
        self._freshness += 1
        assert self._state is not None
        self._state = replace(self._state, materialization_head=decision)

    def emergency_recover(
        self,
        *,
        peer: str,
        expected_peer: str,
        recovery_secret: str,
        expected_recovery_verifier: str,
        new_credential_id: str,
    ) -> CredentialState:
        self._require_active()
        if peer != expected_peer or _digest(recovery_secret.encode()) != expected_recovery_verifier:
            raise TrustInvariantError("operator recovery authentication failed")
        if self._principal is None:
            raise TrustInvariantError("immutable principal missing")
        replacement = CredentialState(
            new_credential_id,
            f"credential:{self._freshness + 1}",
            "",
            f"session:{self._freshness + 1}",
        )
        decision = self._commit(
            "EMERGENCY_RECOVERY",
            {"principal_id": self._principal.value, "credential": asdict(replacement)},
        )
        self._credential = replacement
        self._freshness += 1
        assert self._state is not None
        self._state = replace(self._state, materialization_head=decision)
        return replacement

    def issue_transport_witness(
        self,
        *,
        witness_id: str,
        kind: Literal["TELEGRAM_POLLING", "TELEGRAM_WEBHOOK"],
        candidate_id: str,
        bot_account: str,
        endpoint: str,
        raw_bytes: bytes,
        replay_identity: str,
    ) -> TransportOriginWitness:
        self._require_active()
        assert self._state is not None and self._credential is not None
        fields = {
            "witness_id": witness_id,
            "kind": kind,
            "tenant_id": self._state.tenant_id,
            "candidate_id": candidate_id,
            "bot_account": bot_account,
            "endpoint": endpoint,
            "credential_head": self._credential.head,
            "session_head": self._credential.session_head,
            "trust_head": self._state.trust_head,
            "materialization_head": self._state.materialization_head,
            "raw_digest": _digest(raw_bytes),
            "replay_identity": replay_identity,
        }
        witness = TransportOriginWitness(
            witness_id,
            kind,
            self._state.tenant_id,
            candidate_id,
            bot_account,
            endpoint,
            self._credential.head,
            self._credential.session_head,
            self._state.trust_head,
            self._state.materialization_head,
            _digest(raw_bytes),
            replay_identity,
            self._sign(_canonical(fields), self._broker_secret),
        )
        if replay_identity in self._consumed_replays:
            existing = next(
                (
                    item
                    for item in self._witnesses.values()
                    if item.replay_identity == replay_identity
                ),
                None,
            )
            if existing is not None and (
                existing.witness_id == witness_id
                and existing.kind == kind
                and existing.tenant_id == self._state.tenant_id
                and existing.candidate_id == candidate_id
                and existing.bot_account == bot_account
                and existing.endpoint == endpoint
                and existing.credential_head == self._credential.head
                and existing.session_head == self._credential.session_head
                and existing.trust_head == self._state.trust_head
                and existing.raw_digest == _digest(raw_bytes)
            ):
                return existing
            raise TrustInvariantError("transport replay conflict")
        if witness_id in self._witnesses:
            raise TrustInvariantError("transport witness identity conflict")
        decision = self._commit("TRANSPORT_WITNESS", asdict(witness))
        self._witnesses[witness_id] = witness
        self._witness_decisions[witness_id] = decision
        self._consumed_replays.add(replay_identity)
        self._state = replace(self._state, materialization_head=decision)
        return witness

    def authenticate(self, request: AuthenticationRequest) -> TrustDecision:
        if self._state is None:
            return TrustDecision("INDETERMINATE", None, "trust state unavailable")
        if self._state.phase == "HOLD":
            return TrustDecision("INDETERMINATE", None, "future contour prerequisite hold")
        if self._state.phase != "ACTIVE" or self._principal is None or self._credential is None:
            return TrustDecision("DENIED", None, "single-principal contour inactive")
        credential = self._credential
        if credential.revoked or request.credential_id != credential.credential_id:
            return TrustDecision("STALE", None, "credential is not current")
        if request.session_id != credential.session_id:
            return TrustDecision("STALE", None, "session is not current")
        source_head = "local"
        if request.contour == "TELEGRAM":
            witness = self._witnesses.get(request.transport_witness_id or "")
            if (
                witness is None
                or request.source_id != witness.candidate_id
                or witness.credential_head != credential.head
                or witness.session_head != credential.session_head
                or witness.trust_head != self._state.trust_head
                or self._witness_decisions.get(witness.witness_id)
                != self._state.materialization_head
                or not self._valid_witness(witness)
            ):
                return TrustDecision("DENIED", None, "broker transport witness required")
            source_head = _digest(_canonical(asdict(witness)))
        elif request.contour == "EVIDENCE":
            source = self._sources.get(request.source_id)
            if source is None:
                return TrustDecision("STALE", None, "evidence source is not current")
            source_head = source.head
        elif request.contour != "CLI":
            return TrustDecision("DENIED", None, "unsupported contour")
        reference = TrustReference(
            TenantId(self._state.tenant_id),
            self._principal,
            request.contour,
            credential.head,
            credential.session_head,
            source_head,
            self._state.trust_head,
            self._state.materialization_head,
            self._freshness,
        )
        return TrustDecision("VALID", reference, None)

    def revalidate(self, request: TrustReferenceRevalidation) -> TrustDecision:
        reference = request.reference
        if request.operation != "CREATE_INTENTION_LINE":
            return TrustDecision("DENIED", None, "operation is not admitted by trust contour")
        if request.subject_id.tenant_id != reference.tenant_id:
            return TrustDecision("DENIED", None, "foreign subject")
        if not request.subject_id.value:
            return TrustDecision("DENIED", None, "allocation subject is empty")
        witness = next(
            (
                item
                for item in self._witnesses.values()
                if _digest(_canonical(asdict(item))) == reference.source_head
            ),
            None,
        )
        evidence_source = next(
            (item for item in self._sources.values() if item.head == reference.source_head), None
        )
        current = self.authenticate(
            AuthenticationRequest(
                reference.contour,
                self._credential.credential_id if self._credential else "",
                self._credential.session_id if self._credential else "",
                (
                    witness.candidate_id
                    if witness is not None
                    else evidence_source.source_id
                    if evidence_source is not None
                    else ""
                ),
                witness.witness_id if witness is not None else None,
            )
        )
        if current.disposition != "VALID" or current.reference != reference:
            return TrustDecision("STALE", None, "trust reference no longer current")
        return current

    def authorize_poll_cursor(
        self, source_id: str, cursor: str, page_id: str, cursor_sequence: int
    ) -> str:
        self._require_active()
        if source_id not in self._sources:
            raise TrustInvariantError("unknown poll source")
        page = self._poll_pages.get((source_id, page_id))
        if page is None:
            raise TrustInvariantError("poll cursor requires exact durable page disposition")
        if any(disposition == "HELD" for _, disposition in page[1]):
            raise TrustInvariantError("poll cursor requires terminal member dispositions")
        state = self._poll.get(source_id, PollCursorState(source_id))
        if (
            state.authorized_cursor == cursor
            and state.authorized_page_id == page_id
            and state.authorized_sequence == cursor_sequence
            and state.authorization_decision_id is not None
        ):
            return state.authorization_decision_id
        if state.authorized_page_id == page_id:
            raise TrustInvariantError("poll page already authorized a cursor")
        if cursor_sequence != state.authorized_sequence + 1:
            raise TrustInvariantError("poll cursor sequence is not monotonic")
        decision = self._commit(
            "POLL_CURSOR_ADVANCE_AUTHORIZED",
            {
                "source_id": source_id,
                "cursor": cursor,
                "page_id": page_id,
                "cursor_sequence": cursor_sequence,
            },
        )
        self._poll[source_id] = replace(
            state,
            authorized_cursor=cursor,
            authorized_page_id=page_id,
            authorized_sequence=cursor_sequence,
            authorization_decision_id=decision,
        )
        return decision

    def apply_poll_cursor(self, source_id: str, cursor: str) -> str:
        state = self._poll.get(source_id)
        if state is None or state.authorized_cursor != cursor:
            raise TrustInvariantError("poll cursor was not authorized")
        if state.applied_cursor == cursor and state.application_decision_id is not None:
            return state.application_decision_id
        decision = self._commit(
            "POLL_CURSOR_APPLIED",
            {"source_id": source_id, "cursor": cursor, "page_id": state.authorized_page_id},
        )
        self._poll[source_id] = replace(
            state, applied_cursor=cursor, application_decision_id=decision
        )
        return decision

    def may_emit_poll_request(self, source_id: str, cursor: str) -> bool:
        state = self._poll.get(source_id)
        return state is not None and state.applied_cursor == cursor

    def hold_future_contour(self) -> None:
        self._require_active()
        assert self._state is not None
        decision = self._commit(
            "PRINCIPAL_CONTOUR_PREREQUISITE", {"disposition": "HOLD", "version": 1}
        )
        self._state = replace(self._state, materialization_head=decision, phase="HOLD")

    def record_poll_response_page(
        self,
        source_id: str,
        source_head: str,
        page_id: str,
        raw_page: bytes,
        member_dispositions: tuple[tuple[str, str], ...],
    ) -> str:
        self._require_active()
        source = self._sources.get(source_id)
        if source is None or source.head != source_head or not member_dispositions:
            raise TrustInvariantError("poll page requires authenticated source and members")
        if len({member for member, _ in member_dispositions}) != len(member_dispositions):
            raise TrustInvariantError("poll page members must be unique")
        raw_digest = _digest(raw_page)
        if page_id != raw_digest:
            raise TrustInvariantError("poll page identity must equal authenticated raw digest")
        try:
            decoded_page = json.loads(raw_page)
            raw_members = tuple(str(item["id"]) for item in decoded_page["members"])
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise TrustInvariantError("poll raw page membership is invalid") from error
        if raw_members != tuple(member for member, _ in member_dispositions):
            raise TrustInvariantError("poll page disposition membership is incomplete")
        allowed = {"PUBLISHED", "DUPLICATE", "REJECTED", "HELD"}
        if any(disposition not in allowed for _, disposition in member_dispositions):
            raise TrustInvariantError("poll member disposition is not closed")
        existing = self._poll_pages.get((source_id, page_id))
        if existing is not None:
            if existing[:2] == (raw_digest, member_dispositions):
                return existing[2]
            raise TrustInvariantError("poll page identity replay conflict")
        decision = self._commit(
            "POLL_RESPONSE_PAGE",
            {
                "source_id": source_id,
                "source_head": source_head,
                "page_id": page_id,
                "raw_digest": raw_digest,
                "members": member_dispositions,
            },
        )
        self._poll_pages[(source_id, page_id)] = (raw_digest, member_dispositions, decision)
        return decision

    def mark_authenticated_late_evidence(self, source_id: str, expected_head: str) -> str:
        source = self._sources.get(source_id)
        if source is None or source.head != expected_head:
            raise TrustInvariantError("stale evidence source")
        decision = self._commit(
            "AUTHENTICATED_LATE_EVIDENCE", {"source_id": source_id, "head": expected_head}
        )
        self._sources[source_id] = replace(source, authenticated_late=True)
        return decision

    def recover(self) -> int:
        entries = self._journal.entries()
        predecessor: str | None = None
        recovered = 0
        for decision_id, observed_predecessor, decision in entries:
            if (
                observed_predecessor != predecessor
                or _decision_digest(decision, predecessor) != decision_id
            ):
                if self._state is not None:
                    self._state = replace(self._state, phase="RECOVERY_REQUIRED")
                raise RecoveryRequired("journal prefix or predecessor mismatch")
            if not self._materializer.materialized(decision_id):
                decoded = json.loads(decision)
                record_types = KIND_RECORD_TYPES.get(decoded["kind"], ())
                if not record_types:
                    record_types = ("chiplog.deployment_trust.tenant_decision",)
                records = tuple(
                    _canonical(
                        {
                            "decision_id": decision_id,
                            "operation_kind": decoded["kind"],
                            "record_type_id": record_type,
                            "schema_id": SCHEMA_ID,
                            **decoded["payload"],
                        }
                    )
                    for record_type in (
                        "chiplog.deployment_trust.tenant_decision",
                        *record_types,
                    )
                )
                self._materializer.materialize(decision_id, records)
                recovered += 1
            predecessor = decision_id
        if entries:
            self._reconstruct_core_from_journal(entries)
            self._rebuild_authority_state()
        return recovered

    def _reconstruct_core_from_journal(
        self, entries: tuple[tuple[str, str | None, bytes], ...]
    ) -> None:
        genesis: DatabaseGenesis | None = None
        binding: DeploymentTenantBinding | None = None
        phase: Literal[
            "BOOTSTRAP_REQUIRED", "ACTIVE", "PREPARED", "READY", "RECOVERY_REQUIRED", "HOLD"
        ] = "BOOTSTRAP_REQUIRED"
        generation = 1
        for _, _, raw in entries:
            envelope = json.loads(raw)
            kind, payload = str(envelope["kind"]), envelope["payload"]
            if kind == "INITIALIZE":
                genesis = DatabaseGenesis(**payload["genesis"])
                binding = DeploymentTenantBinding(**payload["binding"])
            elif kind == "BOOTSTRAP":
                phase, generation = "ACTIVE", 2
            elif kind == "TRUST_TRANSITION_PREPARED":
                phase = "PREPARED"
            elif kind == "TRUST_TRANSITION_READY":
                phase = "READY"
            elif kind == "TRUST_TRANSITION_ABORTED":
                phase = "ACTIVE"
            elif kind == "TRUST_TRANSITION_ACCEPTED":
                binding = DeploymentTenantBinding(**payload["binding"])
                phase, generation = "ACTIVE", generation + 1
            elif kind in {"OPERATOR_BINDING_KEY_ROTATION", "JOURNAL_ROOT_ROTATION"}:
                binding = DeploymentTenantBinding(**payload)
                generation += 1
            elif kind == "PRINCIPAL_CONTOUR_PREREQUISITE":
                phase = "HOLD"
        if genesis is None or binding is None:
            raise RecoveryRequired("journal has no complete genesis/binding prefix")
        unsigned = asdict(binding)
        signature = str(unsigned.pop("signature"))
        if (
            binding.tenant_id != genesis.tenant_id
            or binding.database_instance_id != genesis.database_instance_id
            or binding.genesis_digest != _digest(_canonical(asdict(genesis)))
            or binding.journal_identity != self._journal_identity
            or binding.operator_key_id != self._operator_key_id
            or not hmac.compare_digest(signature, self._sign(_canonical(unsigned)))
        ):
            raise RecoveryRequired("replayed binding identity or signature mismatch")
        self._genesis, self._binding = genesis, binding
        self._state = CurrentDatabaseState(
            genesis.tenant_id,
            genesis.database_instance_id,
            _digest(_canonical(asdict(binding))),
            entries[-1][0],
            generation,
            phase,
        )

    def observation(self, decision_id: str) -> Literal["ABORT", "HOLD", "REPLAY"]:
        observed = self._journal.observation(decision_id)
        if observed == "NO_DECISION":
            return "ABORT"
        if observed == "AMBIGUOUS":
            return "HOLD"
        return "REPLAY"

    def prepare_trust_transition(
        self,
        *,
        transition_id: str,
        kind: Literal["OPERATOR_BINDING_KEY_ROTATION", "JOURNAL_ROOT_ROTATION"],
        proposed_key_id: str | None = None,
        proposed_epoch: int | None = None,
    ) -> PendingTrustTransition:
        self._require_active()
        assert self._binding is not None and self._state is not None
        if self._pending is not None:
            raise TrustInvariantError("trust transition already unresolved")
        if kind == "OPERATOR_BINDING_KEY_ROTATION" and not proposed_key_id:
            raise TrustInvariantError("binding-key successor is incomplete")
        if kind == "JOURNAL_ROOT_ROTATION" and proposed_epoch != self._binding.journal_epoch + 1:
            raise TrustInvariantError("journal epoch must advance exactly once")
        decision = self._commit(
            "TRUST_TRANSITION_PREPARED",
            {
                "transition_id": transition_id,
                "kind": kind,
                "predecessor_trust_head": self._state.trust_head,
                "proposed_key_id": proposed_key_id,
                "proposed_epoch": proposed_epoch,
            },
        )
        pending = PendingTrustTransition(
            transition_id,
            kind,
            self._state.trust_head,
            decision,
            proposed_key_id,
            proposed_epoch,
            "PREPARED",
        )
        self._pending = pending
        self._state = replace(self._state, materialization_head=decision, phase="PREPARED")
        return pending

    def mark_transition_ready(self, transition_id: str) -> PendingTrustTransition:
        pending = self._pending
        if pending is None or pending.transition_id != transition_id or pending.phase != "PREPARED":
            raise TrustInvariantError("transition is not prepared")
        assert self._state is not None
        if self._journal.entries()[-1][0] != self._state.materialization_head:
            raise RecoveryRequired("transition readiness fence is stale")
        decision = self._commit(
            "TRUST_TRANSITION_READY",
            {"transition_id": transition_id, "prepared_head": pending.prepared_journal_head},
        )
        pending = replace(pending, phase="READY")
        self._pending = pending
        self._state = replace(self._state, materialization_head=decision, phase="READY")
        return pending

    def abort_trust_transition(self, transition_id: str) -> str:
        pending = self._pending
        if pending is None or pending.transition_id != transition_id or pending.phase != "PREPARED":
            raise TrustInvariantError("only a prepared transition may abort")
        decision = self._commit("TRUST_TRANSITION_ABORTED", {"transition_id": transition_id})
        assert self._state is not None
        self._pending = None
        self._state = replace(self._state, materialization_head=decision, phase="ACTIVE")
        return decision

    def accept_trust_transition(
        self, transition_id: str, *, new_secret: bytes | None = None
    ) -> str:
        pending = self._pending
        if pending is None or pending.transition_id != transition_id or pending.phase != "READY":
            raise TrustInvariantError("only a ready transition may be accepted")
        assert self._binding is not None and self._state is not None
        old_head = self._state.trust_head
        key_id = pending.proposed_key_id or self._binding.operator_key_id
        epoch = pending.proposed_epoch or self._binding.journal_epoch
        signing_secret = new_secret or self._operator_secret
        unsigned = {
            "tenant_id": self._binding.tenant_id,
            "database_instance_id": self._binding.database_instance_id,
            "genesis_digest": self._binding.genesis_digest,
            "journal_identity": self._binding.journal_identity,
            "journal_epoch": epoch,
            "operator_key_id": key_id,
            "predecessor": old_head,
        }
        binding = DeploymentTenantBinding(
            self._binding.tenant_id,
            self._binding.database_instance_id,
            self._binding.genesis_digest,
            self._binding.journal_identity,
            epoch,
            key_id,
            old_head,
            self._sign(_canonical(unsigned), signing_secret),
        )
        decision = self._commit(
            "TRUST_TRANSITION_ACCEPTED",
            {"transition_id": transition_id, "binding": asdict(binding)},
        )
        self._binding = binding
        self._operator_key_id = key_id
        self._operator_secret = signing_secret
        self._pending = None
        self._state = replace(
            self._state,
            trust_head=_digest(_canonical(asdict(binding))),
            materialization_head=decision,
            generation=self._state.generation + 1,
            phase="ACTIVE",
        )
        return decision

    def rotate_binding_key(self, new_key_id: str, new_secret: bytes) -> DeploymentTenantBinding:
        self._require_active()
        assert self._binding is not None and self._state is not None
        old_head = self._state.trust_head
        unsigned = asdict(self._binding)
        unsigned.pop("signature")
        unsigned.update({"operator_key_id": new_key_id, "predecessor": old_head})
        binding = DeploymentTenantBinding(
            self._binding.tenant_id,
            self._binding.database_instance_id,
            self._binding.genesis_digest,
            self._binding.journal_identity,
            self._binding.journal_epoch,
            new_key_id,
            old_head,
            self._sign(_canonical(unsigned), new_secret),
        )
        decision = self._commit("OPERATOR_BINDING_KEY_ROTATION", asdict(binding))
        self._operator_key_id = new_key_id
        self._operator_secret = new_secret
        self._binding = binding
        self._state = replace(
            self._state,
            trust_head=_digest(_canonical(asdict(binding))),
            materialization_head=decision,
            generation=self._state.generation + 1,
        )
        return binding

    def rotate_journal_root(self, new_epoch: int) -> DeploymentTenantBinding:
        self._require_active()
        assert self._binding is not None and self._state is not None
        if new_epoch != self._binding.journal_epoch + 1:
            raise TrustInvariantError("journal epoch must advance exactly once")
        old_head = self._state.trust_head
        unsigned = {
            "tenant_id": self._binding.tenant_id,
            "database_instance_id": self._binding.database_instance_id,
            "genesis_digest": self._binding.genesis_digest,
            "journal_identity": self._binding.journal_identity,
            "journal_epoch": new_epoch,
            "operator_key_id": self._binding.operator_key_id,
            "predecessor": old_head,
        }
        binding = DeploymentTenantBinding(
            self._binding.tenant_id,
            self._binding.database_instance_id,
            self._binding.genesis_digest,
            self._binding.journal_identity,
            new_epoch,
            self._binding.operator_key_id,
            old_head,
            self._sign(_canonical(unsigned)),
        )
        decision = self._commit("JOURNAL_ROOT_ROTATION", asdict(binding))
        self._binding = binding
        self._state = replace(
            self._state,
            trust_head=_digest(_canonical(asdict(binding))),
            materialization_head=decision,
            generation=self._state.generation + 1,
        )
        return binding

    def restore(self, genesis: DatabaseGenesis) -> None:
        if self._genesis != genesis:
            raise RecoveryRequired("restore identity mismatch; relabel is unsupported")
        self.recover()

    def admit_startup(
        self,
        state: CurrentDatabaseState,
        genesis: DatabaseGenesis,
        binding: DeploymentTenantBinding,
    ) -> None:
        """Admit only the protected current identity and fully materialized journal head."""
        entries = self._journal.entries()
        journal_head = entries[-1][0] if entries else None
        unsigned = asdict(binding)
        signature = str(unsigned.pop("signature"))
        binding_head = _digest(_canonical(asdict(binding)))
        valid = (
            state.phase in {"ACTIVE", "BOOTSTRAP_REQUIRED", "PREPARED", "READY"}
            and state.tenant_id == genesis.tenant_id == binding.tenant_id
            and state.database_instance_id
            == genesis.database_instance_id
            == binding.database_instance_id
            and binding.genesis_digest == _digest(_canonical(asdict(genesis)))
            and binding.journal_identity == self._journal_identity
            and binding.operator_key_id == self._operator_key_id
            and signature == self._sign(_canonical(unsigned))
            and state.trust_head == binding_head
            and journal_head == state.materialization_head
            and journal_head is not None
            and self._materializer.materialized(journal_head)
        )
        if not valid:
            self._state = replace(state, phase="RECOVERY_REQUIRED")
            raise RecoveryRequired("startup trust/genesis/journal/materialization mismatch")
        self._state, self._genesis, self._binding = state, genesis, binding
        self._rebuild_authority_state()

    def _rebuild_authority_state(self) -> None:
        """Reconstruct all mutable authority exclusively from durable owner records."""
        seen: set[str] = set()
        self._principal = None
        self._credential = None
        self._sources = {}
        self._witnesses = {}
        self._witness_decisions = {}
        self._consumed_replays = set()
        self._poll = {}
        self._poll_pages = {}
        self._pending = None
        self._freshness = 0
        for raw in self._materializer.records():
            item = json.loads(raw)
            decision_id = str(item["decision_id"])
            if decision_id in seen:
                continue
            seen.add(decision_id)
            kind = str(item["operation_kind"])
            if kind == "BOOTSTRAP":
                self._principal = PrincipalId(str(item["principal_id"]))
                credential = item["credential"]
                self._credential = CredentialState(**credential)
                self._bootstrap_fingerprint = str(item["token_fingerprint"])
                self._freshness += 1
            elif kind == "REGISTER_EVIDENCE_SOURCE":
                source = EvidenceSourceState(
                    str(item["source_id"]),
                    str(item["head"]),
                    bool(item.get("authenticated_late", False)),
                )
                self._sources[source.source_id] = source
            elif kind in {"ROTATE_CREDENTIAL", "EMERGENCY_RECOVERY"}:
                credential = item.get("credential", item)
                self._credential = CredentialState(
                    str(credential["credential_id"]),
                    str(credential["head"]),
                    str(credential["session_id"]),
                    str(credential["session_head"]),
                    bool(credential.get("revoked", False)),
                )
                self._freshness += 1
            elif kind == "REVOKE_CREDENTIAL" and self._credential is not None:
                self._credential = replace(self._credential, revoked=True)
                self._freshness += 1
            elif kind == "TRANSPORT_WITNESS":
                witness = TransportOriginWitness(
                    **{field: item[field] for field in TransportOriginWitness.__dataclass_fields__}
                )
                self._witnesses[witness.witness_id] = witness
                self._witness_decisions[witness.witness_id] = decision_id
                self._consumed_replays.add(witness.replay_identity)
            elif kind == "AUTHENTICATED_LATE_EVIDENCE":
                source_id = str(item["source_id"])
                late_source = self._sources.get(source_id)
                if late_source is not None:
                    self._sources[source_id] = replace(late_source, authenticated_late=True)
            elif kind == "POLL_RESPONSE_PAGE":
                members = tuple((str(a), str(b)) for a, b in item["members"])
                self._poll_pages[(str(item["source_id"]), str(item["page_id"]))] = (
                    str(item["raw_digest"]),
                    members,
                    decision_id,
                )
            elif kind == "POLL_CURSOR_ADVANCE_AUTHORIZED":
                source_id, cursor = str(item["source_id"]), str(item["cursor"])
                previous = self._poll.get(source_id, PollCursorState(source_id))
                self._poll[source_id] = replace(
                    previous,
                    authorized_cursor=cursor,
                    authorized_page_id=str(item["page_id"]),
                    authorized_sequence=int(item["cursor_sequence"]),
                    authorization_decision_id=decision_id,
                )
            elif kind == "POLL_CURSOR_APPLIED":
                source_id, cursor = str(item["source_id"]), str(item["cursor"])
                previous = self._poll.get(source_id, PollCursorState(source_id))
                self._poll[source_id] = replace(
                    previous, applied_cursor=cursor, application_decision_id=decision_id
                )
            elif kind == "TRUST_TRANSITION_PREPARED":
                self._pending = PendingTrustTransition(
                    str(item["transition_id"]),
                    item["kind"],
                    str(item["predecessor_trust_head"]),
                    decision_id,
                    item.get("proposed_key_id"),
                    item.get("proposed_epoch"),
                    "PREPARED",
                )
            elif kind == "TRUST_TRANSITION_READY" and self._pending is not None:
                self._pending = replace(self._pending, phase="READY")
            elif kind in {"TRUST_TRANSITION_ABORTED", "TRUST_TRANSITION_ACCEPTED"}:
                self._pending = None

    def _valid_witness(self, witness: TransportOriginWitness) -> bool:
        fields = asdict(witness)
        signature = str(fields.pop("broker_signature"))
        return hmac.compare_digest(signature, self._sign(_canonical(fields), self._broker_secret))

    def _require_active(self) -> None:
        if self._state is None or self._state.phase != "ACTIVE":
            raise TrustInvariantError("ordinary work denied outside active contour")
