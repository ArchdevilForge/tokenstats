"""Pricing table and cost calculation tests."""

from tokenstats.pricing import Prices, total_tokens
from tokenstats.sessions import Session


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
