import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from chiplog.capabilities.calendar_observations.observations import source_heads_digest
from chiplog.capabilities.evidence_journal.boundary import SourceReference as JournalSource
from chiplog.capabilities.evidence_journal.commands import Command
from chiplog.capabilities.projections.r9_boundary import ConversationEntry
from chiplog.capabilities.projections.workspace_boundary import DisclosureEnvelope, SourceReference
from chiplog.composition.r12 import R12Workspace, open_r12_workspace
from tests.support.calendar_reads import batch, state
from tests.support.evidence_journal import fixture, rows


async def seed_plan(database: Path) -> tuple[SourceReference, ...]:
    from chiplog.capabilities.planning import CreateIntentionLine
    from chiplog.capabilities.projections.disclosure import label
    from chiplog.composition.r6 import open_r6_runtime
    from chiplog.domain_primitives import RecordId, TenantId

    tenant = TenantId("tenant")
    # The retained R6 component is only a fixture writer for the existing R5
    # publication format. The target under test is the canonical R12 composition.
    async with open_r6_runtime(database, operator_secret=b"r12-fixture") as runtime:
        await runtime.bootstrap(
            tenant_id="tenant",
            database_instance_id="db1",
            principal_id="owner",
            credential_id="credential",
            session_id="session",
            token="token",
        )
        outcome = await runtime.create(
            tenant_id="tenant",
            principal_id="owner",
            credential_id="credential",
            session_id="session",
            command=CreateIntentionLine(
                RecordId(tenant, "plan-command"),
                RecordId(tenant, "pool-plan"),
                RecordId(tenant, "pool-revision"),
                "Pool remains planned",
                "direct-act",
            ),
        )
        assert outcome.disposition == "COMMITTED"
    return tuple(
        sorted(
            (
                SourceReference(
                    tenant_id="tenant",
                    owner="planning",
                    record_id=str(row[1]),
                    record_version="1",
                    content_digest=hashlib.sha256(row[2]).hexdigest(),
                    label_head="plan-label-1",
                    label=label("UNRESTRICTED"),
                )
                for row in rows(database)
                if row[0] == "planning" and isinstance(row[2], bytes)
            ),
            key=lambda source: source.record_id,
        )
    )


@asynccontextmanager
async def workspace(
    tmp_path: Path,
    utterance: str = "Я выполнил occurrence:pool 2026-08-18.",
    *,
    with_plan: bool = False,
    restricted: bool = False,
) -> AsyncIterator[tuple[R12Workspace, Command]]:
    identity, command = fixture(utterance)
    if restricted:
        restricted_label = identity.sources[0].label.model_copy(
            update={
                "value": "ENDPOINT_RESTRICTED",
                "allowed_endpoints": ("channel1", "cli"),
            }
        )
        updated_sources = tuple(
            source.model_copy(update={"label": restricted_label}) for source in identity.sources
        )
        identity = identity.model_copy(update={"sources": updated_sources})
        command = command.model_copy(
            update={
                "claim": command.claim.model_copy(
                    update={
                        "envelope": command.claim.envelope.model_copy(
                            update={
                                "sources": updated_sources,
                                "label": restricted_label,
                            }
                        ),
                    }
                )
            }
        )
    plan_sources = await seed_plan(tmp_path / "canonical.db") if with_plan else ()
    observations = batch(count=1)
    observations = observations.model_copy(
        update={
            "observations": tuple(
                item.model_copy(
                    update={
                        "envelope": item.envelope.model_copy(
                            update={
                                "tenant_id": identity.tenant,
                                "policy_head": identity.heads.policy,
                                "contour_head": identity.heads.contour,
                                "deletion_fence_head": identity.heads.deletion,
                                "sources": tuple(
                                    source.model_copy(update={"tenant_id": identity.tenant})
                                    for source in item.envelope.sources
                                ),
                            }
                        )
                    }
                )
                for item in observations.observations
            )
        }
    )
    trusted = state(observations).model_copy(
        update={
            "tenant_id": identity.tenant,
            "principal_id": identity.principal,
            "policy_head": identity.heads.policy,
            "principal_contour_head": identity.heads.contour,
            "deletion_fence_head": identity.heads.deletion,
            "source_heads_digest": source_heads_digest(observations),
        }
    )
    identity = identity.model_copy(
        update={
            "sources": (
                *identity.sources,
                *(
                    JournalSource.model_validate_json(source.model_dump_json())
                    for source in plan_sources
                ),
                *(
                    JournalSource.model_validate_json(source.model_dump_json())
                    for item in observations.observations
                    for source in item.envelope.sources
                ),
            )
        }
    )
    async with open_r12_workspace(
        tmp_path / "canonical.db",
        screens=tmp_path / "screens.db",
        peers={"peer": identity},
        peer="peer",
        channel="channel1",
        database_id="db1",
        calendar_batch=observations,
        calendar_state=trusted,
        calendar_ledger_path=tmp_path / "calendar.db",
        planning_sources={"pool-plan": plan_sources} if with_plan else {},
    ) as runtime:
        source = identity.sources[0]
        envelope = DisclosureEnvelope.model_validate_json(
            command.claim.envelope.model_dump_json()
        ).model_copy(update={"content_digest": source.content_digest})
        assert await runtime.accept(
            ConversationEntry(
                tenant_id=identity.tenant,
                conversation_id="conversation:" + identity.tenant,
                entry_id=source.record_id,
                sequence=1,
                origin_channel_id="channel1",
                visible_channels=("channel1",),
                role="principal",
                accepted_bytes=utterance.encode(),
                envelope=envelope,
            )
        ) in {"COMMITTED", "REPLAY"}
        current = runtime.ingress.authenticate("peer")
        assert current is not None
        yield runtime, command.model_copy(update={"heads": current.heads})
