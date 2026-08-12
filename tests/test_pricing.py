"""Pricing table and cost calculation tests."""

import json

import pytest

from tokenstats import pricing
from tokenstats.pricing import Prices, total_tokens
from tokenstats.sessions import Session


@pytest.fixture(autouse=True)
def fake_litellm_cache(tmp_path, monkeypatch):
    """Point the LiteLLM cache at a fresh empty file so tests never fetch."""
    f = tmp_path / "litellm.json"
    f.write_text("{}")
    monkeypatch.setattr(pricing, "CACHE_FILE", f)
    return f


def s(model, tokens, recorded_cost=None):
    return Session("t", "t", model, tokens=tokens, recorded_cost=recorded_cost)


def test_exact_and_prefix_match():
    p = Prices()
    assert p.cost(s("claude-opus-5", {"input": 1_000_000})) == 5.0
    assert p.cost(s("claude-opus-5-20260230", {"output": 1_000_000})) == 25.0


def test_provider_prefix_match():
    p = Prices()
    assert p.cost(s("deepseek/deepseek-v4-pro", {"input": 1_000_000})) == 0.27
    assert p.cost(s("cline-pass/deepseek-v4-flash", {"input": 1_000_000})) == 0.27


def test_cache_factors():
    p = Prices()
    assert p.cost(s("claude-opus-5", {"cache_read": 1_000_000})) == 0.5
    assert p.cost(s("claude-opus-5", {"cache_write": 1_000_000})) == 6.25


def test_free_models_cost_zero():
    p = Prices()
    assert p.cost(s("deepseek-v4-flash-free", {"input": 1_000_000})) == 0.0


def test_unknown_model_falls_back_to_recorded():
    p = Prices()
    assert p.cost(s("glm-5.2", {"input": 1_000_000})) is None
    assert p.cost(s("glm-5.2", {"input": 1_000_000}, recorded_cost=1.0)) == 1.0


def test_cost_modes():
    sess = s("grok-4", {"input": 1_000_000}, recorded_cost=0.5)
    # auto: the agent's own recorded cost wins over the price table
    assert Prices().cost(sess) == 0.5
    # calculate: always the price table (grok-4 input = 2.0/M)
    assert Prices(mode="calculate").cost(sess) == 2.0
    assert Prices(mode="calculate").cost(
        s("glm-5.2", {"input": 1}, recorded_cost=1.0)) is None
    # display: only recorded costs, never calculates
    assert Prices(mode="display").cost(sess) == 0.5
    assert Prices(mode="display").cost(s("grok-4", {"input": 1})) is None


def test_litellm_exact_beats_builtin_prefix(fake_litellm_cache):
    fake_litellm_cache.write_text(json.dumps({
        "gpt-5.5": {"input_cost_per_token": 1.25e-6,
                    "output_cost_per_token": 1e-5,
                    "cache_read_input_token_cost": 1.25e-7},
        "bogus": {"litellm_provider": "x"},  # no prices -> skipped
    }))
    p = Prices.load(None)
    assert p.cost(s("gpt-5.5", {"input": 1_000_000})) == 1.25
    assert p.cost(s("gpt-5.5", {"cache_read": 1_000_000})) == 0.125
    # unlisted variants still fall back to the built-in "gpt-5" prefix
    assert p.cost(s("gpt-5-nano-x", {"input": 1_000_000})) == 1.5


def test_user_prefix_beats_litellm_exact(fake_litellm_cache, tmp_path):
    fake_litellm_cache.write_text(json.dumps({
        "claude-fable-5": {"input_cost_per_token": 5e-6,
                           "output_cost_per_token": 2.5e-5}}))
    f = tmp_path / "prices.toml"
    f.write_text('[models."claude-fable"]\ninput = 0.0\noutput = 0.0\n')
    p = Prices.load(f)
    assert p.cost(s("claude-fable-5", {"input": 1_000_000})) == 0.0


def test_user_overrides_defaults(tmp_path):
    f = tmp_path / "prices.toml"
    f.write_text('[models."claude-opus"]\ninput = 9.0\noutput = 9.0\n')
    p = Prices.load(f)
    assert p.cost(s("claude-opus-5", {"input": 1_000_000})) == 9.0


def test_user_json_prices(tmp_path):
    f = tmp_path / "prices.json"
    f.write_text('{"models": {"grok-4": {"input": 3.0, "output": 9.0}}}')
    p = Prices.load(f)
    assert p.cost(s("grok-4.5", {"input": 1_000_000})) == 3.0


def test_case_insensitive(tmp_path):
    f = tmp_path / "prices.toml"
    f.write_text('[models."Custom-Model"]\ninput = 1.0\noutput = 2.0\n')
    p = Prices.load(f)
    assert p.cost(s("custom-model", {"input": 1_000_000})) == 1.0


def test_total_tokens():
    sess = s("m", {"input": 1, "output": 2, "cache_read": 3, "cache_write": 4,
                   "reasoning": 5})
    assert total_tokens(sess) == 15
