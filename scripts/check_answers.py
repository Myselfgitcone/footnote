"""Ask real questions end to end (Claude, Pinecone, the XBRL figures) and check the answers.

Needs ANTHROPIC_API_KEY and PINECONE_API_KEY, the model in build/model, and Apple's
filings ingested into out/store (for the figures). With LANGSMITH_API_KEY and
LANGSMITH_TRACING=true each run is traced.
"""

import sys

from langchain_core.tracers.langchain import wait_for_all_tracers

from footnote import ask
from footnote.service import asker
from footnote.settings import Settings
from footnote.store import LocalStore

settings = Settings()
graph = asker(settings, LocalStore(settings.local_dir), settings.model_dir)
problems = []


def run(question: str) -> dict:
    result = graph.invoke({"question": question})
    plan = result["plan"].model_dump_json(exclude_defaults=True)
    print(f"\nQ: {question}\nplan: {plan}\nA: {result['answer']}")
    for source in result["sources"]:
        what = source.get("label") or f"Item {source['item']}"
        print(f"   [{source['id']}] {source['ticker']} FY{source['fiscal_year']} {what}")
    return result


try:
    revenue = run("What was Apple's revenue in fiscal 2024?")
    figures = [s for s in revenue["sources"] if s["kind"] == "figure" and s["metric"] == "revenue"]
    if not any(s["fiscal_year"] == 2024 and s["value"] == 391_035_000_000 for s in figures):
        problems.append("the revenue answer doesn't cite Apple's filed FY2024 revenue of $391,035 million")
    if "391" not in revenue["answer"]:
        problems.append("the revenue answer doesn't state the figure")

    making = run("Where does Apple make its products?")
    if not any(
        "outsourcing partners" in s.get("text", "") for s in making["sources"] if s["kind"] == "passage"
    ):
        problems.append("the manufacturing answer doesn't cite the outsourcing-partners passage")

    elsewhere = run("What is the capital of France?")
    if elsewhere["answer"] != ask.OUT_OF_SCOPE:
        problems.append("an off-topic question wasn't declined")
finally:
    wait_for_all_tracers()

if problems:
    sys.exit("Answers look wrong: " + "; ".join(problems))
print("\nAll answers check out.")
