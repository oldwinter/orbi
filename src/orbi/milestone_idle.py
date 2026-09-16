"""Idle-path helpers for a dangling ``active_milestone`` (Issue #855).

After a release closes the configured Milestone, the three fresh-claim
scans stay scoped to that title and return nothing. The idle tick already
tries to advance, but a failure was only ``LOGGER.exception`` — not a
structured journal event — so the stall was greppable as silence.

This module is imported from ``orbi.github`` (so the wrap is bound
before ``runner`` snapshots ``log_format``) and does not import
``orbi.runner`` at module level (github is a leaf).
"""
from __future__ import annotations

import inspect
import logging
import tomllib
from pathlib import Path

from orbi.journal import event, log_format as _orig_log_format


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
        "active_milestone_dangling",
        level=logging.WARNING,
        active=active,
        state=state,
    )


def install() -> None:
    """Wrap ``runner.load_config`` so the host flag is typed on the config.

    Idempotent. Safe to call before ``orbi.runner`` has finished loading
    (no-op until ``load_config`` exists).
    """
    import sys

    runner = sys.modules.get("orbi.runner")
    if runner is None or not hasattr(runner, "load_config"):
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
    """Bind the log_format wrap before ``runner`` snapshots the name."""
    import orbi.journal as journal

    if getattr(journal.log_format, "_warn_on_dangling_wrap", False):
        return
    _log_format._warn_on_dangling_wrap = True  # type: ignore[attr-defined]
    journal.log_format = _log_format


install_import_hooks()
