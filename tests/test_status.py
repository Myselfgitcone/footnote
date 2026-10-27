import json
import runpy
import sys
from pathlib import Path

import boto3
from moto import mock_aws

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "status.py"


def test_status_counts_what_is_covered(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(key, "testing")
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="footnote-test")
        catalog = [
            {"ticker": "AAPL", "company": "Apple Inc.", "fiscal_year": 2024, "filed": "2024-11-01"},
            {"ticker": "AAPL", "company": "Apple Inc.", "fiscal_year": 2025, "filed": "2025-10-31"},
            {"ticker": "KO", "company": "COCA COLA CO", "fiscal_year": 2021, "filed": "2022-02-22"},
        ]
        s3.put_object(Bucket="footnote-test", Key="clean/catalog.json", Body=json.dumps(catalog))
        for name, chunks in (("AAPL/a.json", 120), ("KO/k.json", 80)):
            s3.put_object(
                Bucket="footnote-test", Key=f"index/filings/{name}", Body=json.dumps({"chunks": chunks})
            )
        target = tmp_path / "status.json"
        monkeypatch.setattr(sys, "argv", ["status.py", "footnote-test", str(target)])
        runpy.run_path(str(SCRIPT), run_name="__main__")
    status = json.loads(target.read_text())
    assert status["companies"] == 2 and status["filings"] == 3
    assert status["indexed_filings"] == 2 and status["passages"] == 200
    assert status["fiscal_years"] == [2021, 2025]
    assert status["latest_filing"] == {
        "ticker": "AAPL",
        "name": "Apple",
        "company": "Apple Inc.",
        "fiscal_year": 2025,
        "filed": "2025-10-31",
    }
