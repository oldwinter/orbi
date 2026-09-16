"""Idle-path auto-create of a release ticket (Issue #856).

When a Milestone is finished (``open_issues == 0``) and nobody opened an
``ai-release`` ticket, the engine idles with no ship and no greppable
event. ``arm_release_ticket`` only labels an existing ticket.

This module wraps that idle call: with host-only
``auto_create_release_ticket`` (default false) it creates one idempotent
ticket from ``.github/release-ticket-template.md`` and leaves arming to
the original function.

``journal.py`` / ``github.py`` / ``cli.py`` / ``repo_config.py`` are too
large to land in this fork's upload path. ``install_import_hooks()``
(called from ``cli_source``, which runner imports before snapshotting
``log_format``) registers the events, rebinds ``HOST_ONLY_KEYS``, and
installs the ``log_format`` wrap so ``load_config`` / ``arm_release_ticket``
get the host flag.
"""
from __future__ import annotations

import inspect
import logging
import re
import tomllib
from pathlib import Path

from orbi.delivery_labels import RELEASE_LABEL
from orbi.journal import event, log_format as _orig_log_format, run_command


_EVENTS = {
    "release_ticket_created": (
        "an idle tick created a release Issue for a finished Milestone"
    ),
    "release_ticket_create_skipped": (
        "idle auto-create skipped (ticket exists, or version_file unknown)"
    ),
    "release_ticket_create_failed": (
        "idle auto-create of a release Issue failed (bypass)"
    ),
}

FINGERPRINT_PREFIX = "orbi-auto-release milestone="
TEMPLATE_RELATIVE = Path(".github") / "release-ticket-template.md"
ISSUE_URL_NUMBER = re.compile(r"/issues/(\d+)$")
# Same order as ``orbi.release.RELEASE_VERSION_FILE_OPTIONS`` minus
# ``none`` — inferred from the delivery checkout, never guessed.
_VERSION_FILE_CANDIDATES = (
    "pyproject.toml", "package.json", "pom.xml", "build.gradle",
    "build.gradle.kts", "gradle.properties", "Cargo.toml",
    "composer.json", "pubspec.yaml",
)


def read_auto_create_release_ticket(
    config_path: object, *, fail_fast: bool = False,
) -> bool:
    """Return the host ``auto_create_release_ticket`` flag (default false)."""
    if config_path is None:
        return False
    try:
        data = tomllib.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        if fail_fast:
            raise
        return False
    value = data.get("auto_create_release_ticket", False)
    if not isinstance(value, bool):
        if fail_fast:
            raise ValueError("auto_create_release_ticket must be a boolean")
        return False
    return value


def fingerprint(milestone: str) -> str:
    """Idempotency token written into the auto-created ticket body."""
    return f"{FINGERPRINT_PREFIX}{milestone}"


def infer_version_file(repo_dir: object) -> str | None:
    """Return the first supported version file in ``repo_dir``, or None."""
    root = Path(repo_dir)
    for name in _VERSION_FILE_CANDIDATES:
        if (root / name).is_file():
            return name
    return None


def render_release_ticket_body(
    template: str, *, version: str, base_branch: str,
    version_file: str | None,
) -> str:
    """Fill the engine template; never emit ``test_command``."""
    body = template.replace("vX.Y.Z", version)
    if base_branch != "main":
        body = body.replace("- base_branch: main", f"- base_branch: {base_branch}", 1)
    if version_file and version_file != "pyproject.toml":
        live = f"- scope_from_milestone: {version}\n"
        body = body.replace(
            live, live + f"- version_file: {version_file}\n", 1,
        )
    token = fingerprint(version)
    if "## 背景\n\n" in body:
        body = body.replace("## 背景\n\n", f"## 背景\n\n{token}\n\n", 1)
    elif token not in body:
        body = token + "\n\n" + body
    return body


def _idle_config() -> object | None:
    """Return the RunnerConfig from an idle-tick caller, if present."""
    frame = inspect.currentframe()
    try:
        while frame is not None:
            cfg = frame.f_locals.get("config")
            if cfg is not None and hasattr(cfg, "auto_create_release_ticket"):
                return cfg
            frame = frame.f_back
        return None
    finally:
        del frame


def _issue_number_from_url(url: str) -> int:
    match = ISSUE_URL_NUMBER.search(url.strip())
    if not match:
        raise RuntimeError(f"release ticket create returned no issue URL: {url!r}")
    return int(match.group(1))


def maybe_create_release_ticket(
    repo: str, milestone: str, *,
    repo_dir: Path, deploy_home: Path, base_branch: str,
) -> int | None:
    """Create one release ticket when the idle conditions hold.

    Returns the new issue number, or ``None`` when skipped. Callers on
    the idle path catch failures; this function raises on GitHub errors.
    """
    from orbi.github import list_issues, list_milestones

    milestones = list_milestones(repo, timeout=30)
    matches = [
        item for item in milestones
        if isinstance(item, dict) and item.get("title") == milestone
    ]
    if not matches or matches[0].get("state") != "open":
        return None
    if matches[0].get("open_issues") != 0:
        return None
    version_file = infer_version_file(repo_dir)
    if version_file is None:
        event(
            "release_ticket_create_skipped", level=logging.WARNING,
            reason="version_file_unknown", milestone=milestone, repo=repo,
        )
        return None
    existing = list_issues(
        repo, state="all",
        search=f'label:{RELEASE_LABEL} milestone:"{milestone}"',
        json_fields="number", limit=1, timeout=30,
    )
    if not existing:
        existing = list_issues(
            repo, state="all",
            search=f'in:body "{fingerprint(milestone)}"',
            json_fields="number", limit=1, timeout=30,
        )
    if existing:
        number = existing[0].get("number")
        event(
            "release_ticket_create_skipped",
            reason="already_exists", milestone=milestone, repo=repo,
            issue=f"#{number}" if isinstance(number, int) else "-",
        )
        return None
    template_path = Path(deploy_home) / TEMPLATE_RELATIVE
    body = render_release_ticket_body(
        template_path.read_text(encoding="utf-8"),
        version=milestone, base_branch=base_branch,
        version_file=version_file,
    )
    if "test_command" in body:
        raise RuntimeError("auto-created release ticket must not contain test_command")
    url = run_command([
        "gh", "issue", "create", "--repo", repo,
        "--title", f"release {milestone}",
        "--body", body,
        "--label", RELEASE_LABEL,
        "--milestone", milestone,
    ], timeout=30)
    number = _issue_number_from_url(url)
    event(
        "release_ticket_created",
        milestone=milestone, issue=f"#{number}", repo=repo,
    )
    return number


def maybe_create_from_idle(repo: str, milestone: str) -> int | None:
    """No-op unless the idle tick's host flag is true."""
    cfg = _idle_config()
    if cfg is None or not getattr(cfg, "auto_create_release_ticket", False):
        return None
    if not repo or not milestone:
        return None
    return maybe_create_release_ticket(
        repo, milestone,
        repo_dir=getattr(cfg, "repo_dir", Path(".")),
        deploy_home=getattr(cfg, "deploy_home", Path(".")),
        base_branch=getattr(cfg, "base_branch", "main"),
    )


def install() -> None:
    """Wrap ``load_config`` and ``arm_release_ticket``. Idempotent."""
    import sys

    runner = sys.modules.get("orbi.runner")
    if runner is None:
        return
    orig_load = getattr(runner, "load_config", None)
    if orig_load is not None and not getattr(orig_load, "_auto_create_release_wrap", False):
        def wrapped_load(path, **kwargs):
            cfg = orig_load(path, **kwargs)
            flag = read_auto_create_release_ticket(path, fail_fast=True)
            object.__setattr__(cfg, "auto_create_release_ticket", flag)
            return cfg

        wrapped_load._auto_create_release_wrap = True  # type: ignore[attr-defined]
        runner.load_config = wrapped_load

    orig_arm = getattr(runner, "arm_release_ticket", None)
    if orig_arm is None or getattr(orig_arm, "_auto_create_release_wrap", False):
        return

    def wrapped_arm(*args, **kwargs):
        repo = args[0] if args else kwargs.get("repo")
        milestone = args[1] if len(args) > 1 else kwargs.get("active_milestone")
        try:
            maybe_create_from_idle(repo, milestone)
        except Exception as exc:
            event(
                "release_ticket_create_failed", level=logging.ERROR,
                repo=repo, milestone=milestone, error=exc,
            )
        return orig_arm(*args, **kwargs)

    wrapped_arm._auto_create_release_wrap = True  # type: ignore[attr-defined]
    runner.arm_release_ticket = wrapped_arm


def _log_format() -> str:
    install()
    return _orig_log_format()


def install_import_hooks() -> None:
    """Bind wraps before ``runner`` snapshots ``log_format``.

    Idempotent. Registers the auto-create events, the host-only key,
    and the ``log_format`` install hook.
    """
    import orbi.journal as journal
    import orbi.repo_config as repo_config

    for kind, meaning in _EVENTS.items():
        journal.JOURNAL_EVENTS.setdefault(kind, meaning)
    if "auto_create_release_ticket" not in repo_config.HOST_ONLY_KEYS:
        repo_config.HOST_ONLY_KEYS = frozenset(
            {*repo_config.HOST_ONLY_KEYS, "auto_create_release_ticket"}
        )
    if getattr(journal.log_format, "_auto_create_release_wrap", False):
        return
    _log_format._auto_create_release_wrap = True  # type: ignore[attr-defined]
    journal.log_format = _log_format


install_import_hooks()
