"""Registered LOGGER.exception kinds go through journal.event().

runner.py / release.py still call LOGGER.exception with registered
kinds (health_check_failed, delivery_review_failed, release_failed, …).
Those files cannot be edited on this fork's upload path; the leaf wrap
in exception_events.py is the emission point. The hygiene net exempts
exception calls, so without the wrap the stall never hit event().
"""
from __future__ import annotations

import logging

import pytest

import orbi.exception_events as exception_events
import orbi.journal as journal
from orbi.journal import JOURNAL_EVENTS


@pytest.fixture(autouse=True)
def _reset_run_binding(monkeypatch):
    monkeypatch.setattr(journal, "_CURRENT_RUN_ID", None)


def _event_records(caplog, kind: str) -> list:
    return [
        record for record in caplog.records
        if record.message.startswith(f"{kind} ") or record.message == kind
    ]


def test_kind_first_emits_event_and_rewrites_traceback(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        try:
            raise RuntimeError("journalctl missing")
        except RuntimeError:
            journal.LOGGER.exception("health_check_failed")
    events = _event_records(caplog, "health_check_failed")
    assert len(events) == 1, caplog.text
    assert events[0].levelno == logging.ERROR
    assert "error=" in events[0].message
    assert "journalctl missing" in events[0].message
    traceback_records = [
        record for record in caplog.records
        if record.exc_info and record.exc_info[1] is not None
    ]
    assert traceback_records
    assert not traceback_records[0].message.startswith("health_check_failed")
    assert "health_check_failed" in traceback_records[0].message


def test_key_first_emits_event_and_keeps_original(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        try:
            raise RuntimeError("review boom")
        except RuntimeError:
            journal.LOGGER.exception(
                "issue=%s delivery_review_failed pr=%s", 18, "https://x/pull/3",
            )
    events = _event_records(caplog, "delivery_review_failed")
    assert len(events) == 1, caplog.text
    assert "issue=18" in events[0].message
    assert "pr=https://x/pull/3" in events[0].message
    assert "error=" in events[0].message
    assert "review boom" in events[0].message
    orig = [
        record for record in caplog.records
        if record.message.startswith("issue=18 delivery_review_failed")
    ]
    assert orig, caplog.text


def test_release_failed_key_first_emits_event(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        try:
            raise RuntimeError("tag push failed")
        except RuntimeError:
            journal.LOGGER.exception("issue=%s release_failed", 99)
    events = _event_records(caplog, "release_failed")
    assert len(events) == 1, caplog.text
    assert "issue=99" in events[0].message
    assert "error=" in events[0].message


def test_stop_scene_prose_emits_activity_snapshot_failed(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        try:
            raise RuntimeError("unreadable session")
        except RuntimeError:
            journal.LOGGER.exception("stop scene activity snapshot failed")
    events = _event_records(caplog, "activity_snapshot_failed")
    assert len(events) == 1, caplog.text
    assert events[0].levelno == logging.ERROR
    assert 'error="unreadable session"' in events[0].message
    assert not _event_records(caplog, "activity")
    orig = [
        record for record in caplog.records
        if record.message.startswith("stop scene activity snapshot failed")
    ]
    assert orig, caplog.text
    assert orig[0].exc_info and orig[0].exc_info[1] is not None


def test_stop_scene_prose_parser_maps_to_registered_kind():
    parsed = exception_events.registered_kind_and_fields(
        "stop scene activity snapshot failed", (),
    )
    assert parsed is not None
    kind, fields = parsed
    assert kind == "activity_snapshot_failed"
    assert "error" in fields


def test_runner_stop_scene_still_uses_the_mapped_prose():
    from pathlib import Path
    runner = (
        Path(__file__).resolve().parent.parent / "src" / "orbi" / "runner.py"
    )
    source = runner.read_text(encoding="utf-8")
    assert 'LOGGER.exception("stop scene activity snapshot failed")' in source
    assert (
        exception_events._PROSE_TO_KIND[
            "stop scene activity snapshot failed"
        ]
        == "activity_snapshot_failed"
    )


def test_activity_scene_prose_emits_activity_snapshot_failed(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        try:
            raise RuntimeError("unreadable session")
        except RuntimeError:
            journal.LOGGER.exception(
                "issue=%s activity scene failed", 4,
            )
    events = _event_records(caplog, "activity_snapshot_failed")
    assert len(events) == 1, caplog.text
    assert events[0].levelno == logging.ERROR
    assert "issue=4" in events[0].message
    assert 'error="unreadable session"' in events[0].message
    assert not _event_records(caplog, "activity")
    orig = [
        record for record in caplog.records
        if record.message.startswith("issue=4 activity scene failed")
    ]
    assert orig, caplog.text
    assert orig[0].exc_info and orig[0].exc_info[1] is not None


def test_activity_scene_prose_parser_maps_issue():
    parsed = exception_events.registered_kind_and_fields(
        "issue=%s activity scene failed", (4,),
    )
    assert parsed is not None
    kind, fields = parsed
    assert kind == "activity_snapshot_failed"
    assert fields["issue"] == 4
    assert "error" in fields


def test_runner_activity_scene_still_uses_the_mapped_prose():
    from pathlib import Path
    runner = (
        Path(__file__).resolve().parent.parent / "src" / "orbi" / "runner.py"
    )
    source = runner.read_text(encoding="utf-8")
    assert source.count(
        'LOGGER.exception("issue=%s activity scene failed", number)'
    ) == 2
    assert (
        exception_events._PROSE_TO_KIND[
            "issue=%s activity scene failed"
        ]
        == "activity_snapshot_failed"
    )


def test_prose_without_kind_token_does_not_emit_event(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            journal.LOGGER.exception("issue=%s failed", 7)
            journal.LOGGER.exception(
                "issue=%s failure history read failed", 18,
            )
    assert not _event_records(caplog, "failed")
    assert not _event_records(caplog, "failure_history_read_failed")
    assert "issue=7 failed" in caplog.text
    assert "failure history read failed" in caplog.text


def test_advance_failed_emits_once_with_fields(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            journal.LOGGER.exception(
                "active_milestone_advance_failed repo=%s milestone=%s",
                "o/r", "v0.6.0",
            )
    events = _event_records(caplog, "active_milestone_advance_failed")
    assert len(events) == 1, caplog.text
    assert "repo=o/r" in events[0].message
    assert "milestone=v0.6.0" in events[0].message
    assert "error=boom" in events[0].message


def test_non_string_message_passes_through(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        journal.LOGGER.exception(42)
    assert not any(
        record.message.startswith(kind)
        for record in caplog.records
        for kind in JOURNAL_EVENTS
    )


def test_install_is_idempotent_and_survives_dangling_reinstall():
    first = journal.LOGGER.exception
    exception_events.install()
    exception_events.install()
    assert journal.LOGGER.exception is first
    import orbi.milestone_idle as milestone_idle
    milestone_idle.install_import_hooks()
    assert journal.LOGGER.exception is first
    assert getattr(journal.LOGGER.exception, "_exception_events_wrap", False)
    assert getattr(journal.LOGGER.exception, "_dangling_exception_wrap", False)


def test_package_import_installs_the_exception_wrap():
    import orbi.cli_source  # noqa: F401
    assert getattr(journal.LOGGER.exception, "_exception_events_wrap", False)
    from orbi.exception_events import install, install_import_hooks
    install_import_hooks()
    install()
    assert getattr(journal.LOGGER.exception, "_exception_events_wrap", False)


def test_registered_kind_parser_ignores_unregistered_tokens():
    assert exception_events.registered_kind_and_fields(
        "issue=%s failed", (7,),
    ) is None
    parsed = exception_events.registered_kind_and_fields(
        "health_check_failed", (),
    )
    assert parsed is not None
    kind, fields = parsed
    assert kind == "health_check_failed"
    assert "error" in fields


def test_first_token_empty_and_punctuation():
    assert exception_events._first_token("") == ""
    assert exception_events._first_token("   ") == ""
    assert exception_events._first_token("health_check_failed:") == (
        "health_check_failed"
    )


def test_parser_skips_non_identifier_keys_and_literal_values():
    parsed = exception_events.registered_kind_and_fields(
        "health_check_failed 1repo=%s =%s issue=18 extra=%s",
        (),
    )
    assert parsed is not None
    kind, fields = parsed
    assert kind == "health_check_failed"
    assert "1repo" not in fields
    assert "issue" not in fields
    assert "extra" not in fields


def test_parser_keeps_the_first_registered_kind():
    parsed = exception_events.registered_kind_and_fields(
        "health_check_failed delivery_review_failed repo=%s",
        ("o/r",),
    )
    assert parsed is not None
    kind, fields = parsed
    assert kind == "health_check_failed"
    assert fields["repo"] == "o/r"


def test_kind_first_without_active_exception_uses_msg_as_error(caplog):
    with caplog.at_level("ERROR", logger="orbi.bootstrap"):
        journal.LOGGER.exception("worktree_reclaim_failed")
    events = _event_records(caplog, "worktree_reclaim_failed")
    assert len(events) == 1, caplog.text
    assert "error=worktree_reclaim_failed" in events[0].message
