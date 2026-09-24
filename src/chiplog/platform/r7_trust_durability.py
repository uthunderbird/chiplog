"""Broker-side mechanical persistence for decisions authorized by the trust owner."""

from __future__ import annotations

import hashlib
import hmac
import json
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)
from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
    HermeticTrustObservationV1,
)
from chiplog.platform.authority_gate import AuthorityGate, FileIdentity
from chiplog.platform.r7_trust import encode_trust_journal

_SCHEMA = "chiplog.deployment_trust.record.v1"
_TYPES = {
    "INITIALIZE": (
        "chiplog.deployment_trust.current_database_state",
        "chiplog.deployment_trust.database_genesis",
        "chiplog.deployment_trust.deployment_tenant_binding",
        "chiplog.deployment_trust.tenant_registry_entry",
    ),
    "BOOTSTRAP": (
        "chiplog.deployment_trust.identity_credential_head",
        "chiplog.deployment_trust.principal_registry_entry",
        "chiplog.deployment_trust.session_head",
        "chiplog.deployment_trust.tenant_principal_contour",
    ),
    "HERMETIC_OUTPUT_SCOPE_V1": ("chiplog.deployment_trust.hermetic_output_scope",),
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _has_string_fields(value: dict[str, object], fields: set[str]) -> bool:
    return all(isinstance(value[field], str) and value[field] for field in fields)


@dataclass(frozen=True)
class FrozenTrustObservation:
    """Exact local inputs retained with a separately evaluated owner response."""

    snapshot_bytes: bytes
    journal_head: str | None
    bundle_path: str
    sources: tuple[FileIdentity, ...]


@dataclass(frozen=True)
class TrustDurabilityObservation:
    tenant_id: str
    database_instance_id: str
    genesis_head: str
    trust_head: str
    materialization_head: str
    phase: str


@dataclass(frozen=True, slots=True)
class HistoricalTrustRecord:
    """One exact materialized row, bound to its signed physical decision."""

    physical_decision_id: str
    physical_predecessor: str | None
    envelope_bytes: bytes
    envelope_fingerprint: str
    logical_decision_id: str
    logical_predecessor: str | None
    record_ordinal: int
    record_bytes: bytes


@dataclass(frozen=True, slots=True)
class HistoricalTrustPrefix:
    """Authenticated physical cut and its canonical logical owner snapshot."""

    observation: HermeticTrustObservationV1
    snapshot_bytes: bytes
    physical_entries: tuple[tuple[str, str | None, bytes], ...]


class BrokerTrustDurability:
    """Applies owner-authorized bytes and verifies their authenticated durable envelope."""

    def __init__(
        self,
        journal: IndependentTenantDecisionJournal,
        materializer: SQLiteTrustMaterializer,
        operator_secret: bytes,
    ) -> None:
        self._journal = journal
        self._materializer = materializer
        self._operator_secret = operator_secret
        if journal.authority_gate != materializer.authority_gate:
            raise RuntimeError("trust durability authority gate binding mismatch")
        self._authority_gate = journal.authority_gate

    @property
    def authority_gate(self) -> AuthorityGate | None:
        return self._authority_gate

    def _authority_scope(self) -> AbstractContextManager[None]:
        return nullcontext() if self._authority_gate is None else self._authority_gate.hold()

    def historical_record(self, decision_id: str, ordinal: int) -> HistoricalTrustRecord:
        """Read an exact historical row after authenticating all durable trust state."""
        if type(decision_id) is not str or not decision_id:
            raise ValueError("decision_id must be a nonempty physical decision id")
        if type(ordinal) is not int or not 0 <= ordinal < 2**63:
            raise ValueError("ordinal must be a nonnegative SQLite integer")
        self._require_historical_gate()
        with self._authority_scope():
            entries, logical_entries = self._authenticated_historical_entries()
            for index, (physical_id, predecessor, raw) in enumerate(entries):
                if physical_id == decision_id:
                    record = self._materializer.record(decision_id, ordinal)
                    if record is None:
                        raise RuntimeError("historical materialized record is absent")
                    logical_id, logical_predecessor, _ = logical_entries[index]
                    return HistoricalTrustRecord(
                        physical_decision_id=physical_id,
                        physical_predecessor=predecessor,
                        envelope_bytes=raw,
                        envelope_fingerprint=hashlib.sha256(raw).hexdigest(),
                        logical_decision_id=logical_id,
                        logical_predecessor=logical_predecessor,
                        record_ordinal=ordinal,
                        record_bytes=record,
                    )
        raise RuntimeError("historical physical decision id is absent")

    def historical_prefix(self, observation: HermeticTrustObservationV1) -> HistoricalTrustPrefix:
        """Return the logical owner snapshot at an exact authenticated physical head."""
        if not isinstance(observation, HermeticTrustObservationV1):
            raise TypeError("historical prefix requires HermeticTrustObservationV1")
        self._require_historical_gate()
        with self._authority_scope():
            entries, logical_entries = self._authenticated_historical_entries()
            physical = observation.physical_journal_head
            if physical.identity != "deployment-trust/journal":
                raise RuntimeError("historical physical journal identity differs")
            for index, (decision_id, _, raw) in enumerate(entries):
                if decision_id != physical.head:
                    continue
                if hashlib.sha256(raw).hexdigest() != physical.fingerprint:
                    raise RuntimeError("historical physical journal fingerprint differs")
                if logical_entries[index][0] != observation.logical_snapshot_head:
                    raise RuntimeError("historical logical snapshot head differs")
                return HistoricalTrustPrefix(
                    observation=observation,
                    snapshot_bytes=encode_trust_journal(logical_entries[: index + 1]),
                    physical_entries=entries[: index + 1],
                )
        raise RuntimeError("historical physical journal head is absent")

    def _require_historical_gate(self) -> None:
        if self._authority_gate is None:
            raise RuntimeError("historical trust reads require a bound authority gate")

    def _authenticated_historical_entries(
        self,
    ) -> tuple[
        tuple[tuple[str, str | None, bytes], ...],
        tuple[tuple[str, str | None, bytes], ...],
    ]:
        """Authenticate the complete source before exposing any historical cut."""
        sources = (*self._journal.physical_sources(), self._materializer.physical_identity())
        entries = self._journal.entries()
        if not entries:
            raise RuntimeError("historical trust journal is empty")
        expected_all: list[bytes] = []
        logical_entries: list[tuple[str, str | None, bytes]] = []
        physical_predecessor: str | None = None
        logical_predecessor: str | None = None
        first_envelope: dict[str, object] | None = None
        for index, (decision_id, predecessor, raw) in enumerate(entries):
            if predecessor != physical_predecessor:
                raise RuntimeError("historical physical predecessor differs")
            if (
                decision_id
                != hashlib.sha256((predecessor or "GENESIS").encode() + b"\x00" + raw).hexdigest()
            ):
                raise RuntimeError("historical physical decision id differs")
            envelope = self._strict_historical_envelope(raw)
            if index == 0:
                first_envelope = envelope
            kind = envelope["kind"]
            payload = envelope["payload"]
            assert isinstance(kind, str)
            assert isinstance(payload, dict)
            self._validate_historical_payload(kind, payload)
            unsigned = {"kind": kind, "payload": payload, "predecessor": envelope["predecessor"]}
            if envelope["predecessor"] != logical_predecessor:
                raise RuntimeError("historical logical predecessor differs")
            logical_id = hashlib.sha256(
                (logical_predecessor or "GENESIS").encode() + b"\x00" + _canonical(unsigned)
            ).hexdigest()
            expected = self._expected_records(decision_id, kind, payload)
            actual = tuple(
                self._materializer.record(decision_id, ordinal) for ordinal in range(len(expected))
            )
            if (
                actual != expected
                or self._materializer.record(decision_id, len(expected)) is not None
            ):
                raise RuntimeError(
                    "historical trust materialization differs from authenticated journal"
                )
            expected_all.extend(expected)
            logical_entries.append((logical_id, logical_predecessor, _canonical(unsigned)))
            physical_predecessor = decision_id
            logical_predecessor = logical_id
        if self._materializer.records() != tuple(expected_all):
            raise RuntimeError(
                "historical trust materialization differs from authenticated journal"
            )
        if self._materializer.decision_ids() != tuple(decision_id for decision_id, _, _ in entries):
            raise RuntimeError(
                "historical trust materialization decisions differ from authenticated journal"
            )
        assert first_envelope is not None
        self._verify_historical_genesis(first_envelope)
        if entries != self._journal.entries() or sources != (
            *self._journal.physical_sources(),
            self._materializer.physical_identity(),
        ):
            raise RuntimeError("historical trust sources changed during read")
        return entries, tuple(logical_entries)

    def _strict_historical_envelope(self, raw: bytes) -> dict[str, object]:
        try:
            envelope = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("historical trust envelope is malformed") from error
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"kind", "operator_authentication", "payload", "predecessor"}
            or _canonical(envelope) != raw
            or not isinstance(envelope["kind"], str)
            or envelope["kind"] not in _TYPES
            or not isinstance(envelope["payload"], dict)
            or (
                envelope["predecessor"] is not None and not isinstance(envelope["predecessor"], str)
            )
            or not isinstance(envelope["operator_authentication"], str)
        ):
            raise RuntimeError("historical trust envelope is incomplete")
        unsigned = {
            "kind": envelope["kind"],
            "payload": envelope["payload"],
            "predecessor": envelope["predecessor"],
        }
        expected = hmac.new(self._operator_secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(envelope["operator_authentication"], expected):
            raise RuntimeError("historical trust decision authentication failed")
        return envelope

    @staticmethod
    def _validate_historical_payload(kind: str, payload: dict[str, object]) -> None:
        if kind == "INITIALIZE":
            binding, genesis = payload.get("binding"), payload.get("genesis")
            if (
                set(payload) != {"binding", "genesis"}
                or not isinstance(binding, dict)
                or not isinstance(genesis, dict)
                or set(binding)
                != {
                    "database_instance_id",
                    "genesis_digest",
                    "journal_epoch",
                    "journal_identity",
                    "operator_key_id",
                    "predecessor",
                    "signature",
                    "tenant_id",
                }
                or set(genesis) != {"database_instance_id", "genesis_version", "tenant_id"}
                or not _has_string_fields(
                    binding,
                    {
                        "database_instance_id",
                        "genesis_digest",
                        "journal_identity",
                        "operator_key_id",
                        "signature",
                        "tenant_id",
                    },
                )
                or type(binding["journal_epoch"]) is not int
                or binding["journal_epoch"] != 1
                or binding["predecessor"] is not None
                or not _has_string_fields(genesis, {"database_instance_id", "tenant_id"})
                or type(genesis["genesis_version"]) is not int
                or genesis["genesis_version"] != 1
            ):
                raise RuntimeError("historical INITIALIZE payload is invalid")
        elif kind == "BOOTSTRAP":
            credential = payload.get("credential")
            if (
                set(payload)
                != {"credential", "principal_id", "recovery_verifier", "token_fingerprint"}
                or not isinstance(credential, dict)
                or set(credential)
                != {
                    "credential_id",
                    "head",
                    "peer_credential",
                    "revoked",
                    "session_head",
                    "session_id",
                }
                or not _has_string_fields(
                    payload, {"principal_id", "recovery_verifier", "token_fingerprint"}
                )
                or not _has_string_fields(
                    credential,
                    {"credential_id", "head", "peer_credential", "session_head", "session_id"},
                )
                or not isinstance(credential["revoked"], bool)
            ):
                raise RuntimeError("historical BOOTSTRAP payload is invalid")
        else:
            from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
                HermeticOutputScopeV1,
            )

            scope_value = payload.get("scope")
            if set(payload) != {"scope"} or not isinstance(scope_value, dict):
                raise RuntimeError("historical H1 scope payload is invalid")
            try:
                scope = HermeticOutputScopeV1.model_validate(scope_value)
            except ValueError as error:
                raise RuntimeError("historical H1 scope payload is invalid") from error
            if scope.model_dump(mode="json") != scope_value:
                raise RuntimeError("historical H1 scope payload is not canonical")

    def _verify_historical_genesis(self, envelope: dict[str, object]) -> None:
        if envelope["kind"] != "INITIALIZE":
            raise RuntimeError("historical trust journal lacks INITIALIZE genesis")
        payload = envelope["payload"]
        assert isinstance(payload, dict)
        binding, genesis = payload["binding"], payload["genesis"]
        assert isinstance(binding, dict)
        assert isinstance(genesis, dict)
        unsigned = {key: value for key, value in binding.items() if key != "signature"}
        expected = hmac.new(self._operator_secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(binding["signature"]), expected):
            raise RuntimeError("historical trust binding authentication failed")
        if binding["genesis_digest"] != hashlib.sha256(_canonical(genesis)).hexdigest():
            raise RuntimeError("historical trust genesis binding mismatch")

    def capture_verified_observation(self) -> FrozenTrustObservation:
        gate = self._authority_gate
        if gate is None:
            raise RuntimeError("verified trust observation requires a bound authority gate")
        with gate.hold():
            sources = (*self._journal.physical_sources(), self._materializer.physical_identity())
            entries = self._journal.entries()
            self.verify()
            expected_records: list[bytes] = []
            for decision_id, _, raw in entries:
                envelope = json.loads(raw)
                kind, payload = envelope["kind"], envelope["payload"]
                expected_records.extend(
                    _canonical(
                        {
                            "decision_id": decision_id,
                            "operation_kind": kind,
                            "record_type_id": record_type,
                            "schema_id": _SCHEMA,
                            **payload,
                        }
                    )
                    for record_type in ("chiplog.deployment_trust.tenant_decision", *_TYPES[kind])
                )
            if self._materializer.records() != tuple(expected_records):
                raise RuntimeError("trust materialization differs from authenticated journal")
            snapshot = encode_trust_journal(self.owner_snapshot_entries())
            if entries != self._journal.entries() or sources != (
                *self._journal.physical_sources(),
                self._materializer.physical_identity(),
            ):
                raise RuntimeError("trust sources changed during observation")
            return FrozenTrustObservation(
                snapshot_bytes=snapshot,
                journal_head=entries[-1][0] if entries else None,
                bundle_path=str(gate.database),
                sources=sources,
            )

    def _append(self, kind: str, payload: dict[str, object]) -> str:
        with self._authority_scope():
            return self._locked_append(kind, payload)

    def append_hermetic_output_scope(self, scope_bytes: bytes) -> tuple[str, int, bytes]:
        """Append one broker-fenced owner proposal to the existing trust lineage."""
        from chiplog.capabilities.deployment_trust.hermetic_output_scope_contracts import (
            HermeticOutputScopeV1,
        )

        scope = HermeticOutputScopeV1.model_validate_json(scope_bytes)
        if scope.canonical_bytes() != scope_bytes:
            raise RuntimeError("H1 scope bytes are not canonical")
        with self._authority_scope():
            decision_id = self._locked_append(
                "HERMETIC_OUTPUT_SCOPE_V1", {"scope": scope.model_dump(mode="json")}
            )
            records = self._expected_records(
                decision_id, "HERMETIC_OUTPUT_SCOPE_V1", {"scope": scope.model_dump(mode="json")}
            )
            return decision_id, len(records) - 1, records[-1]

    @staticmethod
    def _expected_records(
        decision_id: str, kind: str, payload: dict[str, object]
    ) -> tuple[bytes, ...]:
        return tuple(
            _canonical(
                {
                    "decision_id": decision_id,
                    "operation_kind": kind,
                    "record_type_id": record_type,
                    "schema_id": _SCHEMA,
                    **payload,
                }
            )
            for record_type in ("chiplog.deployment_trust.tenant_decision", *_TYPES[kind])
        )

    def _locked_append(self, kind: str, payload: dict[str, object]) -> str:
        entries = self._journal.entries()
        journal_predecessor = entries[-1][0] if entries else None
        logical_predecessor = self._logical_head(entries)
        unsigned = {"kind": kind, "payload": payload, "predecessor": logical_predecessor}
        raw = _canonical(
            {
                **unsigned,
                "operator_authentication": hmac.new(
                    self._operator_secret, _canonical(unsigned), hashlib.sha256
                ).hexdigest(),
            }
        )
        decision_id = self._journal.append(raw, journal_predecessor)
        records = self._expected_records(decision_id, kind, payload)
        self._materializer.materialize(decision_id, records)
        return decision_id

    @staticmethod
    def _logical_head(entries: tuple[tuple[str, str | None, bytes], ...]) -> str | None:
        logical: str | None = None
        for _, _, raw in entries:
            envelope = json.loads(raw)
            unsigned = {
                "kind": envelope["kind"],
                "payload": envelope["payload"],
                "predecessor": envelope["predecessor"],
            }
            if envelope["predecessor"] != logical:
                raise RuntimeError("trust decision logical predecessor mismatch")
            logical = hashlib.sha256(
                (logical or "GENESIS").encode() + b"\x00" + _canonical(unsigned)
            ).hexdigest()
        return logical

    def owner_snapshot_entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        with self._authority_scope():
            return self._locked_owner_snapshot_entries()

    def _locked_owner_snapshot_entries(self) -> tuple[tuple[str, str | None, bytes], ...]:
        """Expose authenticated decisions under predecessor-compatible logical identities."""
        entries = self._journal.entries()
        self._verify_operator_authentication(entries)
        result: list[tuple[str, str | None, bytes]] = []
        logical_predecessor: str | None = None
        for _, _, raw in entries:
            envelope = json.loads(raw)
            unsigned = {
                "kind": envelope["kind"],
                "payload": envelope["payload"],
                "predecessor": envelope["predecessor"],
            }
            logical_id = hashlib.sha256(
                (logical_predecessor or "GENESIS").encode() + b"\x00" + _canonical(unsigned)
            ).hexdigest()
            result.append((logical_id, logical_predecessor, _canonical(unsigned)))
            logical_predecessor = logical_id
        return tuple(result)

    def _verify_operator_authentication(
        self, entries: tuple[tuple[str, str | None, bytes], ...]
    ) -> None:
        for _, _, raw in entries:
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or set(envelope) != {
                "kind",
                "operator_authentication",
                "payload",
                "predecessor",
            }:
                raise RuntimeError("trust decision envelope is incomplete")
            unsigned = {
                "kind": envelope["kind"],
                "payload": envelope["payload"],
                "predecessor": envelope["predecessor"],
            }
            expected = hmac.new(
                self._operator_secret, _canonical(unsigned), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(str(envelope["operator_authentication"]), expected):
                raise RuntimeError("trust decision authentication failed")

    def apply_authorized(self, authorized_bytes: bytes) -> tuple[str, ...]:
        with self._authority_scope():
            return self._locked_apply_authorized(authorized_bytes)

    def _locked_apply_authorized(self, authorized_bytes: bytes) -> tuple[str, ...]:
        """Persist owner bytes; only the secret signature slot is broker-filled."""
        value = json.loads(authorized_bytes)
        if _canonical(value) != authorized_bytes:
            raise RuntimeError("trust owner decisions are not canonical")
        decisions = value.get("decisions") if isinstance(value, dict) else None
        if not isinstance(decisions, list) or not decisions:
            raise RuntimeError("trust owner returned no authorized decisions")
        kinds = tuple(str(item.get("kind")) for item in decisions if isinstance(item, dict))
        expected_kinds = (
            ("INITIALIZE", "BOOTSTRAP") if not self._journal.entries() else ("BOOTSTRAP",)
        )
        if kinds != expected_kinds:
            raise RuntimeError("trust owner decision batch is incomplete or out of order")
        for decision in decisions:
            if not isinstance(decision, dict) or set(decision) != {"kind", "payload"}:
                raise RuntimeError("trust owner decision shape is invalid")
            kind = str(decision["kind"])
            if kind not in _TYPES or not isinstance(decision["payload"], dict):
                raise RuntimeError("trust owner authorized an unsupported durable decision")
            payload = decision["payload"]
            if kind == "INITIALIZE":
                if set(payload) != {"binding", "genesis"}:
                    raise RuntimeError("trust owner INITIALIZE payload is incomplete")
                binding, genesis = payload["binding"], payload["genesis"]
                if (
                    not isinstance(binding, dict)
                    or set(binding)
                    != {
                        "database_instance_id",
                        "genesis_digest",
                        "journal_epoch",
                        "journal_identity",
                        "operator_key_id",
                        "predecessor",
                        "signature",
                        "tenant_id",
                    }
                    or not isinstance(genesis, dict)
                    or set(genesis) != {"database_instance_id", "genesis_version", "tenant_id"}
                ):
                    raise RuntimeError("trust owner INITIALIZE fields are incomplete")
                if (
                    not _has_string_fields(
                        binding,
                        {
                            "database_instance_id",
                            "genesis_digest",
                            "journal_identity",
                            "operator_key_id",
                            "signature",
                            "tenant_id",
                        },
                    )
                    or type(binding["journal_epoch"]) is not int
                    or binding["journal_epoch"] != 1
                    or binding["predecessor"] is not None
                    or not _has_string_fields(genesis, {"database_instance_id", "tenant_id"})
                    or type(genesis["genesis_version"]) is not int
                    or genesis["genesis_version"] != 1
                ):
                    raise RuntimeError("trust owner INITIALIZE field types are invalid")
            elif set(payload) != {
                "credential",
                "principal_id",
                "recovery_verifier",
                "token_fingerprint",
            }:
                raise RuntimeError("trust owner BOOTSTRAP payload is incomplete")
            if kind == "BOOTSTRAP":
                credential = payload["credential"]
                if not isinstance(credential, dict) or set(credential) != {
                    "credential_id",
                    "head",
                    "peer_credential",
                    "revoked",
                    "session_head",
                    "session_id",
                }:
                    raise RuntimeError("trust owner credential payload is incomplete")
                if (
                    not _has_string_fields(
                        payload, {"principal_id", "recovery_verifier", "token_fingerprint"}
                    )
                    or not _has_string_fields(
                        credential,
                        {
                            "credential_id",
                            "head",
                            "peer_credential",
                            "session_head",
                            "session_id",
                        },
                    )
                    or not isinstance(credential["revoked"], bool)
                ):
                    raise RuntimeError("trust owner BOOTSTRAP field types are invalid")
            binding_value = payload.get("binding")
            signature = binding_value.get("signature") if isinstance(binding_value, dict) else None
            if (kind == "INITIALIZE") != (signature == "__BROKER_HMAC_SHA256__"):
                raise RuntimeError("trust owner signature slot is invalid")
        committed: list[str] = []
        for decision in decisions:
            kind = str(decision["kind"])
            payload = dict(decision["payload"])
            if kind == "INITIALIZE":
                binding = dict(payload["binding"])
                unsigned = {key: value for key, value in binding.items() if key != "signature"}
                binding["signature"] = hmac.new(
                    self._operator_secret, _canonical(unsigned), hashlib.sha256
                ).hexdigest()
                payload["binding"] = binding
            committed.append(self._append(kind, payload))
        return tuple(committed)

    def verify(self) -> TrustDurabilityObservation | None:
        with self._authority_scope():
            return self._locked_verify()

    def recover_materialization(self) -> None:
        """Replay only authenticated, exact missing materialization after a crash."""
        with self._authority_scope():
            entries = self._journal.entries()
            self._verify_operator_authentication(entries)
            for decision_id, _, raw in entries:
                envelope = json.loads(raw)
                expected = self._expected_records(
                    decision_id, envelope["kind"], envelope["payload"]
                )
                actual = tuple(
                    self._materializer.record(decision_id, ordinal)
                    for ordinal in range(len(expected))
                )
                if any(value is None for value in actual):
                    if any(value is not None for value in actual):
                        raise RuntimeError("partial trust materialization cannot be recovered")
                    self._materializer.materialize(decision_id, expected)
                elif actual != expected:
                    raise RuntimeError("trust materialization differs from authenticated journal")
            self._locked_verify()

    def _locked_verify(self) -> TrustDurabilityObservation | None:
        entries = self._journal.entries()
        if not entries:
            if self._materializer.decision_ids():
                raise RuntimeError("trust materialization differs from authenticated journal")
            return None
        if self._materializer.decision_ids() != tuple(decision_id for decision_id, _, _ in entries):
            raise RuntimeError("trust materialization differs from authenticated journal")
        self._verify_operator_authentication(entries)
        for decision_id, _, raw in entries:
            if not self._materializer.materialized(decision_id):
                raise RuntimeError("trust decision is not materialized")
            envelope = json.loads(raw)
            if envelope["kind"] not in _TYPES:
                raise RuntimeError("unsupported R7 trust durability record")
            expected_records = self._expected_records(
                decision_id, envelope["kind"], envelope["payload"]
            )
            actual = tuple(
                self._materializer.record(decision_id, ordinal)
                for ordinal in range(len(expected_records))
            )
            if actual != expected_records:
                raise RuntimeError("trust materialization differs from authenticated journal")
        logical_head = self._logical_head(entries)
        first = json.loads(entries[0][2])
        genesis = first["payload"]["genesis"]
        binding = first["payload"]["binding"]
        unsigned = {key: value for key, value in binding.items() if key != "signature"}
        expected = hmac.new(self._operator_secret, _canonical(unsigned), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(str(binding["signature"]), expected):
            raise RuntimeError("trust binding authentication failed")
        if binding["genesis_digest"] != hashlib.sha256(_canonical(genesis)).hexdigest():
            raise RuntimeError("trust genesis binding mismatch")
        return TrustDurabilityObservation(
            tenant_id=str(genesis["tenant_id"]),
            database_instance_id=str(genesis["database_instance_id"]),
            genesis_head=hashlib.sha256(_canonical(genesis)).hexdigest(),
            trust_head=hashlib.sha256(_canonical(binding)).hexdigest(),
            materialization_head=logical_head or "",
            phase="ACTIVE"
            if any(json.loads(raw)["kind"] == "BOOTSTRAP" for _, _, raw in entries)
            else "BOOTSTRAP_REQUIRED",
        )
