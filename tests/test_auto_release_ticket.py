"""Issue #856: auto-create a release ticket when the Milestone is done.

``arm_release_ticket`` only labels an existing ``ai-release`` Issue.
These tests pin:

- ``auto_create_release_ticket`` defaults false (unset == today)
- explicit true + open milestone ``open_issues == 0`` + no release ticket
  creates one ticket whose ``## Release`` section parses
- a second tick is idempotent (fingerprint / existing ticket)
- open issues, an existing (open or closed) ticket, and a non-Python
  checkout with no version file do not create
- the body never contains ``test_command``
- the key is host-only with bool validation
- create failures stay an idle-path bypass
"""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import orbi.auto_release_ticket as auto_release
import orbi.cli  # noqa: F401  — binds the CLI load_config wrap
import orbi.repo_config as repo_config
import orbi.runner as runner
from orbi.release import parse_release_declaration
from seam import seam

ENGINE_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (
    ENGINE_ROOT / ".github" / "release-ticket-template.md"
).read_text(encoding="utf-8")


def _write_prompts(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir(exist_ok=True)
    for name in ("prompt.md", "prompt_review.md"):
        (prompts / name).write_text("prompt", encoding="utf-8")


def _host_toml(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "orbi.toml"
    path.write_text(
        'source_repos = ["owner/repo"]\n'
        'active_milestone = "v0.6.0"\n'
        + extra,
        encoding="utf-8",
    )
    return path


def _python_repo(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    github = tmp_path / ".github"
    github.mkdir(exist_ok=True)
    (github / "release-ticket-template.md").write_text(TEMPLATE, encoding="utf-8")
    return tmp_path


def _milestones(*rows: dict) -> str:
    return json.dumps([list(rows)])


def test_load_config_defaults_auto_create_release_ticket_to_false(tmp_path):
    config = runner.load_config(_host_toml(tmp_path))
    assert config.auto_create_release_ticket is False


def test_load_config_reads_auto_create_release_ticket(tmp_path):
    path = _host_toml(tmp_path, "auto_create_release_ticket = true\n")
    assert runner.load_config(path).auto_create_release_ticket is True
    path.write_text(
        'source_repos = ["owner/repo"]\n'
        "auto_create_release_ticket = false\n",
        encoding="utf-8",
    )
    assert runner.load_config(path).auto_create_release_ticket is False


def test_load_config_rejects_non_bool_auto_create_release_ticket(tmp_path):
    path = _host_toml(tmp_path, 'auto_create_release_ticket = "yes"\n')
    with pytest.raises(
        ValueError, match="auto_create_release_ticket must be a boolean",
    ):
        runner.load_config(path)


def test_repo_config_rejects_auto_create_release_ticket_as_host_only():
    with pytest.raises(
        repo_config.RepoConfigError,
        match=r"host-only key\(s\).*auto_create_release_ticket",
    ):
        repo_config.parse_repo_config("auto_create_release_ticket = true\n")
    assert "auto_create_release_ticket" in repo_config.HOST_ONLY_KEYS
    assert "auto_create_release_ticket" not in repo_config.POLICY_KEYS


def test_render_body_parses_and_omits_test_command():
    body = auto_release.render_release_ticket_body(
        TEMPLATE, version="v0.6.0", base_branch="main",
        version_file="pyproject.toml",
    )
    assert "test_command" not in body
    assert auto_release.fingerprint("v0.6.0") in body
    declaration = parse_release_declaration(body)
    assert declaration["version"] == "v0.6.0"
    assert declaration["base_branch"] == "main"
    assert declaration["scope_from_milestone"] == "v0.6.0"
    assert declaration["version_file"] == "pyproject.toml"


def test_render_body_writes_inferred_non_python_version_file():
    body = auto_release.render_release_ticket_body(
        TEMPLATE, version="v0.6.0", base_branch="develop",
        version_file="package.json",
    )
    declaration = parse_release_declaration(body)
    assert declaration["base_branch"] == "develop"
    assert declaration["version_file"] == "package.json"
    assert "- version_file: package.json" in body


def test_infer_version_file_python_package_json_and_missing(tmp_path):
    assert auto_release.infer_version_file(tmp_path) is None
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    assert auto_release.infer_version_file(tmp_path) == "package.json"
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert auto_release.infer_version_file(tmp_path) == "pyproject.toml"


def _fake_idle_gh(*, milestones, issues=(), created_url=None, calls=None):
    calls = calls if calls is not None else []

    def run(command, **kwargs):
        calls.append(command)
        if command[:2] == ["gh", "api"] and "milestones" in command[2]:
            return _milestones(*milestones)
        if command[:3] == ["gh", "issue", "list"]:
            search = command[command.index("--search") + 1] if "--search" in command else ""
            matched = []
            for item in issues:
                needle = item.get("search")
                if isinstance(needle, str) and needle in search:
                    matched.append({"number": item["number"]})
                elif (
                    "label:ai-release" in search
                    and "in:body" not in search
                    and item.get("kind") == "release"
                ):
                    matched.append({"number": item["number"]})
                elif "in:body" in search and item.get("kind") == "fingerprint":
                    matched.append({"number": item["number"]})
            return json.dumps(matched)
        if command[:3] == ["gh", "issue", "create"]:
            return created_url or "https://github.com/owner/repo/issues/900"
        if command[:3] == ["gh", "issue", "edit"]:
            return ""
        raise AssertionError(command)

    return run, calls


def test_create_when_milestone_complete_and_no_ticket(monkeypatch, tmp_path, caplog):
    repo_dir = _python_repo(tmp_path)
    run, calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "open", "open_issues": 0}],
    )
    monkeypatch.setattr(seam, "run_command", run)
    with caplog.at_level("INFO"):
        number = auto_release.maybe_create_release_ticket(
            "owner/repo", "v0.6.0",
            repo_dir=repo_dir, deploy_home=repo_dir, base_branch="main",
        )
    assert number == 900
    create = next(command for command in calls if command[:3] == ["gh", "issue", "create"])
    assert "--label" in create and create[create.index("--label") + 1] == "ai-release"
    assert "ai-ready" not in create
    assert create[create.index("--milestone") + 1] == "v0.6.0"
    body = create[create.index("--body") + 1]
    assert "test_command" not in body
    declaration = parse_release_declaration(body)
    assert declaration["version"] == "v0.6.0"
    assert declaration["base_branch"] == "main"
    assert declaration["scope_from_milestone"] == "v0.6.0"
    assert "release_ticket_created milestone=v0.6.0 issue=#900" in caplog.text


def test_idempotent_second_call_skips_with_event(monkeypatch, tmp_path, caplog):
    repo_dir = _python_repo(tmp_path)
    run, calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "open", "open_issues": 0}],
        issues=[{"number": 900, "kind": "fingerprint", "search": "orbi-auto-release"}],
    )
    monkeypatch.setattr(seam, "run_command", run)
    with caplog.at_level("INFO"):
        assert auto_release.maybe_create_release_ticket(
            "owner/repo", "v0.6.0",
            repo_dir=repo_dir, deploy_home=repo_dir, base_branch="main",
        ) is None
    assert not any(command[:3] == ["gh", "issue", "create"] for command in calls)
    assert "release_ticket_create_skipped" in caplog.text
    assert "reason=already_exists" in caplog.text


def test_existing_closed_release_ticket_does_not_create(monkeypatch, tmp_path, caplog):
    repo_dir = _python_repo(tmp_path)
    run, calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "open", "open_issues": 0}],
        issues=[{"number": 846, "kind": "release"}],
    )
    monkeypatch.setattr(seam, "run_command", run)
    with caplog.at_level("INFO"):
        assert auto_release.maybe_create_release_ticket(
            "owner/repo", "v0.6.0",
            repo_dir=repo_dir, deploy_home=repo_dir, base_branch="main",
        ) is None
    assert not any(command[:3] == ["gh", "issue", "create"] for command in calls)
    assert "reason=already_exists" in caplog.text


def test_open_issues_does_not_create(monkeypatch, tmp_path):
    repo_dir = _python_repo(tmp_path)
    run, calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "open", "open_issues": 3}],
    )
    monkeypatch.setattr(seam, "run_command", run)
    assert auto_release.maybe_create_release_ticket(
        "owner/repo", "v0.6.0",
        repo_dir=repo_dir, deploy_home=repo_dir, base_branch="main",
    ) is None
    assert not any(command[:3] == ["gh", "issue", "create"] for command in calls)


def test_closed_milestone_does_not_create(monkeypatch, tmp_path):
    repo_dir = _python_repo(tmp_path)
    run, calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "closed", "open_issues": 0}],
    )
    monkeypatch.setattr(seam, "run_command", run)
    assert auto_release.maybe_create_release_ticket(
        "owner/repo", "v0.6.0",
        repo_dir=repo_dir, deploy_home=repo_dir, base_branch="main",
    ) is None
    assert not any(command[:3] == ["gh", "issue", "create"] for command in calls)


def test_non_python_missing_version_file_skips_with_event(
    monkeypatch, tmp_path, caplog,
):
    github = tmp_path / ".github"
    github.mkdir()
    (github / "release-ticket-template.md").write_text(TEMPLATE, encoding="utf-8")
    run, calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "open", "open_issues": 0}],
    )
    monkeypatch.setattr(seam, "run_command", run)
    with caplog.at_level("WARNING"):
        assert auto_release.maybe_create_release_ticket(
            "owner/repo", "v0.6.0",
            repo_dir=tmp_path, deploy_home=tmp_path, base_branch="main",
        ) is None
    assert not any(command[:3] == ["gh", "issue", "create"] for command in calls)
    assert "reason=version_file_unknown" in caplog.text


def test_default_false_arm_path_makes_no_create_calls(monkeypatch, tmp_path):
    _python_repo(tmp_path)
    calls = []
    monkeypatch.setattr(
        seam, "run_command",
        lambda command, **kwargs: calls.append(command) or "[]",
    )
    config = runner.load_config(_host_toml(tmp_path))
    assert config.auto_create_release_ticket is False
    runner.arm_release_ticket("owner/repo", "v0.6.0")
    assert not any(command[:3] == ["gh", "issue", "create"] for command in calls)
    assert any(command[:3] == ["gh", "issue", "list"] for command in calls)


def test_wrap_creates_then_arms_without_dispatch_label_on_create(
    monkeypatch, tmp_path, caplog,
):
    repo_dir = _python_repo(tmp_path)
    path = _host_toml(
        tmp_path,
        "auto_create_release_ticket = true\n"
        f'repo_dir = "{repo_dir}"\n'
        f'deploy_home = "{repo_dir}"\n',
    )
    run, calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "open", "open_issues": 0}],
        created_url="https://github.com/owner/repo/issues/900",
    )
    monkeypatch.setattr(seam, "run_command", run)
    config = runner.load_config(path)
    with caplog.at_level("INFO"):
        runner.arm_release_ticket("owner/repo", "v0.6.0")
    create = next(command for command in calls if command[:3] == ["gh", "issue", "create"])
    assert "ai-ready" not in create
    assert "release_ticket_created milestone=v0.6.0 issue=#900" in caplog.text
    assert any(
        command[:3] == ["gh", "issue", "list"]
        and "-label:ai-ready" in " ".join(command)
        for command in calls
    )


def test_create_failure_does_not_fail_the_tick(monkeypatch, tmp_path, caplog):
    _write_prompts(tmp_path)
    _python_repo(tmp_path)
    path = _host_toml(tmp_path, "auto_create_release_ticket = true\n")
    monkeypatch.setattr(runner, "pick_next_delivery", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "advance_active_milestone_on_idle", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        seam, "run_command",
        lambda command, **kwargs: (_ for _ in ()).throw(
            RuntimeError("gh down"),
        ),
    )
    with caplog.at_level("ERROR"):
        assert runner.main(["--config", str(path)]) == 0
    assert "release_ticket_create_failed" in caplog.text


def test_read_flag_variants(tmp_path):
    assert auto_release.read_auto_create_release_ticket(None) is False
    missing = tmp_path / "absent.toml"
    assert auto_release.read_auto_create_release_ticket(missing) is False
    with pytest.raises(OSError):
        auto_release.read_auto_create_release_ticket(missing, fail_fast=True)
    bad = tmp_path / "bad.toml"
    bad.write_text("= not toml", encoding="utf-8")
    assert auto_release.read_auto_create_release_ticket(bad) is False
    with pytest.raises(tomllib.TOMLDecodeError):
        auto_release.read_auto_create_release_ticket(bad, fail_fast=True)
    path = tmp_path / "orbi.toml"
    path.write_text('auto_create_release_ticket = "yes"\n', encoding="utf-8")
    assert auto_release.read_auto_create_release_ticket(path) is False
    with pytest.raises(ValueError, match="must be a boolean"):
        auto_release.read_auto_create_release_ticket(path, fail_fast=True)
    path.write_text("auto_create_release_ticket = true\n", encoding="utf-8")
    assert auto_release.read_auto_create_release_ticket(path) is True


def test_install_is_idempotent_and_noop_without_runner(monkeypatch):
    auto_release.install()
    auto_release.install()
    monkeypatch.delitem(__import__("sys").modules, "orbi.runner")
    auto_release.install()


def test_log_format_hook_installs_and_keeps_the_format():
    auto_release.install_import_hooks()
    assert auto_release._log_format() == "%(levelname)s %(message)s"


def test_render_inserts_fingerprint_without_background_heading():
    body = auto_release.render_release_ticket_body(
        "- version: vX.Y.Z\n", version="v0.6.0", base_branch="main",
        version_file="pyproject.toml",
    )
    assert body.startswith(auto_release.fingerprint("v0.6.0"))
    already = auto_release.fingerprint("v0.6.0") + "\n- version: vX.Y.Z\n"
    body = auto_release.render_release_ticket_body(
        already, version="v0.6.0", base_branch="main",
        version_file="pyproject.toml",
    )
    assert body.count(auto_release.fingerprint("v0.6.0")) == 1


def test_issue_number_from_url_rejects_unparseable():
    with pytest.raises(RuntimeError, match="no issue URL"):
        auto_release._issue_number_from_url("not-a-url")


def test_template_containing_test_command_fails_fast(monkeypatch, tmp_path):
    repo_dir = _python_repo(tmp_path)
    (repo_dir / ".github" / "release-ticket-template.md").write_text(
        "## 背景\n\ntest_command: make test\n\n## Release\n\n"
        "- version: vX.Y.Z\n- base_branch: main\n"
        "- scope_from_milestone: vX.Y.Z\n",
        encoding="utf-8",
    )
    run, _calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "open", "open_issues": 0}],
    )
    monkeypatch.setattr(seam, "run_command", run)
    with pytest.raises(RuntimeError, match="test_command"):
        auto_release.maybe_create_release_ticket(
            "owner/repo", "v0.6.0",
            repo_dir=repo_dir, deploy_home=repo_dir, base_branch="main",
        )


def test_existing_ticket_without_int_number_skips(monkeypatch, tmp_path, caplog):
    repo_dir = _python_repo(tmp_path)
    run, calls = _fake_idle_gh(
        milestones=[{"title": "v0.6.0", "state": "open", "open_issues": 0}],
        issues=[{"number": "846", "kind": "release"}],
    )
    monkeypatch.setattr(seam, "run_command", run)
    with caplog.at_level("INFO"):
        assert auto_release.maybe_create_release_ticket(
            "owner/repo", "v0.6.0",
            repo_dir=repo_dir, deploy_home=repo_dir, base_branch="main",
        ) is None
    assert "issue=-" in caplog.text
    assert not any(command[:3] == ["gh", "issue", "create"] for command in calls)


def test_maybe_create_from_idle_noop_without_repo_or_flag():
    assert auto_release.maybe_create_from_idle("owner/repo", "v0.6.0") is None

    class Cfg:
        auto_create_release_ticket = True
        repo_dir = Path(".")
        deploy_home = Path(".")
        base_branch = "main"

    def caller():
        config = Cfg()  # noqa: F841
        assert auto_release.maybe_create_from_idle("", "v0.6.0") is None
        assert auto_release.maybe_create_from_idle("owner/repo", "") is None

    caller()


def test_install_noop_when_runner_lacks_arm(monkeypatch):
    import sys
    import types

    fake = types.ModuleType("orbi.runner")
    monkeypatch.setitem(sys.modules, "orbi.runner", fake)
    auto_release.install()
    fake.load_config = lambda path, **kwargs: types.SimpleNamespace()
    auto_release.install()


def test_cli_source_auto_release_hook_is_idempotent():
    from orbi.cli_source import _hook_auto_release_ticket
    _hook_auto_release_ticket()
    _hook_auto_release_ticket()
