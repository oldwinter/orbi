"""Issue #945: fork PR takeover fetches refs/pull/N/head.

A contributor branch exists only on the fork. ``git fetch origin
<headRefName>`` exits 128 (couldn't find remote ref). GitHub exposes
the PR head as ``refs/pull/N/head`` on the base repository; an explicit
destination refspec records ``origin/<head>`` so worktree creation
can check that tracking ref out.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import git

import orbi.gitops as gitops
import orbi.runner as runner
from tests.fakes.github import FakeGh
from tests.fakes.gitops import FakeGit
from tests.test_resume_pr import (
    expected_resume_worktree,
    make_resume_config,
    make_resume_issue,
    make_resume_scene,
)
from seam import seam


def _init_origin_with_pull_ref(tmp_path: Path) -> tuple[Path, Path, str]:
    """Bare origin whose only extra ref is refs/pull/592/head."""
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git(origin, "init", "--bare", "-b", "main")
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "clone", str(origin), str(seed)],
        capture_output=True, text=True, check=True,
    )
    git(seed, "config", "user.email", "pilot@test.local")
    git(seed, "config", "user.name", "Pilot")
    (seed / "readme.txt").write_text("base\n", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "main")
    git(seed, "push", "origin", "HEAD:main")
    git(seed, "checkout", "-b", "fix/outer")
    (seed / "fix.txt").write_text("fork\n", encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "fork head")
    head = git(seed, "rev-parse", "HEAD")
    git(seed, "push", "origin", "HEAD:refs/pull/592/head")

    clone = tmp_path / "clone"
    subprocess.run(
        [
            "git", "clone", "--single-branch", "--branch", "main",
            str(origin), str(clone),
        ],
        capture_output=True, text=True, check=True,
    )
    git(clone, "config", "user.email", "pilot@test.local")
    git(clone, "config", "user.name", "Pilot")
    missing = subprocess.run(
        ["git", "rev-parse", "origin/fix/outer"],
        cwd=clone, capture_output=True, text=True,
    )
    assert missing.returncode == 128
    return origin, clone, head


def test_fetch_origin_head_fails_when_the_branch_lives_only_on_the_fork(
    tmp_path,
):
    _origin, clone, _head = _init_origin_with_pull_ref(tmp_path)
    result = subprocess.run(
        ["git", "fetch", "origin", "fix/outer"],
        cwd=clone, capture_output=True, text=True,
    )
    assert result.returncode == 128
    assert "couldn't find remote ref" in result.stderr


def test_create_worktree_from_pull_head_makes_origin_branch_rev_parse(
    tmp_path,
):
    _origin, clone, head = _init_origin_with_pull_ref(tmp_path)
    path = gitops.create_worktree(
        clone, "o/r", 3, "run1", "base",
        existing_branch=True, branch="fix/outer", pr_number=592,
    )
    assert git(clone, "rev-parse", "origin/fix/outer") == head
    assert git(path, "rev-parse", "HEAD") == head
    assert git(path, "branch", "--show-current") == "fix/outer"


def test_verify_resumed_pr_external_scene_fetches_pull_head(
    monkeypatch, tmp_path,
):
    """Gone worktree + fork PR: recreate from refs/pull/N/head, not origin."""
    fake_git = FakeGit(tmp_path, base_branch="main")
    fake_gh = FakeGh("owner/repo")
    fake_gh.add_issue(9, title="ship")
    external_head = fake_git.commit([fake_git.base_sha])
    fake_git.pull_heads["592"] = external_head
    external_url = "https://github.com/xqliu/orbi/pull/592"
    fake_gh.add_pr(
        592, head="fix/outer", base="main", oid=external_head,
        url=external_url,
    )

    def fake_run(command, **kwargs):
        if command[0] == "git":
            return fake_git(command, **kwargs)
        return fake_gh(command, **kwargs)

    monkeypatch.setattr(seam, "run_command", fake_run)
    scene = make_resume_scene(external_url)
    scene["external"] = "true"
    url = runner.verify_resumed_pr(
        scene, make_resume_issue(),
        make_resume_config(tmp_path), "owner/repo",
    )
    assert url == external_url
    worktree = expected_resume_worktree(tmp_path)
    assert worktree.is_dir()
    assert fake_git.worktrees[str(worktree)]["branch"] == "fix/outer"
    assert fake_git.worktrees[str(worktree)]["head"] == external_head
    assert any(
        command[:3] == ["git", "fetch", "origin"]
        and command[3:] == [
            "+refs/pull/592/head:refs/remotes/origin/fix/outer",
        ]
        for command in fake_git.calls
    )
