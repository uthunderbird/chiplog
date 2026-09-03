"""Broker-side mechanical persistence for decisions authorized by the trust owner."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass

from chiplog.adapters.driven.deployment_trust import (
    IndependentTenantDecisionJournal,
    SQLiteTrustMaterializer,
)

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
}


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _has_string_fields(value: dict[str, object], fields: set[str]) -> bool:
    return all(isinstance(value[field], str) and value[field] for field in fields)


@dataclass(frozen=True)
class TrustDurabilityObservation:
    tenant_id: str
    database_instance_id: str
    genesis_head: str
    trust_head: str
    materialization_head: str
    phase: str


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

    def _append(self, kind: str, payload: dict[str, object]) -> str:
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
        records = tuple(
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
        entries = self._journal.entries()
        if not entries:
            return None
        self._verify_operator_authentication(entries)
        for decision_id, _, raw in entries:
            if not self._materializer.materialized(decision_id):
                raise RuntimeError("trust decision is not materialized")
            envelope = json.loads(raw)
            if envelope["kind"] not in _TYPES:
                raise RuntimeError("unsupported R7 trust durability record")
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
