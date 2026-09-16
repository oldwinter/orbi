"""Serialize `active_milestone` as a TOML basic string (Issue #930).

GitHub Milestone titles are arbitrary text. Interpolating them into
``active_milestone = "{title}"`` wrote invalid TOML for ``"`` and raised
``re.error`` for ``\\``, so ``orbi milestone set`` could report success
while the next tick died in ``load_config``.

Idle auto-advance looks up ``rewrite_active_milestone_line`` on the
runner module at runtime. ``install()`` rebinds that name so both the
CLI command and the idle path share one serializer. ``runner.py`` is
not edited: the replacement is the same byte-for-byte line rewrite,
with the value serialized instead of interpolated.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_LINE = re.compile(r"(?m)^[ \t]*active_milestone[ \t]*=[ \t]*[^\r\n]+")


def rewrite_active_milestone_line(config_path: Path, new_value: str) -> None:
    """Replace only the configured active_milestone line, byte-for-byte.

    The value is serialized, never interpolated: ``json.dumps`` emits a
    TOML-compatible basic string; the lambda keeps the replacement text
    out of the regex escape layer entirely.
    """
    text = config_path.read_bytes().decode("utf-8")
    if not _LINE.search(text):
        raise RuntimeError(
            f"active_milestone line not found in {config_path}"
        )
    serialized = json.dumps(new_value, ensure_ascii=False)
    updated, _ = _LINE.subn(
        lambda _match: f"active_milestone = {serialized}", text, count=1,
    )
    config_path.write_bytes(updated.encode("utf-8"))


def install() -> None:
    """Rebind ``orbi.runner.rewrite_active_milestone_line`` to this serializer.

    The CLI entry installs before the tick; tests install from conftest
    so bootstrap assertions hit the same function idle will call.
    Idempotent: a second call is a no-op when the name is already bound.
    """
    import orbi.runner as runner

    if runner.rewrite_active_milestone_line is rewrite_active_milestone_line:
        return
    runner.rewrite_active_milestone_line = rewrite_active_milestone_line
