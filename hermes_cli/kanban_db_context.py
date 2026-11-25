"""Worker-context rendering for ``build_worker_context``: the capped markdown sections a spawned
worker reads about its task (header, attachments, prior attempts, parent handoffs, role
history, comments).

Split out of ``hermes_cli.kanban_db``; origin-resident helpers are reached late-bound via
``_kb`` so monkeypatching ``kanban_db.<name>`` keeps working.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import hermes_cli.kanban_db as _kb

if TYPE_CHECKING:
    from hermes_cli.kanban_db import Attachment, Comment, Task

# build_worker_context() caps, sized for a ~100k-char prompt with headroom.
_CTX_MAX_PRIOR_ATTEMPTS = 10      # most recent N prior runs shown in full
_CTX_MAX_COMMENTS       = 30      # most recent N comments shown in full
_CTX_MAX_FIELD_BYTES    = 4 * 1024   # per summary/error/metadata/result
_CTX_MAX_BODY_BYTES     = 8 * 1024   # per task.body (opening post)
_CTX_MAX_COMMENT_BYTES  = 2 * 1024   # per comment


# --- lean review brief + resume handoff (2026-11-25, token saving) --------------------------------
# Every model call replays the worker's opening context, so its size is paid per call. Two bounded,
# switchable reshapes of the SAME facts (nothing is deleted from the board; `kanban_show detail='full'`
# and `hermes kanban show` still return the whole thread):
#   * a reviewer run opens with a review brief (implementer handoff, latest changes_requested reason,
#     diff stat, last 3 comments) instead of every prior attempt + 30 comments + role history;
#   * a retry after an interrupted run (rate_limited / reclaimed / crashed / timed_out) opens with a
#     resume handoff (last summary, step reached, files touched per git and per the run's own session)
#     instead of starting cold.
# Off switches: `hermes config set kanban.lean_review_context false`, `kanban.resume_handoff false`
# (or env HERMES_KANBAN_LEAN_REVIEW_CONTEXT=0 / HERMES_KANBAN_RESUME_HANDOFF=0).
_CTX_LEAN_COMMENTS       = 3
_CTX_LEAN_COMMENT_BYTES  = 1200
_CTX_LEAN_HANDOFF_BYTES  = 3000
_CTX_LEAN_REASON_BYTES   = 2000
_CTX_RESUME_SUMMARY_BYTES = 1500
_CTX_RESUME_NOTES        = 3
_CTX_RESUME_NOTE_BYTES   = 300
_CTX_RESUME_FILES        = 20
_CTX_GIT_LINES           = 25
_RESUME_OUTCOMES = frozenset({"rate_limited", "reclaimed", "crashed", "timed_out", "stale", "spawn_failed"})

# Set by _ctx_prior_attempts (which has the connection) for the helpers that render later in the same
# build_worker_context call; _ctx_header resets it first, so a render never sees a previous one's mode.
_MODE = threading.local()


def _flag(name: str) -> bool:
    """True unless switched off by env or by `kanban.<name>: false` in the process or root config."""
    env = (os.environ.get("HERMES_KANBAN_" + name.upper()) or "").strip().lower()
    if env in ("0", "false", "off", "no"):
        return False
    if env in ("1", "true", "on", "yes"):
        return True
    try:
        from hermes_cli.config import load_config
        val = ((load_config() or {}).get("kanban") or {}).get(name)
        if val is False or str(val).strip().lower() in ("false", "off", "0", "no"):
            return False
    except Exception:
        pass
    try:
        import yaml
        from hermes_constants import get_default_hermes_root
        with open(os.path.join(str(get_default_hermes_root()), "config.yaml"), encoding="utf-8") as fh:
            val = ((yaml.safe_load(fh) or {}).get("kanban") or {}).get(name)
        if val is False or str(val).strip().lower() in ("false", "off", "0", "no"):
            return False
    except Exception:
        pass
    return True


def _git_lines(path: Optional[str], *args: str, cap: int = _CTX_GIT_LINES) -> list[str]:
    """Bounded `git -C path ...` output lines; [] on any failure (a context must never fail to render)."""
    if not path or not os.path.isdir(path):
        return []
    try:
        proc = subprocess.run(["git", "-C", path, *args], check=False, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    out = [ln[:200] for ln in proc.stdout.splitlines() if ln.strip()]
    if len(out) > cap:
        out = out[:cap] + [f"... ({len(out) - cap} more lines omitted)"]
    return out


def _is_review_run(conn: sqlite3.Connection, task: Task) -> bool:
    if task.status == "running" and task.current_run_id is not None:
        try:
            return _kb._retry_status_for_run(conn, task.id, task.current_run_id) == "review"
        except Exception:
            pass
    return (task.assignee or "").startswith("reviewer") and task.status in ("review", "running")


def _relative_age(ts: Optional[int], now: Optional[int] = None) -> str:
    """``just now`` / ``18h ago`` / ``3d ago``; "" for a missing/invalid ts. An LLM
    reads a bare absolute timestamp as current fact — the relative age is what
    prompts a worker to re-verify stale sibling work."""
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    if now is None:
        now = int(time.time())
    delta = now - ts
    if delta < 60:  # includes negative = clock skew across machines; never claim "in the future"
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _ctx_cap(s: Optional[str], limit: int = _CTX_MAX_FIELD_BYTES) -> str:
    """Truncate to ``limit`` chars with a visible ellipsis."""
    if not s:
        return ""
    s = s.strip()
    if len(s) <= limit:
        return s
    return s[:limit] + f"… [truncated, {len(s) - limit} chars omitted]"


def _ctx_stamp(ts: int, now: int) -> str:
    """``YYYY-MM-DD HH:MM`` plus a relative age when one is available."""
    disp = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    age = _relative_age(ts, now)
    return f"{disp}, {age}" if age else disp


def _ctx_metadata_line(metadata: Any) -> Optional[str]:
    if not metadata:
        return None
    try:
        return f"_metadata_: `{_ctx_cap(json.dumps(metadata, ensure_ascii=False, sort_keys=True))}`"
    except Exception:
        return None


def _ctx_tail(items: list, cap: int, noun: str) -> tuple[list, Optional[str]]:
    """Keep the newest ``cap`` items; describe the omitted head, if any."""
    omitted = max(0, len(items) - cap)
    if not omitted:
        return items, None
    return items[-cap:], (
        f"_({omitted} earlier {noun}{'s' if omitted != 1 else ''} "
        f"omitted; showing most recent {cap})_"
    )


def _ctx_header(lines: list[str], task: Task) -> None:
    _MODE.review = False
    _MODE.resume = False
    lines.append(f"# Kanban task {task.id}: {task.title}")
    lines.append("")
    lines.append(f"Assignee: {task.assignee or '(unassigned)'}")
    lines.append(f"Status:   {task.status}")
    if task.tenant:
        lines.append(f"Tenant:   {task.tenant}")
    lines.append(f"Workspace: {task.workspace_kind} @ {task.workspace_path or '(unresolved)'}")
    if task.max_runtime_seconds is not None:
        terminal_timeout = _kb._worker_terminal_timeout_env(
            task.max_runtime_seconds, os.environ.get("TERMINAL_TIMEOUT"),
        )
        effective_terminal_timeout = terminal_timeout or os.environ.get("TERMINAL_TIMEOUT")
        lines.append(f"Max runtime: {task.max_runtime_seconds}s")
        if effective_terminal_timeout:
            lines.append(f"Terminal timeout: {effective_terminal_timeout}s")
    if task.branch_name:
        lines.append(f"Branch:   {task.branch_name}")
    lines.append("")
    if task.body and task.body.strip():
        lines.append("## Body")
        lines.append(_ctx_cap(task.body, _CTX_MAX_BODY_BYTES))
        lines.append("")


def _ctx_attachments(lines: list[str], attachments: list[Attachment]) -> None:
    """Absolute on-disk paths so the worker's file tools read them directly
    (remote terminal backends need the attachments dir mounted)."""
    if not attachments:
        return
    lines.append("## Attachments")
    lines.append(
        "Files attached to this task. Read them with the file/terminal "
        "tools at the absolute paths below:"
    )
    for att in attachments:
        size_kb = max(1, (att.size + 1023) // 1024) if att.size else 0
        size_str = f", {size_kb} KB" if size_kb else ""
        ctype = f", {att.content_type}" if att.content_type else ""
        lines.append(f"- `{att.filename}`{ctype}{size_str} → `{att.stored_path}`")
    lines.append("")


def _ctx_prior_attempts(lines: list[str], conn: sqlite3.Connection, task_id: str, now: int) -> None:
    """Closed runs on this task (the active run is this worker), newest
    ``_CTX_MAX_PRIOR_ATTEMPTS`` in full, older ones as a one-line marker."""
    all_prior = [r for r in _kb.list_runs(conn, task_id) if r.ended_at is not None]
    task = _kb.get_task(conn, task_id)
    if task is not None and _flag("lean_review_context") and _is_review_run(conn, task):
        _MODE.review = True
        _ctx_review_brief(lines, task, all_prior, now)
        return
    # A `scheduled` run is the loop guard / scheduler parking the card, not an attempt: look past it.
    last_work = next((r for r in reversed(all_prior) if r.outcome != "scheduled"), None)
    resume = bool(task is not None and last_work is not None and last_work.outcome in _RESUME_OUTCOMES
                  and _flag("resume_handoff"))
    if resume:
        _MODE.resume = True
        _ctx_resume_handoff(lines, conn, task, last_work, now)
    shown, omitted_note = _ctx_tail(all_prior, _CTX_MAX_PRIOR_ATTEMPTS, "attempt")
    if not shown:
        return
    first_shown_idx = len(all_prior) - len(shown) + 1
    lines.append("## Prior attempts on this task")
    if omitted_note:
        lines.append(omitted_note)
    for offset, run in enumerate(shown):
        profile = run.profile or "(unknown)"
        outcome = run.outcome or run.status
        head = f"Attempt {first_shown_idx + offset} — {outcome} ({profile}, {_ctx_stamp(run.started_at, now)})"
        if resume and run.outcome in _RESUME_OUTCOMES and not (run.summary and run.summary.strip()):
            # An interrupted run records no summary; its metadata is pid/claimer bookkeeping. One line.
            lines.append(f"- {head}")
            continue
        lines.append(f"### {head}")
        if run.summary and run.summary.strip():
            lines.append(_ctx_cap(run.summary))
        if run.error and run.error.strip():
            lines.append(f"_error_: {_ctx_cap(run.error)}")
        meta_line = _ctx_metadata_line(run.metadata)
        if meta_line:
            lines.append(meta_line)
        lines.append("")
    if lines and lines[-1] != "":
        lines.append("")


def _ctx_review_brief(lines: list[str], task: Task, all_prior: list, now: int) -> None:
    """A reviewer's opening input: handoff, acceptance criteria (the body, above), latest
    changes_requested reason, diff stat. Not the whole thread."""
    handoff = next((r for r in reversed(all_prior) if r.outcome == "review_requested"), None)
    reason = next((r for r in reversed(all_prior) if r.outcome == "changes_requested"), None)
    lines.append("## Review brief")
    lines.append("_Lean opening context: the acceptance criteria are the Body above. The earlier thread is "
                 "omitted on purpose; `kanban_show` with detail='full' returns all of it if you need it._")
    lines.append("")
    if handoff is not None:
        lines.append(f"### Implementer handoff ({handoff.profile or '(unknown)'}, {_ctx_stamp(handoff.started_at, now)})")
        if handoff.summary and handoff.summary.strip():
            lines.append(_ctx_cap(handoff.summary, _CTX_LEAN_HANDOFF_BYTES))
        meta_line = _ctx_metadata_line(handoff.metadata)
        if meta_line:
            lines.append(_ctx_cap(meta_line, _CTX_LEAN_HANDOFF_BYTES))
        lines.append("")
    if reason is not None:
        lines.append(f"### Latest changes_requested reason ({reason.profile or '(unknown)'}, {_ctx_stamp(reason.started_at, now)})")
        lines.append(_ctx_cap(reason.summary or reason.error or "(no reason recorded)", _CTX_LEAN_REASON_BYTES))
        lines.append("")
    stat: list[str] = []
    for base in ("origin/main", "origin/master", "main", "master"):
        mb = _git_lines(task.workspace_path, "merge-base", "HEAD", base, cap=1)
        if mb:
            stat = _git_lines(task.workspace_path, "diff", "--stat", mb[0], "HEAD", cap=40)
            break
    if stat:
        lines.append("### Diff stat (checked-out head against its merge base; read the diff itself with `git diff`/`gh pr diff`)")
        lines.extend(stat)
        lines.append("")
    older = [r for r in all_prior if r is not handoff and r is not reason]
    if older:
        shown, omitted = _ctx_tail(older, _CTX_MAX_PRIOR_ATTEMPTS, "attempt")
        lines.append("### Earlier attempts (outcome only)")
        if omitted:
            lines.append(omitted)
        for r in shown:
            lines.append(f"- {r.outcome or r.status} ({r.profile or '(unknown)'}, {_ctx_stamp(r.started_at, now)})")
        lines.append("")


def _run_event_payload(conn: sqlite3.Connection, task_id: str, run_id: int, kind: str) -> dict:
    row = conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND run_id = ? AND kind = ? "
        "AND payload IS NOT NULL ORDER BY id DESC LIMIT 1", (task_id, run_id, kind)).fetchone()
    payload = json.loads(row[0]) if row else None
    return payload if isinstance(payload, dict) else {}


def _prior_session_facts(session_conn: sqlite3.Connection, session_id: Any) -> tuple[list[str], str]:
    """(distinct paths edited via write_file/patch in first-edit order, last non-empty assistant text)."""
    files: dict[str, None] = {}
    last_text = ""
    for role, content, tool_calls in session_conn.execute(
        "SELECT role, content, tool_calls FROM messages WHERE session_id = ? ORDER BY rowid", (session_id,),
    ):
        if role != "assistant":
            continue
        if isinstance(content, str) and content.strip():
            last_text = content
        try:
            calls = json.loads(tool_calls) if tool_calls else []
        except (TypeError, ValueError):
            continue
        for call in calls if isinstance(calls, list) else []:
            fn = call.get("function", call) if isinstance(call, dict) else {}
            if fn.get("name") not in ("write_file", "patch"):
                continue
            args = fn.get("arguments")
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except ValueError:
                continue
            path = args.get("path") if isinstance(args, dict) else None
            if isinstance(path, str) and path.strip():
                files.setdefault(path.strip())
    return list(files), last_text


def _prior_run_facts(conn: sqlite3.Connection, task_id: str, run_id: int) -> tuple[list[str], str]:
    """Best-effort (edited files, last output) of the interrupted run, read from its own session DB."""
    files: list[str] = []
    text = ""
    try:
        text = str(_run_event_payload(conn, task_id, run_id, "worker_result").get("last_output") or "")
        admitted = _run_event_payload(conn, task_id, run_id, "owner_admitted")
        session_conn = sqlite3.connect(Path(admitted["db"]).as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            files, session_text = _prior_session_facts(session_conn, admitted["session_id"])
        finally:
            session_conn.close()
        text = text if text.strip() else session_text
    except Exception:  # health: allow BLE001 -- best-effort: an old or foreign session DB must never fail the context
        pass
    return files, text


def _ctx_resume_handoff(lines: list[str], conn: sqlite3.Connection, task: Task, last, now: int) -> None:
    """The retry's opening facts after an interrupted run: last summary, step reached, files touched."""
    lines.append(f"## Resume handoff — the previous run was interrupted ({last.outcome})")
    lines.append("_Continue from here; do not restart. Treat this as a claim and check the tree before you edit._")
    lines.append(f"- Last run: #{last.id} {last.outcome} ({last.profile or '(unknown)'}, {_ctx_stamp(last.started_at, now)})")
    if last.step_key:
        lines.append(f"- Step key: {last.step_key}")
    if last.summary and last.summary.strip():
        lines.append("- Summary: " + _ctx_cap(last.summary, _CTX_RESUME_SUMMARY_BYTES))
    if last.error and last.error.strip():
        lines.append("- Ended with: " + _ctx_cap(last.error, 300))
    files, last_text = _prior_run_facts(conn, task.id, last.id)
    if files:
        lines.append("- Files the interrupted run edited (write_file/patch, from its session):")
        lines.extend(f"  - {p[:200]}" for p in files[:_CTX_RESUME_FILES])
        if len(files) > _CTX_RESUME_FILES:
            lines.append(f"  - ... ({len(files) - _CTX_RESUME_FILES} more)")
    if last_text and not (last.summary and last.summary.strip()):
        lines.append("- Last output: " + _ctx_cap(last_text, _CTX_RESUME_SUMMARY_BYTES))
    notes: list[str] = []
    for row in conn.execute(
        "SELECT payload FROM task_events WHERE task_id = ? AND kind = 'heartbeat' AND payload IS NOT NULL "
        "ORDER BY id DESC LIMIT ?", (task.id, _CTX_RESUME_NOTES),
    ):
        try:
            note = (json.loads(row["payload"]) or {}).get("note")
        except (TypeError, ValueError):
            note = None
        if isinstance(note, str) and note.strip():
            notes.append(_ctx_cap(note, _CTX_RESUME_NOTE_BYTES))
    if notes:
        lines.append("- Step reached (latest heartbeat notes, newest first):")
        lines.extend(f"  - {n}" for n in notes)
    else:
        comment = conn.execute(
            "SELECT body FROM task_comments WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task.id,)).fetchone()
        if comment and (comment["body"] or "").strip():
            lines.append("- Step reached (last comment): " + _ctx_cap(comment["body"], _CTX_RESUME_NOTE_BYTES))
    status = _git_lines(task.workspace_path, "status", "--short")
    stat = _git_lines(task.workspace_path, "diff", "--stat", "HEAD", cap=15)
    commits = _git_lines(task.workspace_path, "log", "--oneline", "-3", cap=3)
    if status or stat or commits:
        lines.append("- Workspace state now:")
        if commits:
            lines.append("  - last commits: " + "; ".join(commits))
        if status:
            lines.append("  - files touched (git status --short):")
            lines.extend(f"    {ln}" for ln in status)
        if stat:
            lines.append("  - uncommitted diff stat:")
            lines.extend(f"    {ln}" for ln in stat)
    lines.append("")


def _ctx_parent_results(lines: list[str], conn: sqlite3.Connection, task_id: str, now: int) -> None:
    """Done-parent handoffs: newest ``completed`` run's summary+metadata,
    falling back to ``task.result`` for pre-runs-table data. Stamped with a
    relative age so the worker re-verifies stale upstream results."""
    parent_rows = conn.execute(
        "SELECT parent_id FROM task_links WHERE child_id = ? ORDER BY parent_id", (task_id,),
    ).fetchall()
    wrote_header = False
    for pid in (r["parent_id"] for r in parent_rows):
        pt = _kb.get_task(conn, pid)
        if not pt or pt.status != "done":
            continue
        runs = [r for r in _kb.list_runs(conn, pid) if r.outcome == "completed"]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        run = runs[0] if runs else None
        if not wrote_header:
            lines.append("## Parent task results")
            lines.append(
                "_Handoffs from upstream tasks, captured when each parent "
                "completed (see age below). These are point-in-time "
                "snapshots, not live state — if a result drives your "
                "current work and it's not recent, re-verify against the "
                "source before acting on it as current._"
            )
            wrote_header = True
        done_ts = run.ended_at if run is not None and run.ended_at else (pt.completed_at or None)
        age = _relative_age(done_ts, now)
        lines.append(f"### {pid}" + (f" (completed {age})" if age else ""))
        if run is not None and run.summary and run.summary.strip():
            lines.append(_ctx_cap(run.summary))
        elif pt.result:
            lines.append(_ctx_cap(pt.result))
        else:
            lines.append("(no result recorded)")
        meta_line = _ctx_metadata_line(run.metadata) if run is not None else None
        if meta_line:
            lines.append(meta_line)
        lines.append("")


def _ctx_role_history(lines: list[str], conn: sqlite3.Connection, task: Task, now: int) -> None:
    """The assignee's 5 most recent completed runs on OTHER tasks — implicit
    role continuity without wiring anything into SOUL.md / MEMORY.md."""
    if not task.assignee or getattr(_MODE, "review", False):
        return
    role_rows = conn.execute(
        "SELECT t.id, t.title, r.summary, r.ended_at "
        "FROM task_runs r JOIN tasks t ON r.task_id = t.id "
        "WHERE r.profile = ? AND r.task_id != ? "
        "  AND r.outcome = 'completed' "
        "ORDER BY r.ended_at DESC LIMIT 5", (task.assignee, task.id),
    ).fetchall()
    if not role_rows:
        return
    lines.append(f"## Recent work by @{task.assignee}")
    for row in role_rows:
        first = _kb._first_line(row["summary"], 200) or "(no summary)"
        lines.append(
            f"- {row['id']} — {row['title']} ({_ctx_stamp(_kb._to_epoch(row['ended_at']) or 0, now)}): {first}"
        )
    lines.append("")


def _ctx_comments(lines: list[str], comments: list[Comment], now: int) -> None:
    """Newest ``_CTX_MAX_COMMENTS`` comments. The explicit "comment from
    worker" framing stops an operator-controlled HERMES_PROFILE like
    "hermes-system" being read as a system directive above an
    attacker-influenceable body (defense-in-depth)."""
    lean = getattr(_MODE, "review", False)
    shown, omitted_note = _ctx_tail(comments, _CTX_LEAN_COMMENTS if lean else _CTX_MAX_COMMENTS, "comment")
    if not shown:
        return
    lines.append("## Comment thread")
    if omitted_note:
        lines.append(omitted_note)
    for c in shown:
        # Render author with explicit "comment from worker" framing so operator-controlled HERMES_PROFILE
        # values like "hermes-system" or "operator" can't be misread by the next worker as a system
        # directive above the (attacker-influenceable) comment body. Defense-in-depth — the LLM-controlled
        # author-forgery surface was already closed in #22435. See #22452.
        safe_author = (c.author or "").replace("`", "")
        lines.append(f"comment from worker `{safe_author}` at {_ctx_stamp(c.created_at, now)}:")
        lines.append(_ctx_cap(c.body, _CTX_LEAN_COMMENT_BYTES if lean else _CTX_MAX_COMMENT_BYTES))
        lines.append("")
