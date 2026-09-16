"""Issue #855: dangling ``active_milestone`` after a release.

The idle tick already advances a closed Milestone, but a failure was
only ``LOGGER.exception`` — not a structured journal event — so the
queue could stall with nothing greppable. These tests pin:

- ``auto_next_milestone=true`` still advances with ``active_milestone_advanced``
- ``auto_next_milestone=false`` still does not rewrite the host file
- idle advance failure emits ``active_milestone_advance_failed`` as an event
- ``warn_on_dangling_milestone`` defaults false (unset == today)
- explicit true emits ``active_milestone_dangling active=… state=closed|missing``
- the key is host-only with bool validation, like ``auto_next_milestone``
"""
from __future__ import annotations

import json
import sys
import tomllib
import types
from pathlib import Path

import pytest

import orbi.cli  # noqa: F401  — binds the CLI load_config wrap (changed lines)
import orbi.journal as journal
import orbi.milestone_idle as milestone_idle
import orbi.repo_config as repo_config
import orbi.runner as runner
from seam import seam


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


def _milestones_payload(*rows: dict) -> str:
    return json.dumps([list(rows)])


def test_load_config_defaults_warn_on_dangling_milestone_to_false(tmp_path):
    config = runner.load_config(_host_toml(tmp_path))
    assert config.warn_on_dangling_milestone is False


def test_load_config_reads_warn_on_dangling_milestone(tmp_path):
    path = _host_toml(tmp_path, "warn_on_dangling_milestone = true\n")
    assert runner.load_config(path).warn_on_dangling_milestone is True
    path.write_text(
        'source_repos = ["owner/repo"]\n'
        "warn_on_dangling_milestone = false\n",
        encoding="utf-8",
    )
    assert runner.load_config(path).warn_on_dangling_milestone is False


def test_load_config_rejects_non_bool_warn_on_dangling_milestone(tmp_path):
    path = _host_toml(tmp_path, 'warn_on_dangling_milestone = "yes"\n')
    with pytest.raises(
        ValueError, match="warn_on_dangling_milestone must be a boolean",
    ):
        runner.load_config(path)


def test_repo_config_rejects_warn_on_dangling_milestone_as_host_only():
    with pytest.raises(
        repo_config.RepoConfigError,
        match=r"host-only key\(s\).*warn_on_dangling_milestone",
    ):
        repo_config.parse_repo_config("warn_on_dangling_milestone = true\n")
    assert "warn_on_dangling_milestone" in repo_config.HOST_ONLY_KEYS
    assert "warn_on_dangling_milestone" not in repo_config.POLICY_KEYS


def test_auto_next_true_advances_closed_active_and_emits_event(
    monkeypatch, tmp_path, caplog,
):
    config = tmp_path / "orbi.toml"
    config.write_text('active_milestone = "v0.6.0"\n', encoding="utf-8")
    monkeypatch.setattr(
        seam, "run_command",
        lambda command, **kwargs: _milestones_payload(
            {"title": "v0.6.0", "state": "closed"},
            {"title": "v0.6.1", "state": "open", "open_issues": 2},
        ),
    )
    with caplog.at_level("INFO"):
        assert runner.advance_active_milestone_on_idle(
            "owner/repo", "v0.6.0", config,
        ) == ("closed", "v0.6.1")
    assert 'active_milestone = "v0.6.1"\n' == config.read_text()
    assert "active_milestone_advanced old=v0.6.0 new=v0.6.1" in caplog.text
    assert "active_milestone_dangling" not in caplog.text


def test_auto_next_false_does_not_rewrite_closed_active(
    monkeypatch, tmp_path, caplog,
):
    config = tmp_path / "orbi.toml"
    config.write_text('active_milestone = "v0.6.0"\n', encoding="utf-8")
    monkeypatch.setattr(
        seam, "run_command",
        lambda command, **kwargs: _milestones_payload(
            {"title": "v0.6.0", "state": "closed"},
            {"title": "v0.6.1", "state": "open", "open_issues": 2},
        ),
    )
    with caplog.at_level("WARNING"):
        assert runner.advance_active_milestone_on_idle(
            "owner/repo", "v0.6.0", config, auto_next_milestone=False,
        ) == ("closed", None)
    assert config.read_text() == 'active_milestone = "v0.6.0"\n'
    assert "active_milestone_advance_pending" in caplog.text
    assert "active_milestone_dangling" not in caplog.text


def test_warn_flag_emits_dangling_closed_without_blocking_advance(
    monkeypatch, tmp_path, caplog,
):
    config = tmp_path / "orbi.toml"
    config.write_text(
        'active_milestone = "v0.6.0"\n'
        "warn_on_dangling_milestone = true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        seam, "run_command",
        lambda command, **kwargs: _milestones_payload(
            {"title": "v0.6.0", "state": "closed"},
            {"title": "v0.6.1", "state": "open", "open_issues": 1},
        ),
    )
    with caplog.at_level("INFO"):
        assert runner.advance_active_milestone_on_idle(
            "owner/repo", "v0.6.0", config,
        ) == ("closed", "v0.6.1")
    assert "active_milestone_dangling active=v0.6.0 state=closed" in caplog.text
    assert "active_milestone_advanced old=v0.6.0 new=v0.6.1" in caplog.text


def test_warn_flag_emits_dangling_missing(monkeypatch, tmp_path, caplog):
    config = tmp_path / "orbi.toml"
    config.write_text(
        'active_milestone = "v0.6.0"\n'
        "warn_on_dangling_milestone = true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        seam, "run_command",
        lambda command, **kwargs: _milestones_payload(
            {"title": "v0.6.1", "state": "open", "open_issues": 1},
        ),
    )
    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError, match="active_milestone_missing"):
            runner.advance_active_milestone_on_idle(
                "owner/repo", "v0.6.0", config,
            )
    assert "active_milestone_dangling active=v0.6.0 state=missing" in caplog.text


def test_warn_flag_stays_silent_when_active_is_open(
    monkeypatch, tmp_path, caplog,
):
    config = tmp_path / "orbi.toml"
    config.write_text(
        'active_milestone = "v0.6.0"\n'
        "warn_on_dangling_milestone = true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        seam, "run_command",
        lambda command, **kwargs: _milestones_payload(
            {"title": "v0.6.0", "state": "open"},
        ),
    )
    with caplog.at_level("WARNING"):
        assert runner.advance_active_milestone_on_idle(
            "owner/repo", "v0.6.0", config,
        ) == ("open", None)
    assert "active_milestone_dangling" not in caplog.text


def test_main_idle_advance_failure_emits_structured_event(
    monkeypatch, tmp_path, caplog,
):
    _write_prompts(tmp_path)
    config = _host_toml(tmp_path)
    monkeypatch.setattr(runner, "pick_next_delivery", lambda *a, **k: None)
    monkeypatch.setattr(runner, "arm_release_ticket", lambda *a, **k: None)
    monkeypatch.setattr(
        runner, "advance_active_milestone_on_idle",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("active_milestone_missing current=v0.6.0"),
        ),
    )
    with caplog.at_level("ERROR"):
        assert runner.main(["--config", str(config)]) == 0
    assert "active_milestone_advance_failed repo=owner/repo milestone=v0.6.0" in caplog.text
    assert 'error="active_milestone_missing current=v0.6.0"' in caplog.text


def test_observe_is_noop_outside_idle_advance(caplog):
    with caplog.at_level("WARNING"):
        milestone_idle.observe_idle_milestones(
            "owner/repo",
            [{"title": "v0.6.0", "state": "closed"}],
        )
    assert "active_milestone_dangling" not in caplog.text


def test_read_warn_flag_variants(tmp_path):
    assert milestone_idle.read_warn_on_dangling_milestone(None) is False
    missing = tmp_path / "absent.toml"
    assert milestone_idle.read_warn_on_dangling_milestone(missing) is False
    with pytest.raises(OSError):
        milestone_idle.read_warn_on_dangling_milestone(missing, fail_fast=True)
    bad = tmp_path / "bad.toml"
    bad.write_text("= not toml", encoding="utf-8")
    assert milestone_idle.read_warn_on_dangling_milestone(bad) is False
    with pytest.raises(tomllib.TOMLDecodeError):
        milestone_idle.read_warn_on_dangling_milestone(bad, fail_fast=True)
    assert milestone_idle.read_warn_on_dangling_milestone(object()) is False
    with pytest.raises(TypeError):
        milestone_idle.read_warn_on_dangling_milestone(object(), fail_fast=True)
    path = tmp_path / "orbi.toml"
    path.write_text('warn_on_dangling_milestone = "yes"\n', encoding="utf-8")
    assert milestone_idle.read_warn_on_dangling_milestone(path) is False
    with pytest.raises(ValueError, match="must be a boolean"):
        milestone_idle.read_warn_on_dangling_milestone(path, fail_fast=True)
    path.write_text("warn_on_dangling_milestone = true\n", encoding="utf-8")
    assert milestone_idle.read_warn_on_dangling_milestone(path) is True


def test_dangling_state_closed_missing_and_open():
    assert milestone_idle.dangling_state([], "v0.6.0") == "missing"
    assert milestone_idle.dangling_state(
        [{"title": "v0.6.0", "state": "closed"}], "v0.6.0",
    ) == "closed"
    assert milestone_idle.dangling_state(
        [{"title": "v0.6.0", "state": "open"}], "v0.6.0",
    ) is None
    assert milestone_idle.dangling_state(["ignore"], "v0.6.0") == "missing"


def test_github_list_milestones_wrap_observes(monkeypatch):
    import orbi.github as github
    seen = []
    monkeypatch.setattr(
        milestone_idle, "observe_idle_milestones",
        lambda repo, milestones: seen.append((repo, milestones)),
    )

    def orig(repo, *, timeout=None):
        del timeout
        return [{"title": "v0.6.0", "state": "closed"}]

    monkeypatch.setattr(github, "list_milestones", orig)
    milestone_idle._wrap_list_milestones()
    assert github.list_milestones("o/r") == [{"title": "v0.6.0", "state": "closed"}]
    assert seen == [("o/r", [{"title": "v0.6.0", "state": "closed"}])]
    milestone_idle._wrap_list_milestones()


def test_wrap_list_milestones_skips_without_github(monkeypatch):
    monkeypatch.delitem(sys.modules, "orbi.github")
    milestone_idle._wrap_list_milestones()


def test_cli_source_milestone_hook_is_idempotent():
    from orbi.cli_source import _hook_milestone_idle
    _hook_milestone_idle()
    _hook_milestone_idle()


def test_install_is_idempotent_and_noop_without_runner(monkeypatch):
    milestone_idle.install()
    milestone_idle.install()
    monkeypatch.delitem(sys.modules, "orbi.runner")
    milestone_idle.install()
    fake = types.ModuleType("orbi.runner")
    monkeypatch.setitem(sys.modules, "orbi.runner", fake)
    milestone_idle.install()


def test_log_format_hook_installs_and_keeps_the_format():
    milestone_idle.install_import_hooks()
    assert milestone_idle._log_format() == "%(levelname)s %(message)s"


def test_exception_wrap_emits_event_and_passes_other_messages(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            journal.LOGGER.exception(
                "active_milestone_advance_failed repo=%s milestone=%s",
                "o/r", "v0.6.0",
            )
            journal.LOGGER.exception("stale_milestone_issue_close_failed repo=%s", "o/r")
        journal._exception_with_advance_failed_event(
            "active_milestone_advance_failed",
        )
        journal._exception_with_advance_failed_event(42)
    assert "active_milestone_advance_failed repo=o/r milestone=v0.6.0" in caplog.text
    assert "error=boom" in caplog.text
    assert "stale_milestone_issue_close_failed" in caplog.text
    assert "error=active_milestone_advance_failed" in caplog.text
