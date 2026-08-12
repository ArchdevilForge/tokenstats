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


class Prices:
    def __init__(self, table: dict | None = None):
        # normalize: lower-case keys, strip model suffixes are handled at match
        self.table = {k.lower(): v for k, v in (table or DEFAULT_PRICES).items()}

    @classmethod
    def load(cls, path: Path | None) -> "Prices":
        if path is None:
            return cls()
        raw = path.read_text()
        if path.suffix == ".json":
            data = json.loads(raw)
        else:
            data = tomllib.loads(raw)
        user = {k.lower(): v for k, v in data.get("models", {}).items()}
        merged = {**DEFAULT_PRICES, **user}
        return cls(merged)

    def _match(self, model: str) -> dict | None:
        m = (model or "").lower()
        if m in self.table:
            return self.table[m]
        # strip provider prefixes like "cline-pass/" or "deepseek/"
        base = m.split("/")[-1]
        hits = [k for k in self.table
                if m.startswith(k) or base.startswith(k)]
        return self.table[max(hits, key=len)] if hits else None

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
        """Cost in USD for a session, or None when unknowable."""
        if not s.tokens:
            return None
        if "free" in (s.model or "").lower():
            return 0.0  # free-tier models cost nothing
        up = self.unit_prices(s.model)
        if up is None:
            return s.recorded_cost  # fall back to what the agent recorded
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
