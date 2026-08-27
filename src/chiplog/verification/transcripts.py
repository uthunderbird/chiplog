from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .identity import canonical_json_bytes, sha256_bytes
from .yaml_subset import SubsetYamlError, parse_yaml_subset

DISCRIMINATORS = {"scenario", "design", "expect", "forbid"}
SCENARIO_FIELDS = {"id", "version", "vision_version", "arrange", "fixtures", "allowed_outcomes"}
ARRANGE_FIELDS = {
    "principal",
    "channel",
    "clock",
    "timezone",
    "calendar_events",
    "planning_state",
    "fact_state",
    "proposal",
    "adoption",
}
EXPECT_FIELDS = {"response", "trace", "state"}
RESPONSE_FIELDS = {"semantics", "exact"}
DESIGN_FIELDS = {"tool_call", "tool_result", "rationale"}
STATE_FIELDS = {
    "planning_changed",
    "external_effects",
    "calendar_events",
    "provider_event_id",
    "receipt_links",
    "fact_claims",
    "planning_revisions_added",
    "run",
    "obligation",
    "provider_outcome",
    "additional_create_attempts",
}


class TranscriptCompileError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledTranscript:
    scenario_id: str
    source_digest: str
    compiled_bundle_digest: str
    compiled: dict[str, object]


def compile_transcript(path: Path) -> CompiledTranscript:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise TranscriptCompileError("transcript must be UTF-8") from error
    semantic: dict[str, list[object]] = {"scenario": [], "expect": [], "forbid": []}
    scenario_id: str | None = None
    comments = _extract_contract_comments(text)
    for body in comments:
        try:
            parsed = parse_yaml_subset(body)
        except SubsetYamlError as error:
            raise TranscriptCompileError(str(error)) from error
        if not isinstance(parsed, dict) or len(parsed) != 1:
            raise TranscriptCompileError("each comment must have exactly one discriminator")
        discriminator, value = next(iter(parsed.items()))
        if discriminator not in DISCRIMINATORS:
            raise TranscriptCompileError(f"unknown discriminator: {discriminator}")
        if not isinstance(value, (dict, list)):
            raise TranscriptCompileError(f"{discriminator} body must be a map or list")
        if discriminator == "scenario":
            if semantic["scenario"]:
                raise TranscriptCompileError("scenario must occur exactly once")
            assert isinstance(value, dict)
            unknown = set(value) - SCENARIO_FIELDS
            if unknown:
                raise TranscriptCompileError(f"unknown scenario fields: {sorted(unknown)}")
            required = {
                "id",
                "version",
                "vision_version",
                "arrange",
                "fixtures",
                "allowed_outcomes",
            }
            if set(value) != required:
                raise TranscriptCompileError("scenario fields must match the closed schema")
            if not isinstance(value["id"], str) or not isinstance(value["version"], int):
                raise TranscriptCompileError("scenario id/version types are invalid")
            if not isinstance(value["arrange"], dict) or not isinstance(value["fixtures"], list):
                raise TranscriptCompileError("scenario arrange/fixtures types are invalid")
            if not isinstance(value["allowed_outcomes"], list):
                raise TranscriptCompileError("allowed_outcomes must be a list")
            _validate_scenario(value)
            scenario_id = value["id"]
        elif discriminator == "expect":
            assert isinstance(value, dict)
            _validate_expect(value)
        elif discriminator == "forbid":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise TranscriptCompileError("forbid must be a list of strings")
        elif discriminator == "design":
            assert isinstance(value, dict)
            _reject_unknown(value, DESIGN_FIELDS, "design")
        if discriminator != "design":
            semantic[discriminator].append(value)
    if scenario_id is None or len(semantic["scenario"]) != 1:
        raise TranscriptCompileError("one scenario block is required")
    compiled: dict[str, object] = {
        "schema_version": 1,
        "scenario": semantic["scenario"][0],
        "expect": semantic["expect"],
        "forbid": semantic["forbid"],
    }
    return CompiledTranscript(
        scenario_id=scenario_id,
        source_digest=sha256_bytes(raw),
        compiled_bundle_digest=sha256_bytes(canonical_json_bytes(compiled)),
        compiled=compiled,
    )


def _extract_contract_comments(text: str) -> list[str]:
    comments: list[str] = []
    position = 0
    while position < len(text):
        start = text.find("<!--", position)
        orphan_end = text.find("-->", position)
        if orphan_end != -1 and (start == -1 or orphan_end < start):
            raise TranscriptCompileError("orphan HTML comment terminator")
        if start == -1:
            break
        end = text.find("-->", start + 4)
        if end == -1:
            raise TranscriptCompileError("unterminated HTML contract comment")
        body = text[start + 4 : end].strip()
        if "<!--" in body:
            raise TranscriptCompileError("nested HTML contract comments are not allowed")
        comments.append(body)
        position = end + 3
    return comments


def _reject_unknown(mapping: dict[str, object], allowed: set[str], context: str) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise TranscriptCompileError(f"unknown {context} fields: {sorted(unknown)}")


def _validate_scenario(value: dict[str, object]) -> None:
    arrange = value["arrange"]
    fixtures = value["fixtures"]
    allowed_outcomes = value["allowed_outcomes"]
    assert isinstance(arrange, dict)
    assert isinstance(fixtures, list)
    assert isinstance(allowed_outcomes, list)
    _reject_unknown(arrange, ARRANGE_FIELDS, "arrange")
    if not all(isinstance(item, str) for item in allowed_outcomes):
        raise TranscriptCompileError("allowed_outcomes must contain only strings")
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise TranscriptCompileError("each fixture must be a map")
        if set(fixture) != {"boundary", "match", "result"}:
            raise TranscriptCompileError("fixture fields must be boundary, match, result")
        if not all(isinstance(item, str) for item in fixture.values()):
            raise TranscriptCompileError("fixture fields must be strings")


def _validate_expect(value: dict[str, object]) -> None:
    _reject_unknown(value, EXPECT_FIELDS, "expect")
    if "response" in value:
        response = value["response"]
        if not isinstance(response, dict):
            raise TranscriptCompileError("expect.response must be a map")
        _reject_unknown(response, RESPONSE_FIELDS, "response")
        modalities = {field for field in RESPONSE_FIELDS if field in response}
        if len(modalities) != 1:
            raise TranscriptCompileError("expect.response must use exactly one modality")
        if "semantics" in response and not (
            isinstance(response["semantics"], list)
            and all(isinstance(item, str) for item in response["semantics"])
        ):
            raise TranscriptCompileError("response.semantics must be a list of strings")
        if "exact" in response and not isinstance(response["exact"], str):
            raise TranscriptCompileError("response.exact must be a string")
    if "trace" in value and not (
        isinstance(value["trace"], list) and all(isinstance(item, str) for item in value["trace"])
    ):
        raise TranscriptCompileError("expect.trace must be a list of strings")
    if "state" in value:
        state = value["state"]
        if not isinstance(state, dict):
            raise TranscriptCompileError("expect.state must be a map")
        _reject_unknown(state, STATE_FIELDS, "state")
