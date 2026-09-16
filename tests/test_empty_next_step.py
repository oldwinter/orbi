"""Terminal progress comments with an empty next-step emit a journal event.

``_rewrite_terminal_outcome`` used to ``LOGGER.warning("empty_next_step ...")``.
That kind was missing from ``JOURNAL_EVENTS`` and the operations event
tables, so the suite hygiene net flagged ``progress.py`` and grepping
the journal for a registered kind missed the stall. ``event()`` is the
single emission point (Issue #791).
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import orbi.journal as journal
import orbi.runner as runner

PROGRESS = Path(__file__).resolve().parent.parent / "src" / "orbi" / "progress.py"
KIND = "empty_next_step"


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
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not _is_event_call(node) or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.append((node.lineno, arg.value))
    return found


def _direct_logger_kind_lines(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"info", "warning", "error", "log", "debug"}
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "LOGGER"
        ):
            continue
        index = 1 if node.func.attr == "log" else 0
        if len(node.args) <= index:
            continue
        fmt = node.args[index]
        if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
            found.append((node.lineno, fmt.value.split(" ")[0]))
    return found


def test_empty_next_step_is_registered():
    assert KIND in journal.JOURNAL_EVENTS
    meaning = journal.JOURNAL_EVENTS[KIND]
    assert "empty" in meaning.lower()
    assert "next-step" in meaning.lower() or "next step" in meaning.lower()


def test_event_emits_empty_next_step_at_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="orbi.bootstrap"):
        journal.event(
            KIND, level=logging.WARNING,
            issue=30, run_id="844cfb38", outcome="blocked",
        )
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert record.message == (
        "empty_next_step issue=30 run_id=844cfb38 outcome=blocked"
    )


def test_progress_source_emits_through_event_not_logger():
    source = PROGRESS.read_text(encoding="utf-8")
    kinds = {kind for _, kind in _event_kind_literals(source)}
    assert KIND in kinds
    logger_first = {first for _, first in _direct_logger_kind_lines(source)}
    assert KIND not in logger_first


def test_unregistered_empty_next_step_would_fail_fast():
    registry = dict(journal.JOURNAL_EVENTS)
    del registry[KIND]
    source = PROGRESS.read_text(encoding="utf-8")
    offenders = [
        (lineno, kind)
        for lineno, kind in _event_kind_literals(source)
        if kind not in registry
    ]
    assert offenders, "dropping the kind from the registry must go red"
    assert all(kind == KIND for _, kind in offenders)


def test_finish_progress_body_journals_structured_empty_next_step(caplog):
    caplog.set_level(logging.WARNING, logger="orbi.bootstrap")
    runner._finish_progress_body(
        number=30, title="task", run_id="844cfb38", role="review",
        branch="orbi/x", worktree=None, pr_url=None, review_round=0,
        priority="normal", detail="the failure", next_step="",
        outcome="blocked", source_repo="owner/repo",
    )
    records = [
        record for record in caplog.records
        if record.message.startswith("empty_next_step ")
    ]
    assert records, caplog.text
    record = records[0]
    assert record.levelno == logging.WARNING
    assert "issue=30" in record.message
    assert "run_id=844cfb38" in record.message
    assert "outcome=blocked" in record.message


def test_rewrite_quotes_spaced_outcome(caplog):
    caplog.set_level(logging.WARNING, logger="orbi.bootstrap")
    runner._finish_progress_body(
        number=39, title="Fix task", run_id="a1b2c3d4", role="review",
        branch=None, worktree=None, pr_url=None, review_round=0,
        priority="normal", detail="the failure", next_step="",
        outcome="fix needed", source_repo="owner/repo",
    )
    records = [
        record for record in caplog.records
        if record.message.startswith("empty_next_step ")
    ]
    assert records, caplog.text
    assert 'outcome="fix needed"' in records[0].message
    assert "issue=39" in records[0].message
    assert "run_id=a1b2c3d4" in records[0].message
