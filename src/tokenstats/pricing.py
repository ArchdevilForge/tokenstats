"""Model pricing table (USD per 1M tokens) and per-session cost.

Built-in prices are official API list prices as of early 2026 — an
approximation. Override with `--prices prices.toml`:

    [models."claude-opus-5"]
    input = 5.0
    output = 25.0
    cache_read = 0.5
    cache_write = 6.25

Models not in the table fall back to the cost recorded by the agent itself
(pi/opencode record one); if neither exists the cost is unknown.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from pathlib import Path

from .sessions import Session

# $ per 1M tokens, input/output. cache defaults apply unless configured:
# cache_read = 0.1x input, cache_write = 1.25x input (Anthropic's rule).
DEFAULT_PRICES: dict[str, dict] = {
    "claude-opus": {"input": 5.0, "output": 25.0},
    "claude-sonnet": {"input": 3.0, "output": 15.0},
    "gpt-5": {"input": 1.5, "output": 6.0},
    "codex": {"input": 1.5, "output": 6.0},
    "gemini-3-pro": {"input": 2.0, "output": 12.0},
    "grok-4": {"input": 2.0, "output": 8.0},
    "deepseek": {"input": 0.27, "output": 1.10},
    "qwen3": {"input": 0.20, "output": 1.20},
}

CACHE_READ_FACTOR = 0.10
CACHE_WRITE_FACTOR = 1.25

TOKEN_BUCKETS = ("input", "output", "cache_read", "cache_write", "reasoning")

# LiteLLM's community-maintained per-model price DB (same source ccusage
# uses). Fetched at most once a week, cached locally, degrades silently to
# the built-in table when offline.
LITELLM_URL = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
               "model_prices_and_context_window.json")
CACHE_FILE = Path(os.environ.get("XDG_CACHE_HOME",
                                 Path.home() / ".cache")) / "tokenstats" / "litellm.json"
CACHE_MAX_AGE = 7 * 86400


def _refresh_cache() -> None:
    import urllib.request

    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(LITELLM_URL, timeout=5) as r:
        data = r.read()
    json.loads(data)  # validate before overwriting a good cache
    CACHE_FILE.write_bytes(data)


def litellm_table() -> dict:
    """Cached LiteLLM prices converted to our per-1M-token format."""
    stale = (not CACHE_FILE.exists()
             or time.time() - CACHE_FILE.stat().st_mtime > CACHE_MAX_AGE)
    if stale:
        try:
            _refresh_cache()
        except Exception:
            # ponytail: on failure, touch the stale cache so we retry weekly
            # instead of paying the 5s timeout on every offline run
            if CACHE_FILE.exists():
                CACHE_FILE.touch()
    try:
        raw = json.loads(CACHE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out = {}
    for name, p in raw.items():
        if not isinstance(p, dict):
            continue
        i, o = p.get("input_cost_per_token"), p.get("output_cost_per_token")
        if not (isinstance(i, (int, float)) and isinstance(o, (int, float))):
            continue
        d = {"input": i * 1e6, "output": o * 1e6}
        cr = p.get("cache_read_input_token_cost")
        cw = p.get("cache_creation_input_token_cost")
        if isinstance(cr, (int, float)):
            d["cache_read"] = cr * 1e6
        if isinstance(cw, (int, float)):
            d["cache_write"] = cw * 1e6
        out[name.lower()] = d
    return out


class Prices:
    def __init__(self, table: dict | None = None, user: dict | None = None,
                 mode: str = "auto"):
        # normalize: lower-case keys, strip model suffixes are handled at match
        self.table = {k.lower(): v for k, v in (table or DEFAULT_PRICES).items()}
        # user keys form a separate tier so a user prefix like "claude-opus"
        # beats an exact LiteLLM key like "claude-opus-5"
        self.user = {k.lower(): v for k, v in (user or {}).items()}
        self.mode = mode  # auto | calculate | display (ccusage semantics)
        self._memo: dict[str, dict | None] = {}

    @classmethod
    def load(cls, path: Path | None, mode: str = "auto") -> "Prices":
        user = {}
        if path is not None:
            raw = path.read_text()
            if path.suffix == ".json":
                data = json.loads(raw)
            else:
                data = tomllib.loads(raw)
            user = data.get("models", {})
        table = DEFAULT_PRICES if mode == "display" \
            else {**DEFAULT_PRICES, **litellm_table()}
        return cls(table, user, mode)

    @staticmethod
    def _lookup(table: dict, m: str, base: str) -> dict | None:
        hit = table.get(m) or table.get(base)
        if hit is None:
            hits = [k for k in table if m.startswith(k) or base.startswith(k)]
            hit = table[max(hits, key=len)] if hits else None
        return hit

    def _match(self, model: str) -> dict | None:
        m = (model or "").lower()
        if m in self._memo:
            return self._memo[m]
        # strip provider prefixes like "cline-pass/" or "deepseek/"
        base = m.split("/")[-1]
        hit = self._lookup(self.user, m, base) or self._lookup(self.table, m, base)
        self._memo[m] = hit
        return hit

    def unit_prices(self, model: str) -> dict | None:
        p = self._match(model)
        if p is None:
            return None
        i = p["input"]
        return {
            "input": i,
            "output": p["output"],
            "cache_read": p.get("cache_read", i * CACHE_READ_FACTOR),
            "cache_write": p.get("cache_write", i * CACHE_WRITE_FACTOR),
            "reasoning": p.get("reasoning", p["output"]),
        }

    def cost(self, s: Session) -> float | None:
        """Cost in USD for a session, or None when unknowable.

        Modes follow ccusage semantics:
          auto      — the cost the agent itself recorded wins (it reflects
                      actual billing, incl. free/discounted routing);
                      calculate from the price table only when missing
          calculate — always from the price table, ignore recorded costs
          display   — only recorded costs, never calculate
        """
        if self.mode == "display":
            return s.recorded_cost
        if not s.tokens:
            return s.recorded_cost if self.mode == "auto" else None
        if "free" in (s.model or "").lower():
            return 0.0  # free-tier models cost nothing
        if self.mode == "auto" and s.recorded_cost is not None:
            return s.recorded_cost
        up = self.unit_prices(s.model)
        if up is None:
            return s.recorded_cost if self.mode == "auto" else None
        total = 0.0
        for bucket, price in up.items():
            total += (s.tokens.get(bucket) or 0) / 1e6 * price
        return total

    def unknown_models(self, sessions: list[Session]) -> set[str]:
        return {s.model for s in sessions
                if s.tokens and "free" not in (s.model or "").lower()
                and self._match(s.model) is None}


def total_tokens(s: Session) -> int:
    return sum(s.tokens.get(k) or 0 for k in TOKEN_BUCKETS)
