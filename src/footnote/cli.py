"""Command line for Footnote."""

from __future__ import annotations

import typer

from footnote import index as indexing
from footnote import ingest as ingesting
from footnote.companies import TICKERS
from footnote.edgar import Edgar
from footnote.settings import Settings
from footnote.store import LocalStore, S3Store

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """Answers about SEC filings that cite their source."""


@app.command()
def ingest(
    ticker: list[str] = typer.Option(None, help="Limit to these tickers. Default: the full list."),
    since_year: int = typer.Option(None, help="First fiscal year to fetch."),
    max_new: int = typer.Option(1000, help="Stop after this many new filings."),
    s3: bool = typer.Option(
        False, help="Write to the S3 bucket in FOOTNOTE_BUCKET instead of a local folder."
    ),
) -> None:
    """Fetch new filings from EDGAR and split them into sections."""
    settings = Settings()
    store = S3Store(settings.bucket) if s3 else LocalStore(settings.local_dir)
    report = ingesting.run(
        Edgar(settings.user_agent),
        store,
        tickers=ticker or TICKERS,
        forms=settings.forms,
        since_year=since_year or settings.since_year,
        max_new=max_new,
    )
    typer.echo(report)
    if report.failed:
        raise typer.Exit(1)


@app.command()
def index(
    ticker: list[str] = typer.Option(None, help="Limit to these tickers."),
    s3: bool = typer.Option(False, help="Read filings from the S3 bucket instead of the local folder."),
    namespace: str = typer.Option(None, help="Pinecone namespace. Default: FOOTNOTE_NAMESPACE or 10k."),
    budget_s: float = typer.Option(3600, help="Stop starting new filings after this many seconds."),
) -> None:
    """Chunk and embed indexed filings into Pinecone."""
    from footnote.embed import DIMENSION, OnnxEmbedder
    from footnote.vectors import PineconeVectors

    settings = Settings()
    store = S3Store(settings.bucket) if s3 else LocalStore(settings.local_dir)
    vectors = PineconeVectors(settings.pinecone_api_key, DIMENSION, namespace=namespace or settings.namespace)
    report = indexing.run(store, OnnxEmbedder(settings.model_dir), vectors, tickers=ticker, budget_s=budget_s)
    typer.echo(report)
    if report.failed:
        raise typer.Exit(1)


@app.command()
def search(
    question: str,
    ticker: list[str] = typer.Option(None, help="Only these companies."),
    year: int = typer.Option(None, help="Only this fiscal year."),
    item: str = typer.Option(None, help="Only this 10-K item, e.g. 1A."),
    k: int = typer.Option(5, help="How many passages."),
    namespace: str = typer.Option(None, help="Pinecone namespace. Default: FOOTNOTE_NAMESPACE or 10k."),
) -> None:
    """Show the passages closest to a question, with where each came from."""
    from footnote.embed import DIMENSION, OnnxEmbedder
    from footnote.vectors import PineconeVectors

    settings = Settings()
    vectors = PineconeVectors(settings.pinecone_api_key, DIMENSION, namespace=namespace or settings.namespace)
    query = OnnxEmbedder(settings.model_dir).query(question)
    for hit in vectors.search(query, k, filters(ticker, year, item)):
        where = f"{hit['ticker']} FY{hit['fiscal_year']} Item {hit['item']}"
        if hit.get("heading"):
            where += f" > {hit['heading']}"
        typer.echo(f"\n[{hit['score']:.3f}] {where}\n{hit['text'][:500]}")


@app.command()
def ask(question: str, s3: bool = typer.Option(False, help="Read figures from the S3 bucket.")) -> None:
    """Answer a question from the filings, with the sources it cites."""
    from footnote.service import asker

    settings = Settings()
    store = S3Store(settings.bucket) if s3 else LocalStore(settings.local_dir)
    try:
        result = asker(settings, store, settings.model_dir).invoke({"question": question})
    finally:
        from langchain_core.tracers.langchain import wait_for_all_tracers

        wait_for_all_tracers()
    plan = result["plan"]
    typer.echo(f"plan: {plan.model_dump_json(exclude_defaults=True)}\n")
    typer.echo(result["answer"] + "\n")
    for source in result["sources"]:
        if source["kind"] == "figure":
            where = f"{source['ticker']} FY{source['fiscal_year']}"
            typer.echo(f"[{source['id']}] {where} {source['label']}: {source['text']}")
        else:
            typer.echo(
                f"[{source['id']}] {source['ticker']} FY{source['fiscal_year']} Item {source['item']}: "
                f"{source['text'][:200]}"
            )


def filters(tickers: list[str] | None, year: int | None, item: str | None) -> dict | None:
    """Pinecone metadata filter from the search options."""
    conditions = []
    if tickers:
        conditions.append({"ticker": {"$in": [t.upper() for t in tickers]}})
    if year:
        conditions.append({"fiscal_year": {"$eq": year}})
    if item:
        conditions.append({"item": {"$eq": item.upper()}})
    if not conditions:
        return None
    return conditions[0] if len(conditions) == 1 else {"$and": conditions}


if __name__ == "__main__":
    app()
