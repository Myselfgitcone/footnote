from footnote import ask
from footnote.ask import Plan, build_graph, keep_known_citations, tidy

APPLE_FIGURES = {
    "years": {
        "2023": {"figures": {"revenue": {"value": 383_285_000_000, "unit": "USD", "concept": "Revenues"}}},
        "2024": {"figures": {"revenue": {"value": 391_035_000_000, "unit": "USD", "concept": "Revenues"}}},
    }
}


def passage(id_, ticker, text, score):
    return {
        "id": id_,
        "score": score,
        "ticker": ticker,
        "company": f"{ticker} Inc.",
        "fiscal_year": 2024,
        "item": "1A",
        "item_title": "Risk Factors",
        "heading": "Supply Chain",
        "text": text,
        "url": f"https://sec.gov/{ticker}",
    }


class FakeVectors:
    def __init__(self):
        self.calls = []

    def search(self, vector, top_k, filter=None):
        self.calls.append((top_k, filter))
        ticker = (filter or {}).get("ticker", {}).get("$eq") or _find_ticker(filter)
        return [passage(f"{ticker}-1", ticker, f"{ticker} relies on outsourcing partners.", 0.7)]


def _find_ticker(filter):
    for condition in (filter or {}).get("$and", []):
        if "ticker" in condition:
            return condition["ticker"]["$eq"]
    return "ANY"


class FakeEmbedder:
    def __init__(self):
        self.queries = []

    def query(self, text):
        self.queries.append(text)
        return [0.0]


def run(plan: Plan, reply: str, vectors=None):
    seen = {}

    def writer(system, user):
        seen["system"], seen["user"] = system, user
        return reply

    vectors = vectors or FakeVectors()
    embedder = FakeEmbedder()
    graph = build_graph(lambda q: plan, writer, embedder, vectors, lambda t: APPLE_FIGURES)
    result = graph.invoke({"question": "q?"})
    return result, seen, vectors, embedder


def test_a_figure_question_gets_the_exact_filed_number_and_cites_it():
    plan = Plan(in_scope=True, tickers=["AAPL"], fiscal_years=[2024], metrics=["revenue"])
    result, seen, vectors, _ = run(plan, "Apple's fiscal 2024 revenue was $391,035 million [F1].")
    assert "[F1] Apple (AAPL), fiscal 2024: Revenue = $391,035 million ($391.0 billion)" in seen["user"]
    assert vectors.calls == []  # figures alone answer it: no text search
    assert [s["id"] for s in result["sources"]] == ["F1"]
    assert result["sources"][0]["value"] == 391_035_000_000


def test_without_a_year_the_latest_two_are_given():
    plan = Plan(in_scope=True, tickers=["AAPL"], metrics=["revenue"])
    _result, seen, *_ = run(plan, "Up from 2023 [F1] to 2024 [F2].")
    assert "fiscal 2023" in seen["user"] and "fiscal 2024" in seen["user"]


def test_a_text_question_searches_each_company_separately():
    plan = Plan(in_scope=True, tickers=["AAPL", "MSFT"], search="manufacturing partners", sections=["1A"])
    result, _seen, vectors, embedder = run(plan, "Both rely on partners [S1][S2].")
    assert embedder.queries == ["manufacturing partners"]
    assert len(vectors.calls) == 2
    first_filter = vectors.calls[0][1]
    assert {"item": {"$in": ["1A"]}} in first_filter["$and"]
    assert {"ticker": {"$eq": "AAPL"}} in first_filter["$and"]
    assert [s["ticker"] for s in result["sources"]] == ["AAPL", "MSFT"]


def test_text_and_figures_run_together_and_both_reach_the_writer():
    plan = Plan(
        in_scope=True, tickers=["AAPL"], fiscal_years=[2024], metrics=["revenue"], search="why sales grew"
    )
    result, seen, _vectors, _ = run(plan, "Sales grew [F1] because of services [S1].")
    assert "[S1] AAPL Inc. (AAPL) 10-K, fiscal 2024, Item 1A Risk Factors > Supply Chain:" in seen["user"]
    assert "[F1]" in seen["user"]
    assert [s["kind"] for s in result["sources"]] == ["figure", "passage"]


def test_out_of_scope_questions_are_answered_without_searching_or_writing():
    plan = Plan(in_scope=False)
    result, seen, vectors, _ = run(plan, "should not be used")
    assert result["answer"] == ask.OUT_OF_SCOPE and result["sources"] == []
    assert seen == {} and vectors.calls == []


def test_made_up_citations_are_dropped():
    sources = {"S1": {"id": "S1"}, "F1": {"id": "F1"}}
    text, cited = keep_known_citations("A [S1]. B [S7]. C [F1][S1].", sources)
    assert text == "A [S1]. B. C [F1][S1]."
    assert [c["id"] for c in cited] == ["S1", "F1"]


def test_the_plan_is_kept_to_known_companies():
    plan = tidy(Plan(in_scope=True, tickers=["aapl", "XYZ", "AAPL", "KO"], fiscal_years=[2024, 1999, 2024]))
    assert plan.tickers == ["AAPL", "KO"] and plan.fiscal_years == [2024]


def test_planner_instructions_name_every_company_and_figure():
    text = ask.planner_instructions()
    assert "KO: Coca-Cola" in text and "eps_diluted (Diluted earnings per share)" in text
