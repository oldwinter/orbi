#!/usr/bin/env python3
"""Automatic GitHub progress publishing.

The runner keeps exactly one live progress comment per run on the source
Issue. The comment carries a hidden HTML run marker
(`<!-- orbi:run=<run_id> -->`) so a restarted process finds the same
comment again and keeps PATCHing it in place — no database, no new
heartbeat comments. Milestone events without a resume scene (plan
ready, tests passed/failed, review findings, merged, blocked) are
published as short standalone comments so GitHub Mobile pushes a
notification for each one.

All GitHub traffic goes through the single subprocess seam
(`orbi.journal.run_command`, `gh api`), which logs the command and fails
fast on any error. There is no fallback or retry.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from importlib import metadata
from pathlib import Path
from typing import Callable

from orbi.delivery_scene import RunContext
from orbi.journal import (
    LOGGER,
    RUN_ID_PATTERN,
    issue_context,
    quote_value,
    validate_run_id,
)
from orbi.pi_activity import activity_snapshot, sanitize

# One marker per run: hidden in the rendered comment, exact for lookup.
# The run-id pattern and its validator live in `orbi.journal` (the run
# binding owns the contract); they are re-exported here for callers.
RUN_MARKER_PATTERN = re.compile(r"<!-- orbi:run=([0-9a-f]{8}) -->")
RUN_MARKER_TEMPLATE = "<!-- orbi:run={run_id} -->"
RUNNER_MARKER_TEMPLATE = "<!-- runner={fingerprint} -->"
_RUNNER_MARKER_PATTERN = re.compile(r"<!-- runner=[^>]+ -->")
# Standalone milestone comments share this prefix so they are recognizable.
MILESTONE_PREFIX = "Orbi:"
# The live progress comment carries this header; together with the run
# marker it identifies the run's progress comment among the run's other
# marker-carrying comments (started Pi / opened PR scenes, milestones).
PROGRESS_HEADER = "**Orbi progress**"
