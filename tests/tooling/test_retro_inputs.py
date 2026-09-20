"""Retro input collection must distinguish session handoff and durable defects."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def clean_environment() -> dict[str, str]:
    """The fixture must never inherit another repository's Git context."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def command(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        arguments,
        cwd=repository,
        env=clean_environment(),
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / ".harness", tmp_path / ".harness")
    (tmp_path / ".harness/handoff.md").unlink(missing_ok=True)
    ledger = tmp_path / ".harness/deferred"
    for entry in ledger.glob("D-*.md"):
        entry.unlink()
    command(tmp_path, "git", "init", "-q")
    command(tmp_path, "git", "config", "user.name", "Retro fixture")
    command(tmp_path, "git", "config", "user.email", "retro@example.invalid")
    command(tmp_path, "git", "config", "core.hooksPath", "/dev/null")
    for name, body in {
        "target.txt": "target\n",
        "review.md": "finding_id: RT-7\n",
        "evidence.txt": "reproducer fails\n",
        "ratification.md": (
            "finding_id: RT-7\ndisposition: non-blocking\nratified_by: primary-agent\n"
        ),
    }.items():
        (tmp_path / name).write_text(body)
    command(tmp_path, "git", "add", ".")
    command(tmp_path, "git", "commit", "-qm", "fixture")
    return tmp_path


def create_entry(repository: Path) -> Path:
    revision = command(repository, "git", "rev-parse", "--verify", "HEAD^{commit}")
    # Exercise the public file contract without importing another test program.
    fields = {
        "finding_id": "RT-7",
        "finding_locator": "review.md#RT-7",
        "original_severity": "P1",
        "ratified_disposition": "non-blocking",
        "ratified_by": "primary-agent",
        "ratified_at": "2026-09-03T12:00:00Z",
        "ratification_locator": "ratification.md",
        "observed_revision": revision,
        "target": "target.txt",
        "evidence_locator": "evidence.txt",
        "expected_verdict": "reproducer exits non-zero",
    }
    for name, filename in (
        ("finding", "review.md"),
        ("ratification", "ratification.md"),
        ("target", "target.txt"),
        ("evidence", "evidence.txt"),
    ):
        fields[name + "_sha256"] = hashlib.sha256((repository / filename).read_bytes()).hexdigest()
    body = ""
    for heading, detail in (
        ("Finding", "Concrete fixture defect."),
        ("Why non-blocking now", "Current acceptance still passes."),
        ("Why deferred", "Repair is outside current fixture scope."),
        ("Promotion condition", "Acceptance starts failing."),
        ("Invalidation condition", "Reproducer no longer reaches target."),
    ):
        body += f"\n## {heading}\n\n"
        if heading.endswith("condition"):
            body += (
                f"- predicate: {detail}\n- owner: fixture owner\n"
                "- next_action: inspect current evidence and resolve\n- unknown: stop\n"
            )
        else:
            body += detail + "\n"
    admission = repository / "admission.md"
    admission.write_text(
        "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n" + body
    )
    relative = command(repository, "sh", ".harness/scripts/defer-defect.sh", str(admission))
    entry = repository / relative
    assert "\nstatus: open\n" in entry.read_text()
    return entry


def collect(repository: Path) -> str:
    return command(repository, "sh", ".harness/scripts/retro-inputs.sh")


def inventory_line(output: str, entry: Path) -> str:
    lines = [line for line in output.splitlines() if entry.name in line]
    assert lines, f"Durable record absent from retro inputs: {entry.name}\n{output}"
    return "\n".join(lines)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert text.count(old) == 1
    path.write_text(text.replace(old, new, 1))
    assert new in path.read_text()


def test_missing_ledger_is_distinguished_from_empty_ledger(repository: Path) -> None:
    shutil.rmtree(repository / ".harness/deferred")
    absent = collect(repository)
    assert ".harness/deferred" in absent
    assert "реестр отсутствует" in absent
    (repository / ".harness/deferred").mkdir()
    empty = collect(repository)
    assert ".harness/deferred" in empty
    assert "записей нет" in empty
    assert "реестр отсутствует" not in empty


def test_empty_ledger_service_files_are_not_reported_as_defects(repository: Path) -> None:
    output = collect(repository)
    assert ".harness/deferred" in output
    assert "записей нет" in output
    assert ".harness/deferred/README.md" not in output
    assert ".harness/deferred/self-test.py" not in output
    assert ".harness/deferred/.lock" not in output


@pytest.mark.parametrize("with_handoff", [False, True])
def test_valid_open_defect_is_visible_independently_of_session_handoff(
    repository: Path, with_handoff: bool
) -> None:
    entry = create_entry(repository)
    assert not (repository / ".harness/handoff.md").exists()
    if with_handoff:
        command(repository, "sh", ".harness/scripts/decide.sh", "session-sentinel", "fixture")
    output = collect(repository)
    assert "open" in inventory_line(output, entry)
    assert "за человека ничего не решено и ничего не отложено" not in output
    if with_handoff:
        assert "session-sentinel" in output


def test_retired_defect_remains_visible_with_terminal_status(repository: Path) -> None:
    entry = create_entry(repository)
    revision = command(repository, "git", "rev-parse", "--verify", "HEAD^{commit}")
    command(
        repository,
        "sh",
        ".harness/scripts/close-defect.sh",
        entry.stem,
        "retired",
        "retro-fixture",
        revision,
        "evidence.txt",
    )
    assert "\nstatus: retired\n" in entry.read_text()
    line = inventory_line(collect(repository), entry)
    assert "retired" in line
    assert "status: open" not in line


def test_unknown_status_is_visible_instead_of_dropping_record(repository: Path) -> None:
    entry = create_entry(repository)
    # A raw-corruption inventory probe, not a valid ledger transition.
    replace_once(entry, "\nstatus: open\n", "\nstatus: unexpected-state\n")
    assert "unexpected-state" in inventory_line(collect(repository), entry)


def test_body_status_is_not_mistaken_for_frontmatter(repository: Path) -> None:
    entry = create_entry(repository)
    with entry.open("a") as stream:
        stream.write("\n## Historical quotation\n\nstatus: body-only-sentinel\n")
    output = collect(repository)
    assert "open" in inventory_line(output, entry)
    assert "body-only-sentinel" not in output


def test_duplicate_status_does_not_silently_select_one_value(repository: Path) -> None:
    entry = create_entry(repository)
    # Collector should expose ambiguity; validation belongs to the ledger API.
    replace_once(entry, "\nstatus: open\n", "\nstatus: open\nstatus: duplicate-sentinel\n")
    output = collect(repository)
    inventory_line(output, entry)
    assert "duplicate-sentinel" in output or any(
        marker in output.lower() for marker in ("duplicate", "дублик", "неоднознач")
    )


def test_missing_frontmatter_status_does_not_fall_back_to_body(repository: Path) -> None:
    entry = create_entry(repository)
    replace_once(entry, "\nstatus: open\n", "\n")
    with entry.open("a") as stream:
        stream.write("\nstatus: body-only-sentinel\n")
    output = collect(repository)
    line = inventory_line(output, entry)
    assert any(marker in line.lower() for marker in ("неизвест", "отсутств", "missing", "unknown"))
    assert "body-only-sentinel" not in output
