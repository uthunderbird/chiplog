from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_codex_hooks_are_wired_through_agents_adapter() -> None:
    config = json.loads((ROOT / ".codex/hooks.json").read_text())

    commands = [
        hook["command"]
        for groups in config["hooks"].values()
        for group in groups
        for hook in group["hooks"]
    ]

    assert commands
    assert all(".agents/hooks/codex-harness.py" in command for command in commands)
    assert all("shasum -a 256 -c --status" in command for command in commands)
    assert all("[ -L" in command for command in commands)


def test_codex_hook_pins_match_current_executables() -> None:
    config = json.loads((ROOT / ".codex/hooks.json").read_text())
    for groups in config["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                command = hook["command"]
                pins = dict(
                    (path, digest)
                    for digest, path in re.findall(r'([0-9a-f]{64}) "\$root/([^"]+)"', command)
                )
                assert pins
                for path, digest in pins.items():
                    actual = subprocess.check_output(
                        ["shasum", "-a", "256", ROOT / path], text=True
                    ).split()[0]
                    assert actual == digest


def test_integration_skill_installs_codex_discoverable_skills() -> None:
    procedure = (ROOT / ".agents/skills/integrate/SKILL.md").read_text()

    assert ".harness/scripts .harness/skills .agents/skills .githooks" in procedure
    assert 'cp -R "$HB/export/.agents/skills" .agents/' in procedure
    assert "if [ -e .agents/skills ]" in procedure
    assert "СТОП: .agents/skills уже существует" in procedure


def test_skill_installer_does_not_fetch_mutable_remote_state() -> None:
    installer_path = ROOT / ".harness/scripts/install-skills.sh"
    installer = installer_path.read_text()

    assert "git clone" not in installer
    assert "SWARM_SKILL_REF" not in installer
    assert "rm -rf" not in installer
    assert "cp -R" not in installer

    before = {
        path.relative_to(ROOT): path.read_bytes()
        for path in (ROOT / ".agents/skills").rglob("*")
        if path.is_file()
    }
    subprocess.run(["sh", str(installer_path)], cwd=ROOT, check=True)
    subprocess.run(["sh", str(installer_path)], cwd=ROOT, check=True)
    after = {
        path.relative_to(ROOT): path.read_bytes()
        for path in (ROOT / ".agents/skills").rglob("*")
        if path.is_file()
    }

    assert after == before


def test_every_agent_skill_has_an_entrypoint() -> None:
    skills = ROOT / ".agents/skills"

    assert (skills / "retro/SKILL.md").is_file()
    assert all((path / "SKILL.md").is_file() for path in skills.iterdir())


def make_installer_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "installer-project"
    scripts = project / ".harness/scripts"
    skill = project / ".agents/skills/example"
    scripts.mkdir(parents=True)
    skill.mkdir(parents=True)
    shutil.copy2(ROOT / ".harness/scripts/install-skills.sh", scripts / "install-skills.sh")
    (skill / "SKILL.md").write_text("---\nname: example\n---\n")
    (skill / "required.md").write_text("required support\n")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "add", ".agents/skills"], cwd=project, check=True)
    return project


def test_skill_installer_rejects_missing_tracked_support_file(tmp_path: Path) -> None:
    project = make_installer_fixture(tmp_path)
    (project / ".agents/skills/example/required.md").unlink()

    result = subprocess.run(
        ["sh", ".harness/scripts/install-skills.sh"], cwd=project, capture_output=True
    )

    assert result.returncode != 0


def test_skill_installer_rejects_external_symlink_substitution(tmp_path: Path) -> None:
    project = make_installer_fixture(tmp_path)
    support = project / ".agents/skills/example/required.md"
    support.unlink()
    support.symlink_to(tmp_path / "outside.md")

    result = subprocess.run(
        ["sh", ".harness/scripts/install-skills.sh"], cwd=project, capture_output=True
    )

    assert result.returncode != 0


def test_skill_installer_rejects_untracked_instruction_state(tmp_path: Path) -> None:
    project = make_installer_fixture(tmp_path)
    rogue = project / ".agents/skills/rogue"
    rogue.mkdir()
    (rogue / "SKILL.md").write_text("unreviewed\n")

    result = subprocess.run(
        ["sh", ".harness/scripts/install-skills.sh"], cwd=project, capture_output=True
    )

    assert result.returncode != 0


def test_skill_installer_rejects_modified_tracked_instruction(tmp_path: Path) -> None:
    project = make_installer_fixture(tmp_path)
    (project / ".agents/skills/example/SKILL.md").write_text("mutated after review\n")

    result = subprocess.run(
        ["sh", ".harness/scripts/install-skills.sh"], cwd=project, capture_output=True
    )

    assert result.returncode != 0


@pytest.mark.parametrize("mode", ["session-start", "healthcheck", "guard-edit"])
def test_codex_hooks_resolve_project_from_nested_payload_cwd(
    tmp_path: Path,
    mode: str,
) -> None:
    project = tmp_path / "project"
    scripts = project / ".harness/scripts"
    scripts.mkdir(parents=True)
    if mode == "guard-edit":
        for name in ("guard-edit.sh", "thresholds.sh"):
            shutil.copy2(ROOT / ".harness/scripts" / name, scripts / name)
    else:
        script_name = {"session-start": "session-start.sh", "healthcheck": "healthcheck.sh"}[mode]
        script = scripts / script_name
        script.write_text(
            '#!/bin/sh\n[ "$HARNESS_PROJECT_DIR" = "$(git rev-parse --show-toplevel)" ]\n'
        )

    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    probes = project / "nested/probes"
    probes.mkdir(parents=True)
    for number in range(50):
        (probes / f"probe-{number}.txt").touch()

    payload = json.dumps(
        {
            "cwd": str(probes),
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
        }
    )
    result = subprocess.run(
        [
            "python3",
            str(ROOT / ".agents/hooks/codex-harness.py"),
            mode,
            str(project),
        ],
        input=payload,
        text=True,
        cwd=tmp_path,
        capture_output=True,
        check=False,
    )

    if mode == "guard-edit":
        assert result.returncode == 2
        assert "правки заблокированы" in result.stderr
    else:
        assert result.returncode == 0


@pytest.mark.parametrize("payload", ["[]", "null", '"text"', "42", "{}", '{"cwd": 42}'])
@pytest.mark.parametrize(
    ("mode", "expected"),
    [("session-start", 0), ("healthcheck", 0), ("guard-edit", 2)],
)
def test_codex_hooks_handle_malformed_payload_shape(
    payload: str,
    mode: str,
    expected: int,
) -> None:
    result = subprocess.run(
        ["python3", str(ROOT / ".agents/hooks/codex-harness.py"), mode, str(ROOT)],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == expected
    assert "Codex harness hook:" in result.stderr


def test_codex_edit_guard_rejects_payload_from_another_worktree(tmp_path: Path) -> None:
    other = tmp_path / "other"
    scripts = other / ".harness/scripts"
    scripts.mkdir(parents=True)
    (scripts / "guard-edit.sh").write_text("#!/bin/sh\necho bypass\nexit 0\n")
    subprocess.run(["git", "init", "-q"], cwd=other, check=True)
    payload = json.dumps({"cwd": str(other)})

    result = subprocess.run(
        [
            "python3",
            str(ROOT / ".agents/hooks/codex-harness.py"),
            "guard-edit",
            str(ROOT),
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "different Git worktree" in result.stderr
    assert "bypass" not in result.stdout


def test_test_gate_clears_git_local_environment_before_verifiers(tmp_path: Path) -> None:
    gate_source = (ROOT / ".harness/scripts/gate.sh").read_text()
    assert (
        'run "тесты и эвалы"          sh .harness/scripts/clean-git-env.sh '
        "sh .harness/scripts/test.sh"
    ) in gate_source

    project = tmp_path / "owner"
    clean_env = os.environ.copy()
    for name in subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines():
        clean_env.pop(name, None)
    subprocess.run(["git", "init", "-q", str(project)], env=clean_env, check=True)
    subprocess.run(
        ["git", "config", "user.email", "owner@example.invalid"],
        cwd=project,
        env=clean_env,
        check=True,
    )
    subprocess.run(["git", "config", "user.name", "Owner"], cwd=project, env=clean_env, check=True)
    (project / "owner.txt").write_text("owner")
    subprocess.run(["git", "add", "owner.txt"], cwd=project, env=clean_env, check=True)
    subprocess.run(["git", "commit", "-qm", "owner"], cwd=project, env=clean_env, check=True)
    owner_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, env=clean_env, text=True
    )
    owner_index = (project / ".git/index").read_bytes()
    owner_config = (project / ".git/config").read_bytes()

    probe = tmp_path / "probe.sh"
    probe.write_text(
        "#!/bin/sh\nset -e\nmkdir nested\ncd nested\n"
        "git init -q\ngit config user.email nested@example.invalid\n"
        "git config user.name Nested\necho nested > nested.txt\n"
        "git add nested.txt\ngit commit -qm nested\n"
    )

    env = os.environ.copy()
    env.update(
        {
            "GIT_DIR": str(project / ".git"),
            "GIT_WORK_TREE": str(project),
            "GIT_COMMON_DIR": str(project / ".git"),
            "GIT_INDEX_FILE": str(project / ".git/index"),
        }
    )
    subprocess.run(
        ["sh", str(ROOT / ".harness/scripts/clean-git-env.sh"), "sh", str(probe)],
        cwd=project,
        env=env,
        check=True,
    )

    current_owner_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, env=clean_env, text=True
    )
    assert current_owner_head == owner_head
    assert (project / ".git/index").read_bytes() == owner_index
    assert (project / ".git/config").read_bytes() == owner_config
    assert (
        subprocess.check_output(
            ["git", "show", "HEAD:nested.txt"],
            cwd=project / "nested",
            env=clean_env,
            text=True,
        )
        == "nested\n"
    )


def test_new_retro_supports_explicit_collision_safe_ids(tmp_path: Path) -> None:
    project = tmp_path / "project"
    scripts = project / ".harness/scripts"
    retro = project / ".harness/retro"
    scripts.mkdir(parents=True)
    retro.mkdir(parents=True)
    shutil.copy2(ROOT / ".harness/scripts/new-retro.sh", scripts / "new-retro.sh")
    shutil.copy2(ROOT / ".harness/retro/TEMPLATE.md", retro / "TEMPLATE.md")

    base = subprocess.run(
        ["sh", str(scripts / "new-retro.sh")], capture_output=True, text=True, check=True
    )
    base_match = re.search(r"создано: (.+)", base.stdout)
    assert base_match is not None
    base_path = Path(base_match.group(1))
    base_bytes = base_path.read_bytes()

    collision = subprocess.run(
        ["sh", str(scripts / "new-retro.sh")], capture_output=True, text=True, check=False
    )
    assert collision.returncode != 0
    assert "→ сделай:" in collision.stderr
    assert "--id" in collision.stderr

    qualified = subprocess.run(
        ["sh", str(scripts / "new-retro.sh"), "--id", "r1"],
        capture_output=True,
        text=True,
        check=True,
    )
    qualified_match = re.search(r"создано: (.+)", qualified.stdout)
    assert qualified_match is not None
    qualified_path = Path(qualified_match.group(1))
    qualified_bytes = qualified_path.read_bytes()
    repeated = subprocess.run(
        ["sh", str(scripts / "new-retro.sh"), "--id", "r1"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert repeated.returncode != 0
    assert base_path.read_bytes() == base_bytes
    assert qualified_path.read_bytes() == qualified_bytes
    assert not list(retro.glob(".new-retro.*"))


def test_retro_skip_streak_uses_commit_order_not_same_day_filename_order(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    checks = project / ".harness/scripts/checks"
    retro = project / ".harness/retro"
    checks.mkdir(parents=True)
    retro.mkdir(parents=True)
    shutil.copy2(ROOT / ".harness/scripts/checks/retro_due.py", checks / "retro_due.py")
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(
        ["git", "config", "user.email", "retro@example.invalid"], cwd=project, check=True
    )
    subprocess.run(["git", "config", "user.name", "Retro"], cwd=project, check=True)

    (retro / "2026-08-27-z.md").write_text(
        "---\ndate: 2026-08-27\noutcome: skip\nreason: first\n---\n"
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "first retro"], cwd=project, check=True)
    (retro / "2026-08-27-a.md").write_text(
        "---\ndate: 2026-08-27\noutcome: change\nrule: .harness/scripts/checks/retro_due.py\n---\n"
    )
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-qm", "second retro"], cwd=project, check=True)

    status = subprocess.check_output(
        ["python3", str(checks / "retro_due.py"), "--root", str(project), "--status"],
        text=True,
    )
    assert "retro_entries=2" in status
    assert "retro_skips=0" in status

    subprocess.run(
        ["git", "mv", ".harness/retro/2026-08-27-z.md", ".harness/retro/2026-08-27-y.md"],
        cwd=project,
        check=True,
    )
    subprocess.run(["git", "commit", "-qm", "rename first retro"], cwd=project, check=True)
    committed_rename_status = subprocess.check_output(
        ["python3", str(checks / "retro_due.py"), "--root", str(project), "--status"],
        text=True,
    )
    assert "retro_skips=0" in committed_rename_status

    subprocess.run(
        ["git", "mv", ".harness/retro/2026-08-27-y.md", ".harness/retro/2026-08-27-x.md"],
        cwd=project,
        check=True,
    )
    staged_rename_status = subprocess.check_output(
        ["python3", str(checks / "retro_due.py"), "--root", str(project), "--status"],
        text=True,
    )
    assert "retro_skips=0" in staged_rename_status
