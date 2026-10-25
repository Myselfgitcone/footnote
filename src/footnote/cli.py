"""Command line for Footnote."""

from __future__ import annotations

import typer

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


if __name__ == "__main__":
    app()
