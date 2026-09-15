"""The test suite's own contract (Issue #908).

Two guards keep the suite honest: no test module may bind the same
function name twice at module scope (the later def silently replaces
the earlier one and pytest collects the surviving name once — the
shadowed body never runs), and the ONE shared fail-fast git scaffold
in ``conftest.py`` must fail loudly on a non-zero exit (previously
asserted by 13 per-file copies of the same test).
"""
import ast
from pathlib import Path

import pytest

from conftest import git

TESTS_DIR = Path(__file__).resolve().parent


def _duplicate_module_level_defs(tree: ast.Module) -> list[tuple[str, int]]:
    """Module-scope def names bound more than once in one module: the
    later def silently replaces the earlier one, and pytest collects
    the surviving name once — the shadowed body never runs."""
    seen: set[str] = set()
    duplicates: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in seen:
                duplicates.append((node.name, node.lineno))
            seen.add(node.name)
    return duplicates


def test_no_test_module_defines_a_function_name_twice():
    """Walk the AST of every test module and fail on any duplicate
    module-scope function name (Issue #908: the dict-era
    ``test_resolve_policy_context_files_are_additive`` copy had never
    executed)."""
    offenders = [
        f"{path.name}:{lineno} {name}"
        for path in sorted(TESTS_DIR.rglob("*.py"))
        for name, lineno in _duplicate_module_level_defs(
            ast.parse(path.read_text(encoding="utf-8"), str(path))
        )
    ]
    assert offenders == []


def test_the_walker_reports_a_shadowed_duplicate():
    """The guard's failure branch: a module that binds the same name
    twice is reported with the shadowing def's line."""
    tree = ast.parse(
        "def dup():\n    return 1\n\n\ndef dup():\n    return 2\n",
        "<synthetic>",
    )
    assert _duplicate_module_level_defs(tree) == [("dup", 5)]


def test_git_helper_fails_fast_on_nonzero_exit(tmp_path):
    """The one shared git scaffold (tests/conftest.py) must fail loudly
    on a git error, never pass a broken setup silently."""
    with pytest.raises(AssertionError, match=r"git .* failed rc=128"):
        git(tmp_path, "rev-parse", "no-such-ref")
