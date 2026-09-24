"""Bounded H1 preparation of an accepted completion's empty terminal work.

This owner verifies only immutable, canonical exchange joins.  It has no selected
source reader and therefore cannot authenticate that an otherwise valid source is
current; the broker must perform that admission before it calls this operation.
"""

from __future__ import annotations

import hashlib
import json

from .completion_terminal_work_sources import (
    AcceptedCompletionWorkSourceV1,
    decode_completion_terminal_work_source,
)
from .post_terminal_contracts import (
    PostTerminalWorkResult,
    PostTerminalWorkView,
    PreparedPostTerminalWork,
    PrepareTerminalWork,
    WorkCanonicalMember,
    WorkPreparationRejected,
)
from .post_terminal_record_contracts import validate_prepared_post_terminal_work


class H1TerminalWorkPreparationError(ValueError):
    """The request does not retain one exact accepted completion exchange."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def prepared_post_terminal_work_commitment(
    request: PrepareTerminalWork,
    ordered_work: tuple[PostTerminalWorkView, ...],
    complete_records: tuple[WorkCanonicalMember, ...],
) -> str:
    """Derive a self-free complete-result commitment from all owner output fields."""
    source_request_fingerprint = hashlib.sha256(request.canonical_bytes()).hexdigest()
    preimage = {
        "source_request_fingerprint": source_request_fingerprint,
        "ordered_work": [item.model_dump(mode="json") for item in ordered_work],
        "complete_records": [item.model_dump(mode="json") for item in complete_records],
    }
    return hashlib.sha256(
        b"chiplog.agent-loop.prepared-post-terminal-work.v1\0" + _canonical(preimage)
    ).hexdigest()


def _accepted_source(request: PrepareTerminalWork) -> AcceptedCompletionWorkSourceV1:
    try:
        source = decode_completion_terminal_work_source(request.original_terminalization_request)
    except ValueError as error:
        raise H1TerminalWorkPreparationError("terminal work source is not canonical") from error
    if not isinstance(source, AcceptedCompletionWorkSourceV1):
        raise H1TerminalWorkPreparationError(
            "H1 terminal work requires an accepted completion source"
        )
    if source.canonical_bytes() != request.original_terminalization_request:
        raise H1TerminalWorkPreparationError(
            "terminal work source bytes differ after canonical decode"
        )
    if (
        request.terminal_run != source.terminal_run
        or request.terminal_manifest != source.terminal_manifest_head
        or request.ordered_open_obligations != source.ordered_open_obligations
    ):
        raise H1TerminalWorkPreparationError("terminal work request differs from accepted source")
    return source


def prepare_h1_terminal_work(request: PrepareTerminalWork) -> PostTerminalWorkResult:
    """Prepare exactly the zero-obligation H1 companion set, without publication authority."""
    request = PrepareTerminalWork.model_validate_json(request.canonical_bytes())
    source = _accepted_source(request)
    if source.ordered_open_obligations:
        return WorkPreparationRejected(code="DENIED", reason="h1-nonempty-obligations")
    result = PreparedPostTerminalWork(
        source_request_fingerprint=hashlib.sha256(request.canonical_bytes()).hexdigest(),
        ordered_work=(),
        complete_records=(),
        complete_commitment=prepared_post_terminal_work_commitment(request, (), ()),
    )
    validate_prepared_post_terminal_work(request, result)
    return result
