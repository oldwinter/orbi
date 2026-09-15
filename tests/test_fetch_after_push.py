"""Issue #898: after push, a single-branch clone has no origin/<branch>.

A Fedora-style checkout configures
``remote.origin.fetch=+refs/heads/main:refs/remotes/origin/main``.
The delivery branch is committed and pushed, but ``git rev-parse
origin/<branch>`` then exits 128 because that remote-tracking ref was
never fetched. Closeout must fetch the exact pushed branch before
rev-parse so the PR can be created.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import git

import orbi.gitops as gitops
import orbi.runner as runner
from orbi.delivery_scene import RunContext
from seam import seam
from tests.test_bootstrap_runner import (
    DELIVER_BRANCH,
    FAKE_HEAD_SHA,
    FAKE_PR_URL,
    FAKE_RUN_ID,
    _seed_deliver_run_state,
    fake_deliver_run,
)


def _init_single_branch_clone(tmp_path: Path) -> tuple[Path, Path]:
    """Bare origin + clone whose fetchspec is only origin/main."""
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
    fetchspec = git(clone, "config", "--get", "remote.origin.fetch")
    assert fetchspec == "+refs/heads/main:refs/remotes/origin/main"
    return origin, clone


def test_rev_parse_of_a_fresh_push_fails_on_a_single_branch_clone(tmp_path):
    """The product bug: push succeeds, origin/<branch> is unknown."""
    _origin, clone = _init_single_branch_clone(tmp_path)
    branch = "orbi/issue-898"
    git(clone, "checkout", "-b", branch)
    (clone / "delivery.txt").write_text("done\n", encoding="utf-8")
    git(clone, "add", ".")
    git(clone, "commit", "-m", "delivery")
    git(clone, "push", "origin", f"HEAD:{branch}")
    result = subprocess.run(
        ["git", "rev-parse", f"origin/{branch}"],
        cwd=clone, capture_output=True, text=True,
    )
    assert result.returncode == 128
    assert "unknown revision" in result.stderr


def test_fetch_origin_branch_makes_the_pushed_branch_rev_parse(tmp_path):
    """After push, an explicit refspec fetch creates origin/<branch>.

    ``git fetch origin <branch>`` only updates FETCH_HEAD when the
    clone's fetchspec is single-branch; rev-parse still fails. The
    destination refspec is required (Issue #898).
    """
    _origin, clone = _init_single_branch_clone(tmp_path)
    branch = "orbi/issue-898"
    git(clone, "checkout", "-b", branch)
    (clone / "delivery.txt").write_text("done\n", encoding="utf-8")
    git(clone, "add", ".")
    git(clone, "commit", "-m", "delivery")
    local_head = git(clone, "rev-parse", "HEAD")
    git(clone, "push", "origin", f"HEAD:{branch}")
    gitops.fetch_origin_branch(clone, branch, cwd=clone)
    assert git(clone, "rev-parse", f"origin/{branch}") == local_head


def test_deliver_pr_fetches_the_pushed_branch_before_rev_parse(
    monkeypatch, tmp_path,
):
    """Closeout fetches origin/<delivery> after push, then creates the PR.

    A single-branch clone has no tracking ref for the new branch.
    rev-parse must not run until that fetch has happened.
    """
    calls = []
    fetched: list[bool] = []
    _seed_deliver_run_state(tmp_path)

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[:3] == ["git", "fetch", "origin"] and command[3:] == [
            f"+refs/heads/{DELIVER_BRANCH}:refs/remotes/origin/{DELIVER_BRANCH}",
        ]:
            fetched.append(True)
            return ""
        if command[:3] == ["git", "rev-parse", f"origin/{DELIVER_BRANCH}"]:
            if not fetched:
                raise subprocess.CalledProcessError(
                    128, command,
                    stderr=(
                        f"fatal: ambiguous argument "
                        f"origin/{DELIVER_BRANCH}: unknown revision "
                        "or path not in the working tree."
                    ),
                )
            return FAKE_HEAD_SHA
        return fake_deliver_run(command, **kwargs)

    monkeypatch.setattr(seam, "run_command", fake_run)
    assert runner.deliver_pr(
        RunContext(
            run_id=FAKE_RUN_ID, issue=4, branch=DELIVER_BRANCH,
            worktree=tmp_path, source_repo="o/r",
        ),
        "main", "9" * 40, issue_title="t", repo_dir=tmp_path,
    ) == FAKE_PR_URL
    push = calls.index(["git", "push", "origin", f"HEAD:{DELIVER_BRANCH}"])
    fetch = calls.index([
        "git", "fetch", "origin",
        f"+refs/heads/{DELIVER_BRANCH}:refs/remotes/origin/{DELIVER_BRANCH}",
    ])
    rev = calls.index(["git", "rev-parse", f"origin/{DELIVER_BRANCH}"])
    assert push < fetch < rev
    assert fetched == [True]
