from __future__ import annotations

from pathlib import Path

import pytest

from chiplog.verification.models import FixtureRegistration
from chiplog.verification.registries import FIXTURES
from chiplog.verification.transcripts import TranscriptCompileError, compile_transcript

ROOT = Path(__file__).resolve().parents[2]


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
    source = tmp_path / "original.md"
    original_text = V2 + "\n```yaml transcript\ndesign:\n  rationale: bind the proposal\n```\n"
    source.write_text(original_text)
    original = compile_transcript(source)
    changed = tmp_path / "changed.md"
    assert "bind the proposal" in original_text
    changed.write_text(original_text.replace("bind the proposal", "bind exactly the proposal", 1))

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
    path = tmp_path / "unknown-nested.md"
    assert "planning_changed:" in V2
    path.write_text(V2.replace("planning_changed:", "mystery:", 1))

    with pytest.raises(TranscriptCompileError, match="unknown state fields"):
        compile_transcript(path)


def test_transcript_rejects_mixed_response_modalities(tmp_path: Path) -> None:
    path = tmp_path / "mixed-response.md"
    assert "response:\n    exact:" in V2
    path.write_text(
        V2.replace(
            "response:\n    exact:", "response:\n    semantics: forbidden mix\n    exact:", 1
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
    source = ROOT / "design-docs/transcripts/fact-claim-without-plan-change.md"
    changed = tmp_path / "unterminated.md"
    changed.write_text(source.read_text() + "\n<!--\nmystery:\n  value: one\n")

    with pytest.raises(TranscriptCompileError, match="unterminated"):
        compile_transcript(changed)


V2 = """```yaml transcript
scenario:
  id: test
  version: 2
  format_version: 2
  status: design
  vision_version: test
  arrange:
    proposal: $message.request
  fixtures: []
  allowed_outcomes: []
```
**Пользователь · request:** Plan swimming.
```yaml transcript
step:
  id: request
  input:
    kind: message
    message_ref: request
  end: proposal.displayed
```
**Chiplog · proposal · пример:** A possible reply.
**Chiplog · receipt · exact:** Saved.
```yaml transcript
expect:
  step: request
  response:
    exact: $message.receipt
  state:
    planning_changed:
      eq: true
```
"""


def test_canonical_messages_resolve_and_have_semantic_identity(tmp_path: Path) -> None:
    source = tmp_path / "v2.md"
    source.write_text(V2)
    original = compile_transcript(source)
    assert original.compiled["scenario"]["arrange"]["proposal"] == "Plan swimming."  # type: ignore[index]
    for before, after, semantic_change in [
        ("A possible reply.", "Another illustration.", False),
        ("Plan swimming.", "Plan running.", True),
        ("Saved.", "Committed.", True),
    ]:
        assert before in V2
        source.write_text(V2.replace(before, after, 1))
        changed = compile_transcript(source)
        assert changed.source_digest != original.source_digest
        assert (
            changed.compiled_bundle_digest != original.compiled_bundle_digest
        ) == semantic_change


@pytest.mark.parametrize(
    "before,after,error",
    [
        ("$message.request", "$message.missing", "dangling"),
        ("message_ref: request", "message_ref: missing", "dangling"),
        ("message_ref: request", "message_ref: receipt", "user message"),
        ("step: request", "step: missing", "existing step"),
        ("      eq: true", "      mystery: true", "operator"),
        ("      eq: true", "      delta: true", "operand"),
        ("      eq: true", "      unchanged: 1", "operand"),
        ("  end: proposal.displayed", "  mystery: proposal.displayed", "step fields"),
        ("    kind: message", "    kind: unknown", "input kind"),
        ("  status: design", "  status: unknown", "valid status"),
        ("```yaml transcript", "```yaml transcript broken", "malformed"),
        (
            "**Chiplog · receipt · exact:** Saved.",
            "**Пользователь · request:** duplicate",
            "duplicate",
        ),
    ],
)
def test_v2_closed_schema_rejects_mutations(
    tmp_path: Path, before: str, after: str, error: str
) -> None:
    assert before in V2
    source = tmp_path / "invalid.md"
    source.write_text(V2.replace(before, after, 1))
    with pytest.raises(TranscriptCompileError, match=error):
        compile_transcript(source)


@pytest.mark.parametrize(
    "suffix,error",
    [
        ("\n<!--\nforbid: []\n-->", "mixed"),
        ("\n```yaml transcript\nexpect:", "unterminated"),
        ("\n~~~yaml transcript\nexpect:\n  step: missing\n~~~\n", "malformed"),
    ],
)
def test_v2_rejects_mixed_or_unterminated_containers(
    tmp_path: Path, suffix: str, error: str
) -> None:
    source = tmp_path / "invalid.md"
    source.write_text(V2 + suffix)
    with pytest.raises(TranscriptCompileError, match=error):
        compile_transcript(source)


def test_canonical_message_inside_code_is_not_a_stimulus(tmp_path: Path) -> None:
    source = tmp_path / "quoted.md"
    source.write_text(V2 + "\n```text\n**Пользователь · request:** quoted duplicate\n```\n")
    compiled = compile_transcript(source)
    source.write_text(V2)
    assert compiled.compiled_bundle_digest == compile_transcript(source).compiled_bundle_digest


def test_transcript_nested_in_outer_code_fence_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "nested.md"
    source.write_text("````markdown\n" + V2 + "\n````\n")
    with pytest.raises(TranscriptCompileError, match="nested"):
        compile_transcript(source)


def test_legacy_hidden_contract_remains_supported(tmp_path: Path) -> None:
    source = tmp_path / "legacy.md"
    source.write_text("""<!--
scenario:
  id: legacy
  version: 1
  vision_version: test
  arrange:
    proposal: legacy input
  fixtures: []
  allowed_outcomes: []
-->
<!--
expect:
  response:
    exact: legacy output
  state:
    planning_changed: false
-->
""")
    compiled = compile_transcript(source)
    assert compiled.compiled["schema_version"] == 1
    assert compiled.scenario_id == "legacy"


def test_referenced_example_enters_semantic_identity(tmp_path: Path) -> None:
    source = tmp_path / "example.md"
    text = V2.replace("exact: $message.receipt", "exact: $message.proposal", 1)
    source.write_text(text)
    original = compile_transcript(source)
    source.write_text(text.replace("A possible reply.", "Another reply.", 1))
    assert original.compiled_bundle_digest != compile_transcript(source).compiled_bundle_digest
