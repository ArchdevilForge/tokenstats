"""Calendar level and layout tests."""

from datetime import date

from tokenstats.calendar import _fmt_tokens, _levels, THEMES


def test_levels_empty():
    assert _levels({date(2026, 1, 1): 0.0}) == ({date(2026, 1, 1): 0}, [0, 0, 0])


def test_levels_single_day():
    lv, cut = _levels({date(2026, 1, 1): 5.0})
    assert lv == {date(2026, 1, 1): 1}


def test_levels_quantiles():
    days = {date(2026, 1, d): float(v)
            for d, v in ((1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0), (5, 0.0))}
    lv, cut = _levels(days)
    assert [lv[date(2026, 1, d)] for d in (1, 2, 3, 4, 5)] == [1, 2, 3, 4, 0]
    assert cut == [1.0, 2.0, 3.0]


def test_levels_ties_collapse():
    days = {date(2026, 1, 1): 1.0, date(2026, 1, 2): 1.0}
    lv, _ = _levels(days)
    assert all(v == 1 for v in lv.values())


def test_levels_scale_invariant():
    """Levels depend on relative spread, not magnitude."""
    small = {date(2026, 1, d): float(v)
             for d, v in ((1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0))}
    big = {date(2026, 1, d): float(v * 1e6)
           for d, v in ((1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0))}
    assert _levels(small)[0] == _levels(big)[0]


def test_fmt_tokens():
    assert _fmt_tokens(0) == "0"
    assert _fmt_tokens(999) == "999"
    assert _fmt_tokens(12_345) == "12.3K"
    assert _fmt_tokens(1_234_567) == "1.23M"
    assert _fmt_tokens(2e9) == "2.00B"


def test_themes_present():
    for theme in ("dark", "light"):
        assert len(THEMES[theme]) == 5
        assert all(c.startswith("#") for c in THEMES[theme])
