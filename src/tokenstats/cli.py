"""tokenstats — aggregate token usage and cost across local agent CLIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from .calendar import render as render_calendar
from .pricing import Prices, total_tokens
from .sessions import Session, agent_status, parse_all

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()



def _group_key(s: Session, by: str) -> str:
    if by == "model":
        return s.model or "unknown"
    if by == "day":
        return s.started.date().isoformat() if s.started else "unknown"
    return s.agent


def _aggregate(sessions: list[Session], by: str, prices: Prices) -> tuple[dict, dict]:
    groups: dict[str, dict] = {}
    for s in sessions:
        k = _group_key(s, by)
        g = groups.setdefault(k, {"sessions": 0, "tokens": {}, "cost": None})
        g["sessions"] += 1
        for b, v in s.tokens.items():
            g["tokens"][b] = g["tokens"].get(b, 0) + v
        c = prices.cost(s)
        if c is not None:
            g["cost"] = (g["cost"] or 0.0) + c
    total = {"sessions": 0, "tokens": {}, "cost": None}
    for g in groups.values():
        total["sessions"] += g["sessions"]
        for b, v in g["tokens"].items():
            total["tokens"][b] = total["tokens"].get(b, 0) + v
        if g["cost"] is not None:
            total["cost"] = (total["cost"] or 0.0) + g["cost"]
    return groups, total


def _cost_style(v: float | None) -> str:
    if v is None:
        return "dim"
    if v >= 100:
        return "bold red"
    if v >= 10:
        return "yellow"
    if v > 0:
        return "green"
    return "dim"


def _fmt_cost(v: float | None) -> Text:
    txt = "—" if v is None else f"${v:,.2f}"
    return Text(txt, style=_cost_style(v))


def _fmt_tokens(n: int) -> str:
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.0f}K"
    return str(n)


def _print_table(groups: dict, total: dict, by: str) -> None:
    buckets = ("input", "cache_read", "cache_write", "output", "reasoning")
    header = {"agent": "Agent", "day": "Day", "model": "Model"}[by]
    t = Table(
        title=f"[bold]Token usage by {by}[/]",
        title_justify="left",
        box=None,
        header_style="bold",
        show_edge=False,
        padding=(0, 1),
    )
    t.add_column(header, no_wrap=True)
    t.add_column("Sessions", justify="right", style="cyan")
    for b in buckets:
        t.add_column(b.replace("_", " "), justify="right")
    t.add_column("Total", justify="right", style="bold")
    t.add_column("Cost", justify="right")

    def row(g: dict, key: str, highlight: bool = False) -> None:
        toks = g["tokens"]
        cells = [str(g["sessions"])]
        for b in buckets:
            cells.append(_fmt_tokens(toks.get(b, 0)))
        cells.append(_fmt_tokens(sum(toks.values())))
        style = "bold" if highlight else None
        t.add_row(key, *cells, _fmt_cost(g["cost"]), style=style)

    for k in sorted(groups):
        row(groups[k], k)
    t.add_section()
    row(total, "TOTAL", highlight=True)
    console.print(t)


# -------------------------------------------------------------- command
# KISS: `tokenstats` shows the per-agent table; a positional verb switches
# view: model | day | cal | agents
VERBS = ("agent", "model", "day", "cal", "agents")


def _load(since: int | None, dirs: list[Path]) -> list[Session]:
    sessions = parse_all(dirs)
    if since:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=since)
        sessions = [s for s in sessions if s.started is None or s.started >= cutoff]
    return sessions


@app.command()
def main(
    verb: str = typer.Argument("agent", help="agent | model | day | cal | agents"),
    since: int | None = typer.Option(None, "--since", help="Only the last N days"),
    prices: Path | None = typer.Option(None, "--prices", help="Price table (TOML/JSON)"),
    dirs: list[Path] = typer.Option([], "--dirs", help="Project dirs to scan for aider"),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON"),
    metric: str = typer.Option("tokens", "--metric", help="Calendar metric: tokens | cost"),
    weeks: int = typer.Option(52, "--weeks", help="Calendar span in weeks"),
    theme: str = typer.Option("dark", "--theme", help="Calendar theme: dark | light"),
    verbose: bool = typer.Option(False, "-v", help="Warn about unknown models"),
) -> None:
    if verb not in VERBS:
        raise typer.BadParameter(f"verb must be one of {', '.join(VERBS)}")
    if metric not in ("tokens", "cost"):
        raise typer.BadParameter("--metric must be tokens or cost")
    if theme not in ("dark", "light"):
        raise typer.BadParameter("--theme must be dark or light")

    if verb == "agents":
        t = Table(title="[bold]Detected agents[/]", title_justify="left",
                  header_style="bold", box=None, show_edge=False)
        t.add_column("Agent", style="cyan")
        t.add_column("Data source")
        for s in agent_status():
            t.add_row(s["agent"], "yes" if s["present"] else "no")
        console.print(t)
        raise typer.Exit()

    sessions = _load(since, dirs)
    prices_obj = Prices.load(prices)

    if verb == "cal":
        # KISS: a --since filter implies a matching calendar window
        if since and weeks == 52:
            weeks = since // 7 + 1
        render_calendar(sessions, prices_obj, metric, weeks, theme, console)
        raise typer.Exit()

    groups, total = _aggregate(sessions, verb, prices_obj)

    if json_out:
        import json

        payload = {
            "by": verb,
            "groups": {
                k: {
                    "sessions": g["sessions"],
                    "tokens": g["tokens"],
                    "cost": round(g["cost"], 6) if g["cost"] is not None else None,
                }
                for k, g in groups.items()
            },
            "total": {
                "sessions": total["sessions"],
                "tokens": total["tokens"],
                "cost": round(total["cost"], 6) if total["cost"] is not None else None,
            },
        }
        console.print_json(data=payload)
    else:
        if not groups:
            console.print("[dim]No usage recorded. Install one of the supported "
                          "agents first, or run `tokenstats agents`.[/]")
        else:
            _print_table(groups, total, verb)

    if verbose:
        unknown = prices_obj.unknown_models(sessions)
        if unknown:
            console.print(
                f"[yellow]No price for model(s): {', '.join(sorted(unknown))}. "
                "Add them via --prices (see pricing.py for the format).[/]"
            )


if __name__ == "__main__":
    app()
