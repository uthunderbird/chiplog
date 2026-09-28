"""The live read-plan owner exposes no caller-shaped authority path."""

from __future__ import annotations

import copy

import pytest


def test_readplan_source_requires_the_exact_installed_runtime() -> None:
    from chiplog.composition.h1_live_readplan_source import H1LiveReadPlanSource

    with pytest.raises(TypeError, match="canonical installed runtime"):
        H1LiveReadPlanSource(object())


def test_readplan_capture_cannot_be_constructed_copied_or_serialized() -> None:
    from chiplog.composition.h1_live_readplan_source import _H1ReadPlanCapture

    with pytest.raises(TypeError, match="source-issued"):
        _H1ReadPlanCapture()
    capture = object.__new__(_H1ReadPlanCapture)
    with pytest.raises(TypeError, match="cannot be copied"):
        copy.copy(capture)
    with pytest.raises(TypeError, match="cannot be serialized"):
        capture.__reduce__()


def test_writer_admission_requires_exact_once_issued_capture_and_same_session() -> None:
    """A capture is not writer authority until this source consumed it for B."""
    from chiplog.composition.h1_live_readplan_source import (
        H1LiveReadPlanSource,
        H1ReadPlanSourceUnavailable,
        _H1ReadPlanCapture,
    )

    source = object.__new__(H1LiveReadPlanSource)
    source._captures = {}
    capture = object.__new__(_H1ReadPlanCapture)
    session = object()
    capture._session = session
    capture._consumed = False
    capture._issued = False
    source._captures[id(capture)] = capture

    with pytest.raises(H1ReadPlanSourceUnavailable, match="not exactly issued"):
        source._require_issued_capture(capture, session)

    capture._consumed = True
    capture._issued = True
    with pytest.raises(H1ReadPlanSourceUnavailable, match="not exactly issued"):
        source._require_issued_capture(capture, object())
    assert source._require_issued_capture(capture, session) is capture
