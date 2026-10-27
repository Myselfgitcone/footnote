import json

from footnote import facts, ingest
from footnote.store import LocalStore
from tests.test_ingest import FILINGS, FakeEdgar

APPLE_2024 = {
    "accession": "a-24",
    "period": "2024-09-28",
    "fiscal_year": 2024,
    "ticker": "AAPL",
    "url": "u24",
}
APPLE_2023 = {
    "accession": "a-23",
    "period": "2023-09-30",
    "fiscal_year": 2023,
    "ticker": "AAPL",
    "url": "u23",
}


def entry(val, accn, end, start=None, form="10-K"):
    row = {"val": val, "accn": accn, "end": end, "form": form, "fy": 2024, "fp": "FY"}
    return row | ({"start": start} if start else {})


COMPANYFACTS = {
    "facts": {
        "us-gaap": {
            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                "label": "Revenue from Contract with Customer, Excluding Assessed Tax",
                "units": {
                    "USD": [
                        entry(391_035_000_000, "a-24", "2024-09-28", "2023-10-01"),
                        # the 2023 figure as repeated in the 2024 10-K: belongs to 2023, not 2024
                        entry(383_285_000_000, "a-24", "2023-09-30", "2022-09-25"),
                        # the 2024 10-K's fourth quarter
                        entry(94_930_000_000, "a-24", "2024-09-28", "2024-06-30"),
                        entry(383_285_000_000, "a-23", "2023-09-30", "2022-09-25"),
                    ]
                },
            },
            "EarningsPerShareDiluted": {
                "label": "Earnings Per Share, Diluted",
                "units": {"USD/shares": [entry(6.08, "a-24", "2024-09-28", "2023-10-01")]},
            },
            "Assets": {
                "label": "Assets",
                "units": {
                    "USD": [
                        entry(364_980_000_000, "a-24", "2024-09-28"),
                        entry(352_583_000_000, "a-24", "2023-09-30"),
                    ]
                },
            },
        }
    }
}


def test_each_year_gets_the_figures_its_own_10k_reported():
    built = facts.build(COMPANYFACTS, [APPLE_2024, APPLE_2023])
    fy24 = built["years"]["2024"]["figures"]
    assert fy24["revenue"]["value"] == 391_035_000_000  # not the quarter, not the prior year
    assert fy24["revenue"]["concept"] == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert fy24["eps_diluted"] == fy24["eps_diluted"] | {"value": 6.08, "unit": "USD/shares"}
    assert fy24["total_assets"]["value"] == 364_980_000_000  # a balance at year end, no start date
    assert fy24["revenue"]["accession"] == "a-24" and fy24["revenue"]["url"] == "u24"
    assert built["years"]["2023"]["figures"]["revenue"]["value"] == 383_285_000_000
    assert "total_assets" not in built["years"]["2023"]["figures"]


def test_describe_reads_like_a_person_would_say_it():
    assert facts.describe(391_035_000_000, "USD") == "$391,035 million ($391.0 billion)"
    assert facts.describe(-512_300_000, "USD") == "-$512.3 million"
    assert facts.describe(6.08, "USD/shares") == "$6.08 per share"


def test_ingest_writes_figures_for_each_company(tmp_path):
    class WithFacts(FakeEdgar):
        def company_facts(self, cik):
            return json.dumps(COMPANYFACTS).encode()

    store = LocalStore(tmp_path)
    ingest.run(WithFacts(FILINGS), store, ["AAPL", "MSFT"])
    built = json.loads(store.get(facts.facts_key("AAPL")))
    assert built["ticker"] == "AAPL"
    assert store.exists(facts.facts_key("MSFT"))  # a company with no matching figures still gets a file


def test_a_rerun_fills_in_figures_that_are_missing(tmp_path):
    store = LocalStore(tmp_path)
    ingest.run(FakeEdgar(FILINGS), store, ["AAPL"])
    (tmp_path / facts.facts_key("AAPL")).unlink()
    ingest.run(FakeEdgar(FILINGS), store, ["AAPL"])  # nothing new to fetch
    assert store.exists(facts.facts_key("AAPL"))
