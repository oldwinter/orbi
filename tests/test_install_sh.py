"""The install.sh platform gate (Issue #849).

The installer is the FIRST user path on a new machine: on macOS it must
require ``launchctl`` (present by default), on Linux ``systemctl``; on a
machine with neither scheduler it must fail with an honest
platform-limitation message carrying the issue link — never the old
opaque ``required command missing: systemctl``. The tests run the real
script with a stubbed PATH (no network, no real clone), so the gate
logic is exercised exactly as a user's shell would.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"

ISSUE_URL = "https://github.com/orbi-build/orbi/issues/849"

# The coreutils the script needs beyond the stubs, symlinked into the
# stub PATH so no real systemctl/launchctl can leak in through /usr/bin.
CORE_TOOLS = (
    "mkdir", "sed", "grep", "mktemp", "timeout", "rm", "cp", "cat", "uname",
)


def make_stub_dir(tmp_path: Path, stubs: dict[str, str]) -> Path:
    """One PATH entry: stub scripts win, core tools are real symlinks."""
    bin_dir = tmp_path / "stubbin"
    bin_dir.mkdir()
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)
    for tool in CORE_TOOLS:
        if tool in stubs:
            continue  # a stub with this name wins (e.g. the uname shim)
        (bin_dir / tool).symlink_to(f"/usr/bin/{tool}")
    return bin_dir


def run_install(tmp_path: Path, bin_dir: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PATH": str(bin_dir),
        "ORBI_HOME": str(tmp_path / "orbi-home"),
    }
    return subprocess.run(
        ["/bin/bash", str(INSTALL_SH)],
        env=env, capture_output=True, text=True, timeout=60,
    )


def pass_stubs() -> dict[str, str]:
    """The commands before the scheduler gate must be present."""
    return {
        "git": "#!/bin/sh\nexit 0\n",
        "gh": "#!/bin/sh\nexit 0\n",
        "curl": "#!/bin/sh\nexit 0\n",
        "uv": "#!/bin/sh\nexit 0\n",
    }


def test_linux_without_systemctl_reports_platform_limitation(tmp_path):
    # The acceptance scene: NO systemctl and NO launchctl on the machine.
    bin_dir = make_stub_dir(tmp_path, pass_stubs())
    result = run_install(tmp_path, bin_dir)
    assert result.returncode == 1
    assert "systemctl" in result.stderr
    assert "launchd" in result.stderr and "macOS" in result.stderr
    assert ISSUE_URL in result.stderr
    # The old opaque message must not come back.
    assert "required command missing: systemctl" not in result.stderr


def test_macos_without_launchctl_reports_platform_limitation(tmp_path):
    stubs = pass_stubs()
    stubs["uname"] = "#!/bin/sh\necho Darwin\n"
    bin_dir = make_stub_dir(tmp_path, stubs)
    result = run_install(tmp_path, bin_dir)
    assert result.returncode == 1
    assert "launchctl" in result.stderr
    assert ISSUE_URL in result.stderr


def test_macos_with_launchctl_passes_the_scheduler_gate(tmp_path):
    # uname says Darwin, launchctl exists: the gate must pass and the
    # install must move on to the NEXT step (the git clone, stubbed to
    # leave a marker and fail fast — never a real network clone).
    marker = tmp_path / "git-reached"
    stubs = pass_stubs()
    stubs["uname"] = "#!/bin/sh\necho Darwin\n"
    stubs["launchctl"] = "#!/bin/sh\nexit 0\n"
    stubs["git"] = (
        "#!/bin/sh\n"
        f"echo reached > {marker}\n"
        "echo 'stub: clone step (out of scope)' >&2\n"
        "exit 1\n"
    )
    bin_dir = make_stub_dir(tmp_path, stubs)
    result = run_install(tmp_path, bin_dir)
    assert marker.read_text().strip() == "reached"
    # The scheduler gate said nothing: no platform-limitation output.
    assert ISSUE_URL not in result.stderr
    assert "required command missing" not in result.stderr


def test_unsupported_platform_names_the_limitation(tmp_path):
    stubs = pass_stubs()
    stubs["uname"] = "#!/bin/sh\necho FreeBSD\n"
    bin_dir = make_stub_dir(tmp_path, stubs)
    result = run_install(tmp_path, bin_dir)
    assert result.returncode == 1
    assert "FreeBSD" in result.stderr
    assert ISSUE_URL in result.stderr


@pytest.mark.parametrize("name", ["install.sh"])
def test_install_script_exists(name):
    assert INSTALL_SH.is_file()
