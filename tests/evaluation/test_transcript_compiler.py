from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.verification.models import FixtureRegistration
from chiplog.verification.registries import FIXTURES
from chiplog.verification.transcripts import TranscriptCompileError, compile_transcript

ROOT = Path(__file__).parents[2]


def test_active_fixture_registry_matches_authored_files() -> None:
    active = {item.relative_path for item in FIXTURES if item.state == "ACTIVE"}
    discovered = {
        str(path.relative_to(ROOT)) for path in (ROOT / "design-docs/transcripts").glob("*.md")
    }

    assert active == discovered
    reserved = [item for item in FIXTURES if item.state == "RESERVED"]
    assert [(item.fixture_id, item.relative_path) for item in reserved] == [("T04", None)]


@pytest.mark.parametrize("registration", [item for item in FIXTURES if item.state == "ACTIVE"])
def test_authored_transcript_compiles_with_two_level_identity(
    registration: FixtureRegistration,
) -> None:
    path_value = registration.relative_path
    scenario_id = registration.scenario_id
    assert isinstance(path_value, str)
    compiled = compile_transcript(ROOT / path_value)

    assert compiled.scenario_id == scenario_id
    assert len(compiled.source_digest) == 64
    assert len(compiled.compiled_bundle_digest) == 64


def test_design_change_changes_source_but_not_compiled_bundle(tmp_path: Path) -> None:
    source = ROOT / "design-docs/transcripts/calendar-proposal-confirmation.md"
    original = compile_transcript(source)
    changed = tmp_path / "changed.md"
    changed.write_text(
        source.read_text().replace("bind the proposal", "bind exactly the proposal", 1)
    )

    recompiled = compile_transcript(changed)

    assert recompiled.source_digest != original.source_digest
    assert recompiled.compiled_bundle_digest == original.compiled_bundle_digest


@pytest.mark.parametrize(
    "mutation, message",
    [
        ("scenario:\n  unknown: value", "unknown scenario fields"),
        ("mystery:\n  value: one", "unknown discriminator"),
        ("scenario:\n  id: one\nexpect:\n  state: {}", "exactly one discriminator"),
        ("scenario: scalar", "body must be a map or list"),
    ],
)
def test_transcript_rejects_malformed_or_unknown_contract(
    tmp_path: Path, mutation: str, message: str
) -> None:
    path = tmp_path / "invalid.md"
    path.write_text(f"# Invalid\n\n<!--\n{mutation}\n-->\n")

    with pytest.raises(TranscriptCompileError, match=message):
        compile_transcript(path)


def test_transcript_rejects_unknown_nested_field(tmp_path: Path) -> None:
    source = ROOT / "design-docs/transcripts/calendar-proposal-confirmation.md"
    path = tmp_path / "unknown-nested.md"
    path.write_text(source.read_text().replace("planning_changed: false", "mystery: false", 1))

    with pytest.raises(TranscriptCompileError, match="unknown state fields"):
        compile_transcript(path)


def test_transcript_rejects_mixed_response_modalities(tmp_path: Path) -> None:
    source = ROOT / "design-docs/transcripts/calendar-proposal-confirmation.md"
    path = tmp_path / "mixed-response.md"
    path.write_text(
        source.read_text().replace(
            "response:\n    semantics:", "response:\n    exact: forbidden mix\n    semantics:", 1
        )
    )

    with pytest.raises(TranscriptCompileError, match="exactly one modality"):
        compile_transcript(path)


def test_unknown_design_fields_fail_closed(tmp_path: Path) -> None:
    source = ROOT / "design-docs/transcripts/fact-claim-without-plan-change.md"
    changed = tmp_path / "changed.md"
    changed.write_text(
        source.read_text().replace(
            "design:\n  tool_call:", "design:\n  future_unknown_field: allowed\n  tool_call:", 1
        )
    )

    with pytest.raises(TranscriptCompileError, match="unknown design fields"):
        compile_transcript(changed)


def test_unterminated_contract_comment_fails_closed(tmp_path: Path) -> None:
    source = ROOT / "design-docs/transcripts/calendar-proposal-confirmation.md"
    changed = tmp_path / "unterminated.md"
    changed.write_text(source.read_text() + "\n<!--\nmystery:\n  value: one\n")

    with pytest.raises(TranscriptCompileError, match="unterminated"):
        compile_transcript(changed)
