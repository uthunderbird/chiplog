"""Focused witnesses for the first H1 recovery-session completion split."""

from __future__ import annotations

import inspect

from chiplog.composition.h1_completion_preparation_session import (
    H1CompletionPreparationSession,
)


def test_completion_public_api_has_no_caller_supplied_recovery_pin() -> None:
    """Only session-retained preflight state can reach the completion dispatcher."""
    prepare = inspect.signature(H1CompletionPreparationSession.prepare_first_path_completion)
    dispatch = inspect.signature(H1CompletionPreparationSession._dispatch_first_path_completion)

    assert tuple(prepare.parameters) == ("self",)
    assert tuple(dispatch.parameters) == ("self",)
