"""Calendar owner canonical evidence, provenance and bounded query preparation."""

import hashlib
import json

from .boundary import WorkspaceRow
from .contracts import CalendarBatch, CalendarObservation


def observation_id(observation: CalendarObservation) -> str:
    return json.dumps(
        [
            observation.provider_id,
            observation.calendar_id,
            observation.event_id,
        ],
        separators=(",", ":"),
    )


def observation_payload(observation: CalendarObservation) -> bytes:
    return json.dumps(
        {
            "calendar_id": observation.calendar_id,
            "end_ns": observation.end_ns,
            "event_id": observation.event_id,
            "freshness": observation.freshness,
            "observed_at_ns": observation.observed_at_ns,
            "provider_id": observation.provider_id,
            "start_ns": observation.start_ns,
            "status": "provider observed",
            "title": observation.title,
            "version": observation.version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def source_heads_digest(batch: CalendarBatch) -> str:
    heads = sorted(
        {
            source.model_dump_json()
            for observation in batch.observations
            for source in observation.envelope.sources
        }
    )
    return hashlib.sha256(json.dumps(heads).encode()).hexdigest()


def prepare_rows(
    batch: CalendarBatch,
    tenant: str,
    policy: str,
    contour: str,
    fence: str,
    endpoint: str,
) -> tuple[WorkspaceRow, ...]:
    rows: list[WorkspaceRow] = []
    ids: set[str] = set()
    for observation in batch.observations:
        envelope = observation.envelope
        payload = observation_payload(observation)
        identity = observation_id(observation)
        if identity in ids or observation.end_ns < observation.start_ns:
            raise ValueError("duplicate calendar identity or invalid interval")
        ids.add(identity)
        if (
            envelope.tenant_id != tenant
            or envelope.policy_head != policy
            or envelope.contour_head != contour
            or envelope.deletion_fence_head != fence
            or envelope.content_digest != hashlib.sha256(payload).hexdigest()
        ):
            raise ValueError("calendar envelope differs from current context or canonical content")
        sources = envelope.sources
        source_ids = [(s.owner, s.record_id, s.record_version) for s in sources]
        if source_ids != sorted(set(source_ids)) or any(s.tenant_id != tenant for s in sources):
            raise ValueError("duplicate, reordered or foreign calendar provenance")
        if not any(
            s.owner == "calendar_observations"
            and s.record_id == identity
            and s.record_version == observation.version
            and s.content_digest == envelope.content_digest
            for s in sources
        ):
            raise ValueError("calendar provenance omits exact source identity/version/digest")
        allowed: set[str] | None = None
        for label in tuple(s.label for s in sources):
            endpoints = label.allowed_endpoints
            if tuple(sorted(set(endpoints))) != endpoints:
                raise ValueError("noncanonical disclosure endpoints")
            if label.value == "DENY_ALL":
                raise ValueError("calendar disclosure denied")
            if label.value == "UNRESTRICTED":
                if endpoints:
                    raise ValueError("unrestricted label carries endpoints")
            else:
                if not endpoints:
                    raise ValueError("empty restricted label")
                allowed = set(endpoints) if allowed is None else allowed.intersection(endpoints)
        expected_value = "UNRESTRICTED" if allowed is None else "ENDPOINT_RESTRICTED"
        expected_endpoints = () if allowed is None else tuple(sorted(allowed))
        if (
            envelope.label.value != expected_value
            or envelope.label.allowed_endpoints != expected_endpoints
            or (allowed is not None and endpoint not in allowed)
        ):
            raise ValueError("calendar disclosure narrows sources or denies endpoint")
        rows.append(
            WorkspaceRow(
                row_id=identity,
                row_version=observation.version,
                order_key=f"{observation.start_ns:030d}:{identity}",
                canonical_payload=payload,
                envelope=envelope,
            )
        )
    return tuple(sorted(rows, key=lambda row: row.order_key))


__all__ = ["observation_id", "observation_payload", "prepare_rows", "source_heads_digest"]
