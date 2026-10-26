"""Print the headline figures found for each filing, and check one against its 10-K.

Apple's fiscal 2024 10-K reports net sales of $391,035 million.

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

apple = json.loads((root / "clean/facts/AAPL.json").read_text())["years"].get("2024", {}).get("figures", {})
if apple.get("revenue", {}).get("value") != 391_035_000_000:
    problems.append(f"AAPL FY2024 revenue is {apple.get('revenue')}, the 10-K says $391,035 million")
if problems:
    sys.exit("Figures look wrong: " + "; ".join(problems))
