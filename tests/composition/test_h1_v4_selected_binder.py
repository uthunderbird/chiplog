"""V4 selected-history routing retains the authenticated issuance version."""

from __future__ import annotations

import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from chiplog.composition import h1_completion_issuance as issuance_module
from chiplog.composition import h1_historical_selected_sources as historical
from chiplog.composition import r14_loop_history
from chiplog.composition.h1_completion_issuance import H1CompletionIssuanceV4
from chiplog.platform._owner_publication_contracts import CompleteDeliveryBatchV2


def test_v4_validation_reaches_the_historical_selected_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old explicit V4 hold is gone only because the selected reader runs."""
    batch = CompleteDeliveryBatchV2.model_construct()
    value = H1CompletionIssuanceV4.model_construct()
    selected: list[tuple[object, object, object]] = []

    monkeypatch.setattr(issuance_module, "_decode_value", lambda _: value)
    monkeypatch.setattr(
        issuance_module,
        "verify_h1_historical_sources",
        lambda actual_batch, actual_value, runtime: selected.append(
            (actual_batch, actual_value, runtime)
        ),
    )

    runtime = object()
    assert issuance_module.validate_h1_completion_issuance(batch, runtime) is value
    assert selected == [(batch, value, runtime)]


def test_v4_selected_binder_preserves_the_exact_decoded_issuance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A V4 owner record reaches the common selected binder without coercion."""
    batch = CompleteDeliveryBatchV2.model_construct()
    value = H1CompletionIssuanceV4.model_construct()
    decision = object()
    runtime = object()

    monkeypatch.setattr(historical, "_require_common_cli_runtime", lambda _: runtime)
    monkeypatch.setattr(issuance_module, "decode_h1_completion_issuance", lambda _: value)
    monkeypatch.setattr(historical, "_verify_historical_selection", lambda *_: decision)

    bound = historical.bind_selected_h1_completion(batch, runtime)
    assert bound.decision is decision
    assert bound.issuance is value


def test_v4_historical_origin_uses_the_retained_v2_owner_request() -> None:
    origin = object()
    value = H1CompletionIssuanceV4.model_construct(
        effects_call=SimpleNamespace(request=SimpleNamespace(retained_origin=origin)),
        assembly=SimpleNamespace(ordered_effects=(SimpleNamespace(),)),
    )

    assert historical._retained_origin(value) is origin


@pytest.mark.parametrize("version", ("V3", "V4"))
def test_r14_routes_v3_and_v4_by_authenticated_schema(
    monkeypatch: pytest.MonkeyPatch, version: str
) -> None:
    """R14 asks the retained-schema dispatcher; it has no runtime-version fallback."""
    batch = CompleteDeliveryBatchV2.model_construct(
        authentication=SimpleNamespace(applicability_schema="retained", applicability_bytes=b"wire")
    )

    monkeypatch.setattr(
        issuance_module,
        "dispatch_h1_completion_issuance_schema",
        lambda outer, raw: version,
    )

    assert r14_loop_history._completion_v2_schema_dispatch(batch) == "H1"


class _Frame:
    def __init__(self, value: object) -> None:
        self._value = value

    def model_dump(self, *, mode: str) -> object:
        assert mode == "json"
        return self._value


def test_v4_selected_preseal_frames_reject_a_substituted_full_broker_dto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V4 needs the selected preseal full-frame proof before historical use."""
    issue = SimpleNamespace(
        sent=_Frame({"request_id": "issue", "metadata": "selected"}),
        returned=_Frame({"request_id": "issue", "result": "selected"}),
        sent_at_ns=1,
        returned_at_ns=2,
    )
    current = SimpleNamespace(
        sent=_Frame({"request_id": "current", "metadata": "selected"}),
        returned=_Frame({"request_id": "current", "result": "selected"}),
        sent_at_ns=3,
        returned_at_ns=4,
    )
    value = H1CompletionIssuanceV4.model_construct(
        scope_issue_exchange=issue,
        scope_current_exchange=current,
    )
    decision = {
        "h1_preseal_pe_anchor": "anchor",
        "h1_preseal_p_scope_wires_v1": {"wires": "selected"},
        "h1_preseal_p_scope_frames_v1": {"frames": "selected"},
    }
    native = SimpleNamespace(
        seal=SimpleNamespace(
            raw_bytes=json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
        )
    )
    anchor = SimpleNamespace(
        binding="binding",
        as_dict=lambda: {
            "p": {
                "accepted_issue_wire_digest": "issue-digest",
                "accepted_current_wire_digest": "current-digest",
            }
        },
    )
    frames = SimpleNamespace(
        wires=lambda **_: (
            SimpleNamespace(
                sent=_Frame({"request_id": "issue", "metadata": "substituted"}),
                returned=issue.returned,
                sent_at_ns=issue.sent_at_ns,
                returned_at_ns=issue.returned_at_ns,
            ),
            current,
        )
    )
    monkeypatch.setattr(historical, "decode_h1_preseal_pe_anchor_record", lambda _: anchor)
    monkeypatch.setattr(
        historical, "decode_h1_preseal_p_scope_wires", lambda *_args, **_kw: object()
    )
    monkeypatch.setattr(
        historical, "decode_h1_preseal_p_scope_frames", lambda *_args, **_kw: frames
    )

    with pytest.raises(ValueError, match="full-frame scope issue differs"):
        historical._require_selected_preseal_scope_frames(value, native)


def test_v4_selected_history_requires_the_preseal_full_frame_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The V4 selection route cannot bypass the selected broker-frame proof."""
    batch = CompleteDeliveryBatchV2.model_construct()
    value = H1CompletionIssuanceV4.model_construct(
        assembly=SimpleNamespace(
            original_completion_request=SimpleNamespace(source=object()),
        ),
        scope_current_exchange=object(),
        terminal_admission=SimpleNamespace(preterminal_current_exchange=object()),
        final_current_exchange=object(),
    )
    decision = {
        "h1_preseal_pe_anchor": "anchor",
        "h1_preseal_p_scope_wires_v1": {"wires": "selected"},
    }
    native = SimpleNamespace(
        seal=SimpleNamespace(
            raw_bytes=json.dumps(decision, sort_keys=True, separators=(",", ":")).encode()
        )
    )
    gate = SimpleNamespace(hold=lambda: nullcontext())
    ports = SimpleNamespace(gate=gate)

    monkeypatch.setattr(historical, "_require_common_cli_runtime", lambda _: object())
    monkeypatch.setattr(historical, "_require_historical_ports", lambda _: ports)
    monkeypatch.setattr(
        historical,
        "_open_historical_ports",
        lambda _: (object(), object(), object(), object()),
    )
    monkeypatch.setattr(historical, "_selected_owner_decision", lambda *_: object())
    monkeypatch.setattr(historical, "_verify_selected_v2_delivery_closure", lambda *_: None)
    monkeypatch.setattr(historical, "_verify_v4_historical_effects_origin", lambda _: None)
    retained = SimpleNamespace(initialization_envelope_bytes=b"x")
    monkeypatch.setattr(historical, "_retained_origin", lambda _: retained)
    monkeypatch.setattr(
        historical, "read_historical_h0_r17", lambda *_: pytest.fail("H0 read must not run")
    )
    monkeypatch.setattr(
        historical,
        "_verify_historical_scope",
        lambda *_args, **_kw: pytest.fail("historical scope read must not run"),
    )
    monkeypatch.setattr(
        historical,
        "_verify_v2_current_as_of",
        lambda *_args, **_kw: pytest.fail("historical CURRENT read must not run"),
    )
    monkeypatch.setattr(
        historical,
        "H1FirstPathSources",
        lambda _: SimpleNamespace(replay_selected_native_cut=lambda *_args, **_kw: native),
    )

    with pytest.raises(
        historical.H1V2SelectedPresealFullFrameUnavailable,
        match="lacks full-frame sibling",
    ):
        historical._verify_historical_selection(batch, value, object())
