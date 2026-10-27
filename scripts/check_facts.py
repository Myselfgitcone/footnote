"""Print the headline figures found for each filing, and check one against its 10-K.

Apple's fiscal 2024 10-K reports net sales of $391,035 million; Exxon's 2024
10-K, net income of $33,680 million.

    python scripts/check_facts.py out/store
"""

import json
import sys
from pathlib import Path

from footnote.facts import METRICS, describe

SHOWN = ("revenue", "net_income", "eps_diluted", "total_assets")
root = Path(sys.argv[1])
problems = []
print(f"{'filing':<13}" + "".join(f"{METRICS[m].label[:22]:>26}" for m in SHOWN) + "  missing")
for path in sorted((root / "clean/facts").glob("*.json")):
    built = json.loads(path.read_text())
    for year, data in sorted(built["years"].items()):
        figures = data["figures"]
        cells = "".join(
            f"{describe(figures[m]['value'], figures[m]['unit']).split(' (')[0]:>26}"
            if m in figures
            else f"{'-':>26}"
            for m in SHOWN
        )
        missing = [m for m in METRICS if m not in figures]
        print(f"{built['ticker'] + ' FY' + year:<13}{cells}  {', '.join(missing)}")
        if "net_income" not in figures:
            problems.append(f"{built['ticker']} FY{year}: no net income")

# Known figures from the 10-Ks themselves. Exxon's also checks a company whose
# ticker moved to a new holding company: its 10-Ks are under the old registrant.
KNOWN = [("AAPL", "2024", "revenue", 391_035_000_000), ("XOM", "2024", "net_income", 33_680_000_000)]
for ticker, year, metric, expected in KNOWN:
    path = root / f"clean/facts/{ticker}.json"
    figures = json.loads(path.read_text())["years"].get(year, {}).get("figures", {}) if path.exists() else {}
    if figures.get(metric, {}).get("value") != expected:
        problems.append(f"{ticker} FY{year} {metric} is {figures.get(metric)}, the 10-K says {expected:,}")
if problems:
    sys.exit("Figures look wrong: " + "; ".join(problems))
