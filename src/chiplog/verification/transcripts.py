from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .identity import canonical_json_bytes, sha256_bytes
from .transcript_schema import StatefulSchemaError
from .transcript_stateful import StatefulCompiler
from .transcript_yaml import ContractYamlError, parse_contract_yaml
from .yaml_subset import SubsetYamlError, parse_yaml_subset

DISCRIMINATORS = {"scenario", "design", "expect", "forbid", "step"}
SCENARIO_FIELDS = {
    "id",
    "version",
    "vision_version",
    "arrange",
    "fixtures",
    "allowed_outcomes",
    "format_version",
    "status",
}
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
EXPECT_FIELDS = {"response", "trace", "state", "step"}
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
    semantic: dict[str, list[object]] = {"scenario": [], "expect": [], "forbid": [], "step": []}
    scenario_id: str | None = None
    comments = _extract_contract_comments(text)
    fences = _extract_contract_fences(text)
    if comments and fences:
        raise TranscriptCompileError("mixed hidden and fenced contracts are not allowed")
    try:
        structured_blocks = [parse_contract_yaml(body) for body in fences]
        if any(
            isinstance(block.get("scenario"), dict) and "protocol_version" in block["scenario"]
            for block in structured_blocks
        ):
            compiled_stateful = StatefulCompiler(path).compile()
            return CompiledTranscript(
                scenario_id=compiled_stateful["scenario"]["id"],
                source_digest=sha256_bytes(raw),
                compiled_bundle_digest=sha256_bytes(canonical_json_bytes(compiled_stateful)),
                compiled=compiled_stateful,
            )
    except (ContractYamlError, StatefulSchemaError, OSError, UnicodeError) as error:
        raise TranscriptCompileError(str(error)) from error
    messages = _extract_messages(text) if fences else []
    message_index = {message["id"]: message for message in messages}
    referenced: set[str] = set()
    bodies = fences or comments
    for index, body in enumerate(bodies):
        try:
            parsed = structured_blocks[index] if fences else parse_yaml_subset(body)
        except SubsetYamlError as error:
            raise TranscriptCompileError(str(error)) from error
        if not isinstance(parsed, dict) or len(parsed) != 1:
            raise TranscriptCompileError("each comment must have exactly one discriminator")
        discriminator, value = next(iter(parsed.items()))
        if discriminator not in DISCRIMINATORS:
            raise TranscriptCompileError(f"unknown discriminator: {discriminator}")
        if not isinstance(value, (dict, list)):
            raise TranscriptCompileError(f"{discriminator} body must be a map or list")
        if discriminator != "forbid" and not isinstance(value, dict):
            raise TranscriptCompileError(f"{discriminator} body must be a map")
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
            if fences:
                required |= {"format_version", "status"}
            if set(value) != required:
                raise TranscriptCompileError("scenario fields must match the closed schema")
            if not isinstance(value["id"], str) or not isinstance(value["version"], int):
                raise TranscriptCompileError("scenario id/version types are invalid")
            if not isinstance(value["arrange"], dict) or not isinstance(value["fixtures"], list):
                raise TranscriptCompileError("scenario arrange/fixtures types are invalid")
            if not isinstance(value["allowed_outcomes"], list):
                raise TranscriptCompileError("allowed_outcomes must be a list")
            if fences and (
                value["format_version"] != 2 or value["status"] not in ("design", "executable")
            ):
                raise TranscriptCompileError(
                    "fenced contract requires format_version 2 and valid status"
                )
            if fences:
                for fixture in value["fixtures"]:
                    if (
                        isinstance(fixture, dict)
                        and fixture.get("boundary") == "model"
                        and isinstance(fixture.get("result"), dict)
                    ):
                        fixture["result"] = canonical_json_bytes(fixture["result"]).decode()
            _validate_scenario(value, structured=bool(fences))
            scenario_id = value["id"]
        elif discriminator == "expect":
            assert isinstance(value, dict)
            _validate_expect(value, structured=bool(fences))
        elif discriminator == "forbid":
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise TranscriptCompileError("forbid must be a list of strings")
        elif discriminator == "design":
            assert isinstance(value, dict)
            _reject_unknown(value, DESIGN_FIELDS, "design")
            if fences and any(
                not isinstance(value[field], dict)
                for field in ("tool_call", "tool_result")
                if field in value
            ):
                raise TranscriptCompileError("design tool_call/tool_result must be maps")
            _resolve_messages(value, message_index, set())
        elif discriminator == "step":
            if not fences or not isinstance(value, dict):
                raise TranscriptCompileError("step requires a fenced map")
            if set(value) != {"id", "input", "end"} or not all(
                isinstance(value[field], str) and value[field] for field in ("id", "end")
            ):
                raise TranscriptCompileError("step fields must be id, input, end")
            step_input = value["input"]
            if not isinstance(step_input, dict):
                raise TranscriptCompileError("step.input must be a map")
            _reject_unknown(step_input, {"kind", "message_ref", "peer", "principal"}, "step.input")
            if step_input.get("kind") not in ("message", "authenticated_adoption"):
                raise TranscriptCompileError("unsupported step input kind")
            ref = step_input.get("message_ref")
            if not isinstance(ref, str) or ref not in message_index:
                raise TranscriptCompileError("dangling step message_ref")
            if message_index[ref]["role"] != "Пользователь":
                raise TranscriptCompileError("step input must reference a user message")
            referenced.add(ref)
        if discriminator != "design":
            semantic[discriminator].append(_resolve_messages(value, message_index, referenced))
    if scenario_id is None or len(semantic["scenario"]) != 1:
        raise TranscriptCompileError("one scenario block is required")
    compiled: dict[str, object] = {
        "schema_version": 1,
        "scenario": semantic["scenario"][0],
        "expect": semantic["expect"],
        "forbid": semantic["forbid"],
    }
    if fences:
        steps = semantic["step"]
        ids = [step["id"] for step in steps if isinstance(step, dict)]
        if not ids or len(set(ids)) != len(ids):
            raise TranscriptCompileError("steps must have unique ids and cannot be empty")
        for expectation in semantic["expect"]:
            if not isinstance(expectation, dict) or expectation.get("step") not in ids:
                raise TranscriptCompileError("expect.step must reference an existing step")
        compiled.update(
            schema_version=2,
            steps=steps,
            messages=[
                message
                for message in messages
                if message["mode"] != "пример" or message["id"] in referenced
            ],
        )
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


def _validate_scenario(value: dict[str, object], *, structured: bool = False) -> None:
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
        if structured and fixture["boundary"] != "model":
            if not isinstance(fixture["boundary"], str) or not all(
                isinstance(fixture[field], dict) for field in ("match", "result")
            ):
                raise TranscriptCompileError(
                    "fixture boundary must be a string and match/result maps"
                )
        elif not all(isinstance(item, str) for item in fixture.values()):
            raise TranscriptCompileError("fixture fields must be strings")


def _validate_expect(value: dict[str, object], *, structured: bool = False) -> None:
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
        for predicate in state.values():
            if isinstance(predicate, dict):
                if len(predicate) != 1 or next(iter(predicate)) not in {"eq", "delta", "unchanged"}:
                    raise TranscriptCompileError(
                        "state predicate must contain one eq/delta/unchanged operator"
                    )
                operator, operand = next(iter(predicate.items()))
                if (operator == "delta" and type(operand) is not int) or (
                    operator == "unchanged" and type(operand) is not bool
                ):
                    raise TranscriptCompileError("state operator operand has invalid type")
            elif structured:
                raise TranscriptCompileError("structured state requires operator maps")


def _extract_contract_fences(text: str) -> list[str]:
    blocks: list[str] = []
    active = False
    outer: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        marker = re.fullmatch(r"\s*(`{3,}|~{3,})(.*)", line)
        if outer is not None:
            if marker and "transcript" in marker[2]:
                raise TranscriptCompileError("nested transcript fence in another code block")
            if (
                marker
                and marker[1][0] == outer[0]
                and len(marker[1]) >= len(outer)
                and not marker[2].strip()
            ):
                outer = None
            continue
        if line.strip() == "```yaml transcript":
            if active:
                raise TranscriptCompileError("nested transcript fence")
            active = True
            body = []
        elif active and line.strip() == "```":
            blocks.append("\n".join(body))
            active = False
        elif active:
            if line.lstrip().startswith("```"):
                raise TranscriptCompileError("malformed transcript fence")
            body.append(line)
        elif marker and "transcript" in marker[2]:
            raise TranscriptCompileError("malformed transcript fence")
        elif marker:
            outer = marker[1]
    if active:
        raise TranscriptCompileError("unterminated transcript fence")
    return blocks


def _extract_messages(text: str) -> list[dict[str, str]]:
    pattern = re.compile(
        r"^\*\*(Пользователь|Chiplog) · ([a-z][a-z0-9_-]*)(?: · (exact|пример))?:\*\* (.+)$"
    )
    messages: list[dict[str, str]] = []
    ids: set[str] = set()
    fence: str | None = None
    for line in text.splitlines():
        marker = re.fullmatch(r"\s*(`{3,}|~{3,})(.*)", line)
        if fence is not None:
            if (
                marker
                and marker[1][0] == fence[0]
                and len(marker[1]) >= len(fence)
                and not marker[2].strip()
            ):
                fence = None
            continue
        if marker:
            fence = marker[1]
            continue
        match = pattern.fullmatch(line)
        if match is None:
            if line.startswith(("**Пользователь ·", "**Chiplog ·")):
                raise TranscriptCompileError("malformed canonical message")
            continue
        role, identity, mode, content = match.groups()
        if identity in ids:
            raise TranscriptCompileError("duplicate message id")
        if (role == "Chiplog" and mode is None) or (role == "Пользователь" and mode is not None):
            raise TranscriptCompileError("invalid canonical message role/mode")
        ids.add(identity)
        messages.append({"id": identity, "role": role, "mode": mode or "input", "text": content})
    return messages


def _resolve_messages(
    value: object, messages: dict[str, dict[str, str]], referenced: set[str]
) -> object:
    if isinstance(value, str) and value.startswith("$message."):
        identity = value.removeprefix("$message.")
        if identity not in messages:
            raise TranscriptCompileError(f"dangling message reference: {identity}")
        referenced.add(identity)
        return messages[identity]["text"]
    if isinstance(value, dict):
        return {key: _resolve_messages(item, messages, referenced) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_messages(item, messages, referenced) for item in value]
    return value
