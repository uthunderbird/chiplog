from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "handoff_under_test", ROOT / ".harness/scripts/handoff.py"
)
assert SPEC is not None and SPEC.loader is not None
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


@pytest.mark.parametrize("removal", ["highest", "all", "ack"])
def test_identifiers_survive_removal_of_latest_entry(tmp_path: Path, removal: str) -> None:
    (tmp_path / ".harness").mkdir()
    assert handoff.add(tmp_path, "решение", "", {"что": "first"}) == 1
    assert handoff.add(tmp_path, "решение", "", {"что": "second"}) == 2
    if removal == "ack":
        assert handoff.ack(tmp_path) == (2, 0)
    else:
        assert handoff.close(tmp_path, 2)
        if removal == "all":
            assert handoff.close(tmp_path, 1)
    assert handoff.add(tmp_path, "решение", "", {"что": "third"}) == 3


def test_existing_journal_is_migrated_before_ack(tmp_path: Path) -> None:
    (tmp_path / ".harness").mkdir()
    handoff.path(tmp_path).write_text(
        "## 33 · решение · new\n- что: choice\n\n"
        "## 34 · хвост · следующим · new\n- что: unfinished\n",
        encoding="utf-8",
    )
    assert handoff.ack(tmp_path) == (1, 1)
    assert [(row["id"], row["status"]) for row in handoff.parse(tmp_path)] == [(34, "carried")]
    assert handoff.close(tmp_path, 34)
    assert handoff.add(tmp_path, "решение", "", {"что": "next"}) == 35


@pytest.mark.parametrize("operation", ["add", "close", "ack"])
def test_corrupt_sequence_preserves_journal_and_refuses_mutation(
    tmp_path: Path, operation: str
) -> None:
    (tmp_path / ".harness").mkdir()
    handoff.add(tmp_path, "решение", "", {"что": "original"})
    before = handoff.path(tmp_path).read_bytes()
    (tmp_path / ".harness/handoff-sequence").write_text("broken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid handoff sequence"):
        if operation == "add":
            handoff.add(tmp_path, "решение", "", {"что": "new"})
        elif operation == "close":
            handoff.close(tmp_path, 1)
        else:
            handoff.ack(tmp_path)
    assert handoff.path(tmp_path).read_bytes() == before


def test_failed_journal_write_does_not_release_reserved_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".harness").mkdir()
    assert handoff.add(tmp_path, "решение", "", {"что": "first"}) == 1
    original_write = handoff.write

    def fail_write(*args) -> None:
        raise OSError("interrupted journal write")

    monkeypatch.setattr(handoff, "write", fail_write)
    with pytest.raises(OSError, match="interrupted journal"):
        handoff.add(tmp_path, "решение", "", {"что": "interrupted"})
    monkeypatch.setattr(handoff, "write", original_write)
    assert handoff.add(tmp_path, "решение", "", {"что": "next"}) == 3
