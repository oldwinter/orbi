"""progress_publish_failed is a journal event, not a LOGGER.exception kind-line.

The kind is already in ``JOURNAL_EVENTS`` (exporter metric
``orbi_progress_publish_failed_total``). Both call sites — the
``_safe_publish`` bypass and the in-stream live-PATCH callback — used
``LOGGER.exception("progress_publish_failed ...")``. The hygiene net
exempts exception calls, so the stall never hit ``journal.event()``
(Issue #791, same contract as ``empty_next_step``).
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path

import orbi.journal as journal
import orbi.progress as progress

SRC = Path(__file__).resolve().parent.parent / "src" / "orbi"
KIND = "progress_publish_failed"
CALL_SITES = ("progress.py", "pi_process.py")


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
    assert "bypass" in journal.JOURNAL_EVENTS[KIND]


def test_call_sites_emit_through_event_not_exception():
    for name in CALL_SITES:
        source = (SRC / name).read_text(encoding="utf-8")
        assert KIND in _event_kind_literals(source), name
        assert KIND not in _logger_exception_first_tokens(source), name


def test_safe_publish_journals_structured_event(caplog):
    caplog.set_level(logging.ERROR, logger="orbi.bootstrap")

    def boom():
        raise RuntimeError("gh: Not Found (HTTP 404)")

    progress._safe_publish(
        run_id="a1b2c3d4", issue=18, source_repo="xqliu/orbi",
        role="implement", action=boom,
    )
    records = [
        record for record in caplog.records
        if record.message.startswith("progress_publish_failed ")
    ]
    assert records, caplog.text
    record = records[0]
    assert record.levelno == logging.ERROR
    assert "run=a1b2c3d4" in record.message
    assert "issue=xqliu/orbi#18" in record.message
    assert "role=implement" in record.message
    assert "Traceback" not in caplog.text


def test_safe_publish_does_not_raise():
    progress._safe_publish(
        run_id="a1b2c3d4", issue=18, source_repo="xqliu/orbi",
        role="review", action=lambda: (_ for _ in ()).throw(OSError("down")),
    )
