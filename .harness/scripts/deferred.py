#!/usr/bin/env python3
"""Evidence-bound, non-blocking deferred-defect ledger."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from typing import NoReturn

STATUSES = {"open", "active", "resolved", "retired", "moved"}
TERMINAL = {"resolved", "retired", "moved"}
FIELD_RE = re.compile(r"^([a-z][a-z0-9_]*):\s*(.*)$")
SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^D-\d{8}T\d{6}Z-[0-9a-f]{8}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
TRACKER_RE = re.compile(r"^tracker:(?:https://[^\s|]+|[A-Za-z][A-Za-z0-9_-]*-[1-9][0-9]*)$")


class DeferredError(Exception):
    pass


def fail(message: str) -> NoReturn:
    raise DeferredError(message)


def git(root: pathlib.Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        fail(f"git {' '.join(args)}: {detail or 'failed'}")
    return result.stdout if binary else result.stdout.decode("utf-8").strip()


def exact_commit(root: pathlib.Path, value: str) -> str:
    resolved = str(git(root, "rev-parse", "--verify", f"{value}^{{commit}}"))
    if value != resolved:
        fail(f"revision must be the full exact commit object: {resolved}")
    return resolved


def locator_path(locator: str) -> str:
    raw = locator.split("#", 1)[0]
    path = pathlib.PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or str(path) != raw:
        fail(f"locator must be a normalized repo-relative path: {locator}")
    return raw


def locator_fragment(locator: str) -> str:
    if "#" not in locator:
        fail(f"finding locator must include an exact fragment: {locator}")
    fragment = locator.split("#", 1)[1]
    if not fragment:
        fail(f"finding locator fragment is empty: {locator}")
    return fragment


def object_bytes(root: pathlib.Path, revision: str, locator: str) -> bytes:
    path = locator_path(locator)
    return bytes(git(root, "show", f"{revision}:{path}", binary=True))


def require_current_bytes(root: pathlib.Path, revision: str, locator: str) -> None:
    path = root / locator_path(locator)
    if not path.is_file() or path.read_bytes() != object_bytes(root, revision, locator):
        fail(f"working file differs from observed revision: {locator}")


def require_digest(root: pathlib.Path, revision: str, locator: str, expected: str) -> None:
    if not SHA256_RE.fullmatch(expected):
        fail(f"invalid sha256 for {locator}")
    actual = hashlib.sha256(object_bytes(root, revision, locator)).hexdigest()
    if actual != expected:
        fail(f"sha256 mismatch for {locator}: expected {expected}, got {actual}")


def parse_document(text: str) -> tuple[dict[str, str], dict[str, str]]:
    if not text.startswith("---\n"):
        fail("document must start with Markdown frontmatter")
    try:
        header, body = text[4:].split("\n---\n", 1)
    except ValueError:
        fail("frontmatter closing delimiter is missing")
    fields: dict[str, str] = {}
    for line in header.splitlines():
        match = FIELD_RE.fullmatch(line)
        if not match:
            fail(f"invalid frontmatter line: {line}")
        key, value = match.groups()
        if key in fields:
            fail(f"duplicate frontmatter field: {key}")
        fields[key] = value

    matches = list(SECTION_RE.finditer(body))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        if name in sections:
            fail(f"duplicate section: {name}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[name] = body[match.end() : end].strip()
    return fields, sections


def parse_condition(text: str, name: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("- "):
            continue
        match = FIELD_RE.fullmatch(line[2:])
        if not match:
            fail(f"invalid {name} condition line: {line}")
        key, value = match.groups()
        if key in fields:
            fail(f"duplicate {name} condition field: {key}")
        fields[key] = value.strip("`")
    required = {"predicate", "owner", "next_action", "unknown"}
    if set(fields) != required or any(not fields[key] for key in required):
        fail(f"{name} condition requires exactly: {', '.join(sorted(required))}")
    if fields["unknown"] != "stop":
        fail(f"{name} condition unknown result must be stop")
    return fields


def require_nonempty_sections(sections: dict[str, str], *, stored: bool = False) -> None:
    required = {
        "Finding",
        "Why non-blocking now",
        "Why deferred",
        "Promotion condition",
        "Invalidation condition",
    }
    expected = required | ({"History"} if stored else set())
    if set(sections) != expected:
        fail(f"document requires exactly these sections: {', '.join(sorted(expected))}")
    empty = sorted(name for name in expected if not sections[name].strip())
    if empty:
        fail(f"empty sections: {', '.join(empty)}")
    parse_condition(sections["Promotion condition"], "promotion")
    parse_condition(sections["Invalidation condition"], "invalidation")


def validate_ratification(fields: dict[str, str], content: bytes) -> None:
    try:
        text = content.decode("utf-8", "strict")
    except UnicodeDecodeError:
        fail("ratification must be UTF-8 text")
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = FIELD_RE.fullmatch(line)
        if not match:
            fail(f"invalid ratification line: {line}")
        key, value = match.groups()
        if key in parsed:
            fail(f"duplicate ratification field: {key}")
        parsed[key] = value
    expected = {
        "finding_id": fields["finding_id"],
        "disposition": fields["ratified_disposition"],
        "ratified_by": fields["ratified_by"],
    }
    if parsed != expected:
        fail("ratification must contain exactly the bound finding_id, disposition, and ratified_by")


def validate_bindings(
    root: pathlib.Path, fields: dict[str, str], *, require_current: bool = False
) -> str:
    if fields["ratified_disposition"] != "non-blocking":
        fail("only a ratified non-blocking finding may be deferred")
    require_history_scalar("ratified_by", fields["ratified_by"])
    require_history_scalar("evidence_locator", fields["evidence_locator"])
    if not UTC_RE.fullmatch(fields["ratified_at"]):
        fail("ratified_at must be an exact UTC timestamp")
    revision = exact_commit(root, fields["observed_revision"])
    if require_current:
        head = str(git(root, "rev-parse", "--verify", "HEAD^{commit}"))
        if revision != head:
            fail(f"observed revision is stale; current HEAD is {head}")
    require_digest(root, revision, fields["target"], fields["target_sha256"])
    require_digest(root, revision, fields["finding_locator"], fields["finding_sha256"])
    if locator_fragment(fields["finding_locator"]) != fields["finding_id"]:
        fail("finding locator fragment must equal finding_id")
    finding = object_bytes(root, revision, fields["finding_locator"]).decode("utf-8", "strict")
    if f"finding_id: {fields['finding_id']}" not in finding.splitlines():
        fail("finding report does not contain the exact finding_id binding")
    require_digest(
        root, revision, fields["ratification_locator"], fields["ratification_sha256"]
    )
    validate_ratification(fields, object_bytes(root, revision, fields["ratification_locator"]))
    require_digest(root, revision, fields["evidence_locator"], fields["evidence_sha256"])
    if require_current:
        for locator in (
            fields["target"], fields["finding_locator"], fields["ratification_locator"],
            fields["evidence_locator"],
        ):
            require_current_bytes(root, revision, locator)
    return revision


def now_utc() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def render(fields: dict[str, str], sections: dict[str, str]) -> str:
    header = "\n".join(f"{key}: {value}" for key, value in fields.items())
    body = "\n\n".join(f"## {name}\n\n{content}" for name, content in sections.items())
    return f"---\n{header}\n---\n\n{body}\n"


def admission_fingerprint(fields: dict[str, str], sections: dict[str, str]) -> str:
    excluded = {"id", "status", "created_at", "admission_sha256"}
    field_lines = [f"{key}: {fields[key]}" for key in sorted(set(fields) - excluded)]
    section_names = (
        "Finding",
        "Why non-blocking now",
        "Why deferred",
        "Promotion condition",
        "Invalidation condition",
    )
    section_lines = [f"## {name}\n{sections[name].strip()}" for name in section_names]
    canonical = "\n".join(field_lines + section_lines)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def atomic_write(path: pathlib.Path, content: str) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    temp = pathlib.Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def ledger_lock(root: pathlib.Path):
    lock_path = root / ".harness" / "deferred" / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def admission(root: pathlib.Path, source: pathlib.Path) -> pathlib.Path:
    text = source.read_text(encoding="utf-8")
    fields, sections = parse_document(text)
    allowed = {
        "finding_id",
        "finding_locator",
        "finding_sha256",
        "original_severity",
        "ratified_disposition",
        "ratified_by",
        "ratified_at",
        "ratification_locator",
        "ratification_sha256",
        "observed_revision",
        "target",
        "target_sha256",
        "evidence_locator",
        "evidence_sha256",
        "expected_verdict",
    }
    if set(fields) != allowed or any(not fields[key] for key in allowed):
        fail(f"admission frontmatter requires exactly: {', '.join(sorted(allowed))}")
    revision = validate_bindings(root, fields, require_current=True)
    require_nonempty_sections(sections)

    created = now_utc()
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    entry_id = f"D-{created.replace('-', '').replace(':', '')}-{digest}"
    if not ID_RE.fullmatch(entry_id):
        fail("generated invalid deferred id")
    destination = root / ".harness" / "deferred" / f"{entry_id}.md"
    if destination.exists():
        fail(f"deferred id collision: {entry_id}")

    output_fields = {
        "id": entry_id,
        "status": "open",
        **fields,
        "created_at": created,
    }
    output_fields["admission_sha256"] = admission_fingerprint(output_fields, sections)
    event_id = transition_id(entry_id, "open", fields["ratified_by"], revision, fields["evidence_locator"])
    output_sections = dict(sections)
    output_sections["History"] = (
        f"- {created} | {event_id} | create | {fields['ratified_by']} | "
        f"{revision} | {fields['evidence_locator']}"
    )
    atomic_write(destination, render(output_fields, output_sections))
    return destination


def transition_id(entry_id: str, status: str, actor: str, revision: str, evidence: str) -> str:
    payload = "\0".join((entry_id, status, actor, revision, evidence))
    return "E-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def require_history_scalar(name: str, value: str, *, allow_empty: bool = False) -> None:
    if not value and allow_empty:
        return
    if not value or "|" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        fail(f"{name} must be a non-empty single-line scalar without pipe or control characters")


def entry_path(root: pathlib.Path, entry_id: str) -> pathlib.Path:
    if not ID_RE.fullmatch(entry_id):
        fail(f"invalid deferred id: {entry_id}")
    path = root / ".harness" / "deferred" / f"{entry_id}.md"
    if not path.is_file():
        fail(f"deferred defect not found: {entry_id}")
    return path


def validate_stored_entry(
    root: pathlib.Path, entry_id: str, fields: dict[str, str], sections: dict[str, str]
) -> None:
    admission_fields = {
        "finding_id", "finding_locator", "finding_sha256", "original_severity",
        "ratified_disposition", "ratified_by", "ratified_at", "ratification_locator",
        "ratification_sha256", "observed_revision", "target", "target_sha256",
        "evidence_locator", "evidence_sha256", "expected_verdict",
    }
    if set(fields) != admission_fields | {"id", "status", "created_at", "admission_sha256"}:
        fail("stored deferred entry has missing or unknown frontmatter fields")
    if fields["id"] != entry_id or fields["status"] not in STATUSES:
        fail("stored deferred entry has invalid identity or status")
    if not UTC_RE.fullmatch(fields["created_at"]):
        fail("stored deferred entry has invalid created_at")
    revision = validate_bindings(root, fields)
    require_nonempty_sections(sections, stored=True)
    if fields["admission_sha256"] != admission_fingerprint(fields, sections):
        fail("stored deferred entry has changed immutable admission content")
    history = sections.get("History", "")
    create_id = transition_id(
        entry_id, "open", fields["ratified_by"], revision, fields["evidence_locator"]
    )
    expected_create = (
        f"- {fields['created_at']} | {create_id} | create | {fields['ratified_by']} | "
        f"{revision} | {fields['evidence_locator']}"
    )
    lines = history.splitlines()
    if not lines or lines[0] != expected_create:
        fail("stored deferred entry has missing or changed create history")
    parts = [line.split(" | ") for line in lines]
    if any(len(part) != 6 for part in parts):
        fail("stored deferred entry has malformed history events")
    event_ids = [part[1] for part in parts]
    if len(set(event_ids)) != len(event_ids):
        fail("stored deferred entry has duplicate history events")
    state = "open"
    for index, part in enumerate(parts):
        timestamp = part[0].removeprefix("- ")
        event_id, action, actor, event_revision, evidence = part[1:]
        if not UTC_RE.fullmatch(timestamp):
            fail("stored deferred entry has invalid history timestamp")
        require_history_scalar("history actor", actor)
        exact_commit(root, event_revision)
        if index == 0:
            if part[0] != f"- {fields['created_at']}" or action != "create":
                fail("stored deferred entry has changed create history")
            desired, normalized_evidence = "open", evidence
        elif action == "take:active" and state == "open" and evidence == "-":
            desired, normalized_evidence, state = "active", "", "active"
        elif action.startswith("close:") and state in {"open", "active"}:
            desired = action.removeprefix("close:")
            if desired not in TERMINAL or evidence == "-":
                fail("stored deferred entry has invalid close history")
            if desired in {"resolved", "retired"}:
                object_bytes(root, event_revision, evidence)
            elif not TRACKER_RE.fullmatch(evidence):
                fail("stored moved history lacks a stable tracker ID or URL")
            normalized_evidence, state = evidence, desired
        else:
            fail("stored deferred entry has illegal history transition")
        expected_id = transition_id(
            entry_id, desired, actor, event_revision, normalized_evidence
        )
        if event_id != expected_id:
            fail("stored deferred entry has changed history payload")
    if fields["status"] != state:
        fail("stored status does not match the validated history state")


def transition(
    root: pathlib.Path,
    entry_id: str,
    desired: str,
    actor: str,
    revision_value: str,
    evidence: str,
) -> pathlib.Path:
    if desired not in STATUSES - {"open"}:
        fail(f"invalid destination state: {desired}")
    require_history_scalar("actor", actor)
    require_history_scalar("evidence", evidence, allow_empty=desired == "active")
    revision = exact_commit(root, revision_value)
    path = entry_path(root, entry_id)
    fields, sections = parse_document(path.read_text(encoding="utf-8"))
    validate_stored_entry(root, entry_id, fields, sections)
    event_id = transition_id(entry_id, desired, actor, revision, evidence)
    history = sections.get("History", "")
    if f"| {event_id} |" in history:
        return path

    current = fields["status"]
    if desired == "active":
        if current != "open" or evidence:
            fail("take permits only open -> active and has no evidence argument")
        action = "take"
    else:
        if desired not in TERMINAL or current not in {"open", "active"}:
            fail(f"close cannot transition {current} -> {desired}")
        if not evidence:
            fail("close evidence is required")
        if desired in {"resolved", "retired"}:
            object_bytes(root, revision, evidence)
        elif not TRACKER_RE.fullmatch(evidence):
            fail("moved evidence must be tracker:<stable-id-or-url>")
        action = "close"

    timestamp = now_utc()
    fields["status"] = desired
    line = f"- {timestamp} | {event_id} | {action}:{desired} | {actor} | {revision} | {evidence or '-'}"
    sections["History"] = f"{history}\n{line}".strip()
    atomic_write(path, render(fields, sections))
    return path


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: deferred.py <root> defer|take|close ...", file=sys.stderr)
        return 2
    root = pathlib.Path(argv[1]).resolve()
    command, args = argv[2], argv[3:]
    try:
        with ledger_lock(root):
            if command == "defer" and len(args) == 1:
                result = admission(root, pathlib.Path(args[0]).resolve())
            elif command == "take" and len(args) == 3:
                result = transition(root, args[0], "active", args[1], args[2], "")
            elif command == "close" and len(args) == 5:
                if args[1] not in TERMINAL:
                    fail("close destination must be resolved, retired, or moved")
                result = transition(root, args[0], args[1], args[2], args[3], args[4])
            else:
                fail(f"invalid arguments for {command}")
    except (DeferredError, OSError) as exc:
        print(f"deferred: {exc}", file=sys.stderr)
        print(
            "  → сделай: исправь аргумент, binding или ledger по точному диагнозу выше, "
            "затем повтори исходный вызов",
            file=sys.stderr,
        )
        print(
            "  ✓ команда завершается с кодом 0 и не ослабляет admission/state contract",
            file=sys.stderr,
        )
        return 2
    print(result.relative_to(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
