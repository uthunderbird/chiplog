import json
from pathlib import Path

import pytest

from chiplog.capabilities.agent_loop.contracts import LoopRejected
from chiplog.verification.loop_scenario import run_scenario
from chiplog.verification.transcripts import compile_transcript

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
    with pytest.raises(LoopRejected, match="unregistered"):
        await run_scenario(source, tmp_path / "loop.sqlite", tmp_path / "result.json")
    assert not (tmp_path / "loop.sqlite").exists()
