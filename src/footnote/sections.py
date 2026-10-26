"""Split a 10-K into its numbered items: Business, Risk Factors, MD&A and so on.

Every 10-K follows the same outline (Item 1, 1A, 1B, ... 16), but each company
lays it out in its own HTML. The approach here doesn't depend on the HTML:

1. Turn the document into lines of text, dropping hidden XBRL headers and page
   furniture (page numbers, "Table of Contents" links).
2. Find every line that looks like an item heading.
3. Each item appears at least twice: in the table of contents, and where the
   section actually starts. For each item, keep the occurrence that runs the
   longest before the next item begins. Table-of-contents entries are a line
   long, so the real section always wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

# The 10-K outline, in order. Anything after an item ends it.
ORDER = ["1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C",
         "10", "11", "12", "13", "14", "15", "16"]  # fmt: skip
TITLES = {
    "1": "Business",
    "1A": "Risk Factors",
    "1C": "Cybersecurity",
    "3": "Legal Proceedings",
    "7": "Management's Discussion and Analysis",
    "7A": "Quantitative and Qualitative Disclosures About Market Risk",
}
# The items worth searching. Item 8 (the financial statements) is mostly tables;
# its numbers come from XBRL instead, which is exact.
KEEP = tuple(TITLES)

HEADING = re.compile(r"^\s*item\s*(\d{1,2}[a-c]?)\s*(?:[.:\-\u2013\u2014]|\s|$)", re.IGNORECASE)
PAGE_FURNITURE = re.compile(r"^\s*(\d{1,3}|[ivxlc]{1,6}|table of contents|index)\s*$", re.IGNORECASE)
MIN_SECTION_CHARS = 400
# A section found only by its title that runs longer than this has run past the
# narrative into the financial statements; better to report it missing.
MAX_TITLE_SECTION_CHARS = 250_000
MAX_HEADING_CHARS = 160


@dataclass
class Section:
    item: str
    title: str
    text: str
    found_by: str = "item"  # "item" heading, or "title" for filings laid out as an annual report


# Some filers (big banks especially) publish the 10-K as an annual report with a
# cross-reference index, so "Item 1A" never appears as a heading. Their sections
# are still titled the standard way; these find them by title instead.
TITLE_PATTERNS = {
    "1": r"business|description of business",
    "1A": r"risk factors",
    "1C": r"cybersecurity",
    # Only the standard title: banks' Item 3 points to a "Litigation" note in the
    # financial statements, and matching that ran to the end of JPMorgan's 10-K.
    "3": r"legal proceedings",
    "7": r"management.s discussion and analysis(?: of financial condition and results of operations)?",
    "7A": r"quantitative and qualitative disclosures? about market risk",
    # boundaries only
    "2": r"properties",
    "8": r"(?:consolidated )?financial statements(?: and supplementary data)?",
    "9A": r"controls and procedures",
    "1B": r"unresolved staff comments",
    "5": r"market for (?:the )?registrant.s common equity.*",
}


def to_lines(html: str | bytes) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    for hidden in soup.select('[style*="display:none"], [style*="display: none"]'):
        hidden.decompose()
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    for tag in soup.find_all(lambda t: t.name and t.name.lower() in ("ix:header",)):
        tag.decompose()
    text = soup.get_text("\n").replace("\xa0", " ").replace("​", "")
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line and not PAGE_FURNITURE.match(line):
            lines.append(line)
    return _join_split_headings(lines)


def _join_split_headings(lines: list[str]) -> list[str]:
    """'Item 1A.' on one line and 'Risk Factors' on the next become one heading line."""
    joined: list[str] = []
    for line in lines:
        if (
            joined
            and re.fullmatch(r"item\s*\d{1,2}[a-c]?\s*[.:\-\u2013\u2014]?", joined[-1], re.IGNORECASE)
            and len(line) < MAX_HEADING_CHARS
        ):
            joined[-1] = f"{joined[-1]} {line}"
        else:
            joined.append(line)
    return joined


def split(html: str | bytes, keep: tuple[str, ...] = KEEP) -> dict[str, Section]:
    lines = to_lines(html)
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = HEADING.match(line)
        if match and len(line) <= MAX_HEADING_CHARS and _looks_like_heading(line):
            item = match.group(1).upper()
            if item in ORDER:
                starts.append((index, item))

    # Length in characters from each line to the next, for measuring candidates.
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)

    sections: dict[str, Section] = {}
    for item in keep:
        rank = ORDER.index(item)
        best: tuple[int, int, int] | None = None  # (length, start, end)
        for index, candidate in starts:
            if candidate != item:
                continue
            end = next((i for i, other in starts if i > index and ORDER.index(other) > rank), len(lines))
            length = offsets[end] - offsets[index + 1]
            if best is None or length > best[0]:
                best = (length, index, end)
        if best and best[0] >= MIN_SECTION_CHARS:
            _, start, end = best
            body = "\n".join(lines[start + 1 : end]).strip()
            sections[item] = Section(item=item, title=TITLES.get(item, f"Item {item}"), text=body)

    missing = [item for item in keep if item not in sections]
    if missing:
        sections.update(_by_title(lines, offsets, missing))
    return sections


def _by_title(lines: list[str], offsets: list[int], items: list[str]) -> dict[str, Section]:
    """Fallback: a line that is just a standard section title starts that section."""
    titled: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if len(line) > MAX_HEADING_CHARS:
            continue
        bare = re.sub(
            r"^(part\s+[ivx]+[,.:]?\s*)?(item\s*\d{1,2}[a-c]?[.:\-\u2013\u2014]?\s*)?",
            "",
            line,
            flags=re.IGNORECASE,
        ).strip(" .:")
        for item, pattern in TITLE_PATTERNS.items():
            if re.fullmatch(pattern, bare, re.IGNORECASE):
                titled.append((index, item))
                break
    found: dict[str, Section] = {}
    for item in items:
        best: tuple[int, int, int] | None = None
        for index, candidate in titled:
            if candidate != item:
                continue
            end = next((i for i, other in titled if i > index and other != item), len(lines))
            length = offsets[end] - offsets[index + 1]
            if length > MAX_TITLE_SECTION_CHARS:
                continue
            if best is None or length > best[0]:
                best = (length, index, end)
        if best and best[0] >= MIN_SECTION_CHARS:
            _, start, end = best
            found[item] = Section(item, TITLES[item], "\n".join(lines[start + 1 : end]).strip(), "title")
    return found


def _looks_like_heading(line: str) -> bool:
    """'Item 7. Management's Discussion' is a heading; 'Item 7 of this report discusses' is a sentence."""
    rest = HEADING.sub("", line, count=1).strip()
    if not rest:
        return True
    first = rest.split(" ", 1)[0]
    return not (first.islower() and first not in {"and"})
