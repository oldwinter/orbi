"""Release and dispatch share one fused base_branch (Issue #931).

The release state machine used to re-read the raw ``[[repositories]]``
entry, skipping the ``.github/orbi.toml`` overlay. With a policy the
dev path froze ``develop`` and release pushed ``main``. Without a
policy file the opposite held: the entry never reached the dev path.
Fusion happens once at dispatch; every consumer reads
``config.base_branch``.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import orbi.release as release
import orbi.repo_config as repo_config
import orbi.runner as runner
from seam import seam


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


def test_release_does_not_reread_the_raw_repository_entry():
    source = Path(release.__file__).read_text(encoding="utf-8")
    assert 'repo.get("base_branch")' not in source
    assert "config.base_branch" in source


def test_process_release_prefers_declaration_then_fused_config():
    source = Path(release.__file__).read_text(encoding="utf-8")
    assert (
        "declaration.get(\"base_branch\") or config.base_branch"
        in source
    )


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
        seen["verify_base"] = config_.base_branch
        return "https://github.com/owner/repo/pull/98"

    monkeypatch.setattr(runner, "verify_resumed_pr", fake_verify)
    monkeypatch.setattr(
        runner, "delivery_step",
        lambda *a, **k: seen.setdefault("wait_base", a[2].base_branch),
    )
    monkeypatch.setattr(seam, "run_command", lambda *a, **k: "[]")
    assert runner.main(["--config", str(config_path)]) == 0
    assert seen == {"verify_base": "develop", "wait_base": "develop"}
