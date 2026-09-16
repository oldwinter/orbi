"""Route registered ``LOGGER.exception`` kinds through ``journal.event``.

``runner.py`` and ``release.py`` are too large to land in this fork's
upload path. Those modules still log registered journal kinds with
``LOGGER.exception`` (``health_check_failed``, ``delivery_review_failed``,
``release_failed``, …). The hygiene net exempts exception calls, so the
stall never hit ``journal.event()`` (Issue #791).

``install_import_hooks()`` (called from ``cli_source``, which runner
imports before snapshotting ``log_format``) wraps ``journal.LOGGER.exception``
so a registered kind token in the format string also emits through the
single emission point. The original traceback is kept: kind-first
formats are rewritten so the traceback record is not kind-first
(the exporter would otherwise double-count if the kind joins
``KNOWN_KINDS``); key-first formats stay as they are.
"""
from __future__ import annotations

import logging
import sys

import orbi.journal as journal
from orbi.journal import JOURNAL_EVENTS, event

# runner.py is too large to land here. The stop-scene snapshot
# read is best-effort prose, not a kind token, so the #21 wrap
# would otherwise treat the English word ``activity`` as the live
# high-frequency kind. Map the exact format string onto the
# registered kind that progress.py already emits.
JOURNAL_EVENTS.setdefault(
    "activity_snapshot_failed",
    "reading the live Pi activity snapshot failed (best-effort; the task continues)",
)
_PROSE_TO_KIND = {
    "stop scene activity snapshot failed": "activity_snapshot_failed",
}


_ORIG_LOGGER_EXCEPTION = logging.Logger.exception.__get__(journal.LOGGER)


def _first_token(msg: str) -> str:
    parts = msg.split()
    if not parts:
        return ""
    return parts[0].rstrip(".,;:")


def registered_kind_and_fields(msg: object, args: tuple) -> tuple[str, dict] | None:
    """Return ``(kind, fields)`` when ``msg`` carries a registered kind.

    Exact prose aliases in ``_PROSE_TO_KIND`` (the stop-scene snapshot
    read in ``runner.py``) map onto a registered kind first, so the
    English word ``activity`` is not treated as the live snapshot kind.
    Remaining prose (``issue=%s failed``, ``failure history read
    failed``) has no registered kind as a whole token and is left
    alone.
    """
    if not isinstance(msg, str):
        return None
    prose_kind = _PROSE_TO_KIND.get(msg)
    if prose_kind is not None:
        error = sys.exc_info()[1]
        return prose_kind, {
            "error": error if error is not None else msg,
        }
    kind: str | None = None
    keys: list[str] = []
    for token in msg.split():
        bare = token.rstrip(".,;:")
        if bare in journal.JOURNAL_EVENTS:
            if kind is None:
                kind = bare
            continue
        if "=" not in bare:
            continue
        key, _, rest = bare.partition("=")
        if not key.isidentifier():
            continue
        if any(spec in rest for spec in ("%s", "%d", "%r", "%i")):
            keys.append(key)
    if kind is None:
        return None
    fields: dict = {}
    for index, key in enumerate(keys):
        if index < len(args):
            fields[key] = args[index]
    error = sys.exc_info()[1]
    fields["error"] = error if error is not None else msg
    return kind, fields


def _exception_with_registered_kind_event(msg, *args, **kwargs):
    """Emit a structured event when the format names a registered kind.

    Replaces the dangling-only wrap on ``journal.LOGGER.exception``
    (that wrap only handled ``active_milestone_advance_failed``).
    Calls the true ``Logger.exception`` so the traceback still lands
    and so that wrap cannot double-emit the same kind.
    """
    parsed = registered_kind_and_fields(msg, args)
    orig_msg = msg
    if parsed is not None:
        kind, fields = parsed
        event(kind, level=logging.ERROR, **fields)
        if _first_token(msg) == kind:
            orig_msg = f"exception {msg}"
    return _ORIG_LOGGER_EXCEPTION(orig_msg, *args, **kwargs)


def install_import_hooks() -> None:
    """Bind the exception wrap before ``runner`` snapshots ``log_format``.

    Idempotent. Marks the wrap with ``_dangling_exception_wrap`` so a
    later ``milestone_idle.install_import_hooks()`` does not overwrite it.
    """
    if getattr(journal.LOGGER.exception, "_exception_events_wrap", False):
        return
    _exception_with_registered_kind_event._exception_events_wrap = True
    _exception_with_registered_kind_event._dangling_exception_wrap = True
    journal._exception_with_registered_kind_event = (
        _exception_with_registered_kind_event
    )
    journal.LOGGER.exception = _exception_with_registered_kind_event


def install() -> None:
    """Install the exception wrap. Safe before or after runner loads."""
    install_import_hooks()


install_import_hooks()
