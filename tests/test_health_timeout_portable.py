"""Health probes must not shell out to GNU ``timeout`` (Issue #928).

macOS has no ``timeout`` binary (Homebrew coreutils installs
``gtimeout``). Five ``run_command`` sites in ``runner_health`` used to
put ``timeout`` as argv[0]; every alert path died with FileNotFoundError
before reaching gh/git, swallowed as one ``health_check_failed`` log
line. The bound now travels through ``run_command(..., timeout=)`` —
subprocess-native, portable.
"""
from __future__ import annotations

import time

import pytest

from orbi import runner_health
from tests.test_runner_health import (
    ORBI_REPO,
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


class RecordingFake(FakeRunCommand):
    """Same routes as FakeRunCommand, plus kwargs on every call."""

    def __init__(self, routes=None):
        super().__init__(routes)
        self.call_kwargs: list[dict] = []

    def __call__(self, command, **kwargs):
        self.call_kwargs.append(dict(kwargs))
        return super().__call__(command, **kwargs)

    def kwargs_for(self, needle: str) -> list[tuple[list[str], dict]]:
        return [
            (call, kwargs)
            for call, kwargs in zip(self.calls, self.call_kwargs)
            if needle in " ".join(call)
        ]


def _assert_no_gnu_timeout(calls: list[list[str]]) -> None:
    assert calls, "the probe must reach gh or git"
    for call in calls:
        assert call[0] != "timeout", (
            "GNU timeout is hardcoded as argv[0]; macOS has none"
        )


def test_health_github_calls_pass_timeout_via_run_command(tmp_path):
    write_state(tmp_path, {
        "runs": [
            run_entry(REPO, 41, "00000001", "fp1"),
            run_entry(REPO, 41, "00000002", "fp1"),
            run_entry(REPO, 41, "00000003", "fp1"),
        ],
        "last_pickup_ts": time.time(), "alerted": [],
    })
    fake = RecordingFake({
        "journalctl --user -u orbi@1.service": "",
        "journalctl --user -u orbi@2.service": "",
    })
    alerts = runner_health.run_health_check(
        make_config(tmp_path), run_command=fake,
    )
    assert alerts == [f"repeated_failure:{REPO}#41"]
    gh_calls = fake.commands("gh issue")
    _assert_no_gnu_timeout(gh_calls)
    timed = fake.kwargs_for("gh issue")
    assert timed
    for _, kwargs in timed:
        assert kwargs.get("timeout") == runner_health.GH_TIMEOUT_SECONDS


def test_create_health_issue_passes_timeout_via_run_command():
    fake = RecordingFake({"in:body": "[]"})
    url = runner_health.create_health_issue(
        ORBI_REPO, "crash_loop", "detail", run_command=fake,
    )
    assert url is None
    assert fake.commands("gh issue list")
    assert fake.commands("gh issue create")
    for call, kwargs in fake.kwargs_for("gh issue"):
        assert call[0] != "timeout"
        assert kwargs.get("timeout") == runner_health.GH_TIMEOUT_SECONDS


def test_orbi_repo_from_deploy_home_passes_timeout_via_run_command(tmp_path):
    fake = RecordingFake({})
    runner_health.orbi_repo_from_deploy_home(tmp_path, run_command=fake)
    git_calls = fake.commands("git")
    _assert_no_gnu_timeout(git_calls)
    for call, kwargs in fake.kwargs_for("git"):
        assert call[0] == "git"
        assert kwargs.get("timeout") == 30


def test_stale_pickup_queue_probe_passes_timeout_via_run_command(tmp_path):
    write_state(tmp_path, {
        "runs": [],
        "last_pickup_ts": time.time() - 48 * 3600,
        "alerted": [],
    })
    fake = RecordingFake({
        "journalctl --user -u orbi@1.service": "",
        "journalctl --user -u orbi@2.service": "",
        "--label ai-ready": "[]",
    })
    alerts = runner_health.run_health_check(
        make_config(tmp_path), run_command=fake,
    )
    assert alerts == []
    list_calls = fake.kwargs_for("gh issue list")
    assert list_calls
    for call, kwargs in list_calls:
        assert call[0] != "timeout"
        assert kwargs.get("timeout") == runner_health.GH_TIMEOUT_SECONDS
