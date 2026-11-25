"""Lean review brief and resume handoff in ``build_worker_context`` (token saving, 2026-11-25).

A reviewer's opening context must be handoff + acceptance criteria + latest changes_requested reason
(not the whole thread); a retry after an interrupted run must open with the last summary, the step
reached and the files touched. Both reshapes have an off switch that restores the legacy rendering.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def conn(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_KANBAN_LEAN_REVIEW_CONTEXT", raising=False)
    monkeypatch.delenv("HERMES_KANBAN_RESUME_HANDOFF", raising=False)
    kb.init_db()
    from hermes_cli import kanban_db_connect as kbc
    c = kbc.connect()
    try:
        yield c
    finally:
        c.close()


def _git_repo(path: Path) -> Path:
    path.mkdir()
    for cmd in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(path), *cmd], check=True)
    (path / "a.txt").write_text("one\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "base"], check=True)
    return path


def _review_round(conn, tid, summary):
    """implementer run -> request_review -> reviewer claims it. Returns nothing; the card is `running` as reviewer."""
    run = conn.execute("SELECT current_run_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    assert kb.request_review(conn, tid, summary=summary, reviewer="reviewer-ds", expected_run_id=run)
    assert kb.claim_review_task(conn, tid) is not None


def _card(conn, **kw):
    tid = kb.create_task(conn, title="t", body="ACCEPTANCE: the thing works", assignee="coder-ds", **kw)
    assert kb.claim_task(conn, tid) is not None
    return tid


def test_reviewer_opens_with_brief_not_the_thread(conn):
    tid = _card(conn)
    for i in range(12):
        kb.add_comment(conn, tid, "worker", f"chatter number {i}")
    _review_round(conn, tid, "HANDOFF-ONE: PR #7 head abc123, CI green")
    ctx = kb.build_worker_context(conn, tid)
    assert "## Review brief" in ctx
    assert "HANDOFF-ONE" in ctx
    assert "ACCEPTANCE: the thing works" in ctx
    assert "## Recent work by @" not in ctx
    assert "chatter number 11" in ctx and "chatter number 9" in ctx
    assert "chatter number 8" not in ctx          # only the last 3 comments
    assert "9 earlier comments omitted" in ctx


def test_latest_changes_requested_reason_is_shown(conn):
    tid = _card(conn)
    _review_round(conn, tid, "first handoff")
    ok, _ = kb.request_changes(conn, tid, reason="REASON-ALPHA: the null case is not handled")
    assert ok
    assert kb.claim_task(conn, tid) is not None
    run = conn.execute("SELECT current_run_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    assert kb.request_review(conn, tid, summary="second handoff", reviewer="reviewer-ds", expected_run_id=run)
    assert kb.claim_review_task(conn, tid) is not None
    ctx = kb.build_worker_context(conn, tid)
    assert "### Latest changes_requested reason" in ctx
    assert "REASON-ALPHA" in ctx
    assert "second handoff" in ctx


def test_lean_review_context_off_switch_restores_legacy(conn, monkeypatch):
    tid = _card(conn)
    _review_round(conn, tid, "HANDOFF-ONE")
    monkeypatch.setenv("HERMES_KANBAN_LEAN_REVIEW_CONTEXT", "0")
    ctx = kb.build_worker_context(conn, tid)
    assert "## Review brief" not in ctx
    assert "## Prior attempts on this task" in ctx


def test_implementer_context_is_not_lean(conn):
    tid = _card(conn)
    ctx = kb.build_worker_context(conn, tid)
    assert "## Review brief" not in ctx and "## Resume handoff" not in ctx


def _interrupt(conn, tid, outcome="rate_limited"):
    """Close the active run the way the dispatcher does for a dead worker and requeue the card."""
    run = conn.execute("SELECT current_run_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    with kb.write_txn(conn):
        conn.execute("UPDATE task_runs SET status=?, outcome=?, ended_at=strftime('%s','now'), "
                     "error='pid 1 exited rate-limited (quota wall)' WHERE id=?", (outcome, outcome, run))
        conn.execute("UPDATE tasks SET status='ready', claim_lock=NULL, claim_expires=NULL, "
                     "current_run_id=NULL, worker_pid=NULL WHERE id=?", (tid,))


def test_retry_after_rate_limit_opens_with_resume_handoff(conn, tmp_path):
    repo = _git_repo(tmp_path / "repo")
    tid = _card(conn, workspace_kind="dir", workspace_path=str(repo))
    kb._append_event(conn, tid, "heartbeat", {"note": "step 3/5: parser done, tests next"})
    (repo / "a.txt").write_text("one\ntwo\n")
    (repo / "new_file.py").write_text("x = 1\n")
    _interrupt(conn, tid)
    assert kb.claim_task(conn, tid) is not None
    ctx = kb.build_worker_context(conn, tid)
    assert "## Resume handoff" in ctx
    assert "step 3/5: parser done, tests next" in ctx
    assert "new_file.py" in ctx and "a.txt" in ctx
    assert "do not restart" in ctx
    assert len(ctx.split("## Resume handoff", 1)[1].split("## Prior attempts", 1)[0]) < 3000


def test_resume_handoff_off_switch(conn, monkeypatch):
    tid = _card(conn)
    _interrupt(conn, tid)
    assert kb.claim_task(conn, tid) is not None
    monkeypatch.setenv("HERMES_KANBAN_RESUME_HANDOFF", "0")
    ctx = kb.build_worker_context(conn, tid)
    assert "## Resume handoff" not in ctx
    assert "### Attempt 1 — rate_limited" in ctx


def test_normal_failure_retry_has_no_resume_block(conn):
    tid = _card(conn)
    _interrupt(conn, tid, outcome="blocked")
    assert kb.claim_task(conn, tid) is not None
    assert "## Resume handoff" not in kb.build_worker_context(conn, tid)


def _session_db(path: Path, rows) -> Path:
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, tool_calls TEXT)")
    for role, content, calls in rows:
        db.execute("INSERT INTO messages VALUES ('s1', ?, ?, ?)", (role, content, json.dumps(calls) if calls else None))
    db.commit()
    db.close()
    return path


def _admit(conn, tid, db, **extra):
    run = conn.execute("SELECT current_run_id FROM tasks WHERE id=?", (tid,)).fetchone()[0]
    with kb.write_txn(conn):
        kb._append_event(conn, tid, "owner_admitted", {"db": str(db), "session_id": "s1"}, run_id=run)
        if extra:
            kb._append_event(conn, tid, "worker_result", extra, run_id=run)


_EDIT_ROWS = [
    ("assistant", "", [
        {"function": {"name": "write_file", "arguments": '{"path": "/x/a.py", "content": "1"}'}},
        {"function": {"name": "patch", "arguments": {"path": "/x/b.py"}}},
        {"name": "write_file", "arguments": {"path": "/x/a.py"}},
    ]),
    ("assistant", "halfway: b.py patched, tests next", None),
]


def test_resume_handoff_lists_session_edits_and_last_output(conn, tmp_path):
    tid = _card(conn, workspace_kind="scratch")
    _admit(conn, tid, _session_db(tmp_path / "state.db", _EDIT_ROWS))
    _interrupt(conn, tid, outcome="reclaimed")
    assert kb.claim_task(conn, tid) is not None
    block = kb.build_worker_context(conn, tid).split("## Resume handoff", 1)[1]
    assert block.count("/x/a.py") == 1 and block.count("/x/b.py") == 1
    assert block.index("/x/a.py") < block.index("/x/b.py")
    assert "halfway: b.py patched, tests next" in block


def test_resume_handoff_prefers_worker_result_last_output(conn, tmp_path):
    tid = _card(conn, workspace_kind="scratch")
    _admit(conn, tid, _session_db(tmp_path / "state.db", _EDIT_ROWS), last_output="FINAL-FROM-RESULT")
    _interrupt(conn, tid, outcome="reclaimed")
    assert kb.claim_task(conn, tid) is not None
    ctx = kb.build_worker_context(conn, tid)
    assert "FINAL-FROM-RESULT" in ctx and "halfway: b.py" not in ctx


def test_resume_handoff_survives_missing_session_db(conn, tmp_path):
    tid = _card(conn, workspace_kind="scratch")
    _admit(conn, tid, tmp_path / "nope" / "state.db")
    _interrupt(conn, tid, outcome="reclaimed")
    assert kb.claim_task(conn, tid) is not None
    ctx = kb.build_worker_context(conn, tid)
    assert "## Resume handoff" in ctx
    assert "Files the interrupted run edited" not in ctx and "Last output" not in ctx
