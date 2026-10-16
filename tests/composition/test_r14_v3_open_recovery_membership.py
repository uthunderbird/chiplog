"""V3 extraction after caller-authenticated history; retained parser is isolated.

These surrogate envelopes establish extraction cardinality, not publication or
journal authority. The mounted cross-seal witness remains separately required.
"""

import json
from types import SimpleNamespace as NS

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.composition import r14_accepted_open_accounting_records as records
from chiplog.composition import r14_native_sealed_accounting_v3_publication as publication


@pytest.mark.parametrize(
    "case", ["empty", "single", "same-seal", "two-seals", "duplicate", "malformed"]
)
def test_v3_extracts_all_distinct_call_seal_recoveries_before_member_selection(monkeypatch, case):
    entries = tuple(
        dict(
            kind="DECIDED",
            operation_kind=records.ACCEPTED_OPEN_RECOVERY_OPERATION,
            operation_id=label,
        )
        for label in ("A", "B")
    )
    if case == "empty":
        entries = ()
    elif case == "single":
        entries = entries[:1]
    evidence = {}
    for label in ("A", "B"):
        evidence[label] = NS(
            request=NS(
                cut=NS(
                    original_call_id="call-A" if case == "duplicate" else "call-" + label,
                    response_id="seal-A" if case in ("same-seal", "duplicate") else "seal-" + label,
                )
            )
        )
    parsed = []

    def retained(entry):
        label = entry["operation_id"]
        parsed.append(label)
        if case == "malformed" and label == "B":
            raise ValueError("malformed marked retained envelope")
        return evidence[label]

    runtime = NS(
        _loop_decisions=lambda: NS(
            entries=lambda: tuple(
                (entry["operation_id"], None, json.dumps(entry).encode()) for entry in entries
            )
        )
    )
    monkeypatch.setattr(records, "retained_accepted_open_recovery", retained)
    if case == "duplicate":
        with pytest.raises(LoopRejected, match="duplicate"):
            publication._authenticated_accepted_open_recoveries(runtime)
    elif case == "malformed":
        with pytest.raises(ValueError, match="malformed marked"):
            publication._authenticated_accepted_open_recoveries(runtime)
    else:
        result = publication._authenticated_accepted_open_recoveries(runtime)
        assert len(result) == len(entries)
        assert all(
            item is evidence[entry["operation_id"]]
            for item, entry in zip(result, entries, strict=True)
        )
    assert parsed == [entry["operation_id"] for entry in entries]
