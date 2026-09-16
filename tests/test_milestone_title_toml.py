"""Milestone titles containing a quote or backslash must write valid TOML
(Issue #930).

The old rewrite interpolated the raw title into a TOML basic string
and passed that text to re.subn:

- a quote in the title wrote invalid TOML on disk while the command
  reported success, so the next tick died in load_config;
- a backslash travelled into the replacement string as an escape and
  raised re.error, outside the command's failure contract (a raw
  traceback instead of one structured milestone_set_failed line).

json.dumps emits a TOML-compatible basic string; a lambda keeps the
replacement out of the regex escape layer. The same serializer is
rebound onto orbi.runner so idle auto-advance cannot write a poisoned
config either.
"""
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

import orbi.runner as runner
from orbi import milestone_toml
from orbi.milestone_toml import rewrite_active_milestone_line
from tests.fakes.github import FakeGh
from tests.test_cli_milestone import REPO, make_world, run_set, wire

from seam import seam

QUOTE_TITLE = 'say "hi"'
BACKSLASH_TITLE = "back" + chr(92) + "slash"
MIXED_TITLE = 'say "hi" ' + chr(92) + " there"


def _write_config(tmp_path: Path, line: str) -> Path:
    config = tmp_path / "orbi.toml"
    config.write_bytes(
        f'# keep\r\nsource_repos = ["owner/repo"]\r\n{line}\r\nother = "x"\r\n'
        .encode()
    )
    return config


def test_quote_title_writes_tomllib_parseable_basic_string(tmp_path):
    config = _write_config(tmp_path, 'active_milestone = "v0.3.0"')
    rewrite_active_milestone_line(config, QUOTE_TITLE)
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["active_milestone"] == QUOTE_TITLE
    assert parsed["other"] == "x"


def test_backslash_title_writes_tomllib_parseable_basic_string(tmp_path):
    config = _write_config(tmp_path, 'active_milestone = "v0.3.0"')
    rewrite_active_milestone_line(config, BACKSLASH_TITLE)
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["active_milestone"] == BACKSLASH_TITLE


def test_quote_and_backslash_title_round_trips(tmp_path):
    config = _write_config(tmp_path, "active_milestone = 'v0.3.0'")
    rewrite_active_milestone_line(config, MIXED_TITLE)
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["active_milestone"] == MIXED_TITLE


def test_plain_title_still_matches_existing_byte_contract(tmp_path):
    """A title without special characters stays the historical quoting."""
    config = _write_config(tmp_path, "active_milestone = 'v0.3.0'")
    rewrite_active_milestone_line(config, "v0.3.1")
    assert config.read_bytes() == (
        '# keep\r\nsource_repos = ["owner/repo"]\r\n'
        'active_milestone = "v0.3.1"\r\nother = "x"\r\n'
    ).encode()


def test_rewrite_fails_when_active_milestone_line_is_missing(tmp_path):
    config = tmp_path / "orbi.toml"
    config.write_text('source_repos = ["owner/repo"]\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="active_milestone line not found"):
        rewrite_active_milestone_line(config, QUOTE_TITLE)


def test_install_rebinds_runner_so_idle_lookup_escapes(tmp_path, monkeypatch):
    """Idle auto-advance calls ``runner.rewrite_active_milestone_line``
    by module global. After install that name is the serializer, so a
    quoted GitHub title cannot poison the next tick."""
    def original(path, value):
        raise AssertionError("original interpolating rewrite must not run")

    monkeypatch.setattr(runner, "rewrite_active_milestone_line", original)
    milestone_toml.install()
    assert (
        runner.rewrite_active_milestone_line
        is milestone_toml.rewrite_active_milestone_line
    )
    milestone_toml.install()
    assert (
        runner.rewrite_active_milestone_line
        is milestone_toml.rewrite_active_milestone_line
    )

    config = _write_config(tmp_path, 'active_milestone = "v0.3.0"')
    runner.rewrite_active_milestone_line(config, QUOTE_TITLE)
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["active_milestone"] == QUOTE_TITLE


def test_milestone_set_title_with_double_quote_writes_valid_toml(
    tmp_path, monkeypatch,
):
    config_path = make_world(tmp_path)
    gh = FakeGh(REPO)
    gh.add_milestone(1, title=QUOTE_TITLE, open_issues=1)
    wire(monkeypatch, gh)

    assert run_set(config_path, QUOTE_TITLE) == 0

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["active_milestone"] == QUOTE_TITLE


def test_milestone_set_title_with_backslash_writes_valid_toml(
    tmp_path, monkeypatch, capsys,
):
    config_path = make_world(tmp_path)
    gh = FakeGh(REPO)
    gh.add_milestone(1, title=BACKSLASH_TITLE, open_issues=1)
    wire(monkeypatch, gh)

    assert run_set(config_path, BACKSLASH_TITLE) == 0
    err = capsys.readouterr().err
    assert "Traceback" not in err

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["active_milestone"] == BACKSLASH_TITLE


def test_milestone_set_timeout_failure_raises_the_structured_error(
    tmp_path, monkeypatch, capsys,
):
    """Every lookup failure raises MilestoneSetError: a hung gh times
    out (TimeoutExpired), a missing gh raises OSError, a bad payload
    raises ValueError — all must collapse into one structured line."""
    config_path = make_world(tmp_path)

    def hung(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(seam, "run_command", hung)

    exit_code = run_set(config_path, "v0.6.0")

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "milestone_set_failed" in err
    assert "milestone lookup failed" in err
    assert "Traceback" not in err


def test_milestone_set_missing_gh_raises_the_structured_error(
    tmp_path, monkeypatch, capsys,
):
    config_path = make_world(tmp_path)

    def missing(command, **kwargs):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(seam, "run_command", missing)

    exit_code = run_set(config_path, "v0.6.0")

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "milestone_set_failed" in err
    assert "milestone lookup failed" in err
    assert "Traceback" not in err


def test_milestone_set_malformed_payload_raises_the_structured_error(
    tmp_path, monkeypatch, capsys,
):
    config_path = make_world(tmp_path)

    def bad_json(command, **kwargs):
        raise ValueError("paginated issue list must be an array of arrays")

    monkeypatch.setattr(seam, "run_command", bad_json)

    exit_code = run_set(config_path, "v0.6.0")

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "milestone_set_failed" in err
    assert "milestone lookup failed" in err
    assert "Traceback" not in err
