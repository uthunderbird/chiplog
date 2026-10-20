"""Private E issuance admits only a live installed worker source."""

from __future__ import annotations

import pytest

from chiplog.composition.h1_pre_request_worker_evidence import H1PreRequestWorkerEvidence


def test_private_issuer_rejects_unmounted_or_cross_owned_dependencies_before_authority() -> None:
    with pytest.raises(TypeError, match="canonical"):
        H1PreRequestWorkerEvidence(object(), object(), object())
