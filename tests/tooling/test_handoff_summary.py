"""Compact handoff navigation must retain details without acknowledging entries."""

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script(tmp_path):
    scripts = tmp_path / ".harness/scripts"
    scripts.mkdir(parents=True)
    for name in ("handoff.py", "handoff.sh"):
        shutil.copy2(ROOT / ".harness/scripts" / name, scripts / name)
    return scripts / "handoff.sh"


@pytest.mark.parametrize("long_text", [False, True])
def test_shell_summary_preserves_all_records_fields_and_durable_files(tmp_path, long_text):
    script = _script(tmp_path)
    what = "не укорачивать " * 1000 if long_text else "выбран точный вариант"
    journal = tmp_path / ".harness/handoff.md"
    sequence = tmp_path / ".harness/handoff-sequence"
    journal.write_text(
        f"## 7 · решение · new\n- что: {what}\n"
        "- на чём основано: реальные байты\n- отклонено: другой путь\n"
        "- когда записано: 2026-10-16 00:00\n\n"
        "## 42 · хвост · сейчас · carried\n- что: ещё не закрыто\n"
        "- почему не сейчас: отдельная проверка\n\n",
        encoding="utf-8",
    )
    sequence.write_text("60\n")
    journal.chmod(0o640)
    sequence.chmod(0o600)
    before = {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in (journal, sequence)}
    result = subprocess.run(["sh", str(script), "--summary"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"7 · решение · new · что: {what.strip()} · на чём основано: реальные байты"
        " · отклонено: другой путь · когда записано: 2026-10-16 00:00",
        "42 · хвост · сейчас · carried · что: ещё не закрыто"
        " · почему не сейчас: отдельная проверка",
    ]
    assert {p: (p.read_bytes(), stat.S_IMODE(p.stat().st_mode)) for p in before} == before
    full = subprocess.run(["sh", str(script)], capture_output=True, text=True)
    assert full.returncode == 0 and full.stdout == journal.read_text()


def test_empty_shell_summary_creates_no_journal_or_sequence(tmp_path):
    script = _script(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    result = subprocess.run(["sh", str(script), "--summary"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "журнал пуст — за человека ничего не решено и ничего не отложено\n"
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before
