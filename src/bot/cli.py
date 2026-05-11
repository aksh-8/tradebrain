from __future__ import annotations

import os
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

def _print_kelly_sizing(picks: list[Pick], budget: float, bankroll_override: Optional[float] = None) -> None:
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
    bankroll = bankroll_override if bankroll_override else get_settings().bankroll_usd
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

def _print_contract_detail(
    ticker: str,
    contract,
    side: str,
    spot: float,
    m: float,
    cost: float,
    dte_days: int,
    greeks: dict,
    used_last: bool,
    target_exp: str,
) -> None:
    """
    Shared contract display panel — used by _cmd_contract and _cmd_flow.
    Shows: contract header, Greeks, breakeven, return targets, DTE warning.
    """
    iv_str     = f"{contract.iv*100:.0f}%" if contract.iv else "—"
    price_src  = "last price [NO BID/ASK]" if used_last else "mid"
    spread_str = (
        f"{(contract.ask - contract.bid) / m * 100:.1f}%"
        if contract.bid and contract.ask and contract.bid > 0 else "—"
    )
    otm_pct   = ((contract.strike - spot) / spot * 100) if side == "call" else ((spot - contract.strike) / spot * 100)
    otm_label = f"{otm_pct:+.1f}% OTM" if otm_pct > 0 else f"{abs(otm_pct):.1f}% ITM"

    lines = []
    lines.append(f"[bold]Contract[/bold]    {ticker} ${contract.strike:g} {side.upper()} {target_exp}")
    lines.append(f"[bold]DTE[/bold]         {dte_days} days")
    lines.append(f"[bold]Strike[/bold]      {otm_label}  (spot ${spot:.2f})")
    lines.append(f"[bold]Price[/bold]       ${m:.2f} ({price_src}) → cost ${cost:.0f}/contract")
    if contract.ask:
        lines.append(f"[bold]Ask[/bold]         ${contract.ask:.2f} (Robinhood execution price)")
    lines.append(f"[bold]IV[/bold]          {iv_str}")
    lines.append(f"[bold]Spread[/bold]      {spread_str}")
    lines.append(f"[bold]OI[/bold]          {contract.oi or 0:,}   Volume {contract.volume or 0:,}")
    lines.append("")

    if greeks:
        delta = greeks.get("delta")
        theta = greeks.get("theta")
        vega  = greeks.get("vega")
        pop   = greeks.get("prob_profit")
        gamma = greeks.get("gamma")

        delta_color = "green" if delta and abs(delta) >= 0.40 else "yellow" if delta and abs(delta) >= 0.15 else "dim"
        theta_color = "red" if theta and theta < -20 else "yellow" if theta and theta < -5 else "dim"
        pop_color   = "green" if pop and pop >= 0.40 else "yellow" if pop and pop >= 0.25 else "red"

        if delta:
            lines.append(f"[bold]Delta[/bold]       [{delta_color}]{delta:+.3f}[/{delta_color}]  (contract moves ${delta*100:+.0f} per $1 {ticker} move)")
        if gamma:
            lines.append(f"[bold]Gamma[/bold]       {gamma:.4f}")
        if theta:
            lines.append(f"[bold]Theta[/bold]       [{theta_color}]${theta:.2f}/day[/{theta_color}]  (time decay cost)")
        if vega:
            lines.append(f"[bold]Vega[/bold]        ${vega:.2f} per 1pt IV move")
        if pop:
            lines.append(f"[bold]PoP[/bold]         [{pop_color}]{pop*100:.0f}%[/{pop_color}]  (probability of profit at expiry)")
        lines.append("")

    breakeven = contract.strike + m if side == "call" else contract.strike - m
    be_pct    = (breakeven - spot) / spot * 100 if spot > 0 else 0
    lines.append(f"[bold]Spot[/bold]        ${spot:.2f}")
    lines.append(f"[bold]Breakeven[/bold]   ${breakeven:.2f}  ({be_pct:+.1f}% move needed)")
    lines.append("")

    if dte_days <= 5:
        theta_val = greeks.get("theta", 0) or 0
        lines.append(
            f"[yellow]⚠ {dte_days} DTE — severe theta decay. "
            f"Contract loses ~${abs(theta_val):.0f}/day. "
            f"Only viable on immediate gap move.[/yellow]"
        )
        lines.append("")

    lines.append("[bold]Return targets at expiry:[/bold]")
    for mult, label in [(2, "2x "), (3, "3x "), (5, "5x "), (10, "10x")]:
        target_val = m * mult
        t_price    = contract.strike + target_val if side == "call" else contract.strike - target_val
        t_pct      = (t_price - spot) / spot * 100 if spot > 0 else 0
        lines.append(f"  {label} → {ticker} at ${t_price:.2f}  ({t_pct:+.1f}%)")
    lines.append("")
    lines.append(
        "[dim]Note: these are expiry targets. You can exit early for profit "
        "if momentum carries the contract value higher before expiry.[/dim]"
    )

    console.print(Panel(
        "\n".join(l for l in lines if l is not None),
        title=f"[bold cyan]{ticker} ${contract.strike:g} {side.upper()} — Contract Analysis[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))

def _cmd_contract(args: argparse.Namespace) -> None:
    """
    tradebrain contract "INTC $150c 2026-06-18" --budget 200
    tradebrain contract "NVDA 220 call 2026-06-18" --budget 500

    Analyzes a specific option contract:
    - Fetches live price and Greeks
    - Runs momentum research on the ticker
    - Shows 2x/3x/5x targets
    - Kelly sizing
    """
    import re
    from bot.chain_yf import get_expirations, get_chain, get_spot, ChainError
    from bot.select import _effective_mid
    from bot.bs import compute_greeks, kelly_size
    from bot.config import get_settings
    from bot.research import research_ticker

    spec = args.spec.strip()

    # --- parse contract spec ---
    # Supported formats (use single quotes in shell to avoid $-expansion):
    #   'INTC $150c 2026-06-18'   'INTC 150c 2026-06-18'
    #   'NVDA $220 call 2026-06-18'   'NVDA 220 put 2026-06-18'
    #
    # Ticker MUST be the first token — anchored match prevents false positives
    # from strike digits or expiry components.
    ticker_m = re.match(r'^([A-Z]{1,6})\s', spec.upper())

    # Dollar sign is optional — shell eats it inside double quotes.
    # Pattern: optional $, digits, optional whitespace, then c/p/call/put
    strike_m = re.search(r'\$?([\d.]+)\s*([CP](?:all|ut)?)\b', spec, re.IGNORECASE)
    expiry_m = re.search(r'(\d{4}-\d{2}-\d{2})', spec)

    if not ticker_m or not strike_m:
        console.print("[red]Could not parse contract spec.[/red]")
        console.print(
            "[dim]Use single quotes to avoid shell variable expansion:[/dim]\n"
            "  [bold]tradebrain contract 'INTC \\$150c 2026-06-18' --budget 200[/bold]\n"
            "[dim]Also works without the $ sign:[/dim]\n"
            "  [bold]tradebrain contract 'INTC 150c 2026-06-18' --budget 200[/bold]"
        )
        return

    ticker = ticker_m.group(1).upper()
    strike = float(strike_m.group(1))
    side_raw = strike_m.group(2).lower()
    side = "call" if side_raw in ("c", "call") else "put"
    expiry = expiry_m.group(1) if expiry_m else None

    console.print(f"\n[dim]Contract:[/dim] {ticker} ${strike:.2f} {side.upper()} "
                  f"exp={expiry or 'nearest'}\n")

    # --- find the contract in the chain ---
    with console.status(f"[cyan]Fetching {ticker} option chain...[/cyan]", spinner="dots"):
        try:
            exps = get_expirations(ticker)
        except ChainError as e:
            console.print(f"[red]Could not fetch expirations: {e}[/red]")
            return

        # find matching expiry
        target_exp = None
        if expiry:
            target_exp = expiry if expiry in exps else None
            if not target_exp:
                # find closest
                from datetime import datetime
                target_dt = datetime.strptime(expiry, "%Y-%m-%d")
                closest = min(exps, key=lambda e: abs(
                    (datetime.strptime(e, "%Y-%m-%d") - target_dt).days
                ))
                target_exp = closest
                console.print(f"[dim]Exact expiry not found, using closest: {target_exp}[/dim]")
        else:
            # use nearest expiry with DTE > 7
            from datetime import date, datetime
            today = date.today()
            valid = [e for e in exps
                     if (datetime.strptime(e, "%Y-%m-%d").date() - today).days > 7]
            target_exp = valid[0] if valid else exps[0]

        try:
            chain = get_chain(ticker, target_exp)
        except ChainError as e:
            console.print(f"[red]Could not fetch chain: {e}[/red]")
            return

        # find closest strike
        matching = [c for c in chain if c.call_put == side]
        if not matching:
            console.print(f"[red]No {side} contracts found for {ticker} {target_exp}[/red]")
            return

        contract = min(matching, key=lambda c: abs(c.strike - strike))
        if abs(contract.strike - strike) > 5:
            console.print(f"[yellow]Exact strike ${strike} not found. "
                          f"Using closest: ${contract.strike}[/yellow]")

    # --- get live price ---
    with console.status("[cyan]Fetching live data...[/cyan]", spinner="dots"):
        try:
            spot = get_spot(ticker)
        except ChainError:
            spot = 0.0

        m, used_last = _effective_mid(contract.bid, contract.ask, contract.last, True)
        if m is None:
            console.print("[red]No price data for this contract — market may be closed.[/red]")
            return

        cost = m * 100
        over_budget = cost > args.budget
        dte_days = max(0, (
            __import__('datetime').datetime.strptime(target_exp, "%Y-%m-%d").date()
            - __import__('datetime').date.today()
        ).days)

    # --- compute Greeks ---
    greeks = {}
    if contract.iv and contract.iv > 0 and spot > 0:
        greeks = compute_greeks(
            spot    = spot,
            strike  = contract.strike,
            dte     = dte_days,
            iv      = contract.iv,
            side    = side,
            premium = m,
        )

    # --- run research ---
    with console.status(f"[cyan]Researching {ticker}...[/cyan]", spinner="dots"):
        direction_word = "bearish downside" if side == "put" else "bullish upside"
        research = research_ticker(
            ticker          = ticker,
            thesis          = f"{ticker} {direction_word} play, analyzing ${contract.strike} {side} at ${spot:.0f} spot",
            budget          = args.budget,
            context_tickers = [],
        )
    
    # --- earnings warning ---
    earnings_warning = None
    if research.earnings_days_away is not None and research.earnings_days_away > 0:
        if research.earnings_days_away < dte_days:
            earnings_warning = (
                f"[bold red]⚠ EARNINGS IN {research.earnings_days_away} DAYS — "
                f"this contract spans the earnings report.[/bold red]\n"
                f"[red]IV will likely crush 30-50% after the report even if the move is correct.\n"
                f"Plan: exit before earnings OR size knowing you're taking earnings risk.[/red]"
            )
        elif research.earnings_days_away <= 7:
            earnings_warning = (
                f"[yellow]⚠ Earnings in {research.earnings_days_away} days — "
                f"contract expires before report. Pure run-up play.[/yellow]"
            )

    # --- display ---
    # over-budget notice
    if over_budget:
        console.print(
            f"\n  [yellow]⚠ Contract costs ${cost:.0f} — exceeds --budget ${args.budget:.0f}. "
            f"Showing analysis anyway.[/yellow]\n"
        )
    
    # earnings warning
    if earnings_warning:
        console.print(earnings_warning)
        console.print()

    _print_contract_detail(
        ticker     = ticker,
        contract   = contract,
        side       = side,
        spot       = spot,
        m          = m,
        cost       = cost,
        dte_days   = dte_days,
        greeks     = greeks,
        used_last  = used_last,
        target_exp = target_exp,
    )

    # research summary
    _print_research(research)

    # direction conflict warning — only meaningful here since engine.py
    # handles this for the main flow
    if research.thesis_verdict == "contradicted":
        direction_label = "bearish put" if side == "put" else "bullish call"
        console.print(
            f"\n  [bold red]⚠ DATA CONTRADICTS your {direction_label} thesis — "
            f"review carefully before entering.[/bold red]\n"
        )

    # Kelly sizing
    if greeks.get("prob_profit"):
        bankroll = args.bankroll if args.bankroll else get_settings().bankroll_usd
        k = kelly_size(
            pop      = greeks["prob_profit"],
            cost     = cost,
            bankroll = bankroll,
        )
        if k:
            color = (
                "green"  if k["max_contracts"] >= 1 and k["suggested_usd"] >= cost else
                "yellow" if k["full_kelly_pct"] > 0 else
                "red"
            )
            console.print(
                f"\n  [bold]Kelly sizing[/bold]  "
                f"PoP=[bold]{greeks['prob_profit']*100:.0f}%[/bold]  "
                f"half-Kelly=[bold]{k['half_kelly_pct']:.1f}%[/bold]  "
                f"suggested=[bold]${k['suggested_usd']:.0f}[/bold]\n"
                f"  [{color}]{k['verdict']}[/{color}]\n"
            )


def _cmd_flow(args: argparse.Namespace) -> None:
    """
    tradebrain flow 'APLD $35c 470k 0DTE' --budget 500
    tradebrain flow 'NVDA 230c 2.3M 2026-06-20' --budget 1000
    tradebrain flow 'MU 600p 150k 30DTE' --budget 500

    Parses an institutional flow alert, scales it to your budget,
    fetches live contract data, Greeks, research, and Kelly sizing.
    """
    import re
    from datetime import date, datetime, timedelta
    from bot.chain_yf import get_expirations, get_chain, get_spot, ChainError
    from bot.select import _effective_mid
    from bot.bs import compute_greeks, kelly_size
    from bot.config import get_settings
    from bot.research import research_ticker

    alert = args.alert.strip()

    # -----------------------------------------------------------------------
    # Parse the flow alert string
    # -----------------------------------------------------------------------
    # Ticker — first token, anchored
    ticker_m = re.match(r'^([A-Z]{1,6})\s', alert.upper())

    # Strike + side — optional $, digits, c/p/call/put
    strike_m = re.search(r'\$?([\d.]+)\s*([CP](?:all|ut)?)\b', alert, re.IGNORECASE)

    # Notional — 470k, 2.3M, 1.2B or plain number like 470000
    notional_m = re.search(r'([\d.]+)\s*([KMB])\b', alert, re.IGNORECASE)
    if not notional_m:
        notional_m = re.search(r'\b([\d]{5,})\b', alert)  # bare number fallback

    # Expiry — YYYY-MM-DD, 0DTE, 30DTE
    date_m  = re.search(r'(\d{4}-\d{2}-\d{2})', alert)
    dte_m   = re.search(r'\b(\d+)DTE\b', alert, re.IGNORECASE)

    if not ticker_m or not strike_m:
        console.print("[red]Could not parse flow alert.[/red]")
        console.print(
            "[dim]Supported formats (use single quotes):[/dim]\n"
            "  [bold]tradebrain flow 'APLD 35c 470k 0DTE' --budget 500[/bold]\n"
            "  [bold]tradebrain flow 'NVDA 230c 2.3M 2026-06-20' --budget 1000[/bold]\n"
            "  [bold]tradebrain flow 'MU 600p 150k 30DTE' --budget 500[/bold]"
        )
        return

    ticker   = ticker_m.group(1).upper()
    strike   = float(strike_m.group(1))
    side_raw = strike_m.group(2).lower()
    side     = "call" if side_raw in ("c", "call") else "put"

    # Parse notional to dollars
    notional_usd: float = 0.0
    if notional_m:
        if notional_m.lastindex == 2:
            val    = float(notional_m.group(1))
            suffix = notional_m.group(2).upper()
            multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
            notional_usd = val * multiplier.get(suffix, 1)
        else:
            notional_usd = float(notional_m.group(1))

    # Resolve expiry
    today = date.today()
    expiry: Optional[str] = None
    if date_m:
        expiry = date_m.group(1)
    elif dte_m:
        target_dte = int(dte_m.group(1))
        expiry = (today + timedelta(days=target_dte)).strftime("%Y-%m-%d")
    # if neither, expiry stays None — chain fetcher picks nearest

    console.print(
        f"\n[dim]Flow alert:[/dim] {ticker} ${strike:.2f} {side.upper()}  "
        f"notional={'${:,.0f}'.format(notional_usd) if notional_usd else 'unknown'}  "
        f"exp={expiry or 'nearest'}\n"
    )

    # -----------------------------------------------------------------------
    # Fetch chain + live price
    # -----------------------------------------------------------------------
    with console.status(f"[cyan]Fetching {ticker} option chain...[/cyan]", spinner="dots"):
        try:
            exps = get_expirations(ticker)
        except ChainError as e:
            console.print(f"[red]Could not fetch expirations: {e}[/red]")
            return

        # resolve target expiry
        target_exp: str
        if expiry:
            if expiry in exps:
                target_exp = expiry
            else:
                target_dt = datetime.strptime(expiry, "%Y-%m-%d")
                target_exp = min(exps, key=lambda e: abs(
                    (datetime.strptime(e, "%Y-%m-%d") - target_dt).days
                ))
                console.print(f"[dim]Exact expiry not found, using closest: {target_exp}[/dim]")
        else:
            valid = [e for e in exps
                     if (datetime.strptime(e, "%Y-%m-%d").date() - today).days > 0]
            target_exp = valid[0] if valid else exps[0]

        try:
            chain = get_chain(ticker, target_exp)
        except ChainError as e:
            console.print(f"[red]Could not fetch chain: {e}[/red]")
            return

        matching = [c for c in chain if c.call_put == side]
        if not matching:
            console.print(f"[red]No {side} contracts found for {ticker} {target_exp}[/red]")
            return

        contract = min(matching, key=lambda c: abs(c.strike - strike))
        if abs(contract.strike - strike) > 10:
            console.print(
                f"[yellow]Exact strike ${strike} not found. "
                f"Using closest: ${contract.strike}[/yellow]"
            )

    with console.status("[cyan]Fetching live price...[/cyan]", spinner="dots"):
        try:
            spot = get_spot(ticker)
        except ChainError:
            spot = 0.0

        m, used_last = _effective_mid(contract.bid, contract.ask, contract.last, True)
        if m is None:
            console.print("[red]No price data for this contract — market may be closed.[/red]")
            return

        cost = m * 100
        dte_days = max(0, (
            datetime.strptime(target_exp, "%Y-%m-%d").date() - today
        ).days)

    # -----------------------------------------------------------------------
    # Flow classification
    # -----------------------------------------------------------------------
    otm_pct = ((strike - spot) / spot * 100) if side == "call" else ((spot - strike) / spot * 100)
    inst_contracts = int(notional_usd / cost) if cost > 0 and notional_usd > 0 else 0

    # Classify flow type using structural signals
    # Institutions buy long-dated puts to hedge longs — that's not a bearish bet
    # Short-dated OTM calls with large notional = directional sweep
    if side == "call":
        if dte_days <= 5:
            flow_type = "AGGRESSIVE BULLISH SWEEP"
            flow_color = "green"
            flow_note = "Near-expiry call buying — high conviction directional bet on immediate move."
        elif dte_days <= 30 and otm_pct > 0:
            flow_type = "BULLISH SWEEP"
            flow_color = "green"
            flow_note = "Short-dated OTM calls — betting on upside move within weeks."
        else:
            flow_type = "BULLISH POSITION"
            flow_color = "green"
            flow_note = "Longer-dated call buying — directional or LEAPS-style position."
    else:  # put
        if dte_days >= 60 and notional_usd >= 500_000:
            flow_type = "LIKELY HEDGE / PROTECTION"
            flow_color = "yellow"
            flow_note = "Long-dated large put — institutions use these to protect long stock positions. May NOT be a directional bearish bet."
        elif dte_days <= 5:
            flow_type = "AGGRESSIVE BEARISH SWEEP"
            flow_color = "red"
            flow_note = "Near-expiry put buying — high conviction bet on immediate downside or known catalyst."
        else:
            flow_type = "BEARISH SWEEP"
            flow_color = "red"
            flow_note = "Short-to-medium dated puts — directional downside bet."

    # Block vs sweep by notional size
    size_label = "BLOCK" if notional_usd >= 1_000_000 else "SWEEP"

    # -----------------------------------------------------------------------
    # Compute Greeks
    # -----------------------------------------------------------------------
    greeks: dict = {}
    if contract.iv and contract.iv > 0 and spot > 0:
        greeks = compute_greeks(
            spot    = spot,
            strike  = contract.strike,
            dte     = dte_days,
            iv      = contract.iv,
            side    = side,
            premium = m,
        )

    # -----------------------------------------------------------------------
    # Run research
    # -----------------------------------------------------------------------
    with console.status(f"[cyan]Researching {ticker}...[/cyan]", spinner="dots"):
        direction_word = "bearish downside" if side == "put" else "bullish upside"
        research = research_ticker(
            ticker          = ticker,
            thesis          = (
                f"Institutional flow alert: {direction_word} — "
                f"${strike} {side} with {'${:,.0f}'.format(notional_usd) if notional_usd else 'large'} notional. "
                f"Why would an institution place this bet? Is the thesis supported?"
            ),
            budget          = args.budget,
            context_tickers = [],
        )

    # Override flow type if earnings are imminent — earnings plays are a
    # distinct category from momentum sweeps and must be labeled differently
    if research.earnings_days_away is not None and research.earnings_days_away <= 5:
        if side == "call":
            flow_type  = "EARNINGS CALL PLAY"
            flow_color = "yellow"
            flow_note  = (
                f"Earnings in {research.earnings_days_away} days — "
                f"betting on a positive earnings surprise, not a momentum sweep. "
                f"IV crush will hit hard if the move disappoints."
            )
        else:
            flow_type  = "EARNINGS PUT PLAY"
            flow_color = "yellow"
            flow_note  = (
                f"Earnings in {research.earnings_days_away} days — "
                f"betting on an earnings miss or disappointment. "
                f"Could also be protective hedging ahead of the report."
            )
    
    # -----------------------------------------------------------------------
    # Earnings warning
    # -----------------------------------------------------------------------
    earnings_warning: Optional[str] = None
    if research.earnings_days_away is not None and research.earnings_days_away > 0:
        if research.earnings_days_away < dte_days:
            earnings_warning = (
                f"[bold red]⚠ EARNINGS IN {research.earnings_days_away} DAYS — "
                f"contract spans the report.[/bold red]\n"
                f"[red]Institutional flow into earnings is often a hedge, not a directional bet. "
                f"IV crush will hit this contract hard post-report.[/red]"
            )
        elif research.earnings_days_away <= 7:
            earnings_warning = (
                f"[yellow]⚠ Earnings in {research.earnings_days_away} days — "
                f"contract expires before report. Pure run-up / catalyst play.[/yellow]"
            )

    # -----------------------------------------------------------------------
    # Display — Panel 1: Flow Summary
    # -----------------------------------------------------------------------
    iv_str     = f"{contract.iv*100:.0f}%" if contract.iv else "—"
    price_src  = "last [NO BID/ASK]" if used_last else "mid"
    spread_str = (
        f"{(contract.ask - contract.bid) / m * 100:.1f}%"
        if contract.bid and contract.ask and contract.bid > 0 else "—"
    )
    otm_label  = f"{otm_pct:+.1f}% OTM" if otm_pct > 0 else f"{abs(otm_pct):.1f}% ITM"

    flow_lines = []
    flow_lines.append(f"[bold]Ticker[/bold]       {ticker}  (spot ${spot:.2f})")
    flow_lines.append(f"[bold]Contract[/bold]     ${contract.strike:g} {side.upper()}  exp={target_exp}  ({dte_days} DTE)")
    flow_lines.append(f"[bold]Strike vs Spot[/bold]  {otm_label}")
    flow_lines.append(f"[bold]Contract price[/bold]  ${m:.2f} ({price_src})  →  ${cost:.0f}/contract")
    if contract.ask:
        flow_lines.append(f"[bold]Ask[/bold]          ${contract.ask:.2f} (execution price)")
    flow_lines.append(f"[bold]IV[/bold]           {iv_str}   Spread {spread_str}   OI {contract.oi or 0:,}   Vol {contract.volume or 0:,}")
    flow_lines.append("")
    if notional_usd > 0:
        flow_lines.append(f"[bold]Notional[/bold]     [bold]{'${:,.0f}'.format(notional_usd)}[/bold]  ({size_label})")
        if inst_contracts > 0:
            flow_lines.append(f"[bold]Inst. contracts[/bold] ~{inst_contracts:,} contracts")
    flow_lines.append("")
    flow_lines.append(f"[bold]Flow type[/bold]    [{flow_color}][bold]{flow_type}[/bold][/{flow_color}]")
    flow_lines.append(f"[dim]{flow_note}[/dim]")
    if earnings_warning:
        flow_lines.append("")
        flow_lines.append(earnings_warning)

    console.print(Panel(
        "\n".join(flow_lines),
        title=f"[bold magenta]{ticker} — Institutional Flow Alert[/bold magenta]",
        border_style="magenta",
        padding=(1, 2),
    ))

    # -----------------------------------------------------------------------
    # Display — Panel 2: Scale to your budget
    # -----------------------------------------------------------------------
    if notional_usd > 0 and cost > 0:
        user_contracts  = max(1, int(args.budget // cost))
        user_notional   = user_contracts * cost
        scale_ratio     = notional_usd / user_notional if user_notional > 0 else 0
        pct_of_inst     = (user_notional / notional_usd * 100) if notional_usd > 0 else 0

        scale_lines = []
        scale_lines.append(
            f"  Institution  [bold]{'${:,.0f}'.format(notional_usd)}[/bold]"
            f"  →  ~{inst_contracts:,} contracts"
        )
        scale_lines.append(
            f"  Your budget  [bold]${args.budget:.0f}[/bold]"
            f"  →  {user_contracts} contract{'s' if user_contracts > 1 else ''}"
            f"  (${user_notional:.0f})"
        )
        scale_lines.append("")
        scale_lines.append(
            f"  You are placing the [bold]same directional bet[/bold] at "
            f"[bold]{pct_of_inst:.2f}%[/bold] of institutional size  "
            f"(1 : {scale_ratio:,.0f} scale)"
        )
        scale_lines.append("")
        scale_lines.append(
            "[dim]Retail edge: you can exit faster and size down. "
            "Institution may be hedging, averaging in, or have information you don't.[/dim]"
        )

        console.print(Panel(
            "\n".join(scale_lines),
            title="[bold]Scale — Your Position vs Institution[/bold]",
            border_style="blue",
            padding=(1, 2),
        ))

    _print_contract_detail(
        ticker     = ticker,
        contract   = contract,
        side       = side,
        spot       = spot,
        m          = m,
        cost       = cost,
        dte_days   = dte_days,
        greeks     = greeks,
        used_last  = used_last,
        target_exp = target_exp,
    )

    # -----------------------------------------------------------------------
    # Research panel
    # -----------------------------------------------------------------------
    _print_research(research)

    # contradiction warning — is the tape agreeing with the flow?
    if side == "call" and research.thesis_verdict == "contradicted":
        console.print(
            "\n  [bold red]⚠ DATA CONTRADICTS this bullish flow — "
            "institution may be hedging or wrong.[/bold red]\n"
        )
    elif side == "put" and research.thesis_verdict == "contradicted":
        console.print(
            "\n  [bold red]⚠ DATA CONTRADICTS this bearish flow — "
            "strong bullish tape. Confirm this isn't a hedge.[/bold red]\n"
        )

    # -----------------------------------------------------------------------
    # Smart side flip — if LLM strongly contradicts the flow direction,
    # show contracts in the opposite direction as the retail recommendation
    # -----------------------------------------------------------------------
    retail_side = side  # default: follow the institutional flow direction
    side_flipped = False

    if research.recommended_direction in ("bearish", "bullish"):
        flow_is_bullish = side == "call"
        data_is_bullish = research.recommended_direction == "bullish"
        if flow_is_bullish != data_is_bullish and research.confidence == "high":
            retail_side = "put" if flow_is_bullish else "call"
            side_flipped = True

    # -----------------------------------------------------------------------
    # Suggested contracts for retail — ranked picks at user budget
    # -----------------------------------------------------------------------
    if side_flipped:
        console.print(Panel(
            f"[bold red]⚠ HIGH CONFIDENCE CONTRA-FLOW SIGNAL[/bold red]\n"
            f"[red]Data strongly disagrees with the institutional direction.\n"
            f"Showing [bold]{retail_side.upper()}[/bold] contracts instead — "
            f"the tape says fade this flow.[/red]\n\n"
            f"[dim]The flow above shows what the institution traded.\n"
            f"The picks below reflect what the DATA supports — "
            f"the opposite direction.[/dim]",
            title="[bold red]Contra-Flow — Fade This Trade[/bold red]",
            border_style="red",
            padding=(1, 2),
        ))
    else:
        console.print(Panel(
            "[dim]The flow above shows what the institution traded.\n"
            "The picks below show what YOU should trade to express the same thesis "
            "at your budget — better DTE, better liquidity, right size.[/dim]",
            title="[bold green]Suggested Contracts — Your Trade[/bold green]",
            border_style="green",
            padding=(1, 2),
        ))

    with console.status("[cyan]Finding best contracts for your budget...[/cyan]", spinner="dots"):
        from bot.engine import get_picks, _dte_window
        dte_min, dte_max, _ = _dte_window(
            "1-3 months",
            earnings_days_away=research.earnings_days_away,
        )
        retail_picks, fail_reason = get_picks(
            ticker     = ticker,
            side       = retail_side,
            underlying = spot,
            budget     = args.budget,
            dte_min    = dte_min,
            dte_max    = dte_max,
        )

    if retail_picks:
        _print_picks(retail_picks, args.budget)
        _print_kelly_sizing(retail_picks, args.budget,
                            bankroll_override=args.bankroll if args.bankroll else None)
    else:
        _print_budget_warning(ticker, args.budget, fail_reason)

def _is_market_open() -> bool:
    """Returns True if US market is currently open (9:30am-4pm ET, Mon-Fri)."""
    from datetime import datetime, timezone, timedelta
    # ET is UTC-5 (EST) or UTC-4 (EDT). Use UTC-4 as approximation (EDT).
    et_offset = timedelta(hours=-4)
    now_et = datetime.now(timezone.utc) + et_offset
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    market_open  = now_et.replace(hour=9,  minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0,  second=0, microsecond=0)
    return market_open <= now_et <= market_close

# ---------------------------------------------------------------------------
# Paper trading commands
# ---------------------------------------------------------------------------

def _fetch_live_contract_price(
    ticker: str,
    strike: float,
    side: str,
    expiry: str,
) -> tuple[Optional[float], bool]:
    """
    Fetches the current mid price for a contract from yfinance.
    Returns (mid_price_dollars, used_last_price).
    used_last_price=True means market is closed — price may be stale.
    """
    from bot.chain_yf import get_chain, ChainError
    from bot.select import _effective_mid
    try:
        chain = get_chain(ticker, expiry)
        matching = [c for c in chain if c.call_put == side]
        if not matching:
            return None, False
        contract = min(matching, key=lambda c: abs(c.strike - strike))
        m, used_last = _effective_mid(contract.bid, contract.ask, contract.last, True)
        if m is None:
            return None, False
        return m * 100, used_last  # return dollars per contract
    except (ChainError, Exception):
        return None, False


def _cmd_log_trade(args: argparse.Namespace) -> None:
    """
    tradebrain log-trade NVDA --strike 235 --expiry 2026-06-18 --side call --quantity 1 --paper --source "tradebrain"
    tradebrain log-trade NVDA --strike 235 --expiry 2026-06-18 --side call --quantity 1 --real --source "tradebrain"
    """
    from bot.logger import log_paper_trade

    ticker = args.ticker.upper().strip()
    strike = args.strike
    side   = args.side.lower()
    expiry = args.expiry
    qty    = args.quantity
    source = args.source or "own research"
    thesis = args.thesis

    trade_type = "real" if args.real else "paper"

    # -----------------------------------------------------------------------
    # Entry price — real: prompt user, paper: fetch live from yfinance
    # -----------------------------------------------------------------------
    if args.real:
        # Real trade — user enters exact Robinhood execution price
        console.print(
            f"\n  [bold]Real trade:[/bold] {ticker} ${strike:g} {side.upper()} "
            f"exp={expiry}  qty={qty}\n"
        )
        try:
            raw = console.input(
                "  [bold cyan]What did you pay on Robinhood? "
                "Enter contract price (e.g. 4.10): [/bold cyan]"
            ).strip()
            entry_cost = float(raw) * 100
            console.print(f"  [dim]${float(raw):.2f} × 100 = ${entry_cost:.2f}/contract[/dim]\n")
        except (ValueError, KeyboardInterrupt):
            console.print("[red]Invalid price. Trade not logged.[/red]")
            return

        if entry_cost <= 0:
            console.print("[red]Price must be greater than 0. Trade not logged.[/red]")
            return

    else:
        # Paper trade — fetch live mid price automatically
        console.print(
            f"\n  [bold]Paper trade:[/bold] {ticker} ${strike:g} {side.upper()} "
            f"exp={expiry}  qty={qty}\n"
        )
        with console.status(
            f"[cyan]Fetching live price for {ticker} ${strike:g} {side.upper()}...[/cyan]",
            spinner="dots"
        ):
            entry_cost, used_last = _fetch_live_contract_price(
                ticker, strike, side, expiry
            )

        if entry_cost is None:
            console.print(
                f"[red]Could not fetch live price for {ticker} ${strike:g} {side.upper()} "
                f"exp={expiry}.\n"
                f"Check ticker, strike, and expiry are correct.[/red]"
            )
            return

        if not _is_market_open():
            console.print(
                f"  [yellow]⚠ Market is closed — using last traded price "
                f"${entry_cost:.2f}/contract (may differ from tomorrow's open).\n"
                f"  Consider re-logging at market open for accuracy.[/yellow]\n"
            )
        else:
            console.print(
                f"  [green]✓ Live mid price fetched: "
                f"${entry_cost:.2f}/contract[/green]\n"
            )

    # -----------------------------------------------------------------------
    # Log the trade
    # -----------------------------------------------------------------------
    total = entry_cost * qty
    trade_id = log_paper_trade(
        ticker     = ticker,
        strike     = strike,
        side       = side,
        expiry     = expiry,
        entry_cost = entry_cost,
        quantity   = qty,
        trade_type = trade_type,
        source     = source,
        thesis     = thesis,
        llm_provider  = getattr(args, 'llm_provider', None),
    )

    type_color = "green" if trade_type == "real" else "blue"
    console.print(Panel(
        f"  [bold]ID[/bold]           #{trade_id}\n"
        f"  [bold]Type[/bold]         [{type_color}]{trade_type.upper()}[/{type_color}]\n"
        f"  [bold]Contract[/bold]     {ticker} ${strike:g} {side.upper()} exp={expiry}\n"
        f"  [bold]Entry price[/bold]  ${entry_cost:.2f}/contract\n"
        f"  [bold]Quantity[/bold]     {qty} contract{'s' if qty > 1 else ''}\n"
        f"  [bold]Total invested[/bold]  ${total:.2f}\n"
        f"  [bold]LLM[/bold]          {getattr(args, 'llm_provider', None) or 'not specified'}\n"
        f"  [bold]Source[/bold]       {source}\n"
        + (f"  [bold]Thesis[/bold]       {thesis}\n" if thesis else ""),
        title=f"[bold {'green' if trade_type == 'real' else 'blue'}]"
              f"Trade Logged — #{trade_id}[/bold {'green' if trade_type == 'real' else 'blue'}]",
        border_style="green" if trade_type == "real" else "blue",
        padding=(1, 2),
    ))
    console.print(
        f"  [dim]Track with: [bold]tradebrain portfolio[/bold]  |  "
        f"Close with: [bold]tradebrain close-trade {trade_id} --exit-cost PRICE[/bold][/dim]\n"
    )


def _cmd_portfolio(args: argparse.Namespace) -> None:
    """
    tradebrain portfolio           — open positions with live P&L
    tradebrain portfolio --all     — open + closed history
    tradebrain portfolio --real    — real trades only
    tradebrain portfolio --paper   — paper trades only
    """
    from bot.logger import get_open_trades, get_all_trades
    from datetime import date, datetime

    if args.all:
        trades = get_all_trades()
    else:
        trades = get_open_trades()

    # filter by type
    if args.real and not args.paper:
        trades = [t for t in trades if t["trade_type"] == "real"]
    elif args.paper and not args.real:
        trades = [t for t in trades if t["trade_type"] == "paper"]

    if not trades:
        console.print(Panel(
            "[dim]No trades logged yet.\n\n"
            "Log your first trade with:\n"
            "  [bold]tradebrain log-trade NVDA --strike 235 --expiry 2026-06-18 "
            "--side call --quantity 1 --paper --source \"tradebrain\"[/bold][/dim]",
            title="[bold]Portfolio — No Trades[/bold]",
            border_style="dim",
            padding=(1, 2),
        ))
        return

    open_trades   = [t for t in trades if t["status"] == "open"]
    closed_trades = [t for t in trades if t["status"] in ("closed", "expired")]

    today = date.today()

    # -----------------------------------------------------------------------
    # Fetch live prices for open trades
    # -----------------------------------------------------------------------
    live_prices: dict[int, Optional[float]] = {}
    stale_ids:   set[int]                   = set()

    market_open = _is_market_open()

    if open_trades:
        with console.status(
            "[cyan]Fetching live prices for open positions...[/cyan]",
            spinner="dots"
        ):
            for t in open_trades:
                price, used_last = _fetch_live_contract_price(
                    t["ticker"], t["strike"], t["side"], t["expiry"]
                )
                live_prices[t["id"]] = price
                if not market_open:
                    stale_ids.add(t["id"])

    # -----------------------------------------------------------------------
    # Open positions table
    # -----------------------------------------------------------------------
    total_invested_open = 0.0
    total_pnl_open      = 0.0
    
    if open_trades:
        open_table = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold cyan",
            padding=(0, 1),
        )
        open_table.add_column("ID",     style="dim", width=4)
        open_table.add_column("Type",   justify="center", width=6)
        open_table.add_column("Ticker", justify="center")
        open_table.add_column("Strike", justify="right")
        open_table.add_column("Side",   justify="center")
        open_table.add_column("Expiry", justify="center")
        open_table.add_column("DTE",    justify="right")
        open_table.add_column("Qty",    justify="right")
        open_table.add_column("Entry",  justify="right")
        open_table.add_column("Now",    justify="right")
        open_table.add_column("P&L",    justify="right")
        open_table.add_column("%",      justify="right")
        open_table.add_column("Source", justify="left")

        for t in open_trades:
            exp_date  = datetime.strptime(t["expiry"], "%Y-%m-%d").date()
            dte       = max(0, (exp_date - today).days)
            dte_color = "red" if dte <= 5 else "yellow" if dte <= 14 else "dim"

            now_price = live_prices.get(t["id"])
            type_color = "green" if t["trade_type"] == "real" else "blue"

            if now_price is not None:
                pnl_dollars = (now_price - t["entry_cost"]) * t["quantity"]
                pnl_pct     = pnl_dollars / t["total_invested"] * 100
                pnl_color   = "green" if pnl_dollars >= 0 else "red"
                now_str     = f"${now_price:.2f}"
                if t["id"] in stale_ids:
                    now_str += "[dim]*[/dim]"
                pnl_str  = f"[{pnl_color}]{'+' if pnl_dollars >= 0 else ''}${pnl_dollars:.0f}[/{pnl_color}]"
                pct_str  = f"[{pnl_color}]{'+' if pnl_pct >= 0 else ''}{pnl_pct:.0f}%[/{pnl_color}]"
                total_pnl_open += pnl_dollars
            else:
                now_str = "[dim]—[/dim]"
                pnl_str = "[dim]—[/dim]"
                pct_str = "[dim]—[/dim]"

            total_invested_open += t["total_invested"]

            source_short = (t["source"] or "")[:18]

            type_label = "REAL" if t["trade_type"] == "real" else "PAPR"
            open_table.add_row(
                str(t["id"]),
                f"[{type_color}]{type_label}[/{type_color}]",
                t["ticker"],
                f"${t['strike']:g}",
                t["side"],
                t["expiry"],
                f"[{dte_color}]{dte}d[/{dte_color}]",
                str(t["quantity"]),
                f"${t['entry_cost']:.2f}",
                now_str,
                pnl_str,
                pct_str,
                source_short,
            )

        pnl_color_total = "green" if total_pnl_open >= 0 else "red"
        console.print(Panel(
            open_table,
            title="[bold]Open Positions[/bold]",
            border_style="cyan",
            padding=(1, 1),
        ))
        if stale_ids:
            console.print(
                "  [dim]* Market closed — using last traded price. "
                "P&L estimates may be inaccurate.[/dim]"
            )

    # -----------------------------------------------------------------------
    # Closed positions table
    # -----------------------------------------------------------------------
    if closed_trades:
        closed_table = Table(
            box=box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold dim",
            padding=(0, 1),
        )
        closed_table.add_column("ID",     style="dim", width=4)
        closed_table.add_column("Type",   justify="center", width=6)
        closed_table.add_column("Ticker", justify="center")
        closed_table.add_column("Strike", justify="right")
        closed_table.add_column("Side",   justify="center")
        closed_table.add_column("Expiry", justify="center")
        closed_table.add_column("Qty",    justify="right")
        closed_table.add_column("Entry",  justify="right")
        closed_table.add_column("Exit",   justify="right")
        closed_table.add_column("P&L",    justify="right")
        closed_table.add_column("%",      justify="right")
        closed_table.add_column("Status", justify="center")
        closed_table.add_column("Source", justify="left")

        for t in closed_trades:
            pnl_dollars = t.get("pnl_dollars") or 0
            pnl_pct     = t.get("pnl_pct") or 0
            pnl_color   = "green" if pnl_dollars >= 0 else "red"
            type_color  = "green" if t["trade_type"] == "real" else "blue"
            status_color = "dim" if t["status"] == "expired" else "cyan"
            source_short = (t["source"] or "")[:18]

            closed_table.add_row(
                str(t["id"]),
                f"[{type_color}]{'REAL' if t['trade_type'] == 'real' else 'PAPR'}[/{type_color}]",
                t["ticker"],
                f"${t['strike']:g}",
                t["side"],
                t["expiry"],
                str(t["quantity"]),
                f"${t['entry_cost']:.2f}",
                f"${t['exit_cost']:.2f}" if t.get("exit_cost") is not None else "—",
                f"[{pnl_color}]{'+' if pnl_dollars >= 0 else ''}${pnl_dollars:.0f}[/{pnl_color}]",
                f"[{pnl_color}]{'+' if pnl_pct >= 0 else ''}{pnl_pct:.0f}%[/{pnl_color}]",
                f"[{status_color}]{t['status']}[/{status_color}]",
                source_short,
            )

        console.print(Panel(
            closed_table,
            title="[bold dim]Closed Positions[/bold dim]",
            border_style="dim",
            padding=(1, 1),
        ))

# -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    all_closed    = get_all_trades() if not args.all else trades
    closed_only   = [t for t in all_closed if t["status"] in ("closed", "expired")]
    wins          = [t for t in closed_only if (t.get("pnl_dollars") or 0) > 0]
    losses        = [t for t in closed_only if (t.get("pnl_dollars") or 0) <= 0]
    win_rate      = f"{len(wins)}/{len(closed_only)} ({len(wins)/len(closed_only)*100:.0f}%)" if closed_only else "—"

    # breakdown by source
    sources: dict[str, dict] = {}
    for t in closed_only:
        src = (t.get("source") or "own research").split()[0][:12]
        if src not in sources:
            sources[src] = {"W": 0, "L": 0}
        if (t.get("pnl_dollars") or 0) > 0:
            sources[src]["W"] += 1
        else:
            sources[src]["L"] += 1

    source_str = "  ".join(
        f"{k}: {v['W']}W/{v['L']}L" for k, v in sources.items()
    ) if sources else "—"

    real_closed   = [t for t in closed_only if t["trade_type"] == "real"]
    paper_closed  = [t for t in closed_only if t["trade_type"] == "paper"]
    real_pnl      = sum(t.get("pnl_dollars") or 0 for t in real_closed)
    paper_pnl     = sum(t.get("pnl_dollars") or 0 for t in paper_closed)

    open_pnl_color = "green" if total_pnl_open >= 0 else "red"

    # new metrics
    roi_pct = (total_pnl_open / total_invested_open * 100) if total_invested_open > 0 else 0
    roi_color = "green" if roi_pct >= 0 else "red"

    # largest position
    largest = max(open_trades, key=lambda t: t["total_invested"]) if open_trades else None
    largest_str = (
        f"{largest['ticker']} ${largest['strike']:g} {largest['side']} "
        f"${largest['total_invested']:.0f} "
        f"({largest['total_invested']/total_invested_open*100:.0f}% of portfolio)"
    ) if largest and total_invested_open > 0 else "—"

    # expiring soon
    expiring = [
        t for t in open_trades
        if (datetime.strptime(t["expiry"], "%Y-%m-%d").date() - today).days <= 14
    ]
    expiring_str = "  ".join(
        f"[red]#{t['id']} {t['ticker']} ({(datetime.strptime(t['expiry'], '%Y-%m-%d').date() - today).days}d)[/red]"
        for t in sorted(expiring, key=lambda t: t["expiry"])
    ) if expiring else "[green]none[/green]"

    summary_lines = []
    if open_trades:
        summary_lines.append(
            f"  [bold]Open P&L:[/bold]   [{open_pnl_color}]"
            f"{'+' if total_pnl_open >= 0 else ''}${total_pnl_open:.0f}[/{open_pnl_color}]"
            f"   [{roi_color}]({'+' if roi_pct >= 0 else ''}{roi_pct:.1f}% ROI)[/{roi_color}]"
            f"   [dim]Invested: ${total_invested_open:.0f}[/dim]"
        )
        summary_lines.append(
            f"  [bold]Largest:[/bold]    {largest_str}"
        )
        summary_lines.append(
            f"  [bold]Expiring soon:[/bold]  {expiring_str}"
        )
    if closed_only:
        summary_lines.append(
            f"  [bold]Win rate:[/bold]   {win_rate}"
        )
        summary_lines.append(
            f"  [bold]By source:[/bold]  {source_str}"
        )
        real_color  = "green" if real_pnl >= 0 else "red"
        paper_color = "green" if paper_pnl >= 0 else "red"
        summary_lines.append(
            f"  [bold]Real:[/bold]  [{real_color}]{'+' if real_pnl >= 0 else ''}${real_pnl:.0f}[/{real_color}]"
            f"  ({len(real_closed)} closed)   "
            f"[bold]Paper:[/bold]  [{paper_color}]{'+' if paper_pnl >= 0 else ''}${paper_pnl:.0f}[/{paper_color}]"
            f"  ({len(paper_closed)} closed)"
        )

    if summary_lines:
        console.print(Panel(
            "\n".join(summary_lines),
            title="[bold]Summary[/bold]",
            border_style="cyan",
            padding=(1, 2),
        ))


def _cmd_close_trade(args: argparse.Namespace) -> None:
    """
    tradebrain close-trade 3 --exit-cost 820
    tradebrain close-trade 4 --exit-cost 0    # expired worthless
    """
    from bot.logger import close_paper_trade, get_trade_by_id

    trade = get_trade_by_id(args.trade_id)
    if not trade:
        console.print(f"[red]Trade ID #{args.trade_id} not found.[/red]")
        return
    if trade["status"] != "open":
        console.print(
            f"[red]Trade #{args.trade_id} is already "
            f"{trade['status']} — cannot close again.[/red]"
        )
        return

    # -----------------------------------------------------------------------
    # Exit price — real: prompt user, paper: auto-fetch live mid
    # -----------------------------------------------------------------------
    if trade["trade_type"] == "real":
        # always prompt for real trades
        if args.exit_cost is not None:
            exit_cost = args.exit_cost
        else:
            try:
                raw = console.input(
                    f"  [bold cyan]What did you sell for on Robinhood? "
                    f"Enter exit price per share (e.g. 6.20): [/bold cyan]"
                ).strip()
                exit_cost = float(raw) * 100
                console.print(f"  [dim]${float(raw):.2f} × 100 = ${exit_cost:.2f}/contract[/dim]\n")
            except (ValueError, KeyboardInterrupt):
                console.print("[red]Invalid price. Trade not closed.[/red]")
                return
    else:
        # paper trade — auto-fetch live mid price
        if args.exit_cost is not None:
            # user explicitly provided price — use it
            exit_cost = args.exit_cost
            console.print(f"  [dim]Using provided exit price: ${exit_cost:.2f}/contract[/dim]\n")
        else:
            with console.status(
                f"[cyan]Fetching live exit price for "
                f"{trade['ticker']} ${trade['strike']:g} {trade['side'].upper()}...[/cyan]",
                spinner="dots"
            ):
                fetched, used_last = _fetch_live_contract_price(
                    trade["ticker"], trade["strike"],
                    trade["side"], trade["expiry"]
                )

            if fetched is None:
                console.print(
                    "[red]Could not fetch live price. "
                    "Provide manually with --exit-cost PRICE.[/red]"
                )
                return

            exit_cost = fetched
            if not _is_market_open():
                console.print(
                    f"  [yellow]⚠ Market closed — using last traded price "
                    f"${exit_cost:.2f}/contract.[/yellow]\n"
                )
            else:
                console.print(
                    f"  [green]✓ Live exit price fetched: "
                    f"${exit_cost:.2f}/contract[/green]\n"
                )

    updated     = close_paper_trade(args.trade_id, exit_cost)
    pnl_dollars = updated["pnl_dollars"]
    pnl_pct     = updated["pnl_pct"]
    pnl_color   = "green" if pnl_dollars >= 0 else "red"
    status      = updated["status"]

    console.print(Panel(
        f"  [bold]ID[/bold]          #{updated['id']}\n"
        f"  [bold]Contract[/bold]    {updated['ticker']} ${updated['strike']:g} "
        f"{updated['side'].upper()} exp={updated['expiry']}\n"
        f"  [bold]Type[/bold]        {updated['trade_type']}\n"
        f"  [bold]Entry[/bold]       ${updated['entry_cost']:.2f}/contract\n"
        f"  [bold]Exit[/bold]        ${exit_cost:.2f}/contract\n"
        f"  [bold]Quantity[/bold]    {updated['quantity']}\n"
        f"  [bold]P&L[/bold]         [{pnl_color}]"
        f"{'+' if pnl_dollars >= 0 else ''}${pnl_dollars:.0f} "
        f"({'+' if pnl_pct >= 0 else ''}{pnl_pct:.1f}%)[/{pnl_color}]\n"
        f"  [bold]Status[/bold]      {status}",
        title=f"[bold {pnl_color}]Trade Closed — "
              f"{'Profit ✓' if pnl_dollars > 0 else 'Loss ✗' if pnl_dollars < 0 else 'Breakeven'}[/bold {pnl_color}]",
        border_style=pnl_color,
        padding=(1, 2),
    ))

def _cmd_delete_trade(args: argparse.Namespace) -> None:
    """
    tradebrain delete-trade 1
    Permanently removes a trade from the database.
    """
    from bot.logger import delete_paper_trade, get_trade_by_id

    trade = get_trade_by_id(args.trade_id)
    if not trade:
        console.print(f"[red]Trade ID #{args.trade_id} not found.[/red]")
        return

    console.print(
        f"\n  [yellow]Delete trade #{args.trade_id}: "
        f"{trade['ticker']} ${trade['strike']:g} {trade['side'].upper()} "
        f"exp={trade['expiry']} ({trade['trade_type']})? (y/n): [/yellow]",
        end=""
    )
    try:
        answer = input().strip().lower()
    except (KeyboardInterrupt, EOFError):
        return

    if answer not in ("y", "yes"):
        console.print("[dim]Cancelled.[/dim]")
        return

    deleted = delete_paper_trade(args.trade_id)
    if deleted:
        console.print(f"  [green]✓ Trade #{args.trade_id} deleted.[/green]\n")
    else:
        console.print(f"  [red]Could not delete trade #{args.trade_id}.[/red]\n")

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
    
    # route log-trade command
    if len(sys.argv) > 1 and sys.argv[1] == "log-trade":
        lt_ap = argparse.ArgumentParser(prog="tradebrain log-trade")
        lt_ap.add_argument("ticker",              help="Ticker symbol e.g. NVDA")
        lt_ap.add_argument("--strike",  type=float, required=True, help="Strike price e.g. 235")
        lt_ap.add_argument("--expiry",  required=True, help="Expiry date YYYY-MM-DD")
        lt_ap.add_argument("--side",    required=True, choices=["call", "put"], help="call or put")
        lt_ap.add_argument("--quantity",type=int, default=1, help="Number of contracts (default 1)")
        lt_ap.add_argument("--source",  help="Source of the trade idea e.g. 'tradebrain', 'twitter @CheddarFlow'")
        lt_ap.add_argument("--thesis",  help="Optional notes on why you took this trade")
        lt_ap.add_argument("--llm-provider", choices=["gemini", "ollama"], help="LLM used for the analysis that led to this trade")
        type_group = lt_ap.add_mutually_exclusive_group(required=True)
        type_group.add_argument("--paper", action="store_true", help="Paper trade — bot fetches live price")
        type_group.add_argument("--real",  action="store_true", help="Real trade — you enter execution price")
        lt_args = lt_ap.parse_args(sys.argv[2:])
        _cmd_log_trade(lt_args)
        return

    # route portfolio command
    if len(sys.argv) > 1 and sys.argv[1] == "portfolio":
        port_ap = argparse.ArgumentParser(prog="tradebrain portfolio")
        port_ap.add_argument("--all",   action="store_true", help="Show open + closed history")
        port_ap.add_argument("--real",  action="store_true", help="Show real trades only")
        port_ap.add_argument("--paper", action="store_true", help="Show paper trades only")
        port_args = port_ap.parse_args(sys.argv[2:])
        _cmd_portfolio(port_args)
        return

    # route close-trade command
    if len(sys.argv) > 1 and sys.argv[1] == "close-trade":
        ct_ap = argparse.ArgumentParser(prog="tradebrain close-trade")
        ct_ap.add_argument("trade_id", type=int, help="Trade ID from portfolio")
        ct_ap.add_argument("--exit-cost", type=float, default=None,
                           help="Exit price per contract in dollars. "
                                "Paper trades auto-fetch if omitted. "
                                "Real trades prompt if omitted. "
                                "Use 0 for expired worthless.")
        ct_args = ct_ap.parse_args(sys.argv[2:])
        _cmd_close_trade(ct_args)
        return
    
    # route delete-trade command
    if len(sys.argv) > 1 and sys.argv[1] == "delete-trade":
        dt_ap = argparse.ArgumentParser(prog="tradebrain delete-trade")
        dt_ap.add_argument("trade_id", type=int, help="Trade ID to permanently delete")
        dt_args = dt_ap.parse_args(sys.argv[2:])
        _cmd_delete_trade(dt_args)
        return
    
    # route flow command
    if len(sys.argv) > 1 and sys.argv[1] == "flow":
        flow_ap = argparse.ArgumentParser(prog="tradebrain flow")
        flow_ap.add_argument("alert", help="Flow alert e.g. 'APLD 35c 470k 0DTE'")
        flow_ap.add_argument("--budget", type=float, default=500.0, help="Your budget to scale against the institutional notional")
        flow_ap.add_argument("--bankroll", type=float, help="Override bankroll for Kelly sizing")
        flow_ap.add_argument("--llm",      choices=["gemini", "ollama"], default=None, help="Override LLM provider")
        flow_args = flow_ap.parse_args(sys.argv[2:])
        if flow_args.llm:
            os.environ["LLM_PROVIDER"] = flow_args.llm
        _cmd_flow(flow_args)
        return

    # route contract command
    if len(sys.argv) > 1 and sys.argv[1] == "contract":
        contract_ap = argparse.ArgumentParser(prog="tradebrain contract")
        contract_ap.add_argument("spec", help='Contract spec e.g. "INTC $150c 2026-06-18"')
        contract_ap.add_argument("--budget", type=float, default=300.0, help="Used for Kelly sizing context only — contract is shown regardless of cost")
        contract_ap.add_argument("--bankroll", type=float, help="Override bankroll for Kelly sizing")
        contract_ap.add_argument("--llm",      choices=["gemini", "ollama"], default=None, help="Override LLM provider")
        contract_args = contract_ap.parse_args(sys.argv[2:])
        if contract_args.llm:
            os.environ["LLM_PROVIDER"] = contract_args.llm
        _cmd_contract(contract_args)
        return
    
    # route watch command
    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        watch_ap = argparse.ArgumentParser(prog="tradebrain watch")
        watch_ap.add_argument("--budget",    type=float, help="Override default budget from watchlist.json")
        watch_ap.add_argument("--direction", choices=["bullish", "bearish"], help="Force direction for all tickers")
        watch_ap.add_argument("--llm",       choices=["gemini", "ollama"], default=None, help="Override LLM provider")
        watch_args = watch_ap.parse_args(sys.argv[2:])
        if watch_args.llm:
            os.environ["LLM_PROVIDER"] = watch_args.llm
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
    ap.add_argument("--deep", action="store_true", help="Fetch full articles for deeper LLM research (slower)")
    ap.add_argument("--bankroll", type=float, help="Override bankroll for Kelly sizing (default: from .env BANKROLL_USD)")
    ap.add_argument("--llm", choices=["gemini", "ollama"], default=None, help="Override LLM provider for this run: gemini or ollama (default: LLM_PROVIDER from .env)")
    args = ap.parse_args()

    # LLM provider override — takes effect before any research calls
    if args.llm:
        os.environ["LLM_PROVIDER"] = args.llm

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
        with console.status(
            f"[cyan]Researching {len(intake.tickers)} tickers (deep mode)...[/cyan]" if args.deep
            else f"[cyan]Researching {len(intake.tickers)} tickers...[/cyan]",
            spinner="dots"
        ):
            from bot.engine import run_multi
            results = run_multi(intake, deep=args.deep)

        for research, picks, reason, direction_note, earnings_dte_note, pre_earnings_picks in results:
            _print_research(research)
            if direction_note:
                console.print(f"\n  {direction_note}\n")
            if earnings_dte_note:
                console.print(f"\n  [yellow]DTE adjusted:[/yellow] [dim]{earnings_dte_note}[/dim]\n")
            if picks:
                _print_picks(picks, args.budget)
                _print_kelly_sizing(picks, args.budget, bankroll_override=getattr(args, 'bankroll', None))
                if pre_earnings_picks:
                    _print_pre_earnings_picks(
                        pre_earnings_picks,
                        args.budget,
                        hv_rank=research.iv_rank,
                    )
            else:
                _print_budget_warning(research.ticker, args.budget, reason)
    else:
        with console.status(
            "[cyan]Researching (deep mode)...[/cyan]" if args.deep else "[cyan]Researching...[/cyan]",
            spinner="dots"
        ):
            research, picks, reason, direction_note, earnings_dte_note, pre_earnings_picks = run(intake, deep=args.deep)
        _print_research(research)
        if direction_note:
            console.print(f"\n  {direction_note}\n")
        if earnings_dte_note:
            console.print(f"\n  [yellow]DTE adjusted:[/yellow] [dim]{earnings_dte_note}[/dim]\n")
        if picks:
            _print_picks(picks, args.budget)
            _print_kelly_sizing(picks, args.budget, bankroll_override=getattr(args, 'bankroll', None))
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
                    research2, picks2, reason2, direction_note2, earnings_dte_note2, pre_earnings_picks2 = run(new_intake, deep=args.deep)
                if direction_note2:
                    console.print(f"\n  {direction_note2}\n")
                if earnings_dte_note2:
                    console.print(f"\n  [yellow]DTE adjusted:[/yellow] [dim]{earnings_dte_note2}[/dim]\n")
                if picks2:
                    _print_picks(picks2, float(suggested))
                    _print_kelly_sizing(picks2, args.budget, bankroll_override=getattr(args, 'bankroll', None))
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