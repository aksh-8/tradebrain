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

    table.add_column("#",       style="dim", width=3)
    table.add_column("Strike",  justify="right")
    table.add_column("Side",    justify="center")
    table.add_column("Expiry",  justify="center")
    table.add_column("DTE",     justify="right")
    table.add_column("Cost",    justify="right")
    table.add_column("Ask",     justify="right")
    table.add_column("Brkeven", justify="right")
    table.add_column("Delta",   justify="right")
    table.add_column("Theta/d", justify="right")
    table.add_column("Vega",    justify="right")
    table.add_column("PoP",     justify="right")
    table.add_column("IV",      justify="right")
    table.add_column("OI",      justify="right")
    table.add_column("Spread",  justify="right")
    table.add_column("Note",    style="dim")

    for i, p in enumerate(picks, 1):
        side_color = "green" if p.side == "call" else "red"
        cost_color = "yellow" if p.cost > budget * 0.8 else "white"

        iv_str     = f"{p.iv*100:.0f}%"    if p.iv           else "—"
        spread_str = f"{p.spread_pct*100:.1f}%" if p.spread_pct < 999 else "—"
        note       = "[yellow]relaxed[/yellow]" if p.relaxed  else ""

        # ask price — real Robinhood execution cost
        ask_str    = f"${p.ask:.2f}"        if p.ask          else "—"

        # Greeks
        delta_str  = f"{p.delta:+.2f}"      if p.delta is not None       else "—"
        theta_str  = f"${p.theta:.1f}/d"    if p.theta is not None       else "—"
        vega_str   = f"${p.vega:.1f}"       if p.vega is not None        else "—"
        pop_str    = f"{p.prob_profit*100:.0f}%" if p.prob_profit is not None else "—"

        # colour delta by conviction
        delta_color = (
            "green"  if p.delta is not None and abs(p.delta) >= 0.40 else
            "yellow" if p.delta is not None and abs(p.delta) >= 0.25 else
            "dim"
        )

        # colour theta — bigger daily bleed = more warning
        theta_color = (
            "red"    if p.theta is not None and p.theta < -20 else
            "yellow" if p.theta is not None and p.theta < -10 else
            "dim"
        )

        # colour PoP
        pop_color = (
            "green"  if p.prob_profit is not None and p.prob_profit >= 0.40 else
            "yellow" if p.prob_profit is not None and p.prob_profit >= 0.25 else
            "red"
        )

        table.add_row(
            str(i),
            f"${p.strike:g}",
            f"[{side_color}]{p.side}[/{side_color}]",
            p.expiration,
            str(p.dte),
            f"[{cost_color}]${p.cost:.0f}[/{cost_color}]",
            ask_str,
            f"${p.breakeven:.2f}",
            f"[{delta_color}]{delta_str}[/{delta_color}]",
            f"[{theta_color}]{theta_str}[/{theta_color}]",
            vega_str,
            f"[{pop_color}]{pop_str}[/{pop_color}]",
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
        f"  [dim]Delta: directional exposure per $1 move  |  "
        f"Theta: daily time decay per contract  |  "
        f"Vega: $ change per 1pt IV move  |  "
        f"PoP: probability of profit at expiry[/dim]\n"
        f"  [dim]Ask = Robinhood execution price. "
        f"Max loss = cost shown. "
        f"1 contract at a time with ${budget:.0f} budget.[/dim]\n"
    )

def _print_pre_earnings_picks(
    picks: list[Pick],
    budget: float,
    hv_rank: Optional[float] = None,
) -> None:
    """
    Displays pre-earnings run-up contracts (Structure 1).
    These expire BEFORE earnings — user must exit 1-2 days before the report.
    """
    if not picks:
        return

    iv_warning = (
        f"\n[red]⚠ HV rank {hv_rank:.0f}/100 — premium elevated. "
        f"Run-up play is less favorable when IV is already high.[/red]"
        if hv_rank is not None and hv_rank > 60 else ""
    )

    console.print("\n")
    console.print(Panel(
        "[bold yellow]Structure 1 — Pre-earnings run-up play[/bold yellow]\n"
        "[dim]These contracts expire BEFORE earnings.\n"
        "Strategy: buy now, ride momentum, EXIT 1-2 days before the report.\n"
        "You never take earnings risk.[/dim]"
        + iv_warning,
        border_style="yellow",
        padding=(1, 2),
    ))
    _print_picks(picks, budget)

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

def _print_kelly_sizing(picks: list[Pick], budget: float) -> None:
    """
    Shows Kelly position sizing for the best pick.
    Uses PoP from Black-Scholes already computed in select.py.
    """
    from bot.bs import kelly_size
    from bot.config import get_settings
    if not picks:
        return

    best = picks[0]
    if best.prob_profit is None:
        return
    bankroll = get_settings().bankroll_usd
    k = kelly_size(
        pop      = best.prob_profit,
        cost     = best.cost,
        bankroll = bankroll,
    )
    if not k:
        return

    color = (
        "green"  if k["max_contracts"] >= 1 and k["suggested_usd"] >= best.cost else
        "yellow" if k["full_kelly_pct"] > 0 else
        "red"
    )

    console.print(
        f"\n  [bold]Kelly sizing[/bold] — best pick  "
        f"PoP=[bold]{best.prob_profit*100:.0f}%[/bold]  "
        f"half-Kelly=[bold]{k['half_kelly_pct']:.1f}%[/bold]  "
        f"suggested=[bold]${k['suggested_usd']:.0f}[/bold]\n"
        f"  [{color}]{k['verdict']}[/{color}]\n"
    )

def _cmd_history(args: argparse.Namespace) -> None:
    """
    tradebrain history --last 10
    tradebrain history --ticker AMD
    tradebrain history --id 5
    """
    from bot.logger import get_recent_runs, get_runs_by_ticker, get_run_detail

    # single run detail
    if args.id:
        run = get_run_detail(args.id)
        if not run:
            console.print(f"[red]No run found with id {args.id}[/red]")
            return
        _print_run_detail(run)
        return

    # filtered by ticker
    if args.ticker:
        runs = get_runs_by_ticker(args.ticker.upper(), n=args.last)
    else:
        runs = get_recent_runs(n=args.last)

    if not runs:
        console.print("[dim]No runs logged yet. Run tradebrain on a ticker first.[/dim]")
        return

    _print_run_table(runs)


def _print_run_table(runs: list[dict]) -> None:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )

    table.add_column("ID",        style="dim",  width=4)
    table.add_column("Date",      justify="left")
    table.add_column("Ticker",    justify="center")
    table.add_column("Direction", justify="center")
    table.add_column("Verdict",   justify="center")
    table.add_column("Conf",      justify="center")
    table.add_column("HV rank",   justify="right")
    table.add_column("Earnings",  justify="right")
    table.add_column("Picks",     justify="right")
    table.add_column("Budget",    justify="right")

    for r in runs:
        verdict = r.get("verdict") or "—"
        verdict_color = {
            "supported":    "green",
            "contradicted": "red",
            "neutral":      "yellow",
        }.get(verdict, "dim")

        direction = r.get("direction") or "—"
        dir_color = {"bullish": "green", "bearish": "red"}.get(direction, "dim")

        conf = r.get("confidence") or "—"
        conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(conf, "dim")

        iv = r.get("iv_rank")
        iv_str = f"{iv:.0f}/100" if iv is not None else "—"

        earn = r.get("earnings_days")
        earn_str = f"{earn}d" if earn is not None else "—"
        earn_color = "red" if earn is not None and earn <= 14 else "yellow" if earn is not None and earn <= 30 else "dim"

        ts = (r.get("ts") or "")[:10]  # just the date

        table.add_row(
            str(r["id"]),
            ts,
            r.get("ticker") or "—",
            f"[{dir_color}]{direction}[/{dir_color}]",
            f"[{verdict_color}]{verdict}[/{verdict_color}]",
            f"[{conf_color}]{conf}[/{conf_color}]",
            iv_str,
            f"[{earn_color}]{earn_str}[/{earn_color}]",
            str(r.get("pick_count") or 0),
            f"${r.get('budget') or 0:.0f}",
        )

    console.print(Panel(
        table,
        title="[bold cyan]tradebrain — run history[/bold cyan]",
        border_style="cyan",
        padding=(1, 1),
    ))
    console.print(
        f"  [dim]Use [bold]tradebrain history --id N[/bold] to see full detail for a specific run.[/dim]\n"
    )


def _print_run_detail(run: dict) -> None:
    lines = []
    lines.append(f"[bold]Run ID[/bold]      {run['id']}")
    lines.append(f"[bold]Date[/bold]        {(run.get('ts') or '')[:19]}")
    lines.append(f"[bold]Ticker[/bold]      {run.get('ticker')}")
    lines.append(f"[bold]Direction[/bold]   {run.get('direction')}")
    lines.append(f"[bold]Budget[/bold]      ${run.get('budget') or 0:.0f}")
    lines.append(f"[bold]Confidence[/bold]  {run.get('confidence')}")

    verdict = run.get("verdict")
    if verdict:
        color = {"supported": "green", "contradicted": "red", "neutral": "yellow"}.get(verdict, "dim")
        lines.append(f"[bold]Verdict[/bold]     [{color}]{verdict}[/{color}]")

    reasoning = run.get("reasoning")
    if reasoning:
        lines.append(f"[bold]Reasoning[/bold]")
        lines.append(f"  [dim]{reasoning}[/dim]")

    thesis = run.get("thesis")
    if thesis:
        lines.append(f"[bold]Thesis[/bold]")
        lines.append(f"  [dim]{thesis}[/dim]")

    news = run.get("news")
    if news:
        lines.append(f"\n[bold]News[/bold]")
        for h in news.split(" | "):
            lines.append(f"  [dim]· {h.strip()}[/dim]")

    console.print(Panel(
        "\n".join(lines),
        title=f"[bold cyan]Run {run['id']} — {run.get('ticker')}[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))

    picks = run.get("picks") or []
    if picks:
        console.print(f"\n  [bold]Picks ({len(picks)}):[/bold]")
        for p in picks:
            console.print(
                f"  #{p['rank']} {p['ticker']} ${p['strike']} {p['side']} "
                f"exp={p['expiration']} cost=${p['cost']:.0f} "
                f"breakeven=${p['breakeven']:.2f}"
            )
    else:
        console.print("\n  [dim]No picks for this run.[/dim]")

def _cmd_watch(args: argparse.Namespace) -> None:
    """
    tradebrain watch
    tradebrain watch --budget 500
    tradebrain watch --direction bullish
    Runs research on every ticker in config/watchlist.json.
    Sorted by confidence then signal strength.
    """
    import json
    from pathlib import Path
    from bot.engine import run

    watchlist_path = Path(__file__).parent.parent.parent / "config" / "watchlist.json"
    if not watchlist_path.exists():
        console.print("[red]No watchlist found. Create config/watchlist.json first.[/red]")
        return

    with open(watchlist_path) as f:
        wl = json.load(f)

    tickers  = wl.get("tickers", [])
    budget   = args.budget or wl.get("default_budget", 300)
    direction = getattr(args, "direction", None) or "unknown"

    if not tickers:
        console.print("[red]Watchlist is empty. Add tickers to config/watchlist.json.[/red]")
        return

    console.print(f"\n[bold cyan]tradebrain watch[/bold cyan] — scanning {len(tickers)} tickers  budget=${budget:.0f}\n")

    results = []
    for ticker in tickers:
        with console.status(f"[cyan]Researching {ticker}...[/cyan]", spinner="dots"):
            from bot.models import Intake
            intake = Intake(
                raw_text        = ticker,
                tickers         = (ticker.upper(),),
                context_tickers = (),
                direction       = direction,  # type: ignore[arg-type]
                thesis          = None,
                timeframe       = "unknown",
                budget          = float(budget),
            )
            research, picks, reason, direction_note, earnings_dte_note, pre_earnings_picks = run(intake)
            results.append((research, picks, reason, direction_note, earnings_dte_note, pre_earnings_picks))

    # sort: confidence high > medium > low, then picks count, then HV rank low > high
    conf_order = {"high": 0, "medium": 1, "low": 2}
    results.sort(key=lambda x: (
        conf_order.get(x[0].confidence, 9),
        -len(x[1]),
        x[0].iv_rank or 100,
    ))

    # summary table first
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold cyan",
        padding=(0, 1),
    )
    table.add_column("Ticker",    justify="center")
    table.add_column("Price",     justify="right")
    table.add_column("1mo",       justify="right")
    table.add_column("Signal",    justify="center")
    table.add_column("Conf",      justify="center")
    table.add_column("Verdict",   justify="center")
    table.add_column("HV rank",   justify="right")
    table.add_column("Earnings",  justify="right")
    table.add_column("Picks",     justify="right")
    table.add_column("Best PoP",  justify="right")

    for research, picks, reason, dn, en, pre in results:
        dir_color  = {"bullish": "green", "bearish": "red"}.get(research.recommended_direction, "dim")
        conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(research.confidence, "dim")
        verdict_color = {"supported": "green", "contradicted": "red", "neutral": "yellow"}.get(
            research.thesis_verdict or "", "dim"
        )

        price_1m = f"{research.price_change_1m:+.1f}%" if research.price_change_1m is not None else "—"
        hv = f"{research.iv_rank:.0f}/100" if research.iv_rank is not None else "—"
        earn = f"{research.earnings_days_away}d" if research.earnings_days_away is not None else "—"
        earn_color = "red" if research.earnings_days_away is not None and research.earnings_days_away <= 14 else "yellow" if research.earnings_days_away is not None and research.earnings_days_away <= 30 else "dim"

        best_pop = "—"
        if picks and picks[0].prob_profit is not None:
            best_pop = f"{picks[0].prob_profit*100:.0f}%"
            pop_color = "green" if picks[0].prob_profit >= 0.40 else "yellow" if picks[0].prob_profit >= 0.25 else "red"
        else:
            pop_color = "dim"

        table.add_row(
            f"[bold]{research.ticker}[/bold]",
            f"${research.price:.2f}",
            price_1m,
            f"[{dir_color}]{research.recommended_direction}[/{dir_color}]",
            f"[{conf_color}]{research.confidence}[/{conf_color}]",
            f"[{verdict_color}]{research.thesis_verdict or '—'}[/{verdict_color}]",
            hv,
            f"[{earn_color}]{earn}[/{earn_color}]",
            str(len(picks)),
            f"[{pop_color}]{best_pop}[/{pop_color}]",
        )

    console.print(Panel(
        table,
        title="[bold cyan]Morning Scan — Watchlist[/bold cyan]",
        border_style="cyan",
        padding=(1, 1),
    ))
    console.print(
        "  [dim]Sorted by confidence then picks. "
        "Run [bold]tradebrain \"TICKER thesis\" --budget N[/bold] for full analysis.[/dim]\n"
    )

    # show full detail for high confidence picks with contracts
    top_picks = [(r, p, dn, en, pre) for r, p, reason, dn, en, pre in results
                 if r.confidence == "high" and len(p) > 0]

    if top_picks:
        console.print(f"[bold green]High confidence picks ({len(top_picks)} tickers):[/bold green]\n")
        for research, picks, direction_note, earnings_dte_note, pre_earnings_picks in top_picks:
            _print_research(research)
            if direction_note:
                console.print(f"\n  {direction_note}\n")
            if earnings_dte_note:
                console.print(f"\n  [yellow]DTE adjusted:[/yellow] [dim]{earnings_dte_note}[/dim]\n")
            _print_picks(picks, budget)
            _print_kelly_sizing(picks, budget)
            if pre_earnings_picks:
                _print_pre_earnings_picks(
                    pre_earnings_picks,
                    budget,
                    hv_rank=research.iv_rank,
                )

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # route history command before argparse to avoid positional conflict
    if len(sys.argv) > 1 and sys.argv[1] == "history":
        hist_ap = argparse.ArgumentParser(prog="tradebrain history")
        hist_ap.add_argument("--last",   type=int, default=10, help="Number of runs to show (default 10)")
        hist_ap.add_argument("--ticker", help="Filter by ticker")
        hist_ap.add_argument("--id",     type=int, help="Show full detail for a specific  run ID")
        hist_args = hist_ap.parse_args(sys.argv[2:])
        _cmd_history(hist_args)
        return
    
    # route watch command
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        watch_ap = argparse.ArgumentParser(prog="tradebrain watch")
        watch_ap.add_argument("--budget",    type=float, help="Override default budget from watchlist.json")
        watch_ap.add_argument("--direction", choices=["bullish", "bearish"], help="Force direction for all tickers")
        watch_args = watch_ap.parse_args(sys.argv[2:])
        _cmd_watch(watch_args)
        return

    ap = argparse.ArgumentParser(
        prog="tradebrain",
        description="Options research + contract selection. Feed it a ticker, a thesis, or both.",
    )

    # main research arguments (default command)
    ap.add_argument(
        "input",
        nargs="?",
        help='Ticker, thesis, or both. e.g. "AMD" or "AMD calls, 5 weeks green"',
    )
    ap.add_argument("--budget",    type=float, default=300.0, help="Max cost per contract in USD (default 300)")
    ap.add_argument("--ticker",    help="Explicit ticker override")
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
            raw_text        = intake.raw_text,
            tickers         = (args.ticker.upper(),) + tuple(
                t for t in intake.tickers if t != args.ticker.upper()
            ),
            context_tickers = intake.context_tickers,
            direction       = intake.direction,
            thesis          = intake.thesis,
            timeframe       = intake.timeframe,
            budget          = intake.budget,
        )
    if args.direction:
        from bot.models import Intake
        intake = Intake(
            raw_text        = intake.raw_text,
            tickers         = intake.tickers,
            context_tickers = intake.context_tickers,
            direction       = args.direction,   # type: ignore[arg-type]
            thesis          = intake.thesis,
            timeframe       = intake.timeframe,
            budget          = intake.budget,
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

        for research, picks, reason, direction_note, earnings_dte_note, pre_earnings_picks in results:
            _print_research(research)
            if direction_note:
                console.print(f"\n  {direction_note}\n")
            if earnings_dte_note:
                console.print(f"\n  [yellow]DTE adjusted:[/yellow] [dim]{earnings_dte_note}[/dim]\n")
            if picks:
                _print_picks(picks, args.budget)
                _print_kelly_sizing(picks, args.budget)
                if pre_earnings_picks:
                    _print_pre_earnings_picks(
                        pre_earnings_picks,
                        args.budget,
                        hv_rank=research.iv_rank,
                    )
            else:
                _print_budget_warning(research.ticker, args.budget, reason)
    else:
        with console.status("[cyan]Researching...[/cyan]", spinner="dots"):
            research, picks, reason, direction_note, earnings_dte_note, pre_earnings_picks = run(intake)
        _print_research(research)
        if direction_note:
            console.print(f"\n  {direction_note}\n")
        if earnings_dte_note:
            console.print(f"\n  [yellow]DTE adjusted:[/yellow] [dim]{earnings_dte_note}[/dim]\n")
        if picks:
            _print_picks(picks, args.budget)
            _print_kelly_sizing(picks, args.budget)
            if pre_earnings_picks:
                _print_pre_earnings_picks(
                    pre_earnings_picks,
                    args.budget,
                    hv_rank=research.iv_rank,
                )
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
                    research2, picks2, reason2, direction_note2, earnings_dte_note2, pre_earnings_picks2 = run(new_intake)
                if direction_note2:
                    console.print(f"\n  {direction_note2}\n")
                if earnings_dte_note2:
                    console.print(f"\n  [yellow]DTE adjusted:[/yellow] [dim]{earnings_dte_note2}[/dim]\n")
                if picks2:
                    _print_picks(picks2, float(suggested))
                    _print_kelly_sizing(picks2, float(suggested))
                    if pre_earnings_picks2:
                        _print_pre_earnings_picks(
                            pre_earnings_picks2,
                            float(suggested),
                            hv_rank=research2.iv_rank,
                        )
                    _evaluate_pick_quality(picks2, research2, float(suggested))
                    break
                else:
                    _print_budget_warning(intake.tickers[0], float(suggested), reason2)
                    current_budget = float(suggested)


if __name__ == "__main__":
    main()