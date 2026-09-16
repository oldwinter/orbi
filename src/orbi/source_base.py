"""Fuse ``base_branch`` for every consumer (Issue #931).

``runner.py`` and ``release.py`` are too large to land in this fork's
upload path. The product change still has to live in the engine:
release used to re-read the raw ``[[repositories]]`` entry (skipping
``.github/orbi.toml``), and the dispatch loop applied the entry fallback
only when a policy file existed.

``install()`` rebinds the release lookups and the dispatch consumers so
they all read one fused ``config.base_branch``. ``runner.py`` / ``release.py``
are not edited.

``delivery_step`` / ``verify_resumed_pr`` must not fetch
``.github/orbi.toml``: ``runner.main`` already loaded the policy once
for the whole delivery. Those wrappers only apply the local
``[[repositories]]`` entry. An already-fused config (policy overlay
from ``apply_repo_policy``) is returned unchanged so the overlay is not
overwritten by the raw entry.
"""
from __future__ import annotations

from dataclasses import is_dataclass, replace

import orbi.release as release
import orbi.repo_config as repo_config
import orbi.runner as runner

_installed = False
_orig_resolve_release_declaration = None
_orig_process_release = None
_orig_process_issue = None
_orig_verify_resumed_pr = None
_orig_delivery_step = None
_orig_apply_repo_policy = None

_FUSED_ATTR = "_source_base_fused"


def resolve_source_base_branch(config: runner.RunnerConfig, source_repo: str,
                               policy: repo_config.RepoPolicy | None,
                               ) -> runner.RunnerConfig:
    """Fuse one source repo's base branch unconditionally: the
    ``[[repositories]]`` entry fallback first, then the repository
    policy's override when a policy file exists. Every consumer
    (dev path AND the release state machine) reads ``config.base_branch``
    and must never re-derive it from the raw entry — the raw entry
    skips the policy layer.
    """
    fused = replace(
        config,
        base_branch=runner.repository_base_branch(config, source_repo),
    )
    if policy is None:
        return fused
    return repo_config.resolve_policy(fused, policy)


def _without_raw_base_entries(config):
    """Hide ``[[repositories]]`` so the leftover inline ``next()`` in
    release falls through to the already-fused ``config.base_branch``.
    """
    if is_dataclass(config) and not isinstance(config, type):
        return replace(config, repositories=())
    repos = getattr(config, "repositories", None)
    if repos is not None:
        try:
            config.repositories = ()
        except Exception:
            pass
    return config


def _already_fused(config) -> bool:
    return bool(getattr(config, _FUSED_ATTR, False))


def _mark_fused(config):
    """Stamp a fused config so later skip-fetch wrappers keep a
    policy overlay instead of rewriting ``base_branch`` from the
    raw ``[[repositories]]`` entry.
    """
    try:
        object.__setattr__(config, _FUSED_ATTR, True)
    except (AttributeError, TypeError):
        pass
    return config


def _fuse(config, source_repo: str, policy=None, *, fetch_policy=True):
    # Hand-built stubs (a bare dict, a Mock) are not RunnerConfig.
    # The original consumers already accepted them — CLOSED-unmerged
    # delivery_step never reads host config. Fusion must be a no-op
    # here: crashing on ``config.repositories`` took down the terminal
    # ``PR closed without a merge → ai-blocked`` path.
    if not is_dataclass(config) or isinstance(config, type):
        return config
    # Production ``main()`` already applied the policy overlay.
    # Re-running entry fallback with ``policy=None`` would drop
    # orbi.toml ``release`` back onto the raw ``[[repositories]]``
    # entry (``develop``). Skip GitHub and skip the rewrite.
    if policy is None and _already_fused(config):
        return config
    if policy is None and fetch_policy:
        try:
            policy = runner.load_repo_policy(config, source_repo)
        except Exception:
            policy = None
    try:
        fused = resolve_source_base_branch(config, source_repo, policy)
    except (TypeError, AttributeError):
        return config
    if policy is not None:
        return _mark_fused(fused)
    return fused


def apply_repo_policy(config, source_repo, policy):
    """Same as ``runner.apply_repo_policy``, then stamp the overlay
    so skip-fetch wrappers do not rewrite ``base_branch``.
    """
    return _mark_fused(_orig_apply_repo_policy(config, source_repo, policy))


def resolve_release_declaration(
        issue, overrides, config, source_repo, repo_dir, release_commit):
    return _orig_resolve_release_declaration(
        issue, overrides, _without_raw_base_entries(config),
        source_repo, repo_dir, release_commit,
    )


def process_release(issue, config, source_repo):
    fused = _fuse(config, source_repo)
    return _orig_process_release(
        issue, _without_raw_base_entries(fused), source_repo,
    )


def process_issue(issue, config, source_repo, repo_policy=None):
    return _orig_process_issue(
        issue, _fuse(config, source_repo, repo_policy), source_repo,
        repo_policy,
    )


def verify_resumed_pr(scene, issue, config, source_repo):
    return _orig_verify_resumed_pr(
        scene, issue, _fuse(config, source_repo, fetch_policy=False),
        source_repo,
    )


def delivery_step(pr_url, issue, config, source_repo, **kwargs):
    return _orig_delivery_step(
        pr_url, issue,
        _fuse(config, source_repo, fetch_policy=False),
        source_repo, **kwargs,
    )


def install() -> None:
    """Rebind runner/release so both paths share one fused base_branch.

    Idempotent: a second call is a no-op. Tests install from conftest;
    the tick installs from ``acquire_slot`` (see ``cli_source``).
    """
    global _installed
    global _orig_resolve_release_declaration
    global _orig_process_release
    global _orig_process_issue
    global _orig_verify_resumed_pr
    global _orig_delivery_step
    global _orig_apply_repo_policy
    if _installed:
        return
    if not hasattr(runner, "process_issue"):
        return

    _orig_resolve_release_declaration = release.resolve_release_declaration
    _orig_process_release = release.process_release
    _orig_process_issue = runner.process_issue
    _orig_verify_resumed_pr = runner.verify_resumed_pr
    _orig_delivery_step = runner.delivery_step
    _orig_apply_repo_policy = runner.apply_repo_policy

    runner.resolve_source_base_branch = resolve_source_base_branch
    release.resolve_release_declaration = resolve_release_declaration
    release.process_release = process_release
    runner.process_issue = process_issue
    runner.verify_resumed_pr = verify_resumed_pr
    runner.delivery_step = delivery_step
    runner.apply_repo_policy = apply_repo_policy
    _installed = True
