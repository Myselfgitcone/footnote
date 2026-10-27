import pytest

from footnote import ask, evals
from footnote.ask import Plan
from footnote.store import LocalStore, put_json

FIGURE_CASE = {
    "id": "aapl",
    "kind": "figure",
    "question": "What was Apple's revenue in fiscal 2024?",
    "figures": [{"ticker": "AAPL", "fiscal_year": 2024, "metric": "revenue", "value": 391035000000}],
}
PASSAGE_CASE = {
    "id": "make",
    "kind": "passage",
    "question": "Where does Apple make its products?",
    "ticker": "AAPL",
    "phrase": "outsourcing partners",
    "items": ["1A"],
}
DECLINE_CASE = {"id": "weather", "kind": "decline", "question": "Weather?"}

APPLE_FIGURE = {
    "id": "F1",
    "kind": "figure",
    "ticker": "AAPL",
    "company": "Apple",
    "fiscal_year": 2024,
    "metric": "revenue",
    "label": "Revenue",
    "value": 391035000000,
    "text": "$391,035 million ($391.0 billion)",
    "concept": "Revenues",
}
APPLE_PASSAGE = {
    "id": "S1",
    "kind": "passage",
    "ticker": "AAPL",
    "company": "Apple Inc.",
    "fiscal_year": 2024,
    "item": "1A",
    "item_title": "Risk Factors",
    "heading": "",
    "text": "Manufacturing is performed by Outsourcing Partners in Asia.",
}


def output(answer, sources=(), tickers=("AAPL",), retrieved=()):
    return {
        "answer": answer,
        "sources": list(sources),
        "plan": {"tickers": list(tickers)},
        "retrieved": list(retrieved),
    }


def test_the_case_file_is_valid():
    cases = evals.load_cases()
    assert len(cases) >= 25
    assert {c["kind"] for c in cases} == {"figure", "passage", "decline"}


def test_bad_cases_are_refused(tmp_path):
    path = tmp_path / "cases.yaml"
    path.write_text(
        "- {id: a, kind: figure, question: q,"
        " figures: [{ticker: PEP, fiscal_year: 2024, metric: revenue, value: 1}]}\n"
        "- {id: a, kind: other, question: q}\n"
    )
    with pytest.raises(ValueError, match=r"unknown ticker.*duplicate id.*unknown kind"):
        evals.load_cases(path)


@pytest.mark.parametrize(
    "answer",
    [
        "Apple's fiscal 2024 revenue was $391,035 million [F1].",
        "Apple's fiscal 2024 revenue was $391.0 billion [F1].",
        "Revenue was $391.04 billion in fiscal 2024 [F1].",
    ],
)
def test_a_figure_stated_any_reasonable_way_counts(answer):
    scores = evals.score(FIGURE_CASE, output(answer, [APPLE_FIGURE]))
    assert scores == {"tickers": 1.0, "figure_cited": 1.0, "figure_stated": 1.0, "cites_claims": 1.0}


def test_a_wrong_or_missing_figure_fails():
    wrong = APPLE_FIGURE | {"value": 383285000000}
    scores = evals.score(FIGURE_CASE, output("Revenue was $383,285 million [F1].", [wrong]))
    assert scores["figure_cited"] == 0 and scores["figure_stated"] == 0
    scores = evals.score(FIGURE_CASE, output("I couldn't find that.", [], tickers=()))
    assert scores == {"tickers": 0.0, "figure_cited": 0.0, "figure_stated": 0.0}


def test_eps_is_matched_to_the_cent():
    case = {
        **FIGURE_CASE,
        "figures": [{"ticker": "AAPL", "fiscal_year": 2024, "metric": "eps_diluted", "value": 6.08}],
    }
    eps = APPLE_FIGURE | {"metric": "eps_diluted", "value": 6.08}
    assert evals.score(case, output("Diluted EPS was $6.08 [F1].", [eps]))["figure_stated"] == 1.0


def test_passage_scores_separate_finding_from_citing():
    other = APPLE_PASSAGE | {"id": "S2", "text": "Nothing relevant here at all."}
    scores = evals.score(
        PASSAGE_CASE, output("Apple uses partners in Asia [S2].", [other], retrieved=[APPLE_PASSAGE, other])
    )
    assert scores["retrieved"] == 1.0 and scores["cited"] == 0.0
    wrong_item = APPLE_PASSAGE | {"item": "7"}
    assert (
        evals.score(PASSAGE_CASE, output("x [S1].", [wrong_item], retrieved=[wrong_item]))["retrieved"] == 0.0
    )


def test_declining_means_offering_no_figure():
    assert evals.score(DECLINE_CASE, output(ask.OUT_OF_SCOPE))["declined"] == 1.0
    assert evals.score(DECLINE_CASE, output("PepsiCo's revenue was $91 billion."))["declined"] == 0.0
    assert (
        evals.score(DECLINE_CASE, output("Apple's latest is below [F1].", [APPLE_FIGURE]))["declined"] == 0.0
    )


def test_citation_share_counts_sentences_without_a_source():
    answer = "Apple makes phones in many countries [S1]. It also sells services to many customers worldwide."
    assert evals.citation_share(answer) == 0.5
    assert evals.citation_share("Short. [S1]") == 1.0


def test_broken_cases_are_caught_before_they_count_as_misses(tmp_path):
    store = LocalStore(tmp_path)
    put_json(store, "clean/catalog.json", [{"ticker": "AAPL", "accession": "a-24"}])
    put_json(
        store, "clean/filings/AAPL/a-24.json", {"sections": {"1A": {"text": "Outsourcing partners in Asia."}}}
    )
    missing = {**PASSAGE_CASE, "id": "nope", "phrase": "underwater factories"}
    assert evals.check_cases_against_corpus([PASSAGE_CASE, missing, DECLINE_CASE], store) == [
        "nope: 'underwater factories' isn't in AAPL's filings"
    ]


class FakeGraph:
    def invoke(self, state):
        if "Weather" in state["question"]:
            return {"answer": ask.OUT_OF_SCOPE, "sources": [], "plan": Plan(in_scope=False)}
        return {
            "answer": "Apple's fiscal 2024 revenue was $391,035 million [F1].",
            "sources": [APPLE_FIGURE],
            "plan": Plan(in_scope=True, tickers=["AAPL"], metrics=["revenue"]),
            "passages": [APPLE_PASSAGE],
        }


def test_a_local_run_scores_judges_and_gates():
    judged = []

    def judge(question, answer, sources):
        judged.append(question)
        return evals.Judgement(supported=False, unsupported=["made-up claim"])

    outcomes = evals.run_local([FIGURE_CASE, DECLINE_CASE], FakeGraph(), judge)
    assert judged == [FIGURE_CASE["question"]]  # declines aren't judged
    summary = evals.summarize(outcomes)
    assert summary["figure_cited"] == (1.0, 1) and summary["declined"] == (1.0, 1)
    assert evals.failures(summary) == ["grounded 0% < 90%"]
    text = evals.report(outcomes, summary, "https://smith.example/x", ["c: broken"])
    assert "| grounded | 0% | 1 | **FAIL** |" in text and "unsupported: made-up claim" in text
    assert "c: broken" in text


def test_dataset_name_follows_the_cases():
    assert evals.dataset_name([FIGURE_CASE]) == evals.dataset_name([dict(FIGURE_CASE)])
    assert evals.dataset_name([FIGURE_CASE]) != evals.dataset_name([FIGURE_CASE, DECLINE_CASE])
