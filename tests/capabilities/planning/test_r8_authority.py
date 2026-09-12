from __future__ import annotations

import hashlib
import json

import pytest

from chiplog.capabilities.planning._r8_authority import (
    AuthorityTraceViolation,
    PlanningAuthorityRecorder,
    require_reproduced_trace,
    validate_proposal_freshness,
)
from chiplog.capabilities.planning.r8_boundary import (
    AuthorityRead,
    AuthorityReadKind,
    AuthorityTrace,
    ProposalFreshnessBinding,
)


class Source:
    def __init__(self, *, proposal: bool = False) -> None:
        registry = "adopted-proposal-create-v1" if proposal else "direct-principal-create-v1"
        sources: tuple[tuple[AuthorityReadKind, str], ...] = (
            ("TRUST", "deployment_trust.current"),
            ("PLANNING", "planning.committed"),
            ("REGISTRY", "planning.authority-registry"),
            ("ADOPTION", "planning.exact-adoption"),
        )
        self.rows: dict[AuthorityReadKind, AuthorityRead] = {
            kind: AuthorityRead(
                kind=kind,
                source_id=source,
                source_version="1",
                head="h",
                generation="g",
                frontier="f",
                valid_until_ns=100,
                canonical_value=(
                    json.dumps([["authority-row", registry]], separators=(",", ":")).encode()
                    if kind == "REGISTRY"
                    else b'{"value":1}'
                ),
            )
            for kind, source in sources
        }

    def read_authority(self, kind: AuthorityReadKind) -> AuthorityRead:
        return self.rows[kind]


def trace(source: Source, *, proposal: bool = False) -> AuthorityTrace:
    recorder = PlanningAuthorityRecorder(
        source, tenant_id="t", principal_id="p", now_ns=10, adopted_proposal=proposal
    )
    for kind in ("TRUST", "PLANNING", "REGISTRY"):
        recorder.read(kind)
    if proposal:
        recorder.read("ADOPTION")
    return recorder.finish()


def test_direct_principal_trace_requires_no_natural_language_adoption() -> None:
    observed = trace(Source())
    assert tuple(read.kind for read in observed.reads) == ("TRUST", "PLANNING", "REGISTRY")
    require_reproduced_trace(observed, trace(Source()))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reordered", "extra"])
def test_trace_rejects_incomplete_or_noncanonical_read_order(mutation: str) -> None:
    recorder = PlanningAuthorityRecorder(Source(), tenant_id="t", principal_id="p", now_ns=10)
    kinds = {
        "missing": ("TRUST",),
        "duplicate": ("TRUST", "TRUST"),
        "reordered": ("PLANNING", "TRUST", "REGISTRY"),
        "extra": ("TRUST", "PLANNING", "REGISTRY", "ADOPTION"),
    }[mutation]
    with pytest.raises(AuthorityTraceViolation):
        for kind in kinds:
            recorder.read(kind)  # type: ignore[arg-type]
        recorder.finish()


@pytest.mark.parametrize("kind", ["TRUST", "PLANNING", "REGISTRY"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("source_id", "projection.cache"),
        ("source_version", "unknown"),
        ("generation", "new"),
        ("frontier", "lagging"),
        ("valid_until_ns", 10),
    ],
)
def test_source_substitution_freshness_and_mixed_frontier_reject(
    kind: AuthorityReadKind, field: str, value: object
) -> None:
    source = Source()
    source.rows[kind] = source.rows[kind].model_copy(update={field: value})
    with pytest.raises(AuthorityTraceViolation):
        trace(source)


@pytest.mark.parametrize("kind", ["TRUST", "PLANNING", "REGISTRY"])
@pytest.mark.parametrize("field,value", [("canonical_value", b'{"value":2}'), ("head", "h2")])
def test_each_discovered_authority_dependency_is_reproduced(
    kind: AuthorityReadKind, field: str, value: object
) -> None:
    source = Source()
    original = trace(source)
    source.rows[kind] = source.rows[kind].model_copy(update={field: value})
    with pytest.raises(AuthorityTraceViolation):
        require_reproduced_trace(original, trace(source))


def binding() -> ProposalFreshnessBinding:
    value = ProposalFreshnessBinding(
        proposal_id="proposal",
        display_digest=hashlib.sha256(b"display").hexdigest(),
        principal_id="p",
        adoption_act_id="adoption",
        ingress_id="ingress",
        interpretation_revision="interpretation",
        command_digest="command",
        proposed_result_digest="result",
        trace=trace(Source(proposal=True), proposal=True),
    )
    source = Source(proposal=True)
    source.rows["ADOPTION"] = source.rows["ADOPTION"].model_copy(
        update={
            "canonical_value": json.dumps(
                value.model_dump(exclude={"trace"}), sort_keys=True, separators=(",", ":")
            ).encode()
        }
    )
    return value.model_copy(update={"trace": trace(source, proposal=True)})


@pytest.mark.parametrize(
    "field",
    [
        "proposal_id",
        "display_digest",
        "principal_id",
        "adoption_act_id",
        "ingress_id",
        "interpretation_revision",
        "command_digest",
        "proposed_result_digest",
    ],
)
def test_freshness_is_whole_binding_not_subset(field: str) -> None:
    original = binding()
    validate_proposal_freshness(original, original, display_bytes=b"display", now_ns=1)
    with pytest.raises(AuthorityTraceViolation, match="new adoption"):
        validate_proposal_freshness(
            original,
            original.model_copy(update={field: "changed"}),
            display_bytes=b"display",
            now_ns=1,
        )


@pytest.mark.parametrize("field,value", [("valid_until_ns", 1), ("source_id", "unknown")])
def test_identical_proposal_bindings_still_require_valid_current_sources(
    field: str, value: object
) -> None:
    original = binding()
    reads = original.trace.reads
    invalid = original.model_copy(
        update={
            "trace": original.trace.model_copy(
                update={"reads": (reads[0].model_copy(update={field: value}), *reads[1:])}
            )
        }
    )
    with pytest.raises(AuthorityTraceViolation):
        validate_proposal_freshness(invalid, invalid, display_bytes=b"display", now_ns=1)
