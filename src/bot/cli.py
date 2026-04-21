from __future__ import annotations

import argparse
import sys

from rich import print as rprint
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from typing import Optional


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
    lines.append(f"[bold]Price[/bold]        ${r.price:.2f}")

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

    # technicals
    if r.sma50 is not None:
        color = "green" if r.above_sma50 else "red"
        label = "ABOVE" if r.above_sma50 else "BELOW"
        lines.append(f"[bold]50 SMA[/bold]       [{color}]{label} ${r.sma50:.2f}[/{color}]")

    if r.sma200 is not None:
        color = "green" if r.above_sma200 else "red"
        label = "ABOVE" if r.above_sma200 else "BELOW"
        lines.append(f"[bold]200 SMA[/bold]      [{color}]{label} ${r.sma200:.2f}[/{color}]")

    # IV
    if r.iv_rank is not None:
        iv_color = "red" if r.iv_rank > 70 else "yellow" if r.iv_rank > 40 else "green"
        iv_note  = "expensive" if r.iv_rank > 70 else "moderate" if r.iv_rank > 40 else "cheap"
        lines.append(f"[bold]HV rank[/bold]      [{iv_color}]{r.iv_rank:.0f}/100 ({iv_note})[/{iv_color}]")

    # earnings
    if r.earnings_days_away is not None:
        e_color = "red" if r.earnings_days_away <= 14 else "yellow" if r.earnings_days_away <= 30 else "dim"
        lines.append(f"[bold]Earnings[/bold]     [{e_color}]in {r.earnings_days_away} days[/{e_color}]")

    # analyst
    if r.analyst_target:
        r_color = (
            "green" if r.analyst_rating and "buy" in r.analyst_rating.lower()
            else "red" if r.analyst_rating and "sell" in r.analyst_rating.lower()
            else "dim"
        )
        upside_str = f" ({r.analyst_upside:+.1f}%)" if r.analyst_upside is not None else ""
        lines.append(
            f"[bold]Analyst[/bold]      [{r_color}]{r.analyst_rating or 'N/A'}[/{r_color}]"
            f"  target ${r.analyst_target:.2f}{upside_str}"
        )

    # volume
    if r.avg_volume:
        lines.append(f"[bold]Avg volume[/bold]   {r.avg_volume:,}")

    # unusual options
    if r.unusual_options_activity:
        lines.append("")
        lines.append(f"[bold]Options flow[/bold] [yellow]{r.unusual_options_activity}[/yellow]")

    # news
    if r.news_summary:
        lines.append("")
        lines.append("[bold]News[/bold]")
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
        lines.append(f"  [dim]{r.thesis_reasoning}[/dim]")

    # signal
    lines.append("")
    dir_color  = {"bullish": "green", "bearish": "red"}.get(r.recommended_direction, "dim")
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


def _get_cheapest_contract(ticker: str) -> Optional[float]:
    from bot.chain_yf import get_expirations, get_chain, ChainError
    from bot.select import _dte, _effective_mid
    try:
        exps = get_expirations(ticker)
        for exp in exps[:4]:
            chain = get_chain(ticker, exp)
            cheapest = None
            for c in chain:
                d = _dte(c.expiration)
                if d < 14:
                    continue
                m, _ = _effective_mid(c.bid, c.ask, c.last, False)
                if m is None:
                    continue
                cost = m * 100
                if cheapest is None or cost < cheapest:
                    cheapest = cost
            if cheapest is not None:
                return cheapest
    except Exception:
        pass
    return None


def _print_budget_warning(ticker: str, budget: float, reason: str) -> None:
    cheapest = _get_cheapest_contract(ticker)
    budget_line = f"[yellow]No contracts found within ${budget:.0f} budget.[/yellow]"
    if cheapest is not None:
        suggest = round(cheapest * 1.2)
        budget_line += (
            f"\n[dim]Cheapest available contract: ~${cheapest:.0f}. "
            f"Try [/dim][bold]--budget {suggest}[/bold][dim] to start seeing picks.[/dim]"
        )
    console.print(Panel(
        f"{budget_line}\n"
        f"[dim]{reason}[/dim]\n\n"
        f"[dim]Try a lower-priced ticker if {ticker} options are out of range.[/dim]",
        title="[yellow]No picks[/yellow]",
        border_style="yellow",
        padding=(1, 2),
    ))

def _evaluate_pick_quality(picks: list[Pick], research: ResearchResult, budget: float) -> None:
    """
    After finding picks at a retried budget, evaluate if they're actually worth taking.
    Surfaces warnings for: high IV, earnings risk, wide spread, low OI.
    """
    warnings = []
    best = picks[0]

    if research.iv_rank is not None and research.iv_rank > 65:
        warnings.append(
            f"[red]HV rank {research.iv_rank:.0f}/100 — premium is expensive. "
            f"You're paying elevated prices for these contracts.[/red]"
        )

    if research.earnings_days_away is not None and research.earnings_days_away <= 21:
        warnings.append(
            f"[red]Earnings in {research.earnings_days_away} days — "
            f"IV crush risk is real. Contract may lose value even if stock moves in your direction.[/red]"
        )

    if best.spread_pct < 999 and best.spread_pct > 0.15:
        warnings.append(
            f"[yellow]Spread {best.spread_pct*100:.1f}% on best pick — wide. "
            f"You'll lose ~{best.spread_pct*50:.0f}% immediately on entry.[/yellow]"
        )

    if (best.oi or 0) < 100:
        warnings.append(
            f"[yellow]OI={best.oi or 0} on best pick — thin liquidity. "
            f"May be hard to exit at a fair price.[/yellow]"
        )

    if budget > 500 and best.cost > budget * 0.7:
        warnings.append(
            f"[yellow]Contract costs ${best.cost:.0f} — "
            f"{best.cost/budget*100:.0f}% of your budget on one trade. "
            f"Consider sizing down.[/yellow]"
        )

    if not warnings:
        console.print(
            "\n  [green]✓ Contracts look tradeable — quality checks passed.[/green]\n"
        )
        return

    console.print("\n  [bold]Quality check:[/bold]")
    for w in warnings:
        console.print(f"  {w}")

    # overall verdict
    red_count = sum(1 for w in warnings if w.startswith("[red]"))
    if red_count >= 2:
        console.print(
            "\n  [red bold]VERDICT: Skip this trade. "
            "Too many risk flags even at the higher budget.[/red bold]\n"
        )
    elif red_count == 1:
        console.print(
            "\n  [yellow]VERDICT: Proceed with caution. "
            "One significant risk flag — size at minimum (1 contract).[/yellow]\n"
        )
    else:
        console.print(
            "\n  [green]VERDICT: Acceptable. Minor flags only — "
            "1 contract maximum.[/green]\n"
        )


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
    tickers_str = ", ".join(intake.tickers) if intake.tickers else "none"
    console.print(f"\n[dim]Parsed:[/dim] {tickers_str}  "
                  f"direction={intake.direction}  "
                  f"timeframe={intake.timeframe}  "
                  f"budget=${intake.budget:.0f}\n")

    # run pipeline
    if len(intake.tickers) > 1:
        with console.status(f"[cyan]Researching {len(intake.tickers)} tickers...[/cyan]", spinner="dots"):
            from bot.engine import run_multi
            results = run_multi(intake)

        for research, picks, reason, direction_note in results:
            _print_research(research)
            if direction_note:
                console.print(f"\n  {direction_note}\n")
            if picks:
                _print_picks(picks, args.budget)
            else:
                _print_budget_warning(research.ticker, args.budget, reason)
    else:
        with console.status("[cyan]Researching...[/cyan]", spinner="dots"):
            research, picks, reason, direction_note = run(intake)

        _print_research(research)
        if direction_note:
            console.print(f"\n  {direction_note}\n")

        if picks:
            _print_picks(picks, args.budget)
        else:
            _print_budget_warning(intake.tickers[0], args.budget, reason)

            # interactive retry loop — multiple attempts, quality gate on success
            current_budget = args.budget
            while True:
                cheapest = _get_cheapest_contract(intake.tickers[0])
                if cheapest is None:
                    break

                suggested = round(cheapest * 1.2)
                if suggested <= current_budget:
                    break  # no point suggesting same or lower budget

                try:
                    console.print(
                        f"\n[dim]Retry with [/dim][bold]--budget {suggested}[/bold]"
                        f"[dim]? (y/n/q to quit):[/dim] ",
                        end=""
                    )
                    answer = input().strip().lower()
                except (KeyboardInterrupt, EOFError):
                    break

                if answer in ("q", "quit", "n", "no"):
                    break

                if answer not in ("y", "yes"):
                    continue

                from bot.models import Intake as _Intake
                new_intake = _Intake(
                    raw_text        = intake.raw_text,
                    tickers         = intake.tickers,
                    context_tickers = intake.context_tickers,
                    direction       = intake.direction,
                    thesis          = intake.thesis,
                    timeframe       = intake.timeframe,
                    budget          = float(suggested),
                )
                console.print(f"\n[dim]Retrying with budget ${suggested}...[/dim]\n")
                with console.status("[cyan]Researching...[/cyan]", spinner="dots"):
                    research2, picks2, reason2, direction_note2 = run(new_intake)

                if direction_note2:
                    console.print(f"\n  {direction_note2}\n")

                if picks2:
                    _print_picks(picks2, float(suggested))
                    _evaluate_pick_quality(picks2, research2, float(suggested))
                    break
                else:
                    _print_budget_warning(intake.tickers[0], float(suggested), reason2)
                    current_budget = float(suggested)


if __name__ == "__main__":
    main()