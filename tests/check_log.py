from bot.logger import get_recent_runs, get_run_picks

runs = get_recent_runs(10)

if not runs:
    print("No runs logged yet.")
else:
    for r in runs:
        print(f"run {r['id']} | {r['ts'][:19]} | {r['ticker']} | {r['direction']} | confidence={r['confidence']} | picks={r['pick_count']}")
        print(f"     thesis: {(r['thesis'] or 'none')[:80]}")
        print(f"     verdict: {r['verdict']} | reasoning: {(r['reasoning'] or 'none')[:100]}")
        picks = get_run_picks(r['id'])
        if picks:
            for p in picks:
                print(f"     #{p['rank']} {p['ticker']} ${p['strike']} {p['side']} exp={p['expiration']} cost=${p['cost']:.0f} breakeven=${p['breakeven']:.2f}")
        else:
            print("     (no picks)")
        print()
