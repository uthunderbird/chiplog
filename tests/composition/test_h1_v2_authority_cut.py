"""Mounted RED witnesses for the versioned V2 post-write authority cut.

The witnesses use the installed V3 Prepare -> V2 seal path.  They do not
manufacture a checkpoint, an authority snapshot, or a recovery capability:
each positive assertion starts from the authenticated native DECIDED entry
and its independently reconstructed physical V2 command.
"""

from __future__ import annotations

from typing import cast

import pytest

from chiplog.composition.h1_v2_authority_cut import resolve_h1_v2_authority_cut
from chiplog.platform._sqlite import PhysicalPublicationCommand
from chiplog.platform.authority_checkpoint import (
    AuthorityCheckpointStore,
)

_CUT_FIELDS = {
    "kind",
    "version",
    "tenant_id",
    "operation_kind",
    "operation_id",
    "request_fingerprint",
    "expected_head",
    "commit_sequence",
    "selected_response_seal",
    "envelope_sha256",
    "database_binding",
    "predecessor",
    "resulting",
    "reference",
}


def test_typed_v2_authority_cut_descriptor_rejects_missing_extension() -> None:
    """No enrolled old V2 fixture exists; do not manufacture one by stripping a journal.

    The production resolver receives an authenticated DECIDED mapping before
    it dispatches this strict descriptor.  This unit witness fixes the legacy
    boundary: an absent extension is not a version-zero authority cut.
    """
    with pytest.raises(ValueError, match="H1 V2 authority cut cannot be verified"):
        resolve_h1_v2_authority_cut(
            {},
            cast(AuthorityCheckpointStore, object()),
            ("unused", 0, 0),
            cast(PhysicalPublicationCommand, object()),
        )


__all__ = []
