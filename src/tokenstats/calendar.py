"""GitHub-style contribution calendar rendering.

Two curated themes (GitHub's own palettes, dark and light) with 5 quantile
levels plus a header stat bar, month labels and a value-aware legend.
"""

from __future__ import annotations

import calendar as _cal
from datetime import date, timedelta

from rich.text import Text

from .pricing import Prices, total_tokens
from .sessions import Session

# GitHub official contribution palettes (hex, truecolor)
THEMES = {
    "dark": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
    "light": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
}


def _fmt_tokens(n: float) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:.0f}"


def _levels(daily: dict[date, float]) -> tuple[dict[date, int], list[float]]:
    """Quantile levels 0..4 (GitHub style) plus the 3 cutoff values."""
    vals = sorted(v for v in daily.values() if v > 0)
    n = len(vals)
    if n == 0:
        return {d: 0 for d in daily}, [0.0, 0.0, 0.0]
    q = [vals[(n - 1) * i // 4] for i in (1, 2, 3)]

    def level(v):
        if v <= 0:
            return 0
        if v <= q[0]:
            return 1
        if v <= q[1]:
            return 2
        if v <= q[2]:
            return 3
        return 4

    return {d: level(v) for d, v in daily.items()}, q


def render(sessions: list[Session], prices: Prices, metric: str, weeks: int,
           theme: str, console) -> None:
    daily: dict[date, float] = {}
    for s in sessions:
        if not s.started:
            continue
        v = prices.cost(s) if metric == "cost" else total_tokens(s)
        if v:
            daily[s.started.date()] = daily.get(s.started.date(), 0.0) + v
    if not daily:
        console.print("[dim]No dated sessions to draw a calendar.[/]")
        return

    end = max(daily)
    start = end - timedelta(weeks=weeks - 1)
    start -= timedelta(days=(start.weekday() + 1) % 7)  # back to Sunday
    lv, cutoffs = _levels(daily)
    wide = console.width >= 120  # 2-char cells on wide terminals
    cell_w = 2 if wide else 1
    colors = THEMES[theme]

    # --------------------------------------------------------- stat bar
    total = sum(daily.values())
    peak = max(daily, key=daily.get)
    unit = "tokens" if metric == "tokens" else "USD"
    stats = Text()
    stats.append("  ")
    for label, val, style in (
        ("Sessions", f"{len(sessions)}", "cyan"),
        (unit, _fmt_tokens(total), "bold green" if metric == "tokens"
         else "bold yellow"),
        ("Active days", f"{len(daily)}", "magenta"),
        ("Peak", f"{peak} ({_fmt_tokens(daily[peak])})", "bright_blue"),
    ):
        stats.append(f"{label} ", style="dim")
        stats.append(f"{val}   ", style=style)
    console.print(stats)
    console.print()

    # --------------------------------------------------- month labels
    month_line = [" "] * (cell_w * weeks)
    prev = None
    for w in range(weeks):
        d = start + timedelta(weeks=w)
        if prev is None or d.month != prev.month:
            lab = _cal.month_abbr[d.month]
            x = w * cell_w
            for i, ch in enumerate(lab):
                if x + i < len(month_line) and month_line[x + i] == " ":
                    month_line[x + i] = ch
        prev = d

    console.print(Text("      " + "".join(month_line), style="bold dim"))
    label_w = 3 if wide else 4
    for r, dow in enumerate(["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]):
        line = Text()
        line.append(dow.ljust(label_w) if dow in ("Sun", "Wed", "Fri") else " " * label_w)
        line.append(" ")
        for w in range(weeks):
            d = start + timedelta(weeks=w, days=r)
            level = lv.get(d, 0)
            if wide:
                line.append("  ", style=f"on {colors[level]}")
            else:
                line.append("█" if level else "·", style=colors[level] if level
                            else colors[0])
        console.print(line)

    # ---------------------------------------------------------- legend
    legend = Text("  Less ")
    for i in range(5):
        cell = "  " if wide else "█"
        legend.append(cell, style=f"on {colors[i]}" if wide else colors[i])
    legend.append(" More  ", style="dim")
    prefix = "$" if metric == "cost" else ""
    legend.append(f"cutoffs {prefix}{_fmt_tokens(cutoffs[0])} / "
                  f"{prefix}{_fmt_tokens(cutoffs[1])} / "
                  f"{prefix}{_fmt_tokens(cutoffs[2])}+", style="dim")
    console.print(legend)
    console.print()
