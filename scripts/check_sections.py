"""Print what the parser found in each filing, and fail if a core section is missing."""

import json
import sys
from pathlib import Path

REQUIRED = ("1A", "7")

root = Path(sys.argv[1])
catalog = json.loads((root / "clean/catalog.json").read_text())
problems = []
print(f"{'filing':<28}{'1':>9}{'1A':>9}{'1C':>8}{'3':>8}{'7':>9}{'7A':>8}  found by")
for entry in catalog:
    filing = json.loads((root / f"clean/filings/{entry['ticker']}/{entry['accession']}.json").read_text())
    parts = filing["sections"]
    sizes = "".join(
        f"{len(parts[k]['text']) if k in parts else 0:>{w}}"
        for k, w in (("1", 9), ("1A", 9), ("1C", 8), ("3", 8), ("7", 9), ("7A", 8))
    )
    how = ",".join(sorted({p["found_by"] for p in parts.values()}))
    print(f"{entry['ticker'] + ' FY' + str(entry['fiscal_year']):<28}{sizes}  {how}")
    missing = [k for k in REQUIRED if k not in parts or len(parts[k]["text"]) < 5_000]
    if missing:
        problems.append(f"{entry['ticker']} FY{entry['fiscal_year']}: {', '.join(missing)}")
if problems:
    sys.exit("Missing or too short: " + "; ".join(problems))
print(f"{len(catalog)} filings, all with risk factors and MD&A.")
