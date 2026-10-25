import httpx
import pytest

from footnote.edgar import Edgar, Filing

TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 19617, "ticker": "JPM", "title": "JPMORGAN CHASE & CO"},
}


def columns(*rows):
    keys = ["accessionNumber", "filingDate", "reportDate", "form", "primaryDocument"]
    return {k: [r[i] for r in rows] for i, k in enumerate(keys)}


RECENT = columns(
    ("0000320193-25-000079", "2025-10-31", "2025-09-27", "10-K", "aapl-20250927.htm"),
    ("0000320193-25-000070", "2025-08-01", "2025-06-28", "10-Q", "aapl-20250628.htm"),
    ("0000320193-24-000123", "2024-11-01", "2024-09-28", "10-K", "aapl-20240928.htm"),
    ("0000320193-24-000200", "2024-12-01", "2024-09-28", "10-K/A", "aapl-amend.htm"),
)
OLDER = columns(
    ("0000320193-21-000105", "2021-10-29", "2021-09-25", "10-K", "aapl-20210925.htm"),
    ("0000320193-20-000096", "2020-10-30", "2020-09-26", "10-K", "aapl-20200926.htm"),
)


def client(calls, fail_first=0):
    state = {"fails": fail_first}

    def handler(request):
        calls.append(request)
        path = request.url.path
        if state["fails"]:
            state["fails"] -= 1
            return httpx.Response(429)
        if path == "/files/company_tickers.json":
            return httpx.Response(200, json=TICKERS)
        if path == "/submissions/CIK0000320193.json":
            return httpx.Response(
                200,
                json={
                    "name": "Apple Inc.",
                    "filings": {
                        "recent": RECENT,
                        "files": [
                            {
                                "name": "CIK0000320193-submissions-001.json",
                                "filingFrom": "2019-01-01",
                                "filingTo": "2022-12-31",
                            },
                            {
                                "name": "CIK0000320193-submissions-002.json",
                                "filingFrom": "2010-01-01",
                                "filingTo": "2018-12-31",
                            },
                        ],
                    },
                },
            )
        if path == "/submissions/CIK0000320193-submissions-001.json":
            return httpx.Response(200, json=OLDER)
        if path.startswith("/Archives/edgar/data/320193/"):
            return httpx.Response(200, content=b"<html>10-K</html>")
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def edgar(calls, **kw):
    return Edgar(
        "Footnote test test@example.com", client(calls, **kw), min_interval_s=0, sleep=lambda _: None
    )


def test_needs_a_contact_email():
    with pytest.raises(ValueError, match="contact email"):
        Edgar("no email here")


def test_ciks_come_from_the_sec_list_and_unknown_tickers_fail():
    calls = []
    assert edgar(calls).ciks(["AAPL", "JPM"]) == {
        "AAPL": (320193, "Apple Inc."),
        "JPM": (19617, "JPMORGAN CHASE & CO"),
    }
    assert calls[0].headers["user-agent"] == "Footnote test test@example.com"
    with pytest.raises(KeyError, match="ZZZZ"):
        edgar([]).ciks(["AAPL", "ZZZZ"])


def test_filings_follow_older_pages_and_keep_only_the_forms_and_years_asked_for():
    calls = []
    found = edgar(calls).filings("AAPL", 320193, ("10-K",), since_year=2021)
    assert [f.accession for f in found] == [
        "0000320193-21-000105",
        "0000320193-24-000123",
        "0000320193-25-000079",
    ]  # no 10-Q, no amendment, nothing before fiscal 2021
    fetched = [c.url.path for c in calls]
    assert "/submissions/CIK0000320193-submissions-001.json" in fetched
    assert "/submissions/CIK0000320193-submissions-002.json" not in fetched  # ends before 2021


def test_filing_url_and_fiscal_year():
    filing = Filing(
        "WMT", 104169, "Walmart", "10-K", "0000104169-25-000021", "2025-03-14", "2025-01-31", "wmt.htm"
    )
    assert filing.fiscal_year == 2025  # Walmart's year to January 2025 is its fiscal 2025
    assert filing.url == "https://www.sec.gov/Archives/edgar/data/104169/000010416925000021/wmt.htm"


def test_slows_down_when_the_sec_says_so():
    calls = []
    assert edgar(calls, fail_first=2).ciks(["AAPL"])["AAPL"][0] == 320193
    assert len(calls) == 3
