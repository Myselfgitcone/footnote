# Footnote

Ask questions about public companies' SEC filings and get answers that cite
their source, with the numbers checked against the SEC's own figures.

Work in progress. Done so far: getting the filings.

## Getting the filings

```
SEC EDGAR ──daily──> Lambda ──> S3  raw/edgar/{ticker}/{accession}/…      the filing as served
                                    raw/edgar/{ticker}/companyfacts.json  every XBRL number reported
                                    clean/filings/{ticker}/{accession}.json  split into sections
                                    clean/catalog.json                       one line per filing
```

Twenty large US companies across tech, banks, retail, healthcare and energy, every
10-K for fiscal 2021 onwards (`src/footnote/companies.py`). CIKs come from the
SEC's own ticker file at run time, so a wrong ticker fails loudly.

**Polite to the SEC.** Every request carries a name and contact email, as EDGAR's
fair-access rules ask, stays under 10 requests a second, and backs off when told to.

**Complete history.** A company's submissions file only lists its most recent
filings inline. Banks file thousands of prospectuses a year, which can push their
10-Ks out of that list within months, so older pages are followed too.

**Split by section.** A 10-K follows a fixed outline (Item 1 Business, 1A Risk
Factors, 7 MD&A, ...), but every company formats it differently, and each item
heading appears twice: once in the table of contents and once where the section
starts. The parser finds every heading and, for each item, keeps the occurrence
that runs longest before the next item, which is never the contents entry. Banks
often publish the 10-K as an annual report with a cross-reference index and no
"Item" headings at all; for those, sections are found by their standard titles.
Every section records which way it was found.

**Incremental.** A filing that already has its clean copy is skipped, so the
daily run only works when something new is filed, and a run that dies picks up
where it stopped. Each run is capped to fit Lambda's 15 minutes; the first
backfill loops until nothing is left.

**Checked on real filings.** CI fetches recent 10-Ks from Apple, JPMorgan,
Goldman Sachs and Walmart and fails if any is missing its risk factors or MD&A.

## AWS, as code

`infra/main` (applied by GitHub Actions on every merge): the data bucket
(versioned, encrypted, private), the ingest Lambda, a daily 7:30am Chicago-time
schedule, and a $1 monthly budget alarm.

GitHub Actions logs in to AWS with OIDC: no AWS keys are stored anywhere. The
role it assumes can only manage resources named `footnote-*`, and only `main`
can deploy.

### One-time setup

`infra/bootstrap` creates the Terraform state bucket and that OIDC role. Run it
once from your machine with an AWS admin login:

```bash
aws configure                     # or aws sso login
cd infra/bootstrap
terraform init && terraform apply
```

Then give the repository the two outputs as variables (not secrets; neither is
sensitive):

```bash
gh variable set AWS_ROLE_ARN --body "$(terraform output -raw deploy_role_arn)"
gh variable set TF_STATE_BUCKET --body "$(terraform output -raw state_bucket)"
```

The next merge deploys. Run the `backfill` workflow once to load the history.

## Run it locally

```bash
uv sync
uv run footnote ingest --ticker AAPL --since-year 2024   # writes to out/store
uv run pytest
```
