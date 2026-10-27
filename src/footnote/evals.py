"""Run the questions in evals/cases.yaml through the real pipeline and score the answers.

Scores, each 0 to 1 per question:

    figure_cited     every expected figure is among the cited sources, with the filed value
    figure_stated    and the answer states it (in millions, billions, or per share)
    retrieved        a retrieved passage from the right company contains the phrase
    cited            a cited passage does
    declined         an off-topic or unanswerable question gets no dollar figure
    tickers          the plan named the companies the question is about
    cites_claims     the share of the answer's sentences that cite a source
    grounded         a judge (a stronger Claude than the one answering) finds every
                     claim supported by the sources it cites

With LANGSMITH_API_KEY set, the run is a LangSmith experiment on a dataset made
from cases.yaml, so runs can be compared over time. Either way the same scores
decide whether the run passes: GATES below.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import BaseModel, Field

from footnote import ask
from footnote.companies import NAMES
from footnote.facts import METRICS

CASES = Path(__file__).resolve().parents[2] / "evals" / "cases.yaml"
JUDGE_MODEL = "claude-sonnet-5-5"
# The lowest acceptable average for each score across the questions it applies to.
GATES = {
    "completed": 1.0,  # every question answered and scored; a crash is a failure, not a skip
    "figure_cited": 1.0,
    "figure_stated": 1.0,
    "declined": 1.0,
    "tickers": 0.95,
    "retrieved": 0.8,
    "cited": 0.7,
    "cites_claims": 0.9,
    "grounded": 0.9,
}


# ---------------------------------------------------------------- cases


def load_cases(path: Path = CASES) -> list[dict]:
    import yaml

    cases = yaml.safe_load(path.read_text())
    problems = []
    seen = set()
    for case in cases:
        where = case.get("id", "?")
        if where in seen:
            problems.append(f"{where}: duplicate id")
        seen.add(where)
        if case.get("kind") not in ("figure", "passage", "decline"):
            problems.append(f"{where}: unknown kind {case.get('kind')!r}")
        for figure in case.get("figures", []):
            if figure["ticker"] not in NAMES or figure["metric"] not in METRICS:
                problems.append(f"{where}: unknown ticker or metric in {figure}")
        if case.get("kind") == "passage" and (case.get("ticker") not in NAMES or not case.get("phrase")):
            problems.append(f"{where}: a passage case needs a known ticker and a phrase")
    if problems:
        raise ValueError("bad eval cases: " + "; ".join(problems))
    return cases


def dataset_name(cases: list[dict]) -> str:
    """Named by content, so changing the cases makes a new dataset instead of mixing runs."""
    digest = hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest()[:8]
    return f"footnote-evals-{digest}"


# ---------------------------------------------------------------- scoring


def stated_forms(value: float, unit: str) -> list[str]:
    """How an answer might write a figure: $391,035 million, $391.0 billion, $391 billion, $2.46."""
    if unit == "USD/shares":
        return [f"{value:.2f}"]
    millions = abs(value) / 1e6
    forms = [f"{millions:,.0f}", f"{millions:.0f}"]
    if millions >= 1000:
        billions = millions / 1000
        for digits in (0, 1, 2):
            forms += [f"{billions:,.{digits}f} billion", f"{billions:,.{digits}f}B"]
    return forms


SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])|\n+")


def citation_share(answer: str) -> float:
    """The share of sentences (of any length) that cite at least one source."""
    sentences = [s for s in SENTENCE.split(answer) if len(s.strip()) >= 25]
    if not sentences:
        return 1.0
    return sum(bool(ask.CITATION.search(s)) for s in sentences) / len(sentences)


def score(case: dict, output: dict) -> dict[str, float]:
    """Deterministic scores for one answer. The judge's `grounded` is added separately."""
    answer, sources = output["answer"], output["sources"]
    plan = output.get("plan", {})
    scores: dict[str, float] = {}
    if case["kind"] == "decline":
        offered_figure = re.search(r"\$\s?\d", answer) or any(s["kind"] == "figure" for s in sources)
        scores["declined"] = 0.0 if offered_figure else 1.0
        return scores

    expected_tickers = {f["ticker"] for f in case.get("figures", [])} or {case.get("ticker")}
    scores["tickers"] = float(expected_tickers <= set(plan.get("tickers", [])))
    if case["kind"] == "figure":
        cited = [s for s in sources if s["kind"] == "figure"]
        found = stated = 0
        for want in case["figures"]:
            match = next(
                (
                    s
                    for s in cited
                    if s["ticker"] == want["ticker"]
                    and s["fiscal_year"] == want["fiscal_year"]
                    and s["metric"] == want["metric"]
                ),
                None,
            )
            found += bool(match and abs(match["value"] - want["value"]) < 0.005)
            unit = METRICS[want["metric"]].unit
            stated += any(form in answer for form in stated_forms(want["value"], unit))
        scores["figure_cited"] = found / len(case["figures"])
        scores["figure_stated"] = stated / len(case["figures"])
    else:
        scores["retrieved"] = float(any(_has_phrase(p, case) for p in output.get("retrieved", [])))
        scores["cited"] = float(any(_has_phrase(s, case) for s in sources if s["kind"] == "passage"))
    if sources:
        scores["cites_claims"] = citation_share(answer)
    return scores


def _has_phrase(passage: dict, case: dict) -> bool:
    return (
        passage.get("ticker") == case["ticker"]
        and case["phrase"].lower() in passage.get("text", "").lower()
        and (not case.get("items") or passage.get("item") in case["items"])
    )


# ---------------------------------------------------------------- the judge


class Judgement(BaseModel):
    """Whether an answer's claims are supported by the sources it cites."""

    unsupported: list[str] = Field(
        default_factory=list, description="Each claim in the answer that its cited sources don't support."
    )
    supported: bool = Field(description="True if every claim is supported by the sources it cites.")


JUDGE_INSTRUCTIONS = """\
You check answers written from company filings. You get a question, an answer whose sentences cite \
sources like [S1] or [F2], and those sources. Decide whether every claim in the answer is supported by \
the sources it cites.

- A figure must match its source; a different rounding of the same figure is fine ($391.0 billion for \
$391,035 million).
- A change or ratio computed from cited figures is supported if the arithmetic is right.
- Saying the sources don't cover something is supported.
- Don't use outside knowledge, and don't penalize what the answer leaves out."""


def make_judge(api_key: str, model: str = JUDGE_MODEL):
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(model=model, api_key=api_key, max_tokens=600, max_retries=2)
    structured = llm.with_structured_output(Judgement)

    def judge(question: str, answer: str, sources: list[dict]) -> Judgement:
        listing = "\n\n".join(ask.source_line(s) for s in sources)
        prompt = f"Question: {question}\n\nAnswer:\n{answer}\n\nSources:\n\n{listing}"
        return structured.invoke([("system", JUDGE_INSTRUCTIONS), ("human", prompt)])

    return judge


# ---------------------------------------------------------------- running


def answer_for_eval(graph, question: str) -> dict:
    """Run the pipeline and keep what scoring needs, including passages retrieved but not cited."""
    result = graph.invoke({"question": question})
    keep = ("ticker", "fiscal_year", "item", "heading", "text", "score")
    return {
        "answer": result["answer"],
        "sources": result.get("sources", []),
        "plan": result["plan"].model_dump(),
        "retrieved": [{k: p.get(k) for k in keep} for p in result.get("passages", [])],
    }


def check_cases_against_corpus(cases: list[dict], store) -> list[str]:
    """Passage cases whose phrase isn't anywhere in that company's filings: the case is wrong, not search."""
    from footnote.ingest import CATALOG
    from footnote.store import get_json

    catalog = get_json(store, CATALOG, [])
    broken = []
    for case in (c for c in cases if c["kind"] == "passage"):
        phrase, found = case["phrase"].lower(), False
        for entry in (e for e in catalog if e["ticker"] == case["ticker"]):
            filing = get_json(store, f"clean/filings/{entry['ticker']}/{entry['accession']}.json", {})
            for item, section in filing.get("sections", {}).items():
                if (not case.get("items") or item in case["items"]) and phrase in section["text"].lower():
                    found = True
                    break
            if found:
                break
        if not found:
            broken.append(f"{case['id']}: {case['phrase']!r} isn't in {case['ticker']}'s filings")
    return broken


@dataclass
class Outcome:
    case: dict
    output: dict
    scores: dict[str, float]
    notes: list[str] = field(default_factory=list)


# The score every question of a kind must have; without it the question wasn't really scored.
PRIMARY = {"figure": "figure_cited", "passage": "retrieved", "decline": "declined"}


def evaluate_case(case: dict, output: dict | None, judge, error: str | None = None) -> Outcome:
    """Score one answer. An answer that never came back counts as failed, never as skipped."""
    if not output or "answer" not in output:
        return Outcome(case, output or {}, {"completed": 0.0}, [f"no answer: {error or 'the run failed'}"])
    scores = score(case, output)
    notes = []
    if judge and case["kind"] != "decline" and output["sources"]:
        for attempt in (1, 2):
            try:
                verdict = judge(case["question"], output["answer"], output["sources"])
            except Exception as failure:  # a judge that fails twice leaves `grounded` unscored, noted
                if attempt == 2:
                    notes.append(f"judge failed: {type(failure).__name__}: {failure}"[:300])
                continue
            scores["grounded"] = float(verdict.supported)
            notes += [f"unsupported: {claim}" for claim in verdict.unsupported]
            break
    scores["completed"] = float(PRIMARY[case["kind"]] in scores)
    return Outcome(case, output, scores, notes)


def run_local(cases: list[dict], graph, judge, workers: int = 4) -> list[Outcome]:
    def one(case: dict) -> Outcome:
        try:
            output = answer_for_eval(graph, case["question"])
        except Exception as failure:
            return evaluate_case(case, None, judge, f"{type(failure).__name__}: {failure}"[:300])
        return evaluate_case(case, output, judge)

    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(one, cases))


def run_langsmith(cases: list[dict], graph, judge, workers: int = 4) -> tuple[list[Outcome], str | None]:
    """The same run as a LangSmith experiment: one example per case, scores as feedback."""
    from langsmith import Client, evaluate

    client = Client()
    name = dataset_name(cases)
    if not client.has_dataset(dataset_name=name):
        client.create_dataset(name, description="Footnote's eval questions (evals/cases.yaml).")
        client.create_examples(
            dataset_name=name,
            examples=[
                {"inputs": {"question": c["question"]}, "outputs": {"case": c}, "metadata": {"id": c["id"]}}
                for c in cases
            ],
        )

    def target(inputs: dict) -> dict:
        return answer_for_eval(graph, inputs["question"])

    def scorer(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
        outcome = evaluate_case(reference_outputs["case"], outputs, judge)
        results: list[dict[str, Any]] = [{"key": k, "score": v} for k, v in outcome.scores.items()]
        if outcome.notes:
            results.append({"key": "notes", "value": "; ".join(outcome.notes)})
        return {"results": results}

    experiment = evaluate(
        target,
        data=name,
        evaluators=[scorer],
        experiment_prefix="footnote",
        metadata={"model": os.environ.get("FOOTNOTE_ANSWER_MODEL", "claude-haiku-5-5"), "judge": JUDGE_MODEL},
        max_concurrency=workers,
    )
    outcomes = []
    for row in experiment:
        case = row["example"].outputs["case"]
        results = row["evaluation_results"]["results"]
        scores = {r.key: float(r.score) for r in results if r.score is not None}
        notes = [str(r.value) for r in results if r.key == "notes" and r.value]
        if "completed" not in scores:
            # The pipeline or the scorer raised: LangSmith logs it, the gate must see it.
            error = row["run"].error or next(
                (str(r.comment) for r in results if r.comment), "the scorer failed"
            )
            scores, notes = {"completed": 0.0}, [*notes, f"not scored: {error}"[:300]]
        outcomes.append(Outcome(case, row["run"].outputs or {}, scores, notes))
    return outcomes, experiment.url


# ---------------------------------------------------------------- reporting


def summarize(outcomes: list[Outcome]) -> dict[str, tuple[float, int]]:
    """Average of each score over the questions it applies to, and how many that was."""
    keys = sorted({k for o in outcomes for k in o.scores})
    return {
        k: (mean(o.scores[k] for o in outcomes if k in o.scores), sum(k in o.scores for o in outcomes))
        for k in keys
    }


def failures(summary: dict[str, tuple[float, int]]) -> list[str]:
    return [
        f"{key} {summary[key][0]:.0%} < {floor:.0%}"
        for key, floor in GATES.items()
        if key in summary and summary[key][0] < floor
    ]


def report(outcomes: list[Outcome], summary: dict, url: str | None, broken: list[str]) -> str:
    lines = ["## Footnote evals", ""]
    if url:
        lines += [f"LangSmith experiment: {url}", ""]
    lines += ["| score | average | questions | gate |", "|---|---|---|---|"]
    for key, (avg, n) in summary.items():
        gate = GATES.get(key)
        mark = "" if gate is None else ("pass" if avg >= gate else "**FAIL**")
        lines.append(f"| {key} | {avg:.0%} | {n} | {mark} |")
    if broken:
        lines += ["", "Cases to fix (the phrase isn't in the filings):", *[f"- {b}" for b in broken]]
    lines += ["", "| question | kind | scores | answer |", "|---|---|---|---|"]
    for o in sorted(outcomes, key=lambda o: (min(o.scores.values(), default=1), o.case["id"])):
        shown = ", ".join(f"{k} {v:.0%}" for k, v in sorted(o.scores.items()) if v < 1) or "all pass"
        if o.scores.get("completed") == 0:
            shown = "**not scored**"
        answer = o.output.get("answer", "").replace("\n", " ").replace("|", "/")
        note = (" — " + "; ".join(o.notes)) if o.notes else ""
        lines.append(f"| {o.case['id']} | {o.case['kind']} | {shown} | {answer[:220]}{note[:300]} |")
    return "\n".join(lines)
