"""Readable ai-blocked / fix-needed progress comments (Issue #952).

The terminal progress scene is the first thing a stranger sees when a
run dies. It must name what happened, what they should do, and what
Orbi will do next — not a Python exception repr with an empty next step.
"""
from orbi import runner


def test_humanize_failure_turns_command_repr_into_one_line():
    detail = (
        "Command '['gh', 'issue', 'comment', '30', '--repo', "
        "'IchenDEV/doubao-skin', '--body', 'huge payload']' returned "
        "non-zero exit status 1. stderr=GraphQL: Something went wrong "
        "while executing your query."
    )
    assert runner._humanize_failure(detail) == (
        "gh issue comment failed (exit 1): GraphQL: Something went "
        "wrong while executing your query."
    )


def test_humanize_failure_collapses_plain_detail_to_one_line():
    assert runner._humanize_failure("plain\n  error") == "plain error"


def test_humanize_failure_command_without_stderr():
    detail = (
        "Command '['git', 'fetch', 'origin']' returned non-zero "
        "exit status 128."
    )
    assert runner._humanize_failure(detail) == (
        "git fetch origin failed (exit 128)"
    )


def test_humanize_failure_empty_stderr_after_marker():
    detail = (
        "Command '['git', 'fetch', 'origin']' returned non-zero "
        "exit status 128. stderr="
    )
    assert runner._humanize_failure(detail) == (
        "git fetch origin failed (exit 128)"
    )


def test_humanize_failure_empty_is_a_fallback_sentence():
    assert runner._humanize_failure("  ") == "An error occurred."


def test_humanize_failure_command_without_exit_status_stays_one_line():
    assert runner._humanize_failure(
        "Command '['gh', 'issue']' exploded\n  extra"
    ) == "Command '['gh', 'issue']' exploded extra"


def test_finish_progress_body_uses_three_sections_and_folds_raw_error(caplog):
    """Blocked comments must be readable by a stranger (Issue #952)."""
    caplog.set_level("WARNING")
    detail = (
        "Command '['gh', 'issue', 'comment', '30', '--repo', "
        "'owner/repo', '--body', 'huge payload']' returned non-zero "
        "exit status 1. stderr=GraphQL: Something went wrong."
    )
    pr_url = "https://github.com/owner/repo/pull/31"
    body = runner._finish_progress_body(
        number=30, title="task", run_id="844cfb38", role="review",
        branch="orbi/x", worktree=None, pr_url=pr_url, review_round=0,
        priority="normal", detail=detail, next_step="",
        outcome="blocked", source_repo="owner/repo",
    )
    visible, _, raw = body.partition("<details>")
    assert body.startswith("**Orbi blocked**")
    assert "What happened: gh issue comment failed (exit 1): GraphQL:" in visible
    assert "What you need to do: Nothing" in visible
    assert "What Orbi will do next:" in visible
    assert pr_url in visible.split("What Orbi will do next:", 1)[1].split("\n", 1)[0]
    assert "huge payload" not in visible
    assert "Command '['gh'" not in visible
    assert "<summary>Raw error</summary>" in raw
    assert "huge payload" in raw
    assert "next step:" not in body
    assert "<!-- orbi:run=844cfb38 -->" in body
    assert "empty_next_step" in caplog.text
    assert "844cfb38" in caplog.text


def test_finish_progress_body_fix_needed_user_does_nothing():
    body = runner._finish_progress_body(
        number=39, title="Fix task", run_id="a1b2c3d4", role="review",
        branch="orbi/x", worktree=None,
        pr_url="https://github.com/owner/repo/pull/46",
        review_round=1, priority="normal", detail="the failure",
        next_step=(
            "the next tick resumes the same run, branch, worktree "
            "and PR automatically (the Issue stays ai-fix-needed)"
        ),
        outcome="fix needed", source_repo="owner/repo",
    )
    visible, _, raw = body.partition("<details>")
    assert "What happened: the failure" in visible
    assert "What you need to do: Nothing" in visible
    assert (
        "What Orbi will do next: the next tick resumes the same run, "
        "branch, worktree and PR automatically (the Issue stays "
        "ai-fix-needed) Open PR: https://github.com/owner/repo/pull/46"
    ) in visible
    assert "failure: the failure" in raw
    assert "next step: the next tick resumes the same run" in raw
    assert "<!-- orbi:run=a1b2c3d4 -->" in body


def test_finish_progress_body_blocked_without_pr_keeps_user_action():
    body = runner._finish_progress_body(
        number=39, title="Blocked task", run_id="a1b2c3d4",
        role="review", branch=None, worktree=None, pr_url=None,
        review_round=0, priority="normal", detail="the failure",
        next_step="fix the failure above and re-run this Issue",
        outcome="blocked", source_repo="owner/repo",
    )
    visible, _, _ = body.partition("<details>")
    assert "What you need to do: fix the failure above and re-run this Issue" in visible
    assert "The Issue stays ai-blocked until a human decides." in visible
    assert "Open PR:" not in visible


def test_finish_progress_body_empty_next_step_on_fix_needed(caplog):
    caplog.set_level("WARNING")
    body = runner._finish_progress_body(
        number=39, title="Fix task", run_id="a1b2c3d4", role="review",
        branch=None, worktree=None, pr_url=None, review_round=0,
        priority="normal", detail="the failure", next_step="",
        outcome="fix needed", source_repo="owner/repo",
    )
    visible, _, _ = body.partition("<details>")
    assert "What you need to do: Nothing" in visible
    assert "The next tick resumes the same run automatically." in visible
    assert "empty_next_step" in caplog.text
