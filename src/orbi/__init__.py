"""Orbi runtime package.

Standard src layout: the uv editable install maps this WHOLE package
directory onto the deployment checkout, so a newly added module in this
package is importable by the next CLI process without regenerating any
module list (the #158 stale-finder root cause).

The runtime entry points:

- `orbi.cli` — the `orbi` console script target
  (`orbi = "orbi.cli:main"` in pyproject.toml);
- `orbi.runner` — the Runner tick (`python3 -m
  orbi.runner --config ...`), the systemd ExecStart target
  through the installed console script.

The checkout root carries NO `orbi.py`: a flat file named like
the package would shadow the installed package for every process with
the checkout root on sys.path. The direct-execution compatibility entry
is `python3 -m orbi.cli` (development path only).
"""
__version__ = "0.5.7"


def _install_exception_events() -> None:
    """Issue #791: wrap registered LOGGER.exception kinds through event()."""
    from orbi.exception_events import install
    install()


_install_exception_events()
