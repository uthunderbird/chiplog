"""Mounted witnesses for the private H1 native-member evidence issuer."""

from __future__ import annotations

from typing import Any, cast

import pytest

from chiplog.composition.h1_pre_request_member_evidence import (
    H1PreRequestMemberEvidence,
)


def test_member_issuer_requires_canonical_installed_dependencies() -> None:
    with pytest.raises(TypeError, match="canonical"):
        H1PreRequestMemberEvidence(cast(Any, object()), cast(Any, object()), cast(Any, object()))
