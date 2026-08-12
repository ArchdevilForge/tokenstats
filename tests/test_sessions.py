"""Parser tests: construct fake agent data dirs under a tmp HOME."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from tokenstats import sessions as S


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "HOME", tmp_path)
    return tmp_path


def _pi_file(root, agent, rel, rows):
    p = root / agent / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(json.dumps(r) for r in rows))


# --------------------------------------------------------------- pi
def test_pi_nested(home):
    _pi_file(home, ".pi/agent/sessions", "--proj--/s1.jsonl", [
        {"type": "session", "timestamp": "2026-07-14T02:14:45.471Z", "cwd": "/x"},
        {"type": "message", "message": {"role": "user", "content": "hi"}},
        {"type": "message", "message": {
            "role": "assistant", "model": "grok-4.5",
            "usage": {"input": 100, "output": 20, "cacheRead": 50,
                      "cacheWrite": 0, "reasoning": 5,
                      "cost": {"total": 0.5}}}},
        {"type": "message", "message": {
            "role": "assistant", "model": "grok-4.5",
            "usage": {"input": 200, "output": 30, "cacheRead": 0,
                      "cacheWrite": 0, "reasoning": 0,
                      "cost": {"total": 0.25}}}},
    ])
    out = S.parse_pi()
    assert len(out) == 1
    s = out[0]
    assert s.agent == "pi" and s.model == "grok-4.5"
    assert s.tokens == {"input": 300, "output": 50, "cache_read": 50,
                        "cache_write": 0, "reasoning": 5}
    assert s.recorded_cost == pytest.approx(0.75)
    # ISO timestamps are converted to naive local time
    expected = datetime.fromisoformat(
        "2026-07-14T02:14:45.471+00:00").astimezone().replace(tzinfo=None)
    assert s.started == expected


def test_pi_empty_usage_skipped(home):
    _pi_file(home, ".pi/agent/sessions", "--proj--/s1.jsonl", [
        {"type": "session", "timestamp": "2026-07-14T02:14:45.471Z"},
        {"type": "message", "message": {"role": "assistant", "model": "m",
                                        "usage": {"input": 0, "output": 0,
                                                  "cost": {"total": 0}}}},
    ])
    assert S.parse_pi() == []


# ---------------------------------------------------------- primeagent
def test_primeagent_flat(home):
    _pi_file(home, ".prime/agent/sessions", "s1.jsonl", [
        {"type": "session", "timestamp": "2026-08-07T03:22:09.658Z"},
        {"type": "message", "message": {
            "role": "assistant", "model": "deepseek-v4-flash",
            "usage": {"input": 10, "output": 5, "cost": {"total": 0.01}}}},
    ])
    out = S.parse_primeagent()
    assert len(out) == 1
    assert out[0].agent == "primeagent"
    assert out[0].tokens["input"] == 10


# ------------------------------------------------------------- claude
def test_claude_both_formats_and_dedup(home):
    rows = [
        # old format: streamed snapshots repeat the same message id
        {"type": "message", "timestamp": "2026-08-01T10:00:00.000Z",
         "message": {"id": "m1", "model": "claude-opus-5",
                                        "usage": {"input_tokens": 2,
                                                  "output_tokens": 0,
                                                  "cache_creation": 0,
                                                  "cache_read": 100}}},
        {"type": "message", "message": {"id": "m1", "model": "claude-opus-5",
                                        "usage": {"input_tokens": 500,
                                                  "output_tokens": 40,
                                                  "cache_creation": 0,
                                                  "cache_read": 900}}},
        # new format: cache_creation_input_tokens + nested ephemeral
        {"type": "assistant", "message": {"id": "m2", "model": "claude-opus-5",
                                          "usage": {"input_tokens": 3,
                                                    "output_tokens": 7,
                                                    "cache_creation_input_tokens": 0,
                                                    "cache_read_input_tokens": 50,
                                                    "cache_creation": {
                                                        "ephemeral_1h_input_tokens": 200,
                                                        "ephemeral_5m_input_tokens": 100}}}},
    ]
    _pi_file(home, ".claude/projects", "-proj-/sess.jsonl", rows)
    out = S.parse_claude()
    assert len(out) == 1
    s = out[0]
    # m1 counted once (last snapshot), m2 once
    assert s.tokens["input"] == 503
    assert s.tokens["output"] == 47
    assert s.tokens["cache_read"] == 950
    assert s.tokens["cache_write"] == 300
    assert s.started is not None  # timestamp harvested from records


def test_claude_no_usage_skipped(home):
    _pi_file(home, ".claude/projects", "-proj-/sess.jsonl", [
        {"type": "user", "message": {"role": "user", "content": "hi"}},
    ])
    assert S.parse_claude() == []


def test_claude_subagents_and_crossfile_dedup(home):
    row = {"type": "assistant", "message": {
        "id": "m1", "model": "claude-opus-5",
        "usage": {"input_tokens": 10, "output_tokens": 5}}}
    # main session + a deep subagent transcript + a resumed copy of m1
    _pi_file(home, ".claude/projects", "-proj-/sess.jsonl", [row])
    _pi_file(home, ".claude/projects",
             "-proj-/sess/subagents/workflows/wf_1/agent-a.jsonl", [
                 {"type": "assistant", "message": {
                     "id": "m2", "model": "claude-opus-5",
                     "usage": {"input_tokens": 100, "output_tokens": 50}}}])
    _pi_file(home, ".claude/projects", "-proj-/resumed.jsonl", [row])
    out = S.parse_claude()
    total_in = sum(s.tokens["input"] for s in out)
    assert total_in == 110  # m1 counted once, subagent m2 counted


# --------------------------------------------------------------- codex
def test_codex_sessions_only(home):
    _pi_file(home, ".codex/sessions", "2026/06/30/rollout-x.jsonl", [
        {"type": "session_meta", "payload": {"session_id": "x",
                                             "model_provider": "anyrouter"}},
        {"type": "response_item", "payload": {"type": "message",
                                              "message": {"model": "gpt-5.2"}}},
    ])
    out = S.parse_codex()
    assert len(out) == 1
    assert out[0].tokens == {}  # no token_count events -> session only


def test_codex_token_count_last_total_wins(home):
    def tc(inp, cached, outp):
        return {"type": "event_msg", "timestamp": "2026-06-27T02:18:33.594Z",
                "payload": {"type": "token_count", "info": {
                    "total_token_usage": {
                        "input_tokens": inp, "cached_input_tokens": cached,
                        "output_tokens": outp, "reasoning_output_tokens": 1,
                        "total_tokens": inp + outp}}}}
    _pi_file(home, ".codex/sessions", "2026/06/27/rollout-y.jsonl", [
        {"type": "turn_context", "payload": {"model": "gpt-5.5"}},
        tc(100, 60, 10),
        tc(1000, 900, 40),  # cumulative: the last event is the session total
        {"type": "event_msg", "payload": {"type": "token_count",
                                          "info": None}},  # ignored
    ])
    out = S.parse_codex()
    assert len(out) == 1
    s = out[0]
    assert s.model == "gpt-5.5"
    assert s.tokens == {"input": 100, "cache_read": 900, "output": 40}
    assert s.started is not None


# ------------------------------------------------------------- opencode
def test_opencode_sqlite(home):
    db = home / ".local/share/opencode/opencode.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE session (id TEXT, model TEXT, cost REAL, "
                "tokens_input INTEGER, tokens_output INTEGER, "
                "tokens_reasoning INTEGER, tokens_cache_read INTEGER, "
                "tokens_cache_write INTEGER, time_created INTEGER)")
    con.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)",
                ("s1", '{"id":"deepseek-v4-pro","providerID":"runanytime"}',
                 1.25, 100, 20, 3, 400, 0, 1783995293))
    con.commit()
    con.close()
    out = S.parse_opencode()
    assert len(out) == 1
    s = out[0]
    assert s.model == "deepseek-v4-pro"  # JSON blob unwrapped
    assert s.tokens["cache_read"] == 400
    assert s.recorded_cost == 1.25


# ----------------------------------------------------------------- agy
def test_agy_from_db(home):
    db = home / ".gemini/antigravity-cli/conversation_summaries.db"
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE conversation_summaries (conversation_id TEXT, "
                "last_modified_time TEXT)")
    con.execute("INSERT INTO conversation_summaries VALUES (?,?)",
                ("c1", "2026-07-22 02:54:23.788685282+00:00"))
    con.commit()
    con.close()
    out = S.parse_agy()
    assert len(out) == 1
    expected = datetime.fromisoformat(
        "2026-07-22T02:54:23.788685+00:00").astimezone().replace(tzinfo=None)
    assert out[0].started == expected


def test_agy_fallback_history(home):
    h = home / ".gemini/antigravity-cli/history.jsonl"
    h.parent.mkdir(parents=True)
    h.write_text("\n".join([
        json.dumps({"display": "a", "conversationId": "c1",
                    "timestamp": 1784682505192}),
        json.dumps({"display": "b", "conversationId": "c1",
                    "timestamp": 1784682544716}),
        json.dumps({"display": "c", "conversationId": "c2",
                    "timestamp": 1784682560735}),
    ]))
    out = S.parse_agy()
    assert [s.sid for s in out] == ["c1", "c2"]
    assert out[0].started is not None


# ------------------------------------------------------------ generic
def test_generic_harvests_usage(home):
    root = home / ".local/share/goose/sessions"
    _pi_file(home, ".local/share/goose/sessions", "g1.jsonl", [
        {"role": "assistant", "usage": {"input_tokens": 10, "output_tokens": 5},
         "cost": 0.01},
        {"role": "user", "content": "hi"},
    ])
    out = S.parse_generic("goose", root)
    assert len(out) == 1
    assert out[0].tokens["total"] == 15
    assert out[0].recorded_cost == pytest.approx(0.01)


# ----------------------------------------------------------------- qwen
def test_qwen_ui_telemetry(home):
    _pi_file(home, ".qwen/projects", "-proj-/chats/c1.jsonl", [
        {"type": "user", "timestamp": "2026-02-02T10:38:00.000Z",
         "message": {"content": "hi"}},
        {"type": "system", "subtype": "ui_telemetry",
         "timestamp": "2026-02-02T10:38:39.270Z",
         "systemPayload": {"uiEvent": {
             "event.name": "qwen-code.api_response", "model": "coder-model",
             "input_token_count": 19020, "output_token_count": 9,
             "cached_content_token_count": 20, "thoughts_token_count": 3,
             "tool_token_count": 5}}},
        {"type": "system", "subtype": "ui_telemetry",
         "systemPayload": {"uiEvent": {
             "event.name": "qwen-code.tool_call"}}},  # not an api_response
    ])
    out = S.parse_qwen()
    assert len(out) == 1
    s = out[0]
    assert s.model == "coder-model"
    assert s.tokens == {"input": 19005, "cache_read": 20, "output": 9,
                        "reasoning": 3}
    assert s.started is not None


# -------------------------------------------------------------- cursor
def test_cursor_transcripts(home):
    t = home / ".cursor/projects/my-proj/agent-transcripts/aa/aa.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"role":"user","message":{"content":[]}}\n')
    out = S.parse_cursor()
    assert len(out) == 1
    assert out[0].agent == "cursor"
    assert out[0].started is not None  # file mtime
    assert out[0].tokens == {}


def test_cursor_meta(home):
    m = home / ".cursor/chats/aa/bb/meta.json"
    m.parent.mkdir(parents=True)
    m.write_text(json.dumps({"schemaVersion": 1, "createdAtMs": 1783663864548,
                             "title": "Docker Compose Run"}))
    out = S.parse_cursor()
    assert len(out) == 1
    assert out[0].agent == "cursor"
    assert out[0].started.year == 2026 and out[0].started.month == 7
    assert out[0].tokens == {}


def test_parse_all_registry(home):
    _pi_file(home, ".pi/agent/sessions", "--proj--/s1.jsonl", [
        {"type": "message", "message": {"role": "assistant", "model": "m",
                                        "usage": {"input": 1,
                                                  "cost": {"total": 0}}}}])
    _pi_file(home, ".prime/agent/sessions", "s1.jsonl", [
        {"type": "message", "message": {"role": "assistant", "model": "m",
                                        "usage": {"input": 1,
                                                  "cost": {"total": 0}}}}])
    assert {s.agent for s in S.parse_all()} == {"pi", "primeagent"}


def test_agent_status(home):
    status = {s["agent"]: s["present"] for s in S.agent_status()}
    assert status["pi"] is False  # nothing under tmp home
    assert set(status) == {"pi", "primeagent", "agy", "claude", "codex",
                           "opencode", "cursor", "goose", "qwen", "amp"}
