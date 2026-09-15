"""Resume the same PR from its opened-PR state (Issue #45, #82).

Unit tests for the runner's resume path: an Issue in an opened-PR state
(`ai-pr-opened` or `ai-fix-needed`) carries a run-scoped scene in its
`Orbi opened PR:` comment. The next tick recovers run_id, branch,
worktree and PR URL from that comment and resumes the delivery on the
ORIGINAL branch, worktree and PR. Issue #82 removed the cold-start
fixer: both states resume into the SAME independent review session,
which fixes findings in the same session. Failures mark the Issue
`ai-blocked` and preserve the PR, branch and worktree.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

import orbi.runner as runner
from orbi import progress
from orbi import scene as scene_mod
from tests.test_progress_wiring import make_fake_gh
from tests.fakes.github import FakeGh
from tests.fakes.gitops import FakeGit
from seam import seam
import orbi.journal as journal
import orbi.github as github
from orbi.delivery_scene import RunContext


FAKE_RUN_ID = "a1b2c3d4"
FAKE_BRANCH = f"orbi/owner-repo-issue-9"
FAKE_WORKTREE = "/srv/repo/.worktrees/orbi-owner-repo-issue-9-a1b2c3d4"
FAKE_PR_URL = "https://github.com/owner/repo/pull/9"


@pytest.fixture(autouse=True)
def _reset_run_id(monkeypatch):
    """Each test starts without a bound run id."""
    monkeypatch.setattr(journal, "_CURRENT_RUN_ID", None)
