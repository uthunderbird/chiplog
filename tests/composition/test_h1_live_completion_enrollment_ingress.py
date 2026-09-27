"""Fail-closed private B-to-authority ingress before its payload contract exists."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from chiplog.composition.h1_completion_preparation_session import (
    H1CompletionPreparationSession,
    H1CompletionSessionCut,
)
from chiplog.composition.h1_live_completion_enrollment import (
    H1LiveCompletionEnrollmentUnavailable,
    _EnrollmentRecord,
    _H1LiveCompletionEnrollment,
    _H1LiveCompletionHandle,
    _H1LiveCompletionIssuance,
    _IssuanceRecord,
)
from chiplog.composition.h1_live_publication_authority import H1LivePublicationAuthority
from chiplog.platform.authority_gate import AuthorityGate


def _enrollment(tmp_path: Path) -> tuple[_H1LiveCompletionEnrollment, H1LivePublicationAuthority]:
    """Build only the identity graph checked before future authority work."""
    enrollment = object.__new__(_H1LiveCompletionEnrollment)
    authority = H1LivePublicationAuthority()
    runtime = SimpleNamespace()
    mount = SimpleNamespace(_runtime=runtime, _authority=authority)
    first_path = object()
    enrollment._runtime = runtime
    enrollment._first_path_sources = first_path
    enrollment._native_sources = object()
    enrollment._scope_port = object()
    enrollment._conversation_sources = object()
    enrollment._completion_registry = object()
    enrollment._authority = authority
    enrollment._publication_mount = mount
    enrollment._gate = AuthorityGate.for_database(tmp_path / "authority.sqlite3")
    enrollment._records = {}
    enrollment._clearances = {}
    enrollment._issuances = {}
    enrollment._closed = False
    runtime._h1_live_completion_enrollment = enrollment
    runtime._h1_first_path_sources = enrollment._first_path_sources
    runtime._h1_native_member_sources = enrollment._native_sources
    runtime._h1_preissuance_registration_source_port = enrollment._scope_port
    runtime._h1_conversation_source_port = enrollment._conversation_sources
    runtime._h1_completion_exchange_registry = enrollment._completion_registry
    runtime._h1_live_publication_authority = authority
    runtime._h1_live_completion_mount = mount
    return enrollment, authority


def test_private_ingress_denies_unissued_foreign_stale_and_revoked_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No caller-shaped value can cross the frozen authority boundary."""
    enrollment, authority = _enrollment(tmp_path)
    require_open = enrollment._require_open_and_mounted

    def require_open_held() -> None:
        enrollment._gate.require_held()
        require_open()

    monkeypatch.setattr(enrollment, "_require_open_and_mounted", require_open_held)

    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="not enrollment-issued"):
        enrollment._consume_authority_issuance(authority=authority, source=object())
    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="authority is foreign"):
        enrollment._consume_authority_issuance(
            authority=H1LivePublicationAuthority(), source=object()
        )

    forged = object.__new__(_H1LiveCompletionIssuance)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(forged)
    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="not enrollment-issued"):
        enrollment._consume_authority_issuance(authority=authority, source=forged)

    # This white-box record models an otherwise identity-held source whose
    # session cut has gone stale.  It never supplies a positive payload; the
    # gate-held deny path must burn it before any future authority work.
    session = object.__new__(H1CompletionPreparationSession)
    cut = H1CompletionSessionCut(
        original_identity=cast(Any, object()),
        original_fingerprint="0" * 64,
        selected_seal=cast(Any, object()),
        first_path=cast(Any, object()),
        conversation=object(),
    )
    session._sources = enrollment._first_path_sources
    session._cut = cut
    enrolled = _EnrollmentRecord(session, object.__new__(_H1LiveCompletionHandle), cut)
    enrollment._records[id(session)] = enrolled
    stale = _IssuanceRecord(enrolled, forged, authority, state="ISSUED")
    enrollment._issuances[id(forged)] = stale

    def reject_stale(session_arg: object, cut_arg: object) -> None:
        assert session_arg is session
        assert cut_arg is cut
        raise H1LiveCompletionEnrollmentUnavailable("H1 session source cut is stale")

    monkeypatch.setattr(enrollment, "_require_live", reject_stale)
    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="source cut is stale"):
        enrollment._consume_authority_issuance(authority=authority, source=forged)
    assert stale.state == "BURNED"

    revoked_source = object.__new__(_H1LiveCompletionIssuance)
    revoked = _IssuanceRecord(enrolled, revoked_source, authority, state="ISSUED")
    enrollment._issuances[id(revoked_source)] = revoked
    enrollment._revoke_all()
    assert revoked.state == "BURNED"
    with pytest.raises(H1LiveCompletionEnrollmentUnavailable, match="closed"):
        enrollment._consume_authority_issuance(authority=authority, source=revoked_source)
