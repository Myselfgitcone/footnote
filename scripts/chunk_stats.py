"""How real filings chunk: passages per filing, their sizes, how many found a subheading.

python scripts/chunk_stats.py out/store
"""

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

from footnote.chunks import MAX_CHARS, OVERLAP_CHARS, chunk_filing

root = Path(sys.argv[1])
catalog = json.loads((root / "clean/catalog.json").read_text())
problems = []
print(f"{'filing':<14}{'passages':>9}{'median':>8}{'max':>7}{'headed':>8}  common subheadings")
for entry in catalog:
    filing = json.loads((root / f"clean/filings/{entry['ticker']}/{entry['accession']}.json").read_text())
    chunks = chunk_filing(filing)
    sizes = [len(c.text) for c in chunks]
    headed = sum(bool(c.heading) for c in chunks) / len(chunks)
    common = ", ".join(h for h, _ in Counter(c.heading for c in chunks if c.heading).most_common(3))
    name = f"{entry['ticker']} FY{entry['fiscal_year']}"
    median = statistics.median(sizes)
    print(f"{name:<14}{len(chunks):>9}{median:>8.0f}{max(sizes):>7}{headed:>8.0%}  {common[:70]}")
    if max(sizes) > MAX_CHARS + OVERLAP_CHARS:
        problems.append(f"{name}: a passage of {max(sizes)} characters")
if problems:
    sys.exit("Passages too long: " + "; ".join(problems))
