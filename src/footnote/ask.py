"""Answer a question from the filings, citing a source for every claim.

A LangGraph graph with four steps:

    plan ──┬── retrieve (passages from Pinecone) ──┬── answer
           └── lookup   (figures from XBRL)    ────┘

`plan` has Claude read the question and decide which companies and years it's
about, which headline figures it needs, and what to search the text for.
`retrieve` and `lookup` run in parallel. `answer` gives Claude only what they
found, numbered [S1], [S2] for passages and [F1], [F2] for figures, and asks for
an answer that cites them; citations to sources that weren't given are dropped.

Figures always come from the XBRL data, never from reading numbers off the
text, so "Apple's 2024 revenue" is the exact figure Apple filed.

The models and stores are passed in, so tests run the whole graph with stand-ins.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from footnote.companies import COMPANIES, NAMES
from footnote.facts import METRICS, describe
from footnote.vectors import Vectors

MAX_COMPANIES = 4
PASSAGES = 10
SECTION_TITLES = {
    "1": "Business",
    "1A": "Risk Factors",
    "1C": "Cybersecurity",
    "3": "Legal Proceedings",
    "7": "Management's Discussion and Analysis",
    "7A": "Market Risk",
}
OUT_OF_SCOPE = (
    "I can only answer questions about the annual reports (10-Ks) of the companies Footnote covers: "
    + ", ".join(c.name for c in COMPANIES)
    + "."
)
NOTHING_FOUND = "I couldn't find anything in these filings that answers that."

MetricName = Literal[tuple(METRICS)]  # type: ignore[valid-type]
SectionName = Literal[tuple(SECTION_TITLES)]  # type: ignore[valid-type]


class Plan(BaseModel):
    """How to answer the question."""

    in_scope: bool = Field(
        description="True if the annual reports of the listed companies could answer it; false otherwise."
    )
    tickers: list[str] = Field(
        default_factory=list, description="Tickers of the companies it's about, from the list. Empty if none."
    )
    fiscal_years: list[int] = Field(
        default_factory=list, description="Fiscal years it asks about. Empty means the most recent."
    )
    metrics: list[MetricName] = Field(  # type: ignore[valid-type]
        default_factory=list, description="Headline figures needed to answer it, if any."
    )
    search: str = Field(
        default="",
        description="A short query for searching the filings' text, or empty if the figures alone answer it.",
    )
    sections: list[SectionName] = Field(  # type: ignore[valid-type]
        default_factory=list,
        description="10-K items likely to hold the answer, only if clear: "
        + "; ".join(f"{k} {v}" for k, v in SECTION_TITLES.items()),
    )


class State(TypedDict, total=False):
    question: str
    plan: Plan
    passages: list[dict]
    figures: list[dict]
    answer: str
    sources: list[dict]


Planner = Callable[[str], Plan]
Writer = Callable[[str, str], str]  # (system, user) -> text
FiguresFor = Callable[[str], dict]  # ticker -> clean/facts/{ticker}.json


def planner_instructions(today: date | None = None) -> str:
    today = today or date.today()
    companies = "\n".join(f"- {c.ticker}: {c.name} ({c.sector})" for c in COMPANIES)
    metrics = ", ".join(f"{k} ({m.label})" for k, m in METRICS.items())
    return (
        "You plan how to answer a question about US public companies' annual reports (10-K filings).\n"
        f"Companies covered:\n{companies}\n"
        f"Filings cover fiscal years 2021 to {today.year}. Today is {today.isoformat()}.\n"
        f"Headline figures available for each fiscal year: {metrics}.\n"
        "Ask for figures only when the question needs a number. Write a search query whenever the "
        "answer needs the filings' words: explanations, risks, strategy, reasons behind a number."
    )


WRITER_INSTRUCTIONS = """\
You answer questions about US public companies from their annual reports (10-K filings), using only \
the numbered sources given with the question.

- Cite every claim with the ids of the sources it rests on, like [S2] or [F1][S4], at the end of the sentence.
- Take figures only from F sources, as written there, and name the fiscal year. Compute a change or ratio \
only if the question asks for one, and cite the figures it's computed from.
- The passages are excerpts. Report what the filing says ("Apple says..."); add nothing from outside them.
- If the sources don't answer the question, say so in one sentence.
- At most 170 words. Plain prose: no headings, no preamble."""


def build_graph(planner: Planner, writer: Writer, embedder, vectors: Vectors, figures_for: FiguresFor):
    from langgraph.graph import END, START, StateGraph

    def plan(state: State) -> State:
        return {"plan": tidy(planner(state["question"]))}

    def route(state: State) -> list[str]:
        p = state["plan"]
        if not p.in_scope:
            return ["answer"]
        steps = []
        if p.metrics and p.tickers:
            steps.append("lookup")
        if p.search or not steps:
            steps.append("retrieve")
        return steps

    def retrieve(state: State) -> State:
        p = state["plan"]
        query = embedder.query(p.search or state["question"])
        shared = []
        if p.fiscal_years:
            shared.append({"fiscal_year": {"$in": p.fiscal_years}})
        if p.sections:
            shared.append({"item": {"$in": list(p.sections)}})
        hits: dict[str, dict] = {}
        # Each company gets its own search, so one with more text can't crowd out the rest.
        for ticker in p.tickers or [None]:
            conditions = shared + ([{"ticker": {"$eq": ticker}}] if ticker else [])
            k = max(4, PASSAGES // max(len(p.tickers), 1))
            for hit in vectors.search(query, k, _and(conditions)):
                hits.setdefault(hit["id"], hit)
        return {"passages": sorted(hits.values(), key=lambda h: -h["score"])}

    def lookup(state: State) -> State:
        p = state["plan"]
        found = []
        for ticker in p.tickers:
            years = figures_for(ticker).get("years", {})
            wanted = [str(y) for y in p.fiscal_years if str(y) in years] or sorted(years)[-2:]
            for year in wanted:
                figures = years[year]["figures"]
                for name in p.metrics:
                    figure = figures.get(name)
                    if figure:
                        found.append(
                            {
                                "ticker": ticker,
                                "company": NAMES[ticker],
                                "fiscal_year": int(year),
                                "metric": name,
                                "label": METRICS[name].label,
                                "text": describe(figure["value"], figure["unit"]),
                            }
                            | figure
                        )
        return {"figures": found}

    def answer(state: State) -> State:
        p = state["plan"]
        if not p.in_scope:
            return {"answer": OUT_OF_SCOPE, "sources": []}
        passages, figures = state.get("passages", []), state.get("figures", [])
        sources = [
            h | {"id": f"S{i}", "kind": "passage", "vector_id": h["id"]} for i, h in enumerate(passages, 1)
        ]
        sources += [f | {"id": f"F{i}", "kind": "figure"} for i, f in enumerate(figures, 1)]
        if not sources:
            return {"answer": NOTHING_FOUND, "sources": []}
        listing = "\n\n".join(source_line(s) for s in sources)
        text = writer(WRITER_INSTRUCTIONS, f"Question: {state['question']}\n\nSources:\n\n{listing}")
        text, cited = keep_known_citations(text, {s["id"]: s for s in sources})
        return {"answer": text, "sources": cited}

    graph = StateGraph(State)
    graph.add_node("plan", plan)
    graph.add_node("retrieve", retrieve)
    graph.add_node("lookup", lookup)
    graph.add_node("answer", answer)
    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route, ["retrieve", "lookup", "answer"])
    graph.add_edge("retrieve", "answer")
    graph.add_edge("lookup", "answer")
    graph.add_edge("answer", END)
    return graph.compile()


def tidy(plan: Plan) -> Plan:
    """Keep the plan to companies and years Footnote has, whatever the model wrote."""
    tickers = list(dict.fromkeys(t.upper() for t in plan.tickers if t.upper() in NAMES))[:MAX_COMPANIES]
    years = sorted({y for y in plan.fiscal_years if 2000 <= y <= 2100})
    return plan.model_copy(update={"tickers": tickers, "fiscal_years": years, "search": plan.search.strip()})


def source_line(source: dict) -> str:
    if source["kind"] == "figure":
        return (
            f"[{source['id']}] {source['company']} ({source['ticker']}), fiscal {source['fiscal_year']}: "
            f"{source['label']} = {source['text']}. From the XBRL data filed with the 10-K "
            f"(us-gaap:{source['concept']})."
        )
    where = f"Item {source['item']} {source['item_title']}"
    if source.get("heading"):
        where += f" > {source['heading']}"
    return (
        f"[{source['id']}] {source['company']} ({source['ticker']}) 10-K, fiscal {source['fiscal_year']}, "
        f"{where}:\n{source['text']}"
    )


CITATION = re.compile(r"\[([SF]\d+)\]")


def keep_known_citations(text: str, by_id: dict[str, dict]) -> tuple[str, list[dict]]:
    """Drop citations of sources that weren't given; list the cited ones in order of first use."""
    cited: list[dict] = []
    seen: set[str] = set()

    def check(match: re.Match) -> str:
        source_id = match.group(1)
        if source_id not in by_id:
            return ""
        if source_id not in seen:
            seen.add(source_id)
            cited.append(by_id[source_id])
        return match.group(0)

    cleaned = CITATION.sub(check, text)
    return re.sub(r" +([.,;])", r"\1", cleaned).strip(), cited


def _and(conditions: list[dict]) -> dict | None:
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


def claude(model: str, api_key: str) -> tuple[Planner, Writer]:
    """The planner and writer, both Claude through LangChain (so LangSmith traces them)."""
    from langchain_anthropic import ChatAnthropic

    planning = ChatAnthropic(model=model, api_key=api_key, temperature=0, max_tokens=500, max_retries=2)
    planning = planning.with_structured_output(Plan)
    writing = ChatAnthropic(model=model, api_key=api_key, temperature=0, max_tokens=700, max_retries=2)

    def planner(question: str) -> Plan:
        return planning.invoke([("system", planner_instructions()), ("human", question)])

    def writer(system: str, user: str) -> str:
        return writing.invoke([("system", system), ("human", user)]).text

    return planner, writer
