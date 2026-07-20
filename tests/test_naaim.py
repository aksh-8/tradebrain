"""
Test whether NAAIM data is actually being pulled and parsed.

Run from repo root:
    python tests/test_naaim.py

Checks two things:
  1. Does your bot's _get_naaim() return a value or None?
  2. What does the raw CSV actually look like — so we can see if the
     column-parsing assumption ("value is in column 2") is correct.
"""

from bot.market_regime import _get_naaim

print("=" * 60)
print("1. Your bot's _get_naaim() result:")
print("=" * 60)
val = _get_naaim()
if val is None:
    print("  -> None  (NAAIM NOT being pulled — fetch or parse failed)")
else:
    print(f"  -> {val}  (NAAIM pulled successfully)")

print()
print("=" * 60)
print("2. Raw CSV inspection (first + last few rows):")
print("=" * 60)

try:
    import urllib.request, csv, io
    url = "https://www.naaim.org/programs/naaim-exposure-index/csv/"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = r.read().decode("utf-8", errors="ignore")

    reader = csv.reader(io.StringIO(data))
    rows = list(reader)
    print(f"  Total rows: {len(rows)}")
    print()
    print("  HEADER (row 0):")
    print(f"    {rows[0] if rows else '(empty)'}")
    print()
    print("  LAST 3 data rows (what the parser scans from the bottom):")
    for row in rows[-3:]:
        print(f"    {row}")
    print()
    print("  The parser reads column index [1] (2nd column) of the last")
    print("  numeric row. Check above whether that column actually holds")
    print("  the exposure index value (usually 0-200, often the LAST column).")
except Exception as e:
    print(f"  Raw fetch failed: {e}")
    print("  -> The naaim.org URL may be down or blocking requests.")
