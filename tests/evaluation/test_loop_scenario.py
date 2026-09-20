import json
import re
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.verification.loop_scenario import run_scenario
from chiplog.verification.transcripts import TranscriptCompileError, compile_transcript

SOURCE = Path(__file__).parents[2] / "design-docs/transcripts/local-planning-receipt.md"


async def test_authored_local_receipt_executes_production_ports_and_retains_evidence(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "result.json"
    result = await run_scenario(SOURCE, tmp_path / "loop.sqlite", artifact)
    assert result["status"] == "PASS"
    assert result["deployment"] == result["provider_execution"] == "HOLD"
    assert result["compiled_bundle_digest"] == compile_transcript(SOURCE).compiled_bundle_digest
    assert result == json.loads(artifact.read_bytes())
    assert result["fault_observer"] == {"name": "proposal-before-authority", "reached": 1}
    with pytest.raises(LoopRejected, match="overwritten"):
        await run_scenario(SOURCE, tmp_path / "loop.sqlite", artifact)


async def test_fixture_answers_only_a_reached_matching_request(tmp_path: Path) -> None:
    source = tmp_path / "wrong-match.md"
    source.write_text(SOURCE.read_text().replace("match: turn 1", "match: turn 7", 1))
    with pytest.raises(LoopRejected, match="matcher"):
        await run_scenario(source, tmp_path / "loop.sqlite", tmp_path / "result.json")
    assert not (tmp_path / "result.json").exists()


async def test_provider_boundary_fixture_cannot_be_registered_as_hermetic(tmp_path: Path) -> None:
    source = tmp_path / "provider.md"
    source.write_text(SOURCE.read_text().replace("boundary: model", "boundary: calendar.create", 1))
    with pytest.raises((LoopRejected, TranscriptCompileError)):
        await run_scenario(source, tmp_path / "loop.sqlite", tmp_path / "result.json")
    assert not (tmp_path / "loop.sqlite").exists()


@pytest.mark.parametrize(
    "before,after",
    [
        ("Help me plan weekly swimming", "Help me plan weekly running"),
        ("Confirm this entry.", "Do not confirm this entry."),
        ("end: proposal.displayed", "end: run.succeeded"),
        ("message_ref: request", "message_ref: accept"),
        ("peer: hermetic-ingress", "peer: unknown"),
        ("step: accept", "step: request"),
        ("status: executable", "status: design"),
        ("receipt · exact", "receipt · пример"),
    ],
)
async def test_changed_input_or_step_contract_cannot_pass_stale_execution(
    tmp_path: Path, before: str, after: str
) -> None:
    text = SOURCE.read_text()
    assert before in text
    source = tmp_path / "changed.md"
    source.write_text(text.replace(before, after, 1))
    with pytest.raises(LoopRejected, match="unsupported"):
        await run_scenario(source, tmp_path / "loop.sqlite", tmp_path / "result.json")
    assert not (tmp_path / "loop.sqlite").exists()


async def test_changed_canonical_exact_text_fails_observed_response(tmp_path: Path) -> None:
    text = SOURCE.read_text()
    before = "**Chiplog · receipt · exact:** Committed local intention: Swim every week."
    assert before in text
    source = tmp_path / "changed.md"
    source.write_text(text.replace(before, "**Chiplog · receipt · exact:** Other receipt.", 1))
    with pytest.raises(LoopRejected, match="does not satisfy"):
        await run_scenario(source, tmp_path / "loop.sqlite", tmp_path / "result.json")
    assert not (tmp_path / "result.json").exists()


async def test_reordered_steps_cannot_pass_stale_execution(tmp_path: Path) -> None:
    text = SOURCE.read_text()
    steps = re.findall(r"```yaml transcript\nstep:\n.*?\n```", text, re.DOTALL)
    assert len(steps) == 2
    changed = text.replace(steps[0], "STEP_PLACEHOLDER", 1).replace(steps[1], steps[0], 1)
    changed = changed.replace("STEP_PLACEHOLDER", steps[1], 1)
    source = tmp_path / "reordered.md"
    source.write_text(changed)
    assert (
        compile_transcript(source).compiled_bundle_digest
        != compile_transcript(SOURCE).compiled_bundle_digest
    )
    with pytest.raises(LoopRejected, match="ordered step"):
        await run_scenario(source, tmp_path / "loop.sqlite", tmp_path / "result.json")
    assert not (tmp_path / "loop.sqlite").exists()


async def test_request_state_is_checked_before_adoption(tmp_path: Path) -> None:
    text = SOURCE.read_text()
    before = "planning_changed:\n      eq: false"
    assert before in text
    source = tmp_path / "changed-request-state.md"
    source.write_text(text.replace(before, "planning_changed:\n      eq: true", 1))
    with pytest.raises(LoopRejected, match="observed request state does not satisfy"):
        await run_scenario(source, tmp_path / "loop.sqlite", tmp_path / "result.json")
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize(
    "before,after",
    [
        ("planning_changed:\n      eq: false", "planning_changed:\n      eq: 0"),
        ("external_effects:\n      eq: 0", "external_effects:\n      eq: false"),
    ],
)
async def test_state_predicate_types_cannot_alias_booleans_and_integers(
    tmp_path: Path, before: str, after: str
) -> None:
    text = SOURCE.read_text()
    assert before in text
    source = tmp_path / "wrong-state-type.md"
    source.write_text(text.replace(before, after, 1))
    with pytest.raises(LoopRejected, match="state predicate type"):
        await run_scenario(source, tmp_path / "loop.sqlite", tmp_path / "result.json")
    assert not (tmp_path / "loop.sqlite").exists()
    assert not (tmp_path / "result.json").exists()
