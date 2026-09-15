"""Run artifacts stay out of version control (Issue #80 review round 1).

`plan.md`, `test.log` and `verify.md` are per-run artifacts written
into the task worktree (prompt.md steps 2 and 5). A worktree is created from the
frozen base SHA (`git worktree add ... <base_sha>`): if these files
were tracked in the base, every new worktree would inherit the
PREVIOUS run's plan and test log, and a run that did not overwrite
them would post the previous run's results as its own `plan ready` /
`tests passed` milestones. They are therefore gitignored (like
`.pi-session/`), and these tests guard the contract.
"""
from pathlib import Path

import pytest

from conftest import git

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_run_artifacts_are_not_tracked_at_repository_root():
    """A tracked plan.md/test.log would be checked out into every new
    task worktree (created from the base SHA) as the previous run's
    stale artifact."""
    tracked = git(REPO_ROOT, "ls-files", "plan.md", "test.log")
    assert tracked == "", f"run artifacts are tracked: {tracked}"


def test_run_artifacts_are_gitignored():
    """The ignore patterns keep the artifacts out of future delivery
    commits (an accidental `git add -A` in a task worktree must not
    re-track them)."""
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    patterns = [
        line.strip()
        for line in gitignore.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert "plan.md" in patterns
    assert "test.log" in patterns
    assert "verify.md" in patterns


def make_repo_with_real_gitignore(tmp_path: Path, pre_tracked=()) -> Path:
    """A fresh repo whose base commit carries the repository's real
    `.gitignore` — exactly the state of a new task worktree created
    from the frozen base SHA. `pre_tracked` is a sequence of
    (relative path, content) pairs committed BEFORE the ignore rules
    land, i.e. files already under version control."""
    repo = tmp_path / "wt"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "pilot@test.local")
    git(repo, "config", "user.name", "Pilot")
    for name, content in pre_tracked:
        (repo / name).write_text(content, encoding="utf-8")
        git(repo, "add", name)
    (repo / ".gitignore").write_text(
        (REPO_ROOT / ".gitignore").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def test_log_and_coverage_artifacts_are_gitignored():
    """Issue #235: a normal test run leaves `coverage.log` (and other
    `*.log` / coverage artifacts) in the task worktree; the
    `deliver_pr` dirty-worktree gate must never see them. `git
    check-ignore` exits 0 only for ignored paths — the git() helper
    fails fast on any other outcome, so reaching the assertion IS the
    "ignored" proof. Tracked files are unaffected by ignore rules,
    so this only proves the untracked-file contract."""
    for path in [
        "coverage.log",
        "test.log",
        "some-other.log",
        ".coverage",
        ".coverage.host123",
        "coverage.xml",
        "coverage.json",
        "htmlcov/index.html",
    ]:
        out = git(REPO_ROOT, "check-ignore", "-v", path)
        assert out.splitlines()[0].endswith("\t" + path)
    # The coverage-file rule is the glob `.coverage*` (the old exact
    # `.coverage` line was replaced), not a coincidental match.
    out = git(REPO_ROOT, "check-ignore", "-v", ".coverage.host123")
    assert out.splitlines()[0].split("\t")[0].rsplit(":", 1)[-1] == ".coverage*"
    # Issue #301: `coverage.json` (the tiered gate's `coverage json -o`
    # artifact) is ignored by its own explicit rule — `.coverage*` does
    # not match it because it does not start with `.coverage`.
    out = git(REPO_ROOT, "check-ignore", "-v", "coverage.json")
    assert out.splitlines()[0].split("\t")[0].rsplit(":", 1)[-1] == "coverage.json"


def test_common_dev_artifacts_are_gitignored():
    """Issue #235: the common local development artifacts are ignored
    too — none of these patterns can mask source, config, credentials
    or delivery files (no tracked path matches them)."""
    for path in [
        ".mypy_cache/cache.db",
        ".ruff_cache/CACHEDIR.TAG",
        ".hypothesis/unicode_data/13.0.0/data.txt",
        ".venv/bin/python",
        "module.pyc",
        "module.pyo",
        "module.pyd",
        ".DS_Store",
        "edit.swp",
        "scratch.tmp",
    ]:
        out = git(REPO_ROOT, "check-ignore", "-v", path)
        assert out.splitlines()[0].endswith("\t" + path)


def test_tracked_log_file_stays_tracked(tmp_path):
    """Issue #235 acceptance: `*.log` only affects untracked files —
    a log file already in the index is not removed from the index and
    not deleted from disk by the ignore rule; a NEW untracked log in
    the same repo is ignored, so the gate sees a clean tree."""
    repo = make_repo_with_real_gitignore(
        tmp_path, pre_tracked=[("tracked.log", "old log\n")],
    )
    # The ignore rule is in place and the tracked log survives it:
    # `git ls-files` still lists it and the file is still on disk.
    assert "tracked.log" in git(repo, "ls-files").splitlines()
    assert (repo / "tracked.log").exists()
    # A new untracked log is ignored: the gate sees a clean tree.
    (repo / "fresh.log").write_text("new log\n", encoding="utf-8")
    assert git(repo, "status", "--porcelain") == ""


def test_untracked_source_and_docs_still_reported(tmp_path):
    """Issue #235 acceptance: the ignore rules must not widen the
    dirty-worktree gate — a real leftover (untracked source and docs)
    is still reported by `git status --porcelain`."""
    repo = make_repo_with_real_gitignore(tmp_path)
    (repo / "unexpected.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "notes.md").write_text("notes\n", encoding="utf-8")
    lines = git(repo, "status", "--porcelain").splitlines()
    assert "?? unexpected.py" in lines
    assert "?? notes.md" in lines


def test_pi_loop_state_is_gitignored():
    """Issue #215: the pi-loop plugin writes `.pi/loops.json` into the
    task worktree cwd at session shutdown (the #214 scene: `deliver_pr`
    fail-fasted on `?? .pi/`). It is not part of the agent's commit
    boundary, so the ignore rules must keep it out of
    `git status --porcelain` — and `.pi-session/` stays ignored."""
    # `git check-ignore -v` prints `<source>:<line>:<pattern>\t<path>`
    # for an ignored path (exit 0); the git() helper fails fast on any
    # other outcome, so reaching the assertions IS the "ignored" proof.
    out = git(REPO_ROOT, "check-ignore", "-v", ".pi/loops.json")
    assert out.splitlines()[0].endswith("\t.pi/loops.json")
    # The matching pattern is the `.pi/` directory rule in .gitignore —
    # not a coincidental substring of another pattern.
    assert out.splitlines()[0].split("\t")[0].rsplit(":", 1)[-1] == ".pi/"
    # `.pi-session/` remains ignored as before.
    out2 = git(REPO_ROOT, "check-ignore", "-v", ".pi-session/sess.jsonl")
    assert out2.splitlines()[0].split("\t")[0].rsplit(":", 1)[-1] == ".pi-session/"


def test_worktree_with_only_coverage_json_is_clean_for_status(tmp_path):
    """Issue #301 acceptance: the tiered coverage gate (#234) writes
    `coverage.json` at the worktree root (`coverage json -o ...`), and
    `.coverage*` does not match it — every gate run hit the dirty-worktree
    gate with `?? coverage.json`. With the explicit rule a worktree whose
    only untracked entry is `coverage.json` is clean for
    `git status --porcelain`, while a real leftover is still reported."""
    repo = make_repo_with_real_gitignore(tmp_path)
    (repo / "coverage.json").write_text('{"totals": {}}\n', encoding="utf-8")
    assert git(repo, "status", "--porcelain") == ""
    # The gate is NOT weakened: untracked source is still reported.
    (repo / "unexpected.py").write_text("x = 1\n", encoding="utf-8")
    assert git(repo, "status", "--porcelain") == "?? unexpected.py"


def test_worktree_with_only_pi_loop_state_is_clean_for_status(tmp_path):
    """Issue #215 acceptance: a worktree whose only untracked entry is
    the pi-loop state (`.pi/loops.json`) is clean for
    `git status --porcelain` — the `deliver_pr` dirty-worktree gate
    sees nothing. The repo's real `.gitignore` is inherited, exactly
    like a new worktree created from the base. The gate is NOT
    weakened: any other untracked file is still reported."""
    repo = tmp_path / "wt"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "pilot@test.local")
    git(repo, "config", "user.name", "Pilot")
    (repo / ".gitignore").write_text(
        (REPO_ROOT / ".gitignore").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    # The base commit carries the tracked .gitignore — exactly the
    # state of a new worktree created from the base SHA.
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-q", "-m", "base")
    (repo / ".pi").mkdir()
    (repo / ".pi" / "loops.json").write_text('{"loops": []}\n', encoding="utf-8")
    # Only the pi-loop state is untracked: the gate sees a clean tree.
    assert git(repo, "status", "--porcelain") == ""
    # Another untracked file is still reported: the dirty-worktree
    # gate is not weakened for real leftovers.
    (repo / "junk.txt").write_text("x", encoding="utf-8")
    assert git(repo, "status", "--porcelain") == "?? junk.txt"
