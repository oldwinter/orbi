"""Release and dispatch share one fused base_branch (Issue #931).

The release state machine used to re-read the raw ``[[repositories]]``
entry, skipping the ``.github/orbi.toml`` overlay. With a policy the
dev path froze ``develop`` and release pushed ``main``. Without a
policy file the opposite held: the entry never reached the dev path.
Fusion happens once; every consumer reads ``config.base_branch``.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import orbi.release as release
import orbi.repo_config as repo_config
import orbi.runner as runner
import orbi.source_base as source_base
from seam import seam

source_base.install()


def test_resolve_release_declaration_uses_the_fused_base_branch(
        monkeypatch):
    """The config a release run receives is already fused:
    repository entry -> .github/orbi.toml policy override. The inline
    entry lookup re-read the RAW entry, discarding the policy layer:
    policy=develop + entry=main resolved dev to develop and release to
    main — freezing and pushing the release onto the wrong branch."""
    monkeypatch.setattr(
        release, "run_command", lambda *args, **kwargs: "pyproject.toml\n",
    )
    config = Mock(
        base_branch="develop",
        repositories=[{"github": "o/r", "base_branch": "main"}],
    )
    declaration = release.resolve_release_declaration(
        {"milestone": {"title": "v0.5.8"}}, {}, config, "o/r",
        Path("/repo"), "abc123",
    )
    assert declaration["base_branch"] == "develop"


def test_resolve_source_base_branch_fuses_entry_without_a_policy_file():
    """With NO repository policy file the entry fallback must STILL
    apply: before this fix the dev path read the host base_branch while
    the release path re-derived the entry value — two paths, two base
    branches for the same repository. A policy, when present, still
    overrides the entry."""
    config = runner.RunnerConfig(
        repo_dir=Path("/repo"), source_repos=("o/r",),
        deploy_home=Path("/repo"), max_concurrency=2,
        base_branch="main",
        repositories=({"github": "o/r", "base_branch": "develop"},),
    )
    fused = runner.resolve_source_base_branch(config, "o/r", None)
    assert fused.base_branch == "develop"
    other = runner.resolve_source_base_branch(config, "other/repo", None)
    assert other.base_branch == "main"

    policy = repo_config.RepoPolicy(base_branch="release")
    overridden = runner.resolve_source_base_branch(config, "o/r", policy)
    assert overridden.base_branch == "release"


def test_main_fuses_entry_base_branch_without_a_policy_file(
        monkeypatch, tmp_path):
    """Dispatch must apply the [[repositories]] entry even when
    `.github/orbi.toml` is missing — otherwise resume verification and
    freeze_base stay on the host branch while release used to re-read
    the entry."""
    (tmp_path / "prompts").mkdir()
    for name in ("prompts/prompt.md", "prompts/prompt_review.md"):
        (tmp_path / name).write_text("prompt", encoding="utf-8")
    config_path = tmp_path / "orbi.toml"
    config_path.write_text('source_repos = ["owner/repo"]\n', encoding="utf-8")

    orig_load = runner.load_config

    def load_with_entry(path):
        return replace(
            orig_load(path),
            repositories=(
                {"github": "owner/repo", "base_branch": "develop"},
            ),
        )

    monkeypatch.setattr(runner, "load_config", load_with_entry)
    monkeypatch.setattr(runner, "validate_config", lambda config: None)
    monkeypatch.setattr(runner, "load_repo_policy", lambda *a, **k: None)
    issue = {"number": 9, "title": "ship", "body": "", "labels": []}
    scene = {
        "run_id": "a1b2c3d4", "base_branch": "develop", "base_sha": "s",
        "pr_url": "https://github.com/owner/repo/pull/98", "external": "",
    }
    monkeypatch.setattr(
        runner, "pick_next_delivery",
        lambda repos, slot_dir, max_concurrency, active_milestone=None,
        **_kwargs: ("owner/repo", issue, scene),
    )
    seen = {}

    def fake_verify(scene_, issue_, config_, source_repo):
        seen["verify_base"] = source_base._fuse(
            config_, source_repo,
        ).base_branch
        return "https://github.com/owner/repo/pull/98"

    monkeypatch.setattr(runner, "verify_resumed_pr", fake_verify)
    monkeypatch.setattr(
        runner, "delivery_step",
        lambda *a, **k: seen.setdefault(
            "wait_base",
            source_base._fuse(a[2], a[3] if len(a) > 3 else "owner/repo").base_branch,
        ),
    )
    monkeypatch.setattr(seam, "run_command", lambda *a, **k: "[]")
    assert runner.main(["--config", str(config_path)]) == 0
    assert seen == {"verify_base": "develop", "wait_base": "develop"}


def test_install_rebinds_dispatch_consumers():
    assert runner.process_issue.__module__ == "orbi.source_base"
    assert runner.verify_resumed_pr.__module__ == "orbi.source_base"
    assert runner.delivery_step.__module__ == "orbi.source_base"
    assert release.resolve_release_declaration.__module__ == "orbi.source_base"
    assert release.process_release.__module__ == "orbi.source_base"


def test_install_is_idempotent():
    wrapped = release.resolve_release_declaration
    source_base.install()
    assert release.resolve_release_declaration is wrapped
    assert runner.resolve_source_base_branch is source_base.resolve_source_base_branch


def test_without_raw_base_entries_clears_a_frozen_config():
    config = runner.RunnerConfig(
        base_branch="develop",
        repositories=({"github": "o/r", "base_branch": "main"},),
    )
    hidden = source_base._without_raw_base_entries(config)
    assert hidden.repositories == ()
    assert hidden.base_branch == "develop"
    assert config.repositories[0]["base_branch"] == "main"


def test_fuse_returns_mock_config_when_replace_is_impossible():
    config = Mock(base_branch="develop", repositories=[])
    assert source_base._fuse(config, "o/r", None) is config


def test_fuse_returns_a_dict_config_without_repositories():
    """CLOSED-unmerged tests (and several bootstrap paths) pass ``{}``.
    Fusion used to AttributeError on ``config.repositories`` before
    the original delivery_step could mark the Issue ai-blocked."""
    config = {}
    assert source_base._fuse(config, "o/r", None) is config


def test_fuse_returns_a_config_class_unchanged():
    assert source_base._fuse(runner.RunnerConfig, "o/r", None) is (
        runner.RunnerConfig
    )


def test_fuse_returns_a_dataclass_without_repositories():
    from dataclasses import dataclass

    @dataclass
    class Bare:
        base_branch: str = "main"

    config = Bare()
    assert source_base._fuse(config, "o/r", None) is config


def test_dispatch_wrappers_fuse_then_call_the_original(monkeypatch):
    seen = {}
    config = runner.RunnerConfig(
        base_branch="main",
        repositories=({"github": "o/r", "base_branch": "develop"},),
    )
    monkeypatch.setattr(runner, "load_repo_policy", lambda *a, **k: None)
    monkeypatch.setattr(
        source_base, "_orig_process_issue",
        lambda issue, config, source_repo, repo_policy=None: seen.setdefault(
            "issue", config.base_branch,
        ),
    )
    monkeypatch.setattr(
        source_base, "_orig_verify_resumed_pr",
        lambda scene, issue, config, source_repo: seen.setdefault(
            "verify", config.base_branch,
        ),
    )
    monkeypatch.setattr(
        source_base, "_orig_delivery_step",
        lambda pr_url, issue, config, source_repo, **kwargs: seen.setdefault(
            "delivery", config.base_branch,
        ),
    )
    monkeypatch.setattr(
        source_base, "_orig_process_release",
        lambda issue, config, source_repo: seen.setdefault(
            "release", (config.base_branch, config.repositories),
        ),
    )
    source_base.process_issue({}, config, "o/r")
    source_base.verify_resumed_pr({}, {}, config, "o/r")
    source_base.delivery_step("url", {}, config, "o/r")
    source_base.process_release({}, config, "o/r")
    assert seen["issue"] == "develop"
    assert seen["verify"] == "develop"
    assert seen["delivery"] == "develop"
    assert seen["release"] == ("develop", ())


def test_without_raw_base_entries_mutates_a_mock():
    config = Mock(repositories=[{"github": "o/r", "base_branch": "main"}])
    assert source_base._without_raw_base_entries(config) is config
    assert config.repositories == ()


def test_without_raw_base_entries_skips_config_without_repositories():
    class Bare:
        pass

    config = Bare()
    assert source_base._without_raw_base_entries(config) is config


def test_without_raw_base_entries_ignores_an_immutable_mock():
    class Frozen:
        @property
        def repositories(self):
            return [{"github": "o/r"}]

        @repositories.setter
        def repositories(self, value):
            raise RuntimeError("frozen")

    config = Frozen()
    assert source_base._without_raw_base_entries(config) is config


def test_install_skips_while_runner_is_still_loading(monkeypatch):
    monkeypatch.setattr(source_base, "_installed", False)
    monkeypatch.delattr(runner, "process_issue")
    source_base.install()
    assert source_base._installed is False


def test_fuse_uses_an_explicit_policy_without_reloading(monkeypatch):
    """A caller that already loaded the policy must not hit GitHub
    again; ``_fuse(..., policy)`` skips ``load_repo_policy``."""
    config = runner.RunnerConfig(
        base_branch="main",
        repositories=({"github": "o/r", "base_branch": "develop"},),
    )
    monkeypatch.setattr(
        runner, "load_repo_policy",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("reloaded")),
    )
    policy = repo_config.RepoPolicy(base_branch="release")
    assert source_base._fuse(config, "o/r", policy).base_branch == "release"


def test_fuse_treats_a_policy_load_failure_as_no_policy(monkeypatch):
    monkeypatch.setattr(
        runner, "load_repo_policy",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("api down")),
    )
    config = runner.RunnerConfig(
        base_branch="main",
        repositories=({"github": "o/r", "base_branch": "develop"},),
    )
    assert source_base._fuse(config, "o/r").base_branch == "develop"


def test_source_base_slot_hook_is_idempotent():
    from orbi.cli_source import _hook_source_base_on_slot
    bound = runner.acquire_slot
    _hook_source_base_on_slot()
    assert runner.acquire_slot is bound
    assert getattr(bound, "_source_base_hooked", False)


def test_source_base_slot_hook_skips_without_acquire_slot(monkeypatch):
    import sys
    import types
    from orbi.cli_source import _hook_source_base_on_slot
    fake = types.ModuleType("orbi.runner")
    monkeypatch.setitem(sys.modules, "orbi.runner", fake)
    _hook_source_base_on_slot()


def test_source_base_slot_hook_skips_without_runner_module(monkeypatch):
    import sys
    from orbi.cli_source import _hook_source_base_on_slot
    monkeypatch.delitem(sys.modules, "orbi.runner")
    _hook_source_base_on_slot()
