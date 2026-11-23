"""A worker spawned with ``cwd=<workspace>`` must not let that workspace shadow the pinned tree.

Regression for the dispatcher spawning the client as ``sys.executable -m
hermes_cli.kanban_worker_client`` with ``cwd=workspace``: when the workspace IS a Hermes checkout,
``sys.path[0]`` was that checkout, so the client's very first import (``hermes_bootstrap``) resolved
the checkout's own copy and the worker exited 1 with "no dependency environment is committed for
this install" before it could even open a session. `_worker_argv` now passes ``-P``.

The probe below deliberately reproduces the interpreter-side effect instead of importing
``hermes_bootstrap``: resolution is observed from a module that only reports whether the process cwd
is on ``sys.path``, so the test is valid on a CI checkout that has no committed PM environment.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli.kanban_db import Task

_PROBE = (
    "import os, sys\n"
    "print('SHADOW' if os.getcwd() in sys.path or '' in sys.path else 'CLEAN')\n"
)


def _task() -> Task:
    return Task(
        id="t_cwd_shadow", title="shadow", body="", assignee="worker", status="running",
        priority=0, created_by=None, created_at=1, started_at=None, completed_at=None,
        workspace_kind="dir", workspace_path=None, claim_lock=None, claim_expires=None,
        tenant=None,
    )


def _run_probe(interpreter: str, flags: list[str], probe_root: Path, workspace: Path):
    """Run a module-mode child in ``workspace`` exactly as the dispatcher would."""
    package = probe_root / "cwdshadowprobe"
    package.mkdir(exist_ok=True)
    (package / "__main__.py").write_text(_PROBE, encoding="utf-8")
    pinned = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(probe_root), str(pinned)])}
    return subprocess.run(
        [interpreter, *flags, "-m", "cwdshadowprobe"],
        cwd=workspace, env=env, capture_output=True, text=True, timeout=120, check=False,
    )


def test_worker_argv_keeps_the_workspace_cwd_off_the_import_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # What the real checkout workspace holds: its own hermes_bootstrap.py.
    (workspace / "hermes_bootstrap.py").write_text("# workspace copy; never the one to load\n",
                                                   encoding="utf-8")
    argv = dispatch._worker_argv(_task(), "worker", None)
    assert argv[-2:] == ["-m", "hermes_cli.kanban_worker_client"]
    flags = argv[1:-2]

    kept_out = _run_probe(argv[0], flags, tmp_path, workspace)
    assert kept_out.returncode == 0, kept_out.stderr
    assert kept_out.stdout.strip().splitlines()[-1] == "CLEAN", kept_out.stdout

    # Sensitivity: the same module-mode spawn without the flags `_worker_argv` adds puts the
    # workspace on sys.path again — the shadow that killed every checkout-workspace worker.
    shadowed = _run_probe(argv[0], [], tmp_path, workspace)
    assert shadowed.returncode == 0, shadowed.stderr
    assert shadowed.stdout.strip().splitlines()[-1] == "SHADOW", shadowed.stdout
