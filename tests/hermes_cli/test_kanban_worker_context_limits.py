"""``kanban.worker_context.*`` ceilings on the worker's opening context.

Defaults must render byte-for-byte what the former hard-coded caps rendered; a configured value
changes only its own ceiling; an invalid value falls back to the default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_context as ctxmod

DEFAULTS = {
    "max_comments": 30,
    "max_prior_attempts": 10,
    "max_field_bytes": 4096,
    "max_body_bytes": 8192,
    "max_comment_bytes": 2048,
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_KANBAN_LEAN_REVIEW_CONTEXT", "0")
    monkeypatch.setenv("HERMES_KANBAN_RESUME_HANDOFF", "0")
    kb.init_db()
    from hermes_cli import kanban_db_connect as kbc
    c = kbc.connect()

    def configure(block):
        import yaml
        (home / "config.yaml").write_text(yaml.safe_dump({"kanban": {"worker_context": block}}))

    try:
        yield c, configure
    finally:
        c.close()


def _add_runs(conn, tid, n, text="", metadata=None):
    with kb.write_txn(conn):
        for i in range(n):
            conn.execute(
                "INSERT INTO task_runs (task_id, profile, status, started_at, ended_at, outcome, summary, error, metadata) "
                "VALUES (?, 'coder-ds', 'done', ?, ?, 'completed', ?, ?, ?)",
                (tid, 1000 + i, 1001 + i, f"S{i} " + text, f"E{i} " + text,
                 json.dumps(metadata) if metadata else None),
            )


def _card(conn, comments=40, comment_len=10, body="plain body", runs=0, run_text="", parent=False):
    parents = []
    if parent:
        pid = kb.create_task(conn, title="parent", assignee="coder-ds")
        assert kb.claim_task(conn, pid) is not None
        assert kb.complete_task(conn, pid, summary="H" * 5000)
        parents = [pid]
    tid = kb.create_task(conn, title="t", body=body, assignee="coder-ds", parents=parents)
    _add_runs(conn, tid, runs, run_text, {"k": "m" * 5000} if runs else None)
    assert kb.claim_task(conn, tid) is not None
    for i in range(comments):
        kb.add_comment(conn, tid, "worker", "x" * comment_len)
    return tid


def test_default_renders_30_comments(env):
    conn, _ = env
    ctx = kb.build_worker_context(conn, _card(conn))
    assert ctx.count("comment from worker") == 30
    assert "_(10 earlier comments omitted; showing most recent 30)_" in ctx


def test_configured_limits_apply(env):
    conn, configure = env
    tid = _card(conn, comment_len=2000, runs=6, run_text="r")
    configure({"max_comments": 8, "max_comment_bytes": 1200, "max_prior_attempts": 3})
    ctx = kb.build_worker_context(conn, tid)
    assert ctx.count("comment from worker") == 8
    assert "_(32 earlier comments omitted; showing most recent 8)_" in ctx
    assert "… [truncated, 800 chars omitted]" in ctx
    assert ctx.count("### Attempt ") == 3
    assert "_(3 earlier attempts omitted; showing most recent 3)_" in ctx


@pytest.mark.parametrize("bad", [0, -1, "8", True, 2.5, None])
def test_invalid_values_fall_back_to_default(env, bad):
    conn, configure = env
    tid = _card(conn)
    configure({"max_comments": bad})
    assert kb.build_worker_context(conn, tid).count("comment from worker") == 30


def test_field_and_body_limits_apply(env):
    conn, configure = env
    tid = _card(conn, comments=0, body="b" * 3000, runs=1, run_text="f" * 3000)
    configure({"max_body_bytes": 1000, "max_field_bytes": 500})
    ctx = kb.build_worker_context(conn, tid)
    assert "… [truncated, 2000 chars omitted]" in ctx
    assert "… [truncated, 2503 chars omitted]" in ctx


def test_no_config_resolves_to_constants(env):
    assert ctxmod._worker_context_limits() == DEFAULTS
    assert DEFAULTS == {
        "max_comments": ctxmod._CTX_MAX_COMMENTS,
        "max_prior_attempts": ctxmod._CTX_MAX_PRIOR_ATTEMPTS,
        "max_field_bytes": ctxmod._CTX_MAX_FIELD_BYTES,
        "max_body_bytes": ctxmod._CTX_MAX_BODY_BYTES,
        "max_comment_bytes": ctxmod._CTX_MAX_COMMENT_BYTES,
    }


def test_defaults_render_is_unchanged_when_every_ceiling_is_exceeded(env):
    conn, configure = env
    tid = _card(conn, comments=40, comment_len=3000, body="B" * 10000, runs=12, run_text="r" * 5000, parent=True)
    bare = kb.build_worker_context(conn, tid)
    configure(DEFAULTS)
    explicit = kb.build_worker_context(conn, tid)
    assert bare == explicit
    assert bare.count("comment from worker") == 30
    assert "showing most recent 30" in bare
    assert "_(2 earlier attempts omitted; showing most recent 10)_" in bare
    assert "… [truncated, 1808 chars omitted]" in bare
    assert "… [truncated, 952 chars omitted]" in bare


def test_profile_config_wins_then_root_config_then_default(env, tmp_path, monkeypatch):
    import yaml
    root = tmp_path / ".hermes"
    profile = root / "profiles" / "p"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(profile))
    (root / "config.yaml").write_text(yaml.safe_dump(
        {"kanban": {"worker_context": {"max_comments": 5, "max_body_bytes": 7000}}}))
    assert ctxmod._worker_context_limits() == {**DEFAULTS, "max_comments": 5, "max_body_bytes": 7000}
    (profile / "config.yaml").write_text(yaml.safe_dump({"kanban": {"worker_context": {"max_comments": 9}}}))
    assert ctxmod._worker_context_limits() == {**DEFAULTS, "max_comments": 9, "max_body_bytes": 7000}
