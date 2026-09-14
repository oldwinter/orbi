"""In-memory launchd fake at the adapter seam (Issue #849).

``FakeLaunchd`` replaces the ``launchctl`` process (and the canned
``git rev-parse``) for the ``orbi.launchd_deploy`` adapter: the test
hands the fake to the scheduler layer as ``run_command``, arranges
loaded/running/disabled state with the helpers, runs the real
scheduler operations, and asserts on the recorded argv (membership,
never order) and on the fake's state transitions.

The observable behavior mirrors the real launchctl contract: ``print``
exits 113 with ``Could not find service`` for a label that is not
loaded, ``bootstrap`` fails for an already-loaded label, ``bootout``
unloads (and would kill a running job — the tests assert it is never
issued for one), ``enable``/``disable`` maintain the persistent
disabled set that ``print-disabled`` reports, and ``kickstart`` is the
restart entry. Unsupported argv fails fast with a
``CalledProcessError`` naming the command, never a silent success.
"""
from __future__ import annotations

import subprocess


class FakeLaunchd:
    """One GUI domain's launchd state, answering launchctl argv."""

    def __init__(self, uid: int = 501, commit: str = "cafecafe"):
        self.uid = uid
        self.commit = commit
        # label -> "running" | "not running"; absent = not loaded.
        self.state: dict[str, str] = {}
        self.disabled: set[str] = set()
        self.commands: list[list[str]] = []

    # -- arrangement helpers ------------------------------------------------
    def load(self, label: str, state: str = "not running") -> None:
        self.state[label] = state

    # -- the subprocess seam ------------------------------------------------
    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command and command[0] == "git" and "rev-parse" in command:
            return self.commit
        if command and command[0] == "launchctl":
            return self._launchctl(command)
        return ""

    # -- launchctl subcommands ----------------------------------------------
    def _launchctl(self, command):
        sub = command[1]
        if sub == "print-disabled":
            return "".join(
                f'"{label}" => disabled\n'
                for label in sorted(self.disabled)
            )
        if sub == "print":
            label = command[2].rsplit("/", 1)[-1]
            state = self.state.get(label)
            if state is None:
                raise subprocess.CalledProcessError(
                    113, command, output="",
                    stderr=(
                        f'Could not find service "{label}" in the '
                        f"domain for gui/{self.uid}"
                    ),
                )
            return f"\tstate = {state}\n"
        if sub == "bootstrap":
            plist = command[3]
            label = plist.rsplit("/", 1)[-1].removesuffix(".plist")
            if self.state.get(label) is not None:
                raise subprocess.CalledProcessError(
                    9, command, stderr=(
                        "Bootstrap failed: 5: Input/output error — a "
                        "stale disabled record must be enabled away first"
                    ),
                )
            self.state[label] = "not running"
            return ""
        if sub == "bootout":
            label = command[2].rsplit("/", 1)[-1]
            if self.state.get(label) is None:
                raise subprocess.CalledProcessError(
                    113, command, stderr=(
                        f'Could not find service "{label}" in the domain '
                        f"for gui/{self.uid}"
                    ),
                )
            # Mirrors reality: a running job would be KILLED here. The
            # fake does not prevent it — the tests assert it never
            # happens to a running label.
            del self.state[label]
            return ""
        if sub == "enable":
            self.disabled.discard(command[2].rsplit("/", 1)[-1])
            return ""
        if sub == "disable":
            self.disabled.add(command[2].rsplit("/", 1)[-1])
            return ""
        if sub == "kickstart":
            return ""
        raise subprocess.CalledProcessError(
            1, command, stderr=f"unknown launchctl subcommand: {sub}",
        )
