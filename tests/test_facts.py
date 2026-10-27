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


def test_a_company_that_reorganized_keeps_its_history(tmp_path, monkeypatch):
    """Exxon: the ticker now belongs to a new holding company with no 10-Ks yet;
    every 10-K so far was filed by Exxon Mobil Corp, its predecessor."""
    from footnote.edgar import Filing

    old = Filing("XOM", 34088, "EXXON MOBIL CORP", "10-K", "x-24", "2025-02-19", "2024-12-31", "xom.htm")

    class Reorganized(FakeEdgar):
        def ciks(self, tickers):
            return {"XOM": (2115436, "ExxonMobil Holdings Corp")}

        def filings(self, ticker, cik, forms, since_year):
            return [old] if cik == 34088 else []

        def company_facts(self, cik):
            assert cik == 34088
            gaap = {
                "NetIncomeLoss": {
                    "label": "Net income",
                    "units": {"USD": [entry(33_680_000_000, "x-24", "2024-12-31", "2024-01-01")]},
                }
            }
            return json.dumps({"facts": {"us-gaap": gaap}}).encode()

    monkeypatch.setattr(ingest, "PREDECESSORS", {"XOM": (34088,)})
    store = LocalStore(tmp_path)
    report = ingest.run(Reorganized([]), store, ["XOM"])
    assert report.added == ["x-24"] and report.no_filings == []
    assert store.exists("raw/edgar/XOM/companyfacts-34088.json")
    built = json.loads(store.get(facts.facts_key("XOM")))
    assert built["years"]["2024"]["figures"]["net_income"]["value"] == 33_680_000_000


def test_a_company_with_no_filings_is_reported(tmp_path):
    class Nothing(FakeEdgar):
        def filings(self, ticker, cik, forms, since_year):
            return []

    report = ingest.run(Nothing([]), LocalStore(tmp_path), ["XOM", "CVX"])
    assert report.no_filings == ["XOM", "CVX"]
    assert "No filings found for: XOM, CVX" in str(report)


def test_figures_reader_does_not_remember_a_missing_file(tmp_path):
    from footnote.service import figures_reader
    from footnote.store import put_json

    store = LocalStore(tmp_path)
    now = [0.0]
    read = figures_reader(store, clock=lambda: now[0])
    assert read("XOM") == {}
    put_json(store, facts.facts_key("XOM"), {"years": {"2024": {}}})
    assert read("XOM") == {"years": {"2024": {}}}  # written later, picked up
    put_json(store, facts.facts_key("XOM"), {"years": {"2025": {}}})
    assert read("XOM") == {"years": {"2024": {}}}  # held for an hour
    now[0] = 3601
    assert read("XOM") == {"years": {"2025": {}}}
