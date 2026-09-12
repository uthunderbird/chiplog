import hashlib

from chiplog.capabilities.calendar_observations.boundary import (
    DisclosureEnvelope,
    DisclosureLabel,
    SourceReference,
    WorkspaceReadContext,
    WorkspaceReadRequest,
)
from chiplog.capabilities.calendar_observations.contracts import CalendarBatch, CalendarObservation
from chiplog.capabilities.calendar_observations.observations import (
    observation_id,
    observation_payload,
    source_heads_digest,
)
from chiplog.platform.calendar_read_ledger import (
    CalendarReadState,
)


def batch(freshness: str = "CURRENT", count: int = 3) -> CalendarBatch:
    label = DisclosureLabel(
        lattice_version="chiplog.disclosure.v1",
        value="UNRESTRICTED",
        allowed_endpoints=(),
    )
    observations = []
    for index in range(count):
        source = SourceReference(
            tenant_id="t",
            owner="calendar_observations",
            record_id="placeholder",
            record_version="v1",
            content_digest="placeholder",
            label_head="label1",
            label=label,
        )
        envelope = DisclosureEnvelope(
            tenant_id="t",
            content_digest="placeholder",
            sources=(source,),
            label=label,
            policy_head="p1",
            contour_head="contour1",
            deletion_fence_head="fence1",
        )
        observation = CalendarObservation(
            provider_id="fixture",
            calendar_id="cal",
            event_id=f"e{index}",
            version="v1",
            title=f"Event {index}",
            start_ns=10 + index,
            end_ns=20 + index,
            observed_at_ns=5,
            freshness=freshness,  # type: ignore[arg-type]
            envelope=envelope,
        )
        digest = hashlib.sha256(observation_payload(observation)).hexdigest()
        source = source.model_copy(
            update={
                "record_id": observation_id(observation),
                "content_digest": digest,
            }
        )
        observations.append(
            observation.model_copy(
                update={
                    "envelope": envelope.model_copy(
                        update={"content_digest": digest, "sources": (source,)}
                    ),
                }
            )
        )
    return CalendarBatch(revision="provider1", observations=tuple(observations))


def state(observations: CalendarBatch) -> CalendarReadState:
    return CalendarReadState(
        tenant_id="t",
        broker_epoch=1,
        credential_session_head="credential1",
        endpoint_channel_head="endpoint1",
        file_wal_observation="file1",
        journal_head="journal1",
        materialization_commitment="material1",
        owner_generation="gen1",
        owner_draining=False,
        principal_contour_head="contour1",
        storage_mutation_generation=1,
        trust_transition_head="trust1",
        authority_surface_digest="calendar1",
        amr_fingerprint="evidence-only",
        database_instance_id="db1",
        principal_id="principal1",
        channel_id="channel1",
        policy_head="p1",
        deletion_fence_head="fence1",
        provider_revision="provider1",
        source_heads_digest=source_heads_digest(observations),
    )


def request(context: WorkspaceReadContext, **changes: object) -> WorkspaceReadRequest:
    values: dict[str, object] = {
        "context": context,
        "query": "CALENDAR_AGENDA",
        "request_id": "req1",
        "read_attempt_id": "attempt1",
        "response_slot_id": "slot1",
        "max_rows": 2,
        "after_cursor": None,
        "detail_id": None,
        "range_start_ns": 0,
        "range_end_ns": 50,
    }
    return WorkspaceReadRequest.model_validate({**values, **changes})
