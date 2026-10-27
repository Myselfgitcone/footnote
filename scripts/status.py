"""Write site/status.json: what Footnote covers right now, for the page and the portfolio hub.

Reads the catalog and the index markers from the bucket. Run by the site workflow
after each deploy and every morning, once the day's filings are fetched and embedded.

    uv run python scripts/status.py <bucket> site/status.json
"""

import json
import sys
from datetime import UTC, datetime

import boto3

from footnote.companies import NAMES

bucket, target = sys.argv[1], sys.argv[2]
s3 = boto3.client("s3")


def read(key: str):
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


catalog = read("clean/catalog.json")
passages = indexed = 0
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="index/filings/"):
    for item in page.get("Contents", []):
        passages += read(item["Key"])["chunks"]
        indexed += 1

years = sorted({entry["fiscal_year"] for entry in catalog})
latest = max(catalog, key=lambda entry: entry["filed"])
status = {
    "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    "companies": len({entry["ticker"] for entry in catalog}),
    "filings": len(catalog),
    "indexed_filings": indexed,
    "passages": passages,
    "fiscal_years": [years[0], years[-1]],
    "latest_filing": {
        "ticker": latest["ticker"],
        "name": NAMES.get(latest["ticker"], latest["company"]),  # "Costco", not "COSTCO WHOLESALE CORP /NEW"
        "company": latest["company"],
        "fiscal_year": latest["fiscal_year"],
        "filed": latest["filed"],
    },
}
with open(target, "w") as out:
    json.dump(status, out, indent=1)
print(json.dumps(status, indent=1))
