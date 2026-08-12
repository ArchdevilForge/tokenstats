"""Per-agent session parsers. Each yields Session objects by scanning the
agent's local data directory. Parsers are defensive: a malformed line or a
missing data source never crashes the whole run.

Verified locally: pi, claude, codex, opencode, gemini.
Best-effort (format not verified on this machine): aider, goose, qwen, amp.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HOME = Path.home()

# token bucket keys; cost is USD
TOKEN_KEYS = ("input", "output", "cache_read", "cache_write", "reasoning")


@dataclass
class Session:
    agent: str
    sid: str
    model: str = "unknown"
    started: datetime | None = None
    tokens: dict = field(default_factory=dict)  # {bucket: int}
    recorded_cost: float | None = None  # cost as recorded by the agent itself


def _num(v) -> int:
    """Coerce json values to int; nested dicts (some claude versions) -> 0."""
    if isinstance(v, bool):
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    return 0


def _ts(ms: int | float | None) -> datetime | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000)
    except (ValueError, OSError, OverflowError):
        return None


def _iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        # convert to naive *local* time: epoch-based parsers (_ts) already
        # return local time, so day grouping stays consistent across agents
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone().replace(tzinfo=None)
    except ValueError:
        return None


def _jsonl(path: Path):
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


# ---------------------------------------------------------------- pi
# prime-agent is a pi fork that shares the exact same session format
def _parse_pi_like(root: Path, agent: str) -> list[Session]:
    # pi nests sessions one dir deep; prime-agent stores them flat
    files = sorted(list(root.glob("*.jsonl")) + list(root.glob("*/*.jsonl")))
    out = []
    for sfile in files:
        tokens: dict = {}
        cost = 0.0
        model = "unknown"
        started = None
        for d in _jsonl(sfile):
            t = d.get("type")
            if t == "session" and started is None:
                started = _iso(d.get("timestamp"))
            elif t == "message":
                m = d.get("message") or {}
                if m.get("role") != "assistant":
                    continue
                if m.get("model"):
                    model = m["model"]
                u = m.get("usage") or {}
                for k, key in (("input", "input"), ("output", "output"),
                               ("cache_read", "cacheRead"), ("cache_write", "cacheWrite"),
                               ("reasoning", "reasoning")):
                    v = u.get(key)
                    if isinstance(v, (int, float)) and not isinstance(v, bool):
                        tokens[k] = tokens.get(k, 0) + int(v)
                c = u.get("cost") or {}
                if isinstance(c.get("total"), (int, float)):
                    cost += float(c["total"])
        if tokens or cost:
            if any(tokens.values()) or cost:
                out.append(Session(agent, sfile.stem, model, started,
                                   tokens, cost or None))
    return out


def parse_pi() -> list[Session]:
    return _parse_pi_like(HOME / ".pi" / "agent" / "sessions", "pi")


def parse_primeagent() -> list[Session]:
    return _parse_pi_like(HOME / ".prime" / "agent" / "sessions", "primeagent")


# ---------------------------------------------------------- claude code
def parse_claude() -> list[Session]:
    root = HOME / ".claude" / "projects"
    out = []
    for sfile in sorted(root.glob("*/*.jsonl")):
        # streamed snapshots repeat the same message id with growing usage;
        # keep the last occurrence per id, then sum
        usage_by_id: dict[str, dict] = {}
        model_by_id: dict[str, str] = {}
        started = None
        for d in _jsonl(sfile):
            if started is None:
                started = _iso(d.get("timestamp"))
            if d.get("type") in ("message", "assistant"):
                m = d.get("message") or {}
                u = m.get("usage")
                if u and isinstance(u, dict):
                    usage_by_id[m.get("id")] = u
                    if m.get("model"):
                        model_by_id[m.get("id")] = m["model"]
        if not usage_by_id:
            continue
        tokens = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        cost = 0.0
        model = "unknown"
        for mid, u in usage_by_id.items():
            tokens["input"] += _num(u.get("input_tokens"))
            tokens["output"] += _num(u.get("output_tokens"))
            # new format: cache_creation_input_tokens / cache_read_input_tokens
            # old format: cache_creation / cache_read (+ nested ephemeral_*)
            cw = (u.get("cache_creation_input_tokens")
                  or u.get("cache_creation") or {})
            cr = (u.get("cache_read_input_tokens")
                  or u.get("cache_read") or 0)
            tokens["cache_write"] += _num(cw) + sum(
                _num(v) for v in cw.values()) if isinstance(cw, dict) else _num(cw)
            tokens["cache_read"] += _num(cr)
            if u.get("cost"):
                cost += float(u["cost"])
            if mid in model_by_id:
                model = model_by_id[mid]
        out.append(Session("claude", sfile.parent.name, model, started,
                           tokens, cost or None))
    return out


# --------------------------------------------------------------- codex
# Codex rollouts do not record token usage (verified on this machine), so we
# only count sessions and remember the model.
def parse_codex() -> list[Session]:
    root = HOME / ".codex" / "sessions"
    out = []
    for sfile in sorted(root.glob("**/*.jsonl")):
        model = "unknown"
        for d in _jsonl(sfile):
            p = d.get("payload") or {}
            m = p.get("model") or d.get("model")
            if m:
                model = m
                break
        out.append(Session("codex", sfile.name, model))
    return out


# ------------------------------------------------------------- opencode
def parse_opencode() -> list[Session]:
    db = HOME / ".local" / "share" / "opencode" / "opencode.db"
    if not db.exists():
        return []
    out = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT id, model, cost, tokens_input, tokens_output, "
            "tokens_reasoning, tokens_cache_read, tokens_cache_write, "
            "time_created FROM session"
        )
        for sid, model, cost, ti, to, tr, tcr, tcw, ts in rows:
            tokens = {
                "input": ti or 0, "output": to or 0,
                "reasoning": tr or 0,
                "cache_read": tcr or 0, "cache_write": tcw or 0,
            }
            # opencode stores the model as a JSON blob when using custom
            # providers; extract the id
            if isinstance(model, str) and model.lstrip().startswith("{"):
                try:
                    model = (json.loads(model) or {}).get("id") or model
                except json.JSONDecodeError:
                    pass
            out.append(Session(
                "opencode", sid, model or "unknown", _ts(ts), tokens,
                float(cost) if cost is not None else None,
            ))
        con.close()
    except sqlite3.Error:
        pass
    return out


# --------------------------------------------------------------- agy
# agy (a.k.a. Gemini CLI / antigravity) records no token usage; the
# conversation_summaries.db holds one row per conversation with timestamps.
# Falls back to history.jsonl (user prompts) when the db is missing.
def _parse_agy_time(ts: str | None) -> datetime | None:
    if not ts or ts.startswith("0001-"):
        return None
    return _iso(ts)


def parse_agy() -> list[Session]:
    db = HOME / ".gemini" / "antigravity-cli" / "conversation_summaries.db"
    if db.exists():
        out = []
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            rows = con.execute(
                "SELECT conversation_id, last_modified_time "
                "FROM conversation_summaries")
            for cid, ts in rows:
                out.append(Session("agy", cid, started=_parse_agy_time(ts)))
            con.close()
            return out
        except sqlite3.Error:
            pass
    # fallback: count unique conversations in history.jsonl
    hfile = HOME / ".gemini" / "antigravity-cli" / "history.jsonl"
    seen: dict[str, datetime | None] = {}
    for d in _jsonl(hfile):
        cid = d.get("conversationId")
        if cid:
            seen.setdefault(cid, _ts(d.get("timestamp")))
    return [Session("agy", cid, started=t) for cid, t in seen.items()]


# ------------------------------------------------------- generic jsonl
# Best-effort parser for agents without a documented local format here:
# counts one session per file, harvests any usage/token numbers it can find.
def _harvest_usage(obj, acc: dict, cost: list[float]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                if kl == "cost":
                    cost.append(float(v))
                elif "token" in kl:
                    acc["total"] = acc.get("total", 0) + int(v)
            else:
                _harvest_usage(v, acc, cost)
    elif isinstance(obj, list):
        for v in obj:
            _harvest_usage(v, acc, cost)


def parse_generic(name: str, root: Path) -> list[Session]:
    if not root.exists():
        return []
    out = []
    for sfile in sorted(root.glob("**/*.jsonl")):
        acc: dict = {}
        cost: list[float] = []
        started = None
        for d in _jsonl(sfile):
            _harvest_usage(d, acc, cost)
            if started is None:
                for k in ("timestamp", "time_created", "ts"):
                    v = d.get(k)
                    if isinstance(v, (int, float)):
                        started = _ts(v)
                        break
        tokens = {"total": acc.get("total", 0)} if acc else {}
        out.append(Session(name, sfile.name, started=started, tokens=tokens,
                           recorded_cost=sum(cost) or None))
    return out


def parse_aider(dirs: list[Path]) -> list[Session]:
    """Aider keeps per-project .aider.tokens.json; scan the given project dirs."""
    out = []
    for d in dirs:
        for tf in sorted(d.glob(".aider.tokens.json")) + \
                  sorted(d.glob("**/.aider.tokens.json")):
            try:
                data = json.loads(tf.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            acc: dict = {}
            _harvest_usage(data, acc, [])
            out.append(Session("aider", str(tf.parent),
                               tokens={"total": acc.get("total", 0)}))
    return out


# -------------------------------------------------------------- cursor
# Cursor CLI keeps one dir per chat with a meta.json (title + timestamps).
# No token/usage is stored locally (billing lives server-side), so we count
# sessions and remember the date only.
def parse_cursor() -> list[Session]:
    out = []
    for mfile in sorted((HOME / ".cursor" / "chats").glob("*/*/meta.json")):
        try:
            d = json.loads(mfile.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        ts = d.get("createdAtMs") or d.get("updatedAtMs")
        out.append(Session("cursor", d.get("title", "?")[:40],
                           started=_ts(ts)))
    return out


# ------------------------------------------------------------ registry
def parse_all(extra_dirs: list[Path] | None = None) -> list[Session]:
    sessions = []
    sessions += parse_pi()
    sessions += parse_primeagent()
    sessions += parse_claude()
    sessions += parse_codex()
    sessions += parse_opencode()
    sessions += parse_agy()
    sessions += parse_cursor()
    sessions += parse_generic("goose", HOME / ".local" / "share" / "goose" / "sessions")
    sessions += parse_generic("qwen", HOME / ".qwen" / "sessions")
    sessions += parse_generic("amp", HOME / ".local" / "share" / "amp")
    sessions += parse_aider(extra_dirs or [])
    return sessions


def agent_status() -> list[dict]:
    """Data-source presence per agent, for --list."""
    sources = {
        "pi": HOME / ".pi" / "agent" / "sessions",
        "primeagent": HOME / ".prime" / "agent" / "sessions",
        "claude": HOME / ".claude" / "projects",
        "codex": HOME / ".codex" / "sessions",
        "opencode": HOME / ".local" / "share" / "opencode" / "opencode.db",
        "cursor": HOME / ".cursor" / "chats",
        "agy": HOME / ".gemini" / "antigravity-cli" / "conversation_summaries.db",
        "goose": HOME / ".local" / "share" / "goose" / "sessions",
        "qwen": HOME / ".qwen" / "sessions",
        "amp": HOME / ".local" / "share" / "amp",
    }
    return [{"agent": a, "present": p.exists()} for a, p in sources.items()]
