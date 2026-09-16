"""Human-decision reviews must not crash the tick (Issue #975).

``_run_review_round`` journals ``review_human_decision_required`` when
the independent review raises ``HumanDecisionRequired``. That kind
was missing from ``JOURNAL_EVENTS``, so ``event()`` fail-fast-raised
and a designed human decision point took the whole unit down.

The existing AST net only saw ``journal.event('literal')``. Runner
imports ``event`` and passes a ternary, so the leak was invisible.
This file walks ``event()`` *and* the ternary, so the next unregistered
literal fails the suite instead of the tick.
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import orbi.journal as journal
import orbi.runner as runner
from seam import seam

REPO = "owner/repo"
PR_URL = f"https://github.com/{REPO}/pull/46"
RUNNER = Path(__file__).resolve().parent.parent / "src" / "orbi" / "runner.py"


def _kind_literals(node: ast.AST) -> list[str]:
    """String constants that can be the kind argument of ``event()``."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _kind_literals(node.body) + _kind_literals(node.orelse)
    return []


def _is_event_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "event":
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "event"
        and isinstance(func.value, ast.Name)
        and func.value.id == "journal"
    )


def _event_kind_literals(source: str) -> list[tuple[int, str]]:
    """``(lineno, kind)`` for every literal kind passed to ``event()``."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not _is_event_call(node) or not node.args:
            continue
        for kind in _kind_literals(node.args[0]):
            found.append((node.lineno, kind))
    return found


def _unregistered_event_kinds(source: str, registry: dict[str, str]
                                ) -> list[tuple[int, str]]:
    return [
        (lineno, kind)
        for lineno, kind in _event_kind_literals(source)
        if kind not in registry
    ]


def test_kind_literals_reads_constants_and_ternary_branches():
    tree = ast.parse(
        "event('review_human_decision_required' "
        "if flag else 'review_rounds_exhausted_expected_terminal')\n"
        "event(kind_variable)\n"
        "event(12)\n"
        "event('run_end' if flag else kind_variable)\n",
    )
    call = tree.body[0].value
    assert _kind_literals(call.args[0]) == [
        "review_human_decision_required",
        "review_rounds_exhausted_expected_terminal",
    ]
    assert _kind_literals(tree.body[1].value.args[0]) == []
    assert _kind_literals(tree.body[2].value.args[0]) == []
    assert _kind_literals(tree.body[3].value.args[0]) == ["run_end"]


def test_event_call_detector_sees_bare_event_and_journal_event():
    source = (
        "journal.event('run_end')\n"
        "event('review_human_decision_required')\n"
        "other.event('not_ours')\n"
        "event()\n"
        "not_a_call\n"
    )
    assert _event_kind_literals(source) == [
        (1, "run_end"),
        (2, "review_human_decision_required"),
    ]


def test_review_human_decision_required_is_registered_next_to_its_sibling():
    kinds = list(journal.JOURNAL_EVENTS)
    sibling = "review_rounds_exhausted_expected_terminal"
    kind = "review_human_decision_required"
    assert kind in journal.JOURNAL_EVENTS, kind
    assert kinds.index(kind) == kinds.index(sibling) + 1
    meaning = journal.JOURNAL_EVENTS[kind]
    assert "human" in meaning.lower()
    assert "bug" in meaning.lower() or "defect" in meaning.lower()


def test_event_emits_review_human_decision_required(caplog):
    """The kind that crashed orbi-cloud must be emit-able."""
    with caplog.at_level(logging.ERROR, logger="orbi.bootstrap"):
        journal.event(
            "review_human_decision_required",
            level=logging.ERROR, issue=504, pr=PR_URL,
            reason="review requires human decision",
        )
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert record.message.startswith("review_human_decision_required ")
    assert "issue=504" in record.message
    assert f"pr={PR_URL}" in record.message


def test_runner_event_literals_are_all_registered():
    """Every ``event()`` literal in runner.py — including ternary
    branches — is in ``JOURNAL_EVENTS``. A single-key assertion would
    let the next missing kind crash a tick the same way."""
    offenders = _unregistered_event_kinds(
        RUNNER.read_text(encoding="utf-8"), journal.JOURNAL_EVENTS,
    )
    assert not offenders, f"unregistered event() kinds in runner.py: {offenders}"
    kinds = {kind for _, kind in _event_kind_literals(
        RUNNER.read_text(encoding="utf-8"),
    )}
    assert "review_human_decision_required" in kinds
    assert "review_rounds_exhausted_expected_terminal" in kinds


def test_dropping_a_runner_kind_from_the_registry_fails_the_net():
    """Removing any kind runner.py actually emits must go red.
    This is the second gate: a whitelist that never shrinks-on-delete
    cannot catch the next leak."""
    registry = dict(journal.JOURNAL_EVENTS)
    del registry["review_human_decision_required"]
    offenders = _unregistered_event_kinds(
        RUNNER.read_text(encoding="utf-8"), registry,
    )
    assert any(
        kind == "review_human_decision_required" for _, kind in offenders
    ), offenders


def _review_round_env(monkeypatch, tmp_path, *, review_exc):
    """Minimal GitHub/git scene so ``_run_review_round`` reaches review."""

    def fake_run(command, **kwargs):
        if command[-1] == "comments":
            return json.dumps({"comments": [{
                "body": (
                    "<!-- orbi:run=a1b2c3d4 -->\n"
                    f"Orbi opened PR: {PR_URL} "
                    "(base_branch=main base_sha=abc123def456 "
                    "run_id=a1b2c3d4)"
                ),
                "authorAssociation": "OWNER",
            }]})
        if command[-1] == "labels":
            return json.dumps({"labels": [{"name": "ai-pr-opened"}]})
        if command == ["git", "branch", "--show-current"]:
            return "orbi/owner-repo-issue-39"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(seam, "run_command", fake_run)
    (tmp_path / ".worktrees"
     / "orbi-owner-repo-issue-39-a1b2c3d4").mkdir(parents=True)
    monkeypatch.setattr(seam, "edit_issue", lambda *a, **k: None)
    monkeypatch.setattr(seam, "comment_issue", lambda *a, **k: None)
    monkeypatch.setattr(runner, "comment_pr", lambda *a, **k: None)
    monkeypatch.setattr(seam, "_safe_publish", lambda **kwargs: None)

    def raise_review(*args, **kwargs):
        raise review_exc

    monkeypatch.setattr(runner, "review_and_merge_if_clean", raise_review)
    monkeypatch.setattr(journal, "_CURRENT_RUN_ID", "a1b2c3d4")


def test_human_decision_review_journals_without_crashing_the_tick(
        monkeypatch, tmp_path, caplog,
):
    """The production crash: HumanDecisionRequired is an intentional
    terminal, but recording it used an unregistered kind and took the
    unit down with ``unknown journal event kind``."""
    _review_round_env(
        monkeypatch, tmp_path,
        review_exc=runner.HumanDecisionRequired(
            "review requires human decision: "
            "note: same failure repeated; "
            "fix: close the open Dependabot PRs",
        ),
    )
    caplog.set_level(logging.ERROR, logger="orbi.bootstrap")
    outcome = runner._run_review_round(
        PR_URL, {"number": 39, "title": "task", "body": ""},
        runner.RunnerConfig(repo_dir=tmp_path, base_branch="main"),
        REPO,
    )
    assert outcome is None
    messages = [record.message for record in caplog.records]
    assert any(
        "review_human_decision_required " in message
        for message in messages
    ), messages
    assert not any(
        "unknown journal event kind" in message for message in messages
    )
    human = [
        record for record in caplog.records
        if "review_human_decision_required " in record.message
    ]
    assert human and all(record.exc_info is None for record in human)


def test_exhausted_rounds_still_journals_the_registered_sibling(
        monkeypatch, tmp_path, caplog,
):
    """The ternary's other branch was already registered; keep it green
    so the two intentional terminals stay on the same emission path."""
    _review_round_env(
        monkeypatch, tmp_path,
        review_exc=runner.ReviewRoundsExhausted("rounds exhausted"),
    )
    caplog.set_level(logging.ERROR, logger="orbi.bootstrap")
    outcome = runner._run_review_round(
        PR_URL, {"number": 39, "title": "task", "body": ""},
        runner.RunnerConfig(repo_dir=tmp_path, base_branch="main"),
        REPO,
    )
    assert outcome is None
    assert any(
        "review_rounds_exhausted_expected_terminal " in record.message
        for record in caplog.records
    )
