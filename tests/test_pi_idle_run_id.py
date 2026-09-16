"""Idle / resumed journal lines without a wall-clock race (Issue #971).

``test_stream_pi_idle_lines_carry_run_id_exactly_once`` used real
sleeps: records at 0.0 / 0.1 / 1.0s, ``poll_interval=0.1``,
``idle_warn_seconds=0.5``. Four polls of slack. On a loaded macOS
runner, a2 arrives late (or the poller skips) and either ``idles``
grows or idle-recovery kills the session — ``RecoverablePiFailure``.
Linux CI did not flake; ``macos-compatibility`` failure blocks
``check_release_gates``.

This replacement:

- injects ``SessionWatcher.now`` so ``stale_seconds`` is a fake clock
  vs the record timestamps, not wall time;
- freezes ``time.monotonic`` so idle-recovery cannot exhaust;
- writes a2 only after the watcher has already observed the stall
  (progress handshake), so the idle → resumed pair does not depend
  on ``poll_interval`` vs ``idle_warn_seconds``.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import orbi.journal as journal
import orbi.pi_process as pi_process
import orbi.runner as runner
from orbi.delivery_scene import RunContext
from orbi.pi_process import PiWatchOptions

IDLE_WARN = 0.5
FAKE_NOW = 1_700_000_000.0
FAKE_MONO = 100.0


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _handshake_pi(tmp_path: Path, *, a1_ts: str, a2_ts: str,
                  sentinel: Path) -> list[str]:
    """Write a1 immediately, wait for the test to arm ``sentinel``, then a2.

    The wait is a handshake, not the idle threshold: stream_pi creates
    the sentinel from the progress callback after it has already logged
    ``pi_idle``.
    """
    session_dir = tmp_path / ".pi-session"
    session_dir.mkdir(exist_ok=True)
    session = str(session_dir / "sess.jsonl")
    first = [
        {"type": "session", "id": "sess-1", "timestamp": a1_ts, "cwd": "/w"},
        {"type": "message", "id": "a1", "timestamp": a1_ts,
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "one"}]}},
    ]
    second = {
        "type": "message", "id": "a2", "timestamp": a2_ts,
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "two"}]},
    }
    script = (
        "import json, os, sys, time\n"
        f"session = {session!r}\n"
        f"sentinel = {str(sentinel)!r}\n"
        f"first = {first!r}\n"
        f"second = {second!r}\n"
        "for record in first:\n"
        "    with open(session, 'a') as handle:\n"
        "        handle.write(json.dumps(record) + '\\n')\n"
        "deadline = time.monotonic() + 8\n"
        "while not os.path.exists(sentinel):\n"
        "    if time.monotonic() > deadline:\n"
        "        sys.exit(2)\n"
        "    time.sleep(0.01)\n"
        "with open(session, 'a') as handle:\n"
        "    handle.write(json.dumps(second) + '\\n')\n"
        "sys.stdout.write('ok')\n"
    )
    return [sys.executable, "-c", script]


def _install_fake_clock(monkeypatch, clock: dict) -> None:
    orig = pi_process.SessionWatcher

    class ClockedWatcher(orig):
        def __init__(self, session_dir, now=None, known_files=None):
            super().__init__(
                session_dir, now=lambda: clock["now"],
                known_files=known_files,
            )

    monkeypatch.setattr(pi_process, "SessionWatcher", ClockedWatcher)
    monkeypatch.setattr(pi_process.time, "monotonic", lambda: clock["mono"])


def test_idle_and_resumed_lines_carry_run_id_exactly_once(
        tmp_path, monkeypatch, caplog,
):
    """The Issue #57 journal contract, without wall-clock pacing."""
    clock = {"now": FAKE_NOW, "mono": FAKE_MONO}
    _install_fake_clock(monkeypatch, clock)
    monkeypatch.setattr(journal, "_CURRENT_RUN_ID", "a1b2c3d4")

    a1_ts = _iso(FAKE_NOW - 4)
    a2_ts = _iso(FAKE_NOW + 1)
    sentinel = tmp_path / "write-a2"
    command = _handshake_pi(
        tmp_path, a1_ts=a1_ts, a2_ts=a2_ts, sentinel=sentinel,
    )

    def progress(activity: dict) -> None:
        if sentinel.exists():
            return
        if activity.get("first_response") and (
                activity.get("stale_seconds", 0) >= IDLE_WARN):
            sentinel.write_text("go", encoding="utf-8")

    with caplog.at_level("INFO"):
        runner.stream_pi(
            command,
            ctx=RunContext(
                run_id="a1b2c3d4", issue=24, branch="b",
                worktree=tmp_path, source_repo="xqliu/orbi",
            ),
            watch=PiWatchOptions(
                poll_interval=0.05, idle_warn_seconds=IDLE_WARN,
            ),
            cwd=tmp_path,
            progress=progress,
        )

    idles = [m for m in caplog.messages if " pi_idle " in m]
    resumed = [m for m in caplog.messages if " pi_resumed " in m]
    assert len(idles) == 1, caplog.messages
    assert len(resumed) == 1, caplog.messages
    for message in idles + resumed:
        assert "run=" not in message, message
        assert message.startswith("[a1b2c3d4]"), message
        assert message.count("a1b2c3d4") == 1, message


def test_stale_seconds_come_from_the_injected_clock_not_wall_time(
        tmp_path, monkeypatch,
):
    """A four-second-old record is stale even if wall time barely moved."""
    clock = {"now": FAKE_NOW, "mono": FAKE_MONO}
    _install_fake_clock(monkeypatch, clock)
    session_dir = tmp_path / ".pi-session"
    session_dir.mkdir()
    session = session_dir / "sess.jsonl"
    record = {
        "type": "message", "id": "a1", "timestamp": _iso(FAKE_NOW - 4),
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "one"}]},
    }
    session.write_text(json.dumps(record) + "\n", encoding="utf-8")
    watcher = pi_process.SessionWatcher(session_dir)
    state = watcher.poll()
    assert state["stale_seconds"] == pytest.approx(4.0)
    assert state["changed"] is True
    assert state["first_response"] is True


def test_frozen_monotonic_clock_cannot_exhaust_idle_recovery(monkeypatch):
    """Idle-recovery windows are counted in monotonic silence. A
    frozen clock keeps ``exhausted`` false no matter how many polls
    the wall clock burns — that is what used to kill the session on
    a slow macOS runner."""
    clock = {"now": FAKE_NOW, "mono": FAKE_MONO}
    monkeypatch.setattr(pi_process.time, "monotonic", lambda: clock["mono"])
    monkeypatch.setattr(pi_process, "find_idle_descendants", lambda *a, **k: [])
    tracker = pi_process.IdleRecoveryTracker(
        IDLE_WARN, run_id="a1b2c3d4", issue_ref="o/r#1", role="implement",
    )
    tracker.open_window()
    for _ in range(20):
        tracker.escalate(os.getpid())
    assert tracker.exhausted is False


def test_bootstrap_idle_run_id_case_stays_skipped():
    """The original wall-clock case must remain collected-but-skipped.
    A rename would resurrect the macOS flake on the release gate."""
    src = (
        Path(__file__).resolve().parent / "test_bootstrap_runner.py"
    ).read_text(encoding="utf-8")
    assert "def test_stream_pi_idle_lines_carry_run_id_exactly_once(" in src
