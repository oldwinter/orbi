"""Issue #933: `milestone set` refuses while auto_next_milestone is true.

The command's success line promises the next tick will claim from the
title just written. Default `auto_next_milestone` is true, so the
idle path silently rewrites the same line. The command is the
confirmation flow behind `auto_next_milestone = false`: while the
flag is still true (explicit or the load_config default), it exits
non-zero, prints one structured `milestone_set_failed` line, leaves
the config untouched, and does not call GitHub.
"""
from tests.fakes.github import FakeGh
from tests.test_cli_milestone import REPO, make_world, run_set, wire


def test_milestone_set_refuses_default_auto_next_without_touching_config(
    tmp_path, monkeypatch, capsys,
):
    config_path = make_world(tmp_path, auto_next_milestone=None)
    original = config_path.read_text(encoding="utf-8")
    assert "auto_next_milestone" not in original
    gh = FakeGh(REPO)
    gh.add_milestone(1, title="v0.5.0", state="closed")
    gh.add_milestone(2, title="v0.6.0", open_issues=3)
    calls = wire(monkeypatch, gh)

    exit_code = run_set(config_path, "v0.6.0")

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    err = captured.err
    assert err.startswith("milestone_set_failed")
    assert "reason=auto_next_milestone" in err
    assert "fix=" in err and "auto_next_milestone = false" in err
    assert "Traceback" not in err
    assert config_path.read_text(encoding="utf-8") == original
    assert calls == []


def test_milestone_set_refuses_explicit_true_without_touching_config(
    tmp_path, monkeypatch, capsys,
):
    config_path = make_world(tmp_path, auto_next_milestone=True)
    original = config_path.read_text(encoding="utf-8")
    assert "auto_next_milestone = true" in original
    gh = FakeGh(REPO)
    gh.add_milestone(1, title="v0.6.0", open_issues=3)
    calls = wire(monkeypatch, gh)

    exit_code = run_set(config_path, "v0.6.0")

    assert exit_code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    err = captured.err
    assert err.startswith("milestone_set_failed")
    assert "reason=auto_next_milestone" in err
    assert "fix=" in err
    assert "Traceback" not in err
    assert config_path.read_text(encoding="utf-8") == original
    assert calls == []


def test_milestone_set_still_writes_when_auto_next_is_false(
    tmp_path, monkeypatch, capsys,
):
    config_path = make_world(tmp_path, auto_next_milestone=False)
    gh = FakeGh(REPO)
    gh.add_milestone(1, title="v0.6.0", open_issues=3)
    wire(monkeypatch, gh)

    assert run_set(config_path, "v0.6.0") == 0
    assert 'active_milestone = "v0.6.0"' in config_path.read_text(
        encoding="utf-8",
    )
    out = capsys.readouterr().out
    assert "active_milestone: v0.5.0 -> v0.6.0" in out
