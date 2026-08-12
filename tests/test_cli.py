"""CLI aggregation tests."""

from datetime import datetime

import pytest

from tokenstats.cli import _aggregate
from tokenstats.pricing import Prices
from tokenstats.sessions import Session


def mk(agent, model, started, tokens, cost=None):
    return Session(agent, "s", model, started, tokens, cost)


def test_aggregate_by_agent():
    prices = Prices()
    sessions = [
        mk("pi", "grok-4", datetime(2026, 1, 1), {"input": 1_000_000}, 2.0),
        mk("pi", "grok-4", datetime(2026, 1, 2), {"output": 1_000_000}),
        mk("claude", "claude-opus-5", datetime(2026, 1, 1), {"input": 500_000}),
    ]
    groups, total = _aggregate(sessions, "agent", prices)
    assert set(groups) == {"pi", "claude"}
    assert groups["pi"]["sessions"] == 2
    assert groups["pi"]["tokens"]["input"] == 1_000_000
    # cost: 2.0 recorded fallback + price-table output 8.0 (grok-4)
    assert groups["pi"]["cost"] == pytest.approx(10.0)
    assert groups["claude"]["cost"] == pytest.approx(2.5)
    assert total["sessions"] == 3
    assert total["cost"] == pytest.approx(12.5)


def test_aggregate_by_model_and_day():
    prices = Prices()
    sessions = [
        mk("pi", "grok-4", datetime(2026, 1, 1), {"input": 1_000_000}),
        mk("pi", "grok-4", datetime(2026, 1, 2), {"input": 1_000_000}),
        mk("claude", "claude-opus-5", datetime(2026, 1, 1), {"input": 1_000_000}),
    ]
    by_model, _ = _aggregate(sessions, "model", prices)
    assert set(by_model) == {"grok-4", "claude-opus-5"}

    by_day, _ = _aggregate(sessions, "day", prices)
    assert set(by_day) == {"2026-01-01", "2026-01-02"}


def test_aggregate_unknown_cost_stays_none():
    prices = Prices()
    sessions = [mk("pi", "no-such-model", None, {"input": 1_000_000})]
    groups, total = _aggregate(sessions, "agent", prices)
    assert groups["pi"]["cost"] is None
    assert total["cost"] is None


def test_aggregate_free_model_zero_cost():
    prices = Prices()
    sessions = [mk("pi", "deepseek-v4-flash-free", None, {"input": 1_000_000})]
    _, total = _aggregate(sessions, "agent", prices)
    assert total["cost"] == 0.0
