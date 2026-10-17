"""Shared builders extracted without changing their behavior."""

from pathlib import Path

from chiplog.adapters.driven.deployment_trust._journal import IndependentTenantDecisionJournal
from chiplog.platform.authority_gate import AuthorityGate, FileIdentity, checked_file_identity


def _provision(tmp_path: Path) -> tuple[Path, AuthorityGate, str, tuple[FileIdentity, ...]]:
    database = tmp_path / "authority.sqlite"
    database.touch()
    gate = AuthorityGate.for_database(database)
    path = tmp_path / "journal"
    journal = IndependentTenantDecisionJournal.for_authority_bundle(path, authority_gate=gate)
    decision_id = journal.append(b"first", None)
    for sidecar in (
        path,
        path.with_suffix(".head"),
        path.with_suffix(".key"),
        path.with_suffix(".lock"),
    ):
        sidecar.chmod(0o600)
    return (
        path,
        gate,
        decision_id,
        tuple(
            checked_file_identity(sidecar)
            for sidecar in (path, path.with_suffix(".key"), path.with_suffix(".lock"))
        ),
    )
