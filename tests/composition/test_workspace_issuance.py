"""Issued owner-verified screens stay bound independently of derivative cache bytes."""

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from chiplog.adapters.driven.deployment_trust import IndependentTenantDecisionJournal
from chiplog.adapters.driven.workspace_issuance import WorkspaceIssuanceJournal
from chiplog.adapters.driven.workspace_sqlite import SQLiteWorkspaceStore
from chiplog.capabilities.projections.disclosure import CurrentDisclosureGuard, label
from chiplog.capabilities.projections.provenance import (
    ProvenanceBinding,
    ProvenanceClosures,
    ProvenanceSubject,
)
from chiplog.capabilities.projections.r9_boundary import (
    BudgetPolicy,
    ScreenSnapshot,
    ScreenSnapshotV2,
    WorkspaceIntegrityError,
    WorkspaceRejected,
    WorkspaceState,
)
from chiplog.capabilities.projections.workspace import (
    DashboardFamilySpec,
    DashboardRegistry,
    QueryDashboardBuilder,
    Workspace,
    _snapshot_identity,
)
from chiplog.capabilities.projections.workspace_boundary import (
    DisclosureEnvelope,
    SourceReference,
    WorkspaceReadContext,
)
from chiplog.platform.authority_gate import AuthorityGate
from tests.support.workspace import Heads, IssuedContext, Queries, context, request, source


class _Derivatives:
    async def register(self, snapshot: ScreenSnapshot) -> None:
        pass


class _BoundHeads(Heads):
    def __init__(self) -> None:
        self.sources = (
            source(),
            source().model_copy(update={"record_id": "other", "label": label("UNRESTRICTED")}),
        )
        self.bindings = tuple(
            ProvenanceBinding(
                subject=ProvenanceSubject(
                    tenant_id="tenant",
                    producer="core.conversation",
                    record_id=item.record_id,
                    revision="1",
                ),
                content_digest=item.content_digest,
                sources=(item,),
            )
            for item in self.sources
        )
        self.closures = ProvenanceClosures(self.bindings)

    def validate(self, candidate: SourceReference, cut: WorkspaceReadContext) -> None:
        assert cut == context()
        if candidate not in self.sources:
            raise WorkspaceRejected("not an independently registered source")

    def validate_manifest(self, candidate: DisclosureEnvelope, cut: WorkspaceReadContext) -> None:
        pytest.fail("subject-bound screen used legacy digest lookup")

    def validate_subject(
        self, subject: ProvenanceSubject, candidate: DisclosureEnvelope, cut: WorkspaceReadContext
    ) -> None:
        assert cut == context()
        self.closures.check(subject, candidate)


def _workspace(
    path: Path, issuance: WorkspaceIssuanceJournal | None = None, *, bound: bool = False
) -> Workspace:
    contexts = IssuedContext()
    return Workspace(
        DashboardRegistry(
            (
                DashboardFamilySpec(
                    "core.conversation",
                    "conversation.builder",
                    "1",
                    QueryDashboardBuilder(Queries(), "CONVERSATION_HISTORY"),
                ),
            )
        ),
        SQLiteWorkspaceStore(path),
        contexts,
        CurrentDisclosureGuard(contexts, _BoundHeads() if bound else Heads()),
        "local",
        _Derivatives(),
        issuance=issuance,
        subject_bound=bound,
    )


async def _state(path: Path) -> WorkspaceState:
    return await _workspace(path).refresh(
        request(), BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
    )


def _journal(path: Path) -> WorkspaceIssuanceJournal:
    raw = IndependentTenantDecisionJournal.for_authority_bundle(
        path / "issuance", authority_gate=AuthorityGate.for_database(path / "canonical.sqlite")
    )
    return WorkspaceIssuanceJournal(raw, "tenant")


async def test_issued_state_survives_reopen_and_missing_cache(tmp_path: Path) -> None:
    state = await _state(tmp_path / "builder.sqlite")
    journal = _journal(tmp_path)
    journal.select_verified(state, 0)
    journal.select_verified(state, 0)
    recovered = _journal(tmp_path).load("channel", 1)
    assert recovered.model_dump_json() == state.model_dump_json()
    cache = SQLiteWorkspaceStore(tmp_path / "recovered.sqlite")
    cache.save(recovered, 0)
    _journal(tmp_path).verify(cache.load("tenant", "channel", 1))
    changed = state.model_copy(update={"invalidations": ("different-selection",)})
    with pytest.raises(WorkspaceRejected, match="already issued different bytes"):
        journal.select_verified(changed, 0)
    with pytest.raises(WorkspaceRejected, match="predecessor conflict"):
        journal.select_verified(state.model_copy(update={"sequence": 3}), 2)


async def test_same_bytes_source_swap_with_recomputed_cache_hash_rejects_after_reopen(
    tmp_path: Path,
) -> None:
    path = tmp_path / "screens.sqlite"
    state = await _state(path)
    _journal(tmp_path).select_verified(state, 0)
    snapshot = state.screens[0]
    original_envelope = snapshot.envelopes[0]
    other_source = original_envelope.sources[0].model_copy(
        update={"record_id": "different-origin", "label": label("UNRESTRICTED")}
    )
    changed_envelope = original_envelope.model_copy(
        update={"sources": (other_source,), "label": other_source.label}
    )
    changed_snapshot = snapshot.model_copy(update={"envelopes": (changed_envelope,)})
    changed_snapshot = changed_snapshot.model_copy(
        update={
            "ref": changed_snapshot.ref.model_copy(
                update={"snapshot_id": _snapshot_identity(changed_snapshot)}
            )
        }
    )
    changed = state.model_copy(update={"screens": (changed_snapshot,)})
    encoded = changed.model_dump_json().encode()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE workspace SET bytes=?,digest=?",
            (encoded, hashlib.sha256(encoded).hexdigest()),
        )
    cached = SQLiteWorkspaceStore(path).load("tenant", "channel", 1)
    assert cached.screens[0].screen_bytes == snapshot.screen_bytes
    with pytest.raises(WorkspaceIntegrityError, match=r"workspace\.issued_verify") as failure:
        _journal(tmp_path).verify(cached)
    assert failure.value.__cause__ is not None


@pytest.mark.parametrize("suffix", ("", ".head", ".key"))
async def test_missing_independent_issuance_component_does_not_authorize_cached_state(
    tmp_path: Path, suffix: str
) -> None:
    state = await _state(tmp_path / "screens.sqlite")
    journal = _journal(tmp_path)
    journal.select_verified(state, 0)
    (tmp_path / ("issuance" + suffix)).unlink()
    with pytest.raises(WorkspaceIntegrityError, match=r"workspace\.issued_read"):
        journal.verify(state)


async def test_authenticated_journal_still_rejects_foreign_or_forked_state(tmp_path: Path) -> None:
    state = await _state(tmp_path / "screens.sqlite")
    journal = _journal(tmp_path)
    with pytest.raises(WorkspaceRejected, match="identity/sequence"):
        journal.select_verified(state.model_copy(update={"tenant_id": "foreign"}), 0)
    journal.select_verified(state, 0)
    next_state = state.model_copy(update={"sequence": 2})
    journal.select_verified(next_state, 1)
    # An old exact selection can be replayed without renewing its context or head.
    journal.select_verified(state, 0)
    assert journal.load("channel", 2) == next_state
    with pytest.raises(WorkspaceIntegrityError, match=r"workspace\.issued_read"):
        journal.load("other-channel", 1)


async def test_workspace_issues_before_cache_write_and_recovers_exact_selected_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = _journal(tmp_path)
    path = tmp_path / "screens.sqlite"
    workspace = _workspace(path, journal)

    def crash_after_issuance(state: WorkspaceState, expected_sequence: int) -> None:
        assert journal.load(state.channel_id, state.sequence) == state
        raise RuntimeError("crash before derivative cache write")

    with monkeypatch.context() as patch:
        patch.setattr(workspace._store, "save", crash_after_issuance)
        with pytest.raises(RuntimeError, match="crash before derivative"):
            await workspace.refresh(
                request(), BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
            )
    original = _journal(tmp_path).load("channel", 1)
    # Recovery consumes the original issuance; it never reruns an owner query.
    assert _journal(tmp_path).recover_cache(SQLiteWorkspaceStore(path), "channel") == original

    async def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("replay reran an owner query")

    monkeypatch.setattr(Queries, "read", forbidden)
    reopened = _workspace(path, _journal(tmp_path))
    assert reopened.replay("tenant", "channel", 1, context()) == original
    assert "secret" in reopened.context_text(original, context())


async def test_workspace_release_and_refresh_reject_rehashed_unissued_predecessor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "screens.sqlite"
    workspace = _workspace(path, _journal(tmp_path))
    policy = BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
    state = await workspace.refresh(request(), policy)
    changed = state.model_copy(update={"tool_availability": ("forged-tool",)})
    raw = changed.model_dump_json().encode()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE workspace SET bytes=?,digest=?", (raw, hashlib.sha256(raw).hexdigest())
        )
    reopened = _workspace(path, _journal(tmp_path))
    with pytest.raises(WorkspaceIntegrityError, match=r"workspace\.issued_verify"):
        reopened.context_text(changed, context())
    with pytest.raises(WorkspaceIntegrityError, match=r"workspace\.issued_verify"):
        await reopened.refresh(request(), policy, changed)


async def test_v2_subject_and_sources_cannot_be_swapped_together_to_another_valid_origin(
    tmp_path: Path,
) -> None:
    path = tmp_path / "screens.sqlite"
    workspace = _workspace(path, _journal(tmp_path), bound=True)
    state = await workspace.refresh(
        request(), BudgetPolicy(total=100, fixed=0, output=0, conversation_floor=32)
    )
    snapshot = state.screens[0]
    assert isinstance(snapshot, ScreenSnapshotV2)
    assert snapshot.subjects == (_BoundHeads().bindings[0].subject,)
    assert isinstance(
        WorkspaceState.model_validate_json(state.model_dump_json()).screens[0], ScreenSnapshotV2
    )
    reopened = _workspace(path, _journal(tmp_path), bound=True)
    assert "secret" in reopened.context_text(state, context())
    alternative = _BoundHeads().bindings[1]
    changed_envelope = snapshot.envelopes[0].model_copy(
        update={"sources": alternative.sources, "label": alternative.sources[0].label}
    )
    # Both candidate origin and source are authentic, but belong to a different row.
    _BoundHeads().validate_subject(alternative.subject, changed_envelope, context())
    changed_snapshot = snapshot.model_copy(
        update={"subjects": (alternative.subject,), "envelopes": (changed_envelope,)}
    )
    changed_snapshot = changed_snapshot.model_copy(
        update={
            "ref": snapshot.ref.model_copy(
                update={"snapshot_id": _snapshot_identity(changed_snapshot)}
            )
        }
    )
    changed = state.model_copy(update={"screens": (changed_snapshot,)})
    raw = changed.model_dump_json().encode()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE workspace SET bytes=?,digest=?", (raw, hashlib.sha256(raw).hexdigest())
        )
    with pytest.raises(WorkspaceIntegrityError, match=r"workspace\.issued_verify"):
        _workspace(path, _journal(tmp_path), bound=True).context_text(changed, context())


def test_published_legacy_workspace_bytes_and_snapshot_identity_remain_unchanged(
    tmp_path: Path,
) -> None:
    # Produced using bf42f45's actual Workspace.refresh, before V2 DTO changes.
    raw = (Path(__file__).parents[1] / "support/fixtures/r9_legacy_workspace.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "daa7fbddf11926df9408ec4c6a82fd25a0b666845b36bbe210066f2975a3b225"
    )
    state = WorkspaceState.model_validate_json(raw)
    assert state.model_dump_json().encode() == raw
    assert all(type(screen) is ScreenSnapshot for screen in state.screens)
    assert all(_snapshot_identity(screen) == screen.ref.snapshot_id for screen in state.screens)
    path = tmp_path / "legacy.sqlite"
    SQLiteWorkspaceStore(path).save(state, 0)
    assert "secret" in _workspace(path).context_text(state, context())


async def test_recovery_rejects_existing_changed_cache_instead_of_silently_repairing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "screens.sqlite"
    state = await _state(path)
    journal = _journal(tmp_path)
    journal.select_verified(state, 0)
    raw = state.model_copy(update={"retained": ("forged.family",)}).model_dump_json().encode()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "UPDATE workspace SET bytes=?,digest=?", (raw, hashlib.sha256(raw).hexdigest())
        )
    with pytest.raises(WorkspaceIntegrityError, match=r"workspace\.cache_recovery"):
        _journal(tmp_path).recover_cache(SQLiteWorkspaceStore(path), "channel")
    assert SQLiteWorkspaceStore(path).load("tenant", "channel", 1).retained == ("forged.family",)
