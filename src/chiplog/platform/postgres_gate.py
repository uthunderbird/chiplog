"""Exact deployment eligibility ordered in the user's PostgreSQL transaction."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable

from chiplog.platform.deployment_gate import CurrentEntitlement, DeploymentGateRequest
from chiplog.platform.postgres import Connection


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class PostgresDeploymentGate:
    def __init__(
        self,
        connection: Connection,
        tenant: str,
        authenticate: Callable[[bytes, bytes], bool] | None,
    ) -> None:
        self.connection, self.tenant, self.authenticate = connection, tenant, authenticate

    def import_current(
        self,
        current: CurrentEntitlement,
        proof: bytes,
        previous: bytes | None,
    ) -> None:
        payload = canonical(current.model_dump(mode="json"))
        if (
            self.authenticate is None
            or not self.authenticate(payload, proof)
            or current.generation.tenant_id != self.tenant
        ):
            raise PermissionError("independent entitlement proof rejected")
        row = self.connection.execute("SELECT canonical_bytes FROM gate_current").fetchone()
        if (None if row is None else bytes(row[0])) != previous:
            raise ValueError("stale entitlement predecessor")
        self.connection.execute(
            "INSERT INTO gate_current VALUES (true,%s,%s) ON CONFLICT(singleton) "
            "DO UPDATE SET canonical_bytes=excluded.canonical_bytes, proof=excluded.proof",
            (payload, proof),
        )

    def require(
        self, request: DeploymentGateRequest, surface: str, payload: bytes, *, live: bool = False
    ) -> bool:
        row = self.connection.execute("SELECT canonical_bytes, proof FROM gate_current").fetchone()
        if (
            row is None
            or self.authenticate is None
            or not self.authenticate(bytes(row[0]), bytes(row[1]))
        ):
            raise PermissionError("HOLD: independent readiness and entitlement absent")
        current = CurrentEntitlement.model_validate_json(bytes(row[0]))
        capability = {
            "planning.create": "planning.create_intention_line",
            "cli.render": "planning.read",
            "effects.first_send": "effects.first_send",
            "delivery.first_send": "delivery.first_send",
        }
        if (
            request.surface_id != surface
            or capability.get(surface) != request.bounds.capability_id
            or request.payload_digest != hashlib.sha256(payload).hexdigest()
            or request.generation.tenant_id != self.tenant
            or request.generation != current.generation
            or request.mode != current.mode
            or request.readiness_head != current.readiness_head
            or request.entitlement_head != current.entitlement_head
            or request.evidence_cursors != current.evidence_cursors
            or request.freshness_leases != current.freshness_leases
            or request.bounds != current.bounds
        ):
            raise PermissionError("HOLD: exact deployment eligibility changed")
        # Exact recorded handoffs remain inspectable after expiry; they never
        # consume another cap slot or publish another batch.
        previous = self.connection.execute(
            "SELECT request, payload FROM gate_handoffs WHERE operation=%s", (request.operation_id,)
        ).fetchone()
        encoded_request = canonical(request.model_dump(mode="json"))
        if previous is not None:
            if tuple(bytes(value) for value in previous) != (encoded_request, payload):
                raise ValueError("deployment handoff identity conflict")
            if not live:
                return False
        now = time.time_ns()
        bounds = current.bounds
        if (
            not bounds.starts_ns <= now < bounds.expires_ns
            or current.readiness != "READY"
            or current.status != "ACTIVE"
            or current.open_causes
            or not current.live_predicates
            or not all(value for _, value in current.live_predicates)
            or any(expiry <= now for _, expiry in current.freshness_leases)
        ):
            raise PermissionError("HOLD: readiness, duration or freshness held")
        for values in (current.live_predicates, current.evidence_cursors, current.freshness_leases):
            keys = [key for key, _ in values]
            if not keys or keys != sorted(set(keys)):
                raise PermissionError("HOLD: incomplete eligibility inputs")
        for names in (bounds.instrumentation, bounds.stop_rules):
            if not names or list(names) != sorted(set(names)):
                raise PermissionError("HOLD: instrumentation or stop rules missing")
        if any(
            not value
            for value in (
                request.operation_id,
                request.payload_digest,
                request.readiness_head,
                request.entitlement_head,
                bounds.capability_id,
                bounds.cohort_id,
                bounds.purpose,
            )
        ):
            raise PermissionError("HOLD: required identity missing")
        count_row = self.connection.execute(
            "SELECT count(*) FROM gate_handoffs WHERE entitlement=%s AND mode=%s",
            (current.entitlement_head, current.mode),
        ).fetchone()
        assert count_row is not None
        if previous is None and count_row[0] >= bounds.cap:
            raise PermissionError("HOLD: entitlement cap exhausted")
        return previous is None

    def crossed(self, request: DeploymentGateRequest, payload: bytes) -> None:
        self.connection.execute(
            "INSERT INTO gate_handoffs VALUES (%s,%s,%s,%s,%s)",
            (
                request.operation_id,
                canonical(request.model_dump(mode="json")),
                payload,
                request.entitlement_head,
                request.mode,
            ),
        )
