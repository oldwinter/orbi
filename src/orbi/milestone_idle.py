"""Idle-path helpers for a dangling ``active_milestone`` (Issue #855).

After a release closes the configured Milestone, the three fresh-claim
scans stay scoped to that title and return nothing. The idle tick already
tries to advance, but a failure was only ``LOGGER.exception`` — not a
structured journal event — so the stall was greppable as silence.

``journal.py`` / ``github.py`` / ``cli.py`` are too large to land in this
fork's upload path. ``install_import_hooks()`` (called from ``cli_source``,
which runner imports before snapshotting ``log_format``) registers the
event, wraps ``LOGGER.exception`` and ``github.list_milestones``, and
rebinds ``log_format`` so ``load_config`` gets the host flag.
"""
from __future__ import annotations

import inspect
import logging
import sys
import tomllib
from pathlib import Path

import orbi.journal as journal
from orbi.journal import event, log_format as _orig_log_format

_DANGLING_EVENT = "active_milestone_dangling"
_DANGLING_MEANING = (
    "the configured active Milestone is closed or missing while idle"
)


def read_warn_on_dangling_milestone(
    config_path: object, *, fail_fast: bool = False,
) -> bool:
    """Return the host ``warn_on_dangling_milestone`` flag (default false).

    ``fail_fast`` is the load_config contract: a present non-bool is a
    misconfiguration. The idle observer never fails the tick — a
    missing/unreadable file or a bad type is treated as false.
    """
    if config_path is None:
        return False
    try:
        data = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        if fail_fast:
            raise
        return False
    value = data.get("warn_on_dangling_milestone", False)
    if not isinstance(value, bool):
        if fail_fast:
            raise ValueError("warn_on_dangling_milestone must be a boolean")
        return False
    return value


def dangling_state(milestones: list, active: object) -> str | None:
    """Return ``closed`` / ``missing`` when ``active`` is not an open title."""
    matches = [
        milestone for milestone in milestones
        if isinstance(milestone, dict) and milestone.get("title") == active
    ]
    if not matches:
        return "missing"
    if matches[0].get("state") == "open":
        return None
    return "closed"


def _advance_idle_frame() -> tuple[object, object]:
    """Return ``(active_milestone, config_path)`` from the idle advance frame."""
    frame = inspect.currentframe()
    try:
        while frame is not None:
            if frame.f_code.co_name == "advance_active_milestone_on_idle":
                locals_ = frame.f_locals
                return (
                    locals_.get("active_milestone"),
                    locals_.get("config_path"),
                )
            frame = frame.f_back
        return None, None
    finally:
        del frame


def observe_idle_milestones(repo: str, milestones: list) -> None:
    """Emit ``active_milestone_dangling`` when the idle advance is dangling.

    No-op unless called (indirectly) from
    ``advance_active_milestone_on_idle`` AND the host flag is true.
    ``repo`` is accepted for call-site symmetry with ``list_milestones``.
    """
    del repo
    active, config_path = _advance_idle_frame()
    if active is None:
        return
    if not read_warn_on_dangling_milestone(config_path, fail_fast=False):
        return
    state = dangling_state(milestones, active)
    if state is None:
        return
    event(
        _DANGLING_EVENT,
        level=logging.WARNING,
        active=active,
        state=state,
    )


_ORIG_LOGGER_EXCEPTION = journal.LOGGER.exception


def _exception_with_advance_failed_event(msg, *args, **kwargs):
    """Emit ``active_milestone_advance_failed`` as a structured event.

    The idle tick swallows advance errors (Issue #614) so a missing
    Milestone cannot fail the process; Issue #855 requires that failure
    to be greppable in the journal, not only as a traceback.
    """
    if isinstance(msg, str) and "active_milestone_advance_failed" in msg:
        error = sys.exc_info()[1]
        repo = args[0] if args else "-"
        milestone = args[1] if len(args) > 1 else "-"
        event(
            "active_milestone_advance_failed",
            level=logging.ERROR,
            repo=repo,
            milestone=milestone,
            error=error if error is not None else msg,
        )
    return _ORIG_LOGGER_EXCEPTION(msg, *args, **kwargs)


def _wrap_list_milestones() -> None:
    github = sys.modules.get("orbi.github")
    if github is None or not hasattr(github, "list_milestones"):
        return
    orig = github.list_milestones
    if getattr(orig, "_dangling_observe_wrap", False):
        return

    def list_milestones(repo: str, *, timeout: int | None = None):
        milestones = orig(repo, timeout=timeout)
        observe_idle_milestones(repo, milestones)
        return milestones

    list_milestones._dangling_observe_wrap = True  # type: ignore[attr-defined]
    github.list_milestones = list_milestones


def install() -> None:
    """Wrap ``runner.load_config`` and ``list_milestones``.

    Idempotent. Safe to call before ``orbi.runner`` has finished loading
    (no-op until the names exist). Also wraps ``github.list_milestones``
    when that module is already imported.
    """
    _wrap_list_milestones()
    runner = sys.modules.get("orbi.runner")
    if runner is None:
        return
    if hasattr(runner, "list_milestones"):
        orig_list = runner.list_milestones
        if not getattr(orig_list, "_dangling_observe_wrap", False):
            def list_milestones(repo: str, *, timeout: int | None = None):
                milestones = orig_list(repo, timeout=timeout)
                observe_idle_milestones(repo, milestones)
                return milestones

            list_milestones._dangling_observe_wrap = True  # type: ignore[attr-defined]
            runner.list_milestones = list_milestones
    if not hasattr(runner, "load_config"):
        return
    orig = runner.load_config
    if getattr(orig, "_warn_on_dangling_wrap", False):
        return

    def wrapped(path, **kwargs):
        cfg = orig(path, **kwargs)
        warn = read_warn_on_dangling_milestone(path, fail_fast=True)
        object.__setattr__(cfg, "warn_on_dangling_milestone", warn)
        return cfg

    wrapped._warn_on_dangling_wrap = True  # type: ignore[attr-defined]
    runner.load_config = wrapped


def _log_format() -> str:
    """Install the load_config wrap on the first tick format setup."""
    install()
    return _orig_log_format()


def install_import_hooks() -> None:
    """Bind wraps before ``runner`` snapshots ``log_format``.

    Idempotent. Registers the dangling event, the exception wrap, the
    ``list_milestones`` observe, and the ``log_format`` install hook.
    """
    journal.JOURNAL_EVENTS.setdefault(_DANGLING_EVENT, _DANGLING_MEANING)
    import orbi.repo_config as repo_config
    if "warn_on_dangling_milestone" not in repo_config.HOST_ONLY_KEYS:
        repo_config.HOST_ONLY_KEYS = frozenset(
            {*repo_config.HOST_ONLY_KEYS, "warn_on_dangling_milestone"}
        )
    journal._exception_with_advance_failed_event = (
        _exception_with_advance_failed_event
    )
    if not getattr(journal.LOGGER.exception, "_dangling_exception_wrap", False):
        _exception_with_advance_failed_event._dangling_exception_wrap = True
        journal.LOGGER.exception = _exception_with_advance_failed_event
    _wrap_list_milestones()
    if getattr(journal.log_format, "_warn_on_dangling_wrap", False):
        return
    _log_format._warn_on_dangling_wrap = True  # type: ignore[attr-defined]
    journal.log_format = _log_format


install_import_hooks()
