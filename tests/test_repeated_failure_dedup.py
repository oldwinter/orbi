"""The repeated_failure alert records its dedup key only after the
comment ships (Issue #929).

The state file is saved unconditionally in ``finally``. Recording the
key before ``gh issue comment`` meant one failed ship (network blip,
5xx, missing GNU timeout) burned the key and the escalation comment
was never retried — the loudest moment silently lost its only signal.
"""
from __future__ import annotations

import json
import time
from subprocess import CalledProcessError

import pytest

from orbi import runner_health
from tests.test_runner_health import (
    REPO,
    FakeRunCommand,
    make_config,
    run_entry,
    write_state,
)

_REAL_RUN_HEALTH_CHECK = runner_health.run_health_check


@pytest.fixture(autouse=True)
def _use_real_health_check(monkeypatch):
    monkeypatch.setattr(
        runner_health, "run_health_check", _REAL_RUN_HEALTH_CHECK,
    )


def _streak_state(tmp_path):
    return write_state(tmp_path, {
        "runs": [
            run_entry(REPO, 41, "00000001", "fp1"),
            run_entry(REPO, 41, "00000002", "fp1"),
            run_entry(REPO, 41, "00000003", "fp1"),
        ],
        "last_pickup_ts": time.time(), "alerted": [],
    })


def test_repeated_failure_alert_retries_when_the_comment_fails(tmp_path):
    _streak_state(tmp_path)
    failing = FakeRunCommand({
        "journalctl --user -u orbi@1.service": "",
        "journalctl --user -u orbi@2.service": "",
        "gh issue comment": CalledProcessError(1, "gh"),
    })
    with pytest.raises(CalledProcessError):
        runner_health.run_health_check(
            make_config(tmp_path), run_command=failing,
        )
    persisted = json.loads(
        runner_health.health_state_path(tmp_path).read_text(
            encoding="utf-8",
        ),
    )
    assert persisted["alerted"] == [], (
        "a failed ship must not burn the dedup key"
    )
    retrying = FakeRunCommand({
        "journalctl --user -u orbi@1.service": "",
        "journalctl --user -u orbi@2.service": "",
    })
    alerts = runner_health.run_health_check(
        make_config(tmp_path), run_command=retrying,
    )
    assert alerts == [f"repeated_failure:{REPO}#41"]
    assert len(retrying.commands("gh issue comment")) == 1


def test_repeated_failure_alert_still_dedups_after_a_successful_ship(
        tmp_path):
    _streak_state(tmp_path)
    healthy = FakeRunCommand({
        "journalctl --user -u orbi@1.service": "",
        "journalctl --user -u orbi@2.service": "",
    })
    assert runner_health.run_health_check(
        make_config(tmp_path), run_command=healthy,
    ) == [f"repeated_failure:{REPO}#41"]
    assert runner_health.run_health_check(
        make_config(tmp_path), run_command=healthy,
    ) == []
    assert len(healthy.commands("gh issue comment")) == 1
