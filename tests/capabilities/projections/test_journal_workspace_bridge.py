from chiplog.adapters.driven.workspace_journal import JournalWorkspaceQueries
from chiplog.capabilities.evidence_journal.commands import (
    JournalQuery,
    JournalRecord,
    JournalRow,
    JournalView,
)
from chiplog.capabilities.projections.workspace_boundary import WorkspaceReadContext
from tests.support.evidence_journal import fixture
from tests.support.workspace import request


async def test_complete_owner_lineage_retains_ancestor_restrictions_and_sources() -> None:
    identity, command = fixture()
    current = JournalRecord(
        record_id="current",
        tenant=identity.tenant,
        principal=identity.principal,
        kind="FactClaim",
        family="line",
        predecessor="ancestor",
        operation="CORRECT",
        claim=command.claim,
        display=None,
        command_id="correction",
        fingerprint="owner-current",
    )
    restricted = command.claim.envelope.label.model_copy(
        update={
            "value": "ENDPOINT_RESTRICTED",
            "allowed_endpoints": ("cli",),
        }
    )
    ancestor_source = command.claim.envelope.sources[0].model_copy(
        update={
            "record_id": "ancestor-source",
            "label": restricted,
        }
    )
    ancestor = current.model_copy(
        update={
            "record_id": "ancestor",
            "predecessor": None,
            "operation": "RECORD",
            "claim": command.claim.model_copy(
                update={
                    "envelope": command.claim.envelope.model_copy(
                        update={
                            "label": restricted,
                            "sources": (ancestor_source,),
                        }
                    ),
                }
            ),
        }
    )
    owner_row = JournalRow(
        record=current, status="corrected", current_positive=True, lineage=(ancestor, current)
    )
    read = request().model_copy(update={"query": "JOURNAL_CLAIMS"})

    class Contexts:
        def validate(self, context: WorkspaceReadContext) -> None:
            assert context == read.context

    class Queries:
        def project(self, peer: str, query: JournalQuery) -> JournalView:
            return JournalView(
                disposition="CURRENT", head=read.context.snapshot_frontier, rows=(owner_row,)
            )

    result = await JournalWorkspaceQueries(Queries(), "peer", identity.heads, Contexts()).read(read)
    assert JournalRow.model_validate_json(result.rows[0].canonical_payload) == owner_row
    assert result.rows[0].envelope.label.value == "ENDPOINT_RESTRICTED"
    assert result.rows[0].envelope.label.allowed_endpoints == ("cli",)
    assert {source.record_id for source in result.rows[0].envelope.sources} == {
        "ancestor-source",
        "ingress-1",
    }
