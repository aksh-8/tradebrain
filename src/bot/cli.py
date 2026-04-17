from __future__ import annotations

import argparse
import sys

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from bot.intake import parse_intake
from bot.engine import run
from bot.models import ResearchResult, Pick

console = Console()


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _print_research(r: ResearchResult) -> None:
    lines = []

    # price block
    price_str = f"${r.price:.2f}" if r.price else "unavailable"
    lines.append(f"[bold]Price[/bold]        {price_str}")

    if r.price_change_5d is not None:
        arrow = "↑" if r.price_change_5d >= 0 else "↓"
        color = "green" if r.price_change_5d >= 0 else "red"
        lines.append(f"[bold]5-day move[/bold]   [{color}]{arrow} {abs(r.price_change_5d):.1f}%[/{color}]")

    if r.price_change_1m is not None:
        arrow = "↑" if r.price_change_1m >= 0 else "↓"
        color = "green" if r.price_change_1m >= 0 else "red"
        lines.append(f"[bold]1-month move[/bold] [{color}]{arrow} {abs(r.price_change_1m):.1f}%[/{color}]")

    if r.week_52_high and r.week_52_low:
        lines.append(f"[bold]52w range[/bold]    ${r.week_52_low:.2f} – ${r.week_52_high:.2f}")

    if r.iv_rank is not None:
        iv_color = "red" if r.iv_rank > 70 else "yellow" if r.iv_rank > 40 else "green"
        iv_note  = "expensive" if r.iv_rank > 70 else "moderate" if r.iv_rank > 40 else "cheap"
        lines.append(f"[bold]IV rank[/bold]      [{iv_color}]{r.iv_rank:.0f}/100 ({iv_note})[/{iv_color}]")

    if r.earnings_days_away is not None:
        e_color = "yellow" if r.earnings_days_away < 14 else "dim"
        lines.append(f"[bold]Earnings[/bold]     [{e_color}]in {r.earnings_days_away} days[/{e_color}]")

    if r.avg_volume:
        lines.append(f"[bold]Avg volume[/bold]   {r.avg_volume:,}")

    # news
    if r.news_summary:
        lines.append("")
        lines.append(f"[bold]News[/bold]")
        for headline in r.news_summary.split(" | "):
            lines.append(f"  [dim]· {headline.strip()}[/dim]")

    # thesis verdict
    if r.thesis_verdict:
        color = {"supported": "green", "contradicted": "red", "neutral": "yellow"}.get(
            r.thesis_verdict, "dim"
        )
        lines.append("")
        lines.append(f"[bold]Thesis[/bold]       [{color}]{r.thesis_verdict.upper()}[/{color}]")
        if r.thesis_reasoning:
            lines.append(f"  [dim]{r.thesis_reasoning}[/dim]")
    elif r.thesis_reasoning:
        lines.append("")
        lines.append(f"[dim]{r.thesis_reasoning}[/dim]")

    # direction
    lines.append("")
    dir_color = {"bullish": "green", "bearish": "red"}.get(r.recommended_direction, "dim")
    conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(r.confidence, "dim")
    lines.append(
        f"[bold]Signal[/bold]       [{dir_color}]{r.recommended_direction.upper()}[/{dir_color}]"
        f"  confidence [{conf_color}]{r.confidence}[/{conf_color}]"
    )

    if r.skip_reason:
        lines.append(f"  [yellow]⚠  {r.skip_reason}[/yellow]")

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold cyan]{r.ticker} — Research[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))


def _print_picks(picks: list[Pick], budget: float) -> None:
    if not picks:
        return

    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )

    table.add_column("#",          style="dim",   width=3)
    table.add_column("Strike",     justify="right")
    table.add_column("Side",       justify="center")
    table.add_column("Expiry",     justify="center")
    table.add_column("DTE",        justify="right")
    table.add_column("Cost",       justify="right")
    table.add_column("Breakeven",  justify="right")
    table.add_column("OTM%",       justify="right")
    table.add_column("IV",         justify="right")
    table.add_column("OI",         justify="right")
    table.add_column("Spread",     justify="right")
    table.add_column("Note",       style="dim")

    for i, p in enumerate(picks, 1):
        side_color = "green" if p.side == "call" else "red"
        cost_color = "yellow" if p.cost > budget * 0.8 else "white"

        iv_str     = f"{p.iv*100:.0f}%" if p.iv else "—"
        spread_str = f"{p.spread_pct*100:.1f}%" if p.spread_pct < 999 else "—"
        note       = f"[yellow]relaxed[/yellow]" if p.relaxed else ""

        table.add_row(
            str(i),
            f"${p.strike:g}",
            f"[{side_color}]{p.side}[/{side_color}]",
            p.expiration,
            str(p.dte),
            f"[{cost_color}]${p.cost:.0f}[/{cost_color}]",
            f"${p.breakeven:.2f}",
            f"{p.otm_pct*100:.1f}%",
            iv_str,
            f"{p.oi:,}" if p.oi else "—",
            spread_str,
            note,
        )

    console.print(Panel(
        table,
        title="[bold green]Recommended Contracts[/bold green]",
        border_style="green",
        padding=(1, 1),
    ))

    console.print(
        f"  [dim]Max loss per contract = cost shown. "
        f"Suggested sizing: 1 contract at a time with ${budget:.0f} budget.[/dim]\n"
    )


def _print_budget_warning(ticker: str, budget: float, reason: str) -> None:
    console.print(Panel(
        f"[yellow]No contracts found within ${budget:.0f} budget.[/yellow]\n"
        f"[dim]{reason}[/dim]\n\n"
        f"[dim]Try: increase --budget, or pick a lower-priced ticker than {ticker}.[/dim]",
        title="[yellow]No picks[/yellow]",
        border_style="yellow",
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(
        prog="tradebrain",
        description="Options research + contract selection. Feed it a ticker, a thesis, or both.",
    )
    ap.add_argument(
        "input",
        nargs="?",
        help='Ticker, thesis, or both. e.g. "AMD" or "AMD calls, 5 weeks green"',
    )
    ap.add_argument("--budget", type=float, default=300.0, help="Max cost per contract in USD (default 300)")
    ap.add_argument("--ticker", help="Explicit ticker override")
    ap.add_argument("--direction", choices=["bullish", "bearish"], help="Force direction")
    args = ap.parse_args()

    raw = args.input or args.ticker
    if not raw:
        console.print("[red]Provide input: tradebrain 'AMD calls, breakout thesis' --budget 500[/red]")
        sys.exit(1)

    # parse intake
    intake = parse_intake(raw, args.budget)

    # apply overrides
    if args.ticker:
        from bot.models import Intake
        intake = Intake(
            raw_text  = intake.raw_text,
            tickers   = (args.ticker.upper(),) + tuple(
                t for t in intake.tickers if t != args.ticker.upper()
            ),
            direction = intake.direction,
            thesis    = intake.thesis,
            timeframe = intake.timeframe,
            budget    = intake.budget,
        )
    if args.direction:
        from bot.models import Intake
        intake = Intake(
            raw_text  = intake.raw_text,
            tickers   = intake.tickers,
            direction = args.direction,   # type: ignore[arg-type]
            thesis    = intake.thesis,
            timeframe = intake.timeframe,
            budget    = intake.budget,
        )

    if not intake.tickers:
        console.print("[red]Could not extract a ticker. Try: tradebrain 'AMD bullish' --budget 300[/red]")
        sys.exit(1)

    # show what we parsed
    console.print(f"\n[dim]Parsed:[/dim] {intake.tickers[0]}  "
                  f"direction={intake.direction}  "
                  f"timeframe={intake.timeframe}  "
                  f"budget=${intake.budget:.0f}\n")

    # run pipeline
    if len(intake.tickers) > 1:
        with console.status(f"[cyan]Researching {len(intake.tickers)} tickers...[/cyan]", spinner="dots"):
            from bot.engine import run_multi
            results = run_multi(intake)

        for research, picks, reason in results:
            _print_research(research)
            if picks:
                _print_picks(picks, args.budget)
            else:
                _print_budget_warning(research.ticker, args.budget, reason)
    else:
        with console.status("[cyan]Researching...[/cyan]", spinner="dots"):
            research, picks, reason = run(intake)

        _print_research(research)
        if picks:
            _print_picks(picks, args.budget)
        else:
            _print_budget_warning(intake.tickers[0], args.budget, reason)


if __name__ == "__main__":
    main()