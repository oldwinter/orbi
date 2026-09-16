"""activity_snapshot_failed is a journal event, not a LOGGER.exception prose line.

``_progress_state`` used to ``LOGGER.exception("issue=%s activity snapshot
failed")``. That is key-first prose, so the #21 exception wrap never
routes it through ``journal.event()``. The snapshot is best-effort
(Issue #18): a read failure must still emit a registered kind, then
continue with "no session yet".
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import orbi.journal as journal
import orbi.progress as progress
from orbi.delivery_scene import RunContext

PROGRESS = Path(__file__).resolve().parent.parent / "src" / "orbi" / "progress.py"
KIND = "activity_snapshot_failed"


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


def _event_kind_literals(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not _is_event_call(node) or not node.args:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            found.append(arg.value)
    return found


def _logger_exception_first_tokens(source: str) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "exception"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "LOGGER"
            and node.args
        ):
            continue
        fmt = node.args[0]
        if isinstance(fmt, ast.Constant) and isinstance(fmt.value, str):
            found.append(fmt.value.split(" ")[0])
    return found


def test_kind_is_registered():
    assert KIND in journal.JOURNAL_EVENTS
    meaning = journal.JOURNAL_EVENTS[KIND]
    assert "snapshot" in meaning.lower()
    assert "best-effort" in meaning.lower() or "best effort" in meaning.lower()


def test_progress_source_emits_through_event_not_exception():
    source = PROGRESS.read_text(encoding="utf-8")
    assert KIND in _event_kind_literals(source)
    assert KIND not in _logger_exception_first_tokens(source)
    assert "LOGGER.exception" not in source


def test_unregistered_kind_would_fail_fast():
    registry = dict(journal.JOURNAL_EVENTS)
    del registry[KIND]
    source = PROGRESS.read_text(encoding="utf-8")
    offenders = [
        kind for kind in _event_kind_literals(source) if kind not in registry
    ]
    assert KIND in offenders


def test_progress_state_journals_structured_event(caplog, tmp_path, monkeypatch):
    caplog.set_level(logging.ERROR, logger="orbi.bootstrap")

    def boom(_path):
        raise RuntimeError("unreadable session")

    monkeypatch.setattr(progress, "activity_snapshot", boom)
    state = progress._progress_state(
        RunContext(
            run_id="a1b2c3d4", issue=4, branch="b",
            worktree=tmp_path, source_repo="owner/repo",
        ),
        title="t", role="review", started=0.0, pr_url=None,
        review_round=0, priority="normal",
    )
    assert state["phase"] == "starting"
    assert state["session"] is None
    records = [
        record for record in caplog.records
        if record.message.startswith("activity_snapshot_failed ")
    ]
    assert records, caplog.text
    record = records[0]
    assert record.levelno == logging.ERROR
    assert "issue=4" in record.message
    assert "run_id=a1b2c3d4" in record.message
    assert 'error="unreadable session"' in record.message
    assert "Traceback" not in caplog.text
