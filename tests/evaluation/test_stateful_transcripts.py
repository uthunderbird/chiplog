from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from chiplog.verification.transcript_suite import discover_transcripts, run_transcripts
from chiplog.verification.transcript_yaml import ContractYamlError, parse_contract_yaml
from chiplog.verification.transcripts import TranscriptCompileError, compile_transcript

ROOT = Path(__file__).resolve().parents[2]
TRANSCRIPTS = ROOT / "design-docs/transcripts"
DRAFTS = TRANSCRIPTS / "drafts/r13"


@pytest.fixture
def drafts(tmp_path: Path) -> Path:
    destination = tmp_path / "transcripts"
    shutil.copytree(DRAFTS, destination)
    return destination


def change(path: Path, before: str, after: str) -> None:
    original = path.read_text()
    assert before in original
    path.write_text(original.replace(before, after, 1))


@pytest.mark.parametrize("source", sorted(DRAFTS.glob("0*.md")), ids=lambda p: p.name)
def test_every_authored_stateful_scenario_compiles_all_variants(source: Path) -> None:
    compiled: Any = compile_transcript(source).compiled
    assert compiled["schema_version"] == 3
    assert {v["id"] for v in compiled["variants"]} == set(compiled["scenario"]["variants"])
    assert any(d["path"].endswith("common.md") for d in compiled["dependencies"])
    for variant in compiled["variants"]:
        assert [s["id"] for s in variant["steps"]] == compiled["scenario"]["variants"][
            variant["id"]
        ]["steps"]
        assert [e["step"] for e in variant["expect"]] == [s["id"] for s in variant["steps"]]
        assert all(s["end"] in variant["graph"] for s in variant["steps"])


def test_library_is_not_misrepresented_as_an_executable_scenario() -> None:
    compiled: Any = compile_transcript(DRAFTS / "common.md").compiled
    assert compiled["scenario"]["kind"] == "library"
    assert compiled["variants"] == []
    assert (
        compiled["library"]["scenario"]["fixtures"]["history-unavailable"]["result"]["messages"]
        is None
    )


def test_prefix_exposes_only_captures_reached_before_the_cutoff() -> None:
    compiled: Any = compile_transcript(DRAFTS / "02-adoption.md").compiled
    prefix = compiled["variants"][0]["prefix"]
    assert prefix["until"] == "request.display"
    assert "proposal" in prefix["captures"]
    assert "result" not in prefix["captures"]
    assert "accept.commit" not in prefix["reached"]


def test_split_expectations_are_merged_without_losing_state_or_calls() -> None:
    compiled: Any = compile_transcript(DRAFTS / "01-intention-from-history.md").compiled
    request = compiled["variants"][0]["expect"][0]
    assert len(request["calls"]) == 2
    assert request["events"][0]["id"] == "request.display"
    assert request["state"]["planning.results"] == {"delta": 0}
    assert request["context"]["updates"][0]["after_event"] == "request.display"


def test_transitive_common_contract_changes_invalidate_consumers_but_prose_does_not(
    drafts: Path,
) -> None:
    source = drafts / "03-replay.md"
    before = compile_transcript(source)
    common = drafts / "common.md"
    change(common, "model_calls: 8", "model_calls: 9")
    after = compile_transcript(source)
    assert after.source_digest == before.source_digest
    assert after.compiled_bundle_digest != before.compiled_bundle_digest
    common.write_text(common.read_text() + "\nРедакторское пояснение, не часть контракта.\n")
    assert compile_transcript(source).compiled_bundle_digest == after.compiled_bundle_digest


@pytest.mark.parametrize(
    "file,before,after,error",
    [
        ("01-intention-from-history.md", "  calls:", "  extra_calls:", "unknown expect"),
        (
            "01-intention-from-history.md",
            "message_ref: request",
            "message_ref: missing",
            "message_ref",
        ),
        (
            "01-intention-from-history.md",
            "purpose: $purpose",
            "purpose: $result",
            "forward capture",
        ),
        ("01-intention-from-history.md", "end: request.display", "end: missing", "step end"),
        (
            "02-adoption.md",
            "proposal: $prefix.proposal",
            "proposal: $prefix.missing",
            "unknown capture",
        ),
        ("02-adoption.md", "require_reached: true", "require_reached: false", "reached injection"),
        ("02-adoption.md", "before: stale.adoption", "before: missing", "unknown before"),
        ("03-replay.md", "until: accept.commit", "until: request.display", "unknown capture"),
        (
            "01-intention-from-history.md",
            "source: real_boundary",
            "source: fixture",
            "real_boundary",
        ),
        (
            "01-intention-from-history.md",
            "from_observed_result: $proposal_screen",
            "after_event: request.display",
            "duplicate contract key",
        ),
        (
            "01-intention-from-history.md",
            "after_event: request.display",
            "after_event: missing",
            "predecessor",
        ),
        (
            "01-intention-from-history.md",
            "common.md#/scenario/arrange",
            "common.md#/scenario/missing",
            "unresolved import",
        ),
        (
            "01-intention-from-history.md",
            "planning.results:",
            "planning.unknown:",
            "observation paths",
        ),
    ],
)
def test_invalid_stateful_contracts_fail_closed(
    drafts: Path, file: str, before: str, after: str, error: str
) -> None:
    source = drafts / file
    change(source, before, after)
    with pytest.raises(TranscriptCompileError, match=error):
        compile_transcript(source)


def test_duplicate_step_expect_field_is_not_overwritten(drafts: Path) -> None:
    source = drafts / "01-intention-from-history.md"
    source.write_text(
        source.read_text() + "\n```yaml transcript\nexpect:\n  step: request\n  calls: []\n```\n"
    )
    with pytest.raises(TranscriptCompileError, match="repeated expect"):
        compile_transcript(source)


def test_import_and_prefix_cycles_are_rejected(drafts: Path) -> None:
    common = drafts / "common.md"
    original = common.read_text()
    change(common, "  arrange:\n", "  arrange:\n    use: common.md#/scenario/arrange\n")
    with pytest.raises(TranscriptCompileError, match="cyclic transcript import"):
        compile_transcript(drafts / "01-intention-from-history.md")
    common.write_text(original)
    source = drafts / "03-replay.md"
    change(source, "source: 01-intention-from-history.md", "source: 03-replay.md")
    change(source, "variant: quarterly", "variant: identical")
    with pytest.raises(TranscriptCompileError, match="cyclic prefix"):
        compile_transcript(source)


@pytest.mark.parametrize(
    "payload",
    [
        "scenario: {id: a, id: b}",
        "scenario: &s {recursive: *s}",
        "scenario: !!python/object:bad {}",
        "scenario: {value: .nan}",
        "scenario: {1: value}",
    ],
)
def test_unsafe_or_ambiguous_yaml_is_rejected(payload: str) -> None:
    with pytest.raises(ContractYamlError):
        parse_contract_yaml(payload)


async def test_directory_run_compiles_every_kind_and_retains_honest_runtime_statuses(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "suite.json"
    result = await run_transcripts(TRANSCRIPTS, artifact)
    assert result == json.loads(artifact.read_bytes())
    assert result["compilation"] == "PASS"
    assert result["status"] == "NOT_RUNNABLE"
    assert {r["source"] for r in result["results"]} == {
        str(p) for p in discover_transcripts(TRANSCRIPTS)
    }
    by_id = {r["scenario_id"]: r for r in result["results"]}
    assert by_id["local-planning-receipt"]["status"] == "PASS"
    assert by_id["local-planning-receipt"]["observed"]["receipt"]["evidence_id"]
    assert by_id["r13-common"]["status"] == "COMPILED"
    assert by_id["calendar-proposal-confirmation"]["status"] == "NOT_RUNNABLE"
    for source in DRAFTS.glob("0*.md"):
        compiled: Any = compile_transcript(source).compiled
        reported = by_id[compiled["scenario"]["id"]]["variants"]
        assert {v["id"] for v in reported} == set(compiled["scenario"]["variants"])
        assert all(v["status"] == "NOT_RUNNABLE" and v["reasons"] for v in reported)
    with pytest.raises(ValueError, match="overwritten"):
        await run_transcripts(TRANSCRIPTS, artifact)


async def test_compile_failure_gets_an_artifact_and_cannot_become_not_runnable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "invalid.md"
    source.write_text("```yaml transcript\nscenario: {mystery: true}\n```\n")
    result = await run_transcripts(source, tmp_path / "result.json")
    assert result["status"] == result["compilation"] == "FAIL"
    assert result["results"][0]["phase"] == "compilation"


@pytest.mark.parametrize(
    "file,before,after,error",
    [
        (
            "01-intention-from-history.md",
            "message_ref: request",
            "message_ref: request\n    after: []",
            "input.after",
        ),
        ("common.md", "  bindings:\n", "  bindings:\n    bad: null\n", "binding must be a map"),
        (
            "01-intention-from-history.md",
            "  arrange:\n",
            "  arrange:\n    missing: $never_declared\n",
            "unknown capture",
        ),
        (
            "01-intention-from-history.md",
            "    arguments:\n",
            "    envelope: $never_declared\n    arguments:\n",
            "mutually exclusive",
        ),
        (
            "01-intention-from-history.md",
            "    history-unavailable:\n      operations:",
            "    typo-step:\n      operations:",
            "forbid scope",
        ),
    ],
)
async def test_malformed_bindings_and_references_retain_compile_failure_artifact(
    drafts: Path, tmp_path: Path, file: str, before: str, after: str, error: str
) -> None:
    change(drafts / file, before, after)
    result = await run_transcripts(
        drafts / "01-intention-from-history.md", tmp_path / "failure.json"
    )
    assert result["status"] == result["compilation"] == "FAIL"
    assert error in result["results"][0]["error"]
    assert (tmp_path / "failure.json").is_file()


async def test_directory_discovery_cannot_hide_unsupported_contract_fences(tmp_path: Path) -> None:
    (tmp_path / "bad.md").write_text("~~~yaml transcript\nscenario: {}\n~~~\n")
    result = await run_transcripts(tmp_path, tmp_path / "result.json")
    assert result["status"] == result["compilation"] == "FAIL"


def test_literal_canonical_message_is_not_interpreted_as_capture_reference(drafts: Path) -> None:
    change(
        drafts / "01-intention-from-history.md",
        "**Пользователь · request:** Добавь в планы то, что мы обсуждали про отчёт.",
        "**Пользователь · request:** $not_a_capture",
    )
    compiled: Any = compile_transcript(drafts / "01-intention-from-history.md").compiled
    assert compiled["variants"][0]["messages"]["request"]["text"] == "$not_a_capture"


@pytest.mark.parametrize(
    "scenario_id,expected_variants",
    [
        (
            "calendar-proposal-confirmation",
            {
                "confirmed",
                "duplicate-adoption",
                "changed-replay",
                "stale-display",
                "preaccept-dispatch",
                "crash-before-publication",
                "crash-after-publication",
                "invalidation-before-send",
                "send-before-invalidation",
            },
        ),
        (
            "unknown-calendar-outcome",
            {
                "unknown-held",
                "opaque-transmission",
                "restart-held",
                "takeover-held",
                "late-confirmation",
                "conflict-before-closure",
                "conflict-after-closure",
                "wrong-correlation",
                "exact-replay",
                "changed-replay",
                "replacement",
            },
        ),
    ],
)
async def test_calendar_variants_compile_without_claiming_runtime_execution(
    tmp_path: Path, scenario_id: str, expected_variants: set[str]
) -> None:
    source = TRANSCRIPTS / f"{scenario_id}.md"
    compiled: Any = compile_transcript(source).compiled
    assert compiled["schema_version"] == 3
    assert {v["id"] for v in compiled["variants"]} == expected_variants
    result = await run_transcripts(source, tmp_path / "calendar.json")
    assert result["compilation"] == "PASS"
    assert result["status"] == "NOT_RUNNABLE"
    reported = result["results"][0]["variants"]
    assert {v["id"] for v in reported} == expected_variants
    assert all(v["status"] == "NOT_RUNNABLE" and v["reasons"] for v in reported)


def test_unknown_calendar_prefix_stops_before_transmission_and_cannot_export_it() -> None:
    compiled: Any = compile_transcript(TRANSCRIPTS / "unknown-calendar-outcome.md").compiled
    for variant in compiled["variants"]:
        prefix = variant["prefix"]
        assert prefix["until"] == "adopt.commit"
        assert prefix["contract"]["scenario_id"] == "calendar-proposal-confirmation"
        assert {"display", "accepted_command", "effect"} <= set(prefix["captures"])
        assert "transmission" not in prefix["captures"]
        assert not any(event.startswith("send.") for event in prefix["reached"])
        assert variant["steps"][0]["id"] in {"lost", "opaque"}


def test_unknown_calendar_faults_preserve_distinct_observer_knowledge() -> None:
    compiled: Any = compile_transcript(TRANSCRIPTS / "unknown-calendar-outcome.md").compiled
    faults = {fault["id"]: fault for fault in compiled["scenario"]["faults"]}
    lost = faults["lost-response"]
    opaque = faults["opaque-response"]
    assert lost["boundary"] == "provider.after_effect_before_response"
    assert opaque["boundary"] == "provider.after_possible_transmission_before_observation"
    assert lost["change"]["observer_effect_knowledge"] == "ONE"
    assert opaque["change"]["observer_effect_knowledge"] == "UNKNOWN"
    assert all(f["require_reached"] is True and f["count"] == 1 for f in (lost, opaque))
    variants = {v["id"]: v for v in compiled["variants"]}
    known_effect = variants["unknown-held"]["expect"][0]["state"]
    unknown_effect = variants["opaque-transmission"]["expect"][0]["state"]
    assert known_effect["cal.provider_events"] == {"delta": 1}
    assert "cal.provider_events" not in unknown_effect
    # Submission is observable even when neither delivery nor the remote effect is known.
    # This closes the initial window against duplicate bytes with one transmission ID.
    assert unknown_effect["cal.provider_sends"] == {"delta": 1}
    assert known_effect["cal.outcome"] == unknown_effect["cal.outcome"] == {"eq": "UNKNOWN"}


def test_unknown_calendar_keeps_uncertainty_and_exact_replay_through_final_window() -> None:
    compiled: Any = compile_transcript(TRANSCRIPTS / "unknown-calendar-outcome.md").compiled
    variants = {v["id"]: v for v in compiled["variants"]}
    for variant in variants.values():
        for expectation in variant["expect"]:
            if expectation["step"] in {"drain", "wrong", "replay", "changed", "replacement"}:
                assert expectation["state"]["cal.outcome"] == {"eq": "UNKNOWN"}
    replay = next(e for e in variants["exact-replay"]["expect"] if e["step"] == "replay")
    assert replay["calls"][0]["result"]["fields"]["result"] == "$prefix.publication_result"
    assert replay["state"]["cal.command_result"] == {"eq": "$prefix.publication_result"}
