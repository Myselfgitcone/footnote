"""Headline figures for each 10-K, taken from the XBRL data filed with it.

The SEC's companyfacts file holds every number a company has tagged, in every
filing, including restated prior years. For each of our 10-Ks this keeps only
the figures that filing reported for its own fiscal year: the fact's accession
number is the filing's, and its period ends on the filing's report date. So
"Apple's 2024 revenue" is the number in Apple's 2024 10-K, not a later restatement.

Answers quote these instead of numbers pulled from the text, which are easy
to misread (millions or billions? which year's column?).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from footnote.store import Store, get_json, put_json


@dataclass(frozen=True)
class Metric:
    label: str
    concepts: tuple[str, ...]  # us-gaap concepts in order of preference
    unit: str = "USD"
    flow: bool = True  # over the year (revenue) rather than at year end (assets)


METRICS: dict[str, Metric] = {
    "revenue": Metric(
        "Revenue",
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenuesNetOfInterestExpense",
            "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
        ),
    ),
    "gross_profit": Metric("Gross profit", ("GrossProfit",)),
    "operating_income": Metric("Operating income", ("OperatingIncomeLoss",)),
    "net_income": Metric("Net income", ("NetIncomeLoss", "ProfitLoss")),
    "eps_diluted": Metric("Diluted earnings per share", ("EarningsPerShareDiluted",), "USD/shares"),
    "research_and_development": Metric("Research and development", ("ResearchAndDevelopmentExpense",)),
    "operating_cash_flow": Metric("Cash from operations", ("NetCashProvidedByUsedInOperatingActivities",)),
    "capital_expenditure": Metric(
        "Capital expenditure",
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    ),
    "dividends_per_share": Metric(
        "Dividends declared per share", ("CommonStockDividendsPerShareDeclared",), "USD/shares"
    ),
    "total_assets": Metric("Total assets", ("Assets",), flow=False),
    "total_liabilities": Metric("Total liabilities", ("Liabilities",), flow=False),
    "shareholders_equity": Metric(
        "Shareholders' equity",
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        flow=False,
    ),
    "cash": Metric(
        "Cash and cash equivalents",
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        flow=False,
    ),
    "long_term_debt": Metric("Long-term debt", ("LongTermDebtNoncurrent", "LongTermDebt"), flow=False),
}


def facts_key(ticker: str) -> str:
    return f"clean/facts/{ticker}.json"


def build(companyfacts: dict, filings: list[dict]) -> dict:
    """{fiscal year: {metric: figure}} for each of the company's 10-Ks in `filings`."""
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    years: dict[str, dict] = {}
    for filing in sorted(filings, key=lambda f: f["period"]):
        found = {}
        for name, metric in METRICS.items():
            figure = _find(gaap, metric, filing["accession"], filing["period"])
            if figure:
                found[name] = figure | {"accession": filing["accession"], "url": filing["url"]}
        if found:
            years[str(filing["fiscal_year"])] = {"period": filing["period"], "figures": found}
    return {"ticker": filings[0]["ticker"] if filings else "", "years": years}


def write(store: Store, ticker: str, filings: list[dict]) -> dict:
    companyfacts = get_json(store, f"raw/edgar/{ticker}/companyfacts.json", {})
    built = build(companyfacts, filings)
    put_json(store, facts_key(ticker), built)
    return built


def _find(gaap: dict, metric: Metric, accession: str, period: str) -> dict | None:
    for concept in metric.concepts:
        entries = gaap.get(concept, {}).get("units", {}).get(metric.unit, [])
        for entry in entries:
            if entry.get("accn") != accession or entry.get("end") != period:
                continue
            if metric.flow != ("start" in entry):
                continue
            if metric.flow and not 300 <= _days(entry["start"], entry["end"]) <= 380:
                continue  # a quarter or a stub period inside the annual report
            return {
                "value": entry["val"],
                "unit": metric.unit,
                "concept": concept,
                "concept_label": gaap[concept].get("label") or concept,
            }
    return None


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def describe(value: float, unit: str) -> str:
    """$391,035 million; $6.08 per share."""
    if unit == "USD/shares":
        return f"${value:,.2f} per share"
    if unit == "USD":
        sign = "-" if value < 0 else ""
        millions = abs(value) / 1e6
        if millions >= 1000:
            return f"{sign}${millions:,.0f} million ({sign}${millions / 1000:,.1f} billion)"
        return f"{sign}${millions:,.1f} million"
    return f"{value:,} {unit}"
