import itertools
import re

from footnote import chunks
from footnote.chunks import MAX_CHARS, chunk_filing, looks_like_subheading, passages


def prose(n: int, topic: str) -> str:
    return " ".join(f"Sentence {i} about {topic} explains a risk in some detail." for i in range(n))


SECTION = "\n".join(
    [
        "Supply Chain Risks",
        prose(12, "suppliers"),
        prose(12, "factories"),
        prose(30, "shipping"),  # one paragraph longer than a passage
        "Net sales by category",  # a table: label lines followed by cells, not prose
        "iPhone",
        "$ 201,183",
        "Total net sales",
        "$ 391,035",
        "Competition",
        prose(10, "rivals"),
        "None of the above applies.",
    ]
)

FILING = {
    "ticker": "AAPL",
    "company": "Apple Inc.",
    "form": "10-K",
    "fiscal_year": 2024,
    "period": "2024-09-28",
    "filed": "2024-11-01",
    "accession": "0000320193-24-000123",
    "url": "https://www.sec.gov/x.htm",
    "sections": {
        "1A": {"title": "Risk Factors", "found_by": "item", "text": SECTION},
        "7": {"title": "Management's Discussion and Analysis", "found_by": "item", "text": prose(5, "sales")},
    },
}


def test_passages_stay_inside_the_size_limit_and_lose_nothing():
    found = passages(SECTION)
    assert all(len(text) <= MAX_CHARS + chunks.OVERLAP_CHARS for _, text in found)
    joined = "\n".join(text for _, text in found)
    for sentence in re.findall(r"Sentence \d+ about \w+", SECTION):
        assert sentence in joined
    assert "$ 391,035" in joined
    assert "None of the above applies." in joined


def test_each_passage_knows_its_subheading():
    found = passages(SECTION)
    assert found[0][0] == "Supply Chain Risks"
    assert any("shipping" in text for heading, text in found if heading == "Supply Chain Risks")
    rivals = [heading for heading, text in found if "rivals" in text]
    assert set(rivals) == {"Competition"}


def test_table_labels_are_not_subheadings():
    found = passages(SECTION)
    headings = {heading for heading, _ in found}
    assert "Total net sales" not in headings
    assert "Net sales by category" not in headings


def test_consecutive_passages_overlap():
    found = [text for heading, text in passages(SECTION) if heading == "Supply Chain Risks"]
    assert len(found) >= 3
    for before, after in itertools.pairwise(found):
        last_sentence = re.findall(r"Sentence \d+ about \w+", before)[-1]
        assert last_sentence in after[: chunks.OVERLAP_CHARS]


def test_a_short_remainder_joins_the_passage_before_it():
    found = passages(SECTION)
    assert not any(text == "None of the above applies." for _, text in found)


def test_subheading_rules():
    assert looks_like_subheading("Results of Operations")
    assert looks_like_subheading("Risks Related to Our Business")
    assert not looks_like_subheading("We depend on suppliers.")
    assert not looks_like_subheading("$ 391,035")
    assert not looks_like_subheading("iPhone")
    assert looks_like_subheading("Competition")
    assert not looks_like_subheading("Fiscal 2024 compared with 2023:")


def test_chunk_ids_are_unique_and_grouped_by_filing():
    found = chunk_filing(FILING)
    ids = [c.id for c in found]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("0000320193-24-000123#") for i in ids)
    assert {c.item for c in found} == {"1A", "7"}


def test_embedding_text_names_the_company_year_and_section():
    first = chunk_filing(FILING)[0]
    assert first.for_embedding.startswith(
        "Apple Inc. (AAPL) 10-K for fiscal 2024, Item 1A Risk Factors: Supply Chain Risks\n"
    )
    assert "Apple" not in first.text  # the header is for the embedding, not the quoted passage


def test_page_headers_and_footers_are_left_out():
    pages = []
    for page in range(1, 6):
        pages += [
            "THE GOLDMAN SACHS GROUP, INC. AND SUBSIDIARIES",
            "Management's Discussion and Analysis",
            prose(8, f"page{page}"),
            f"Goldman Sachs 2024 Form 10-K {page + 40}",
        ]
    filing = FILING | {
        "sections": {
            "7": {
                "title": "Management's Discussion and Analysis",
                "found_by": "item",
                "text": "\n".join(pages),
            }
        }
    }
    found = chunk_filing(filing)
    text = "\n".join(c.text for c in found)
    assert "SUBSIDIARIES" not in text and "Form 10-K" not in text
    assert "Discussion and Analysis" not in text
    assert all("page" in c.text for c in found)
    assert {c.heading for c in found} == {""}


def test_footer_patterns():
    from footnote.chunks import FOOTER

    assert FOOTER.match("Apple Inc. | 2024 Form 10-K | 7")
    assert FOOTER.match("45 JPMorgan Chase & Co./2024 Form 10-K")
    assert FOOTER.match("Walmart Inc. 2025 Annual Report 33")
    assert not FOOTER.match("See Part II, Item 7 of this Form 10-K")
    assert not FOOTER.match("Form 10-K")


def test_parts_and_sentence_fragments_are_not_subheadings():
    assert not looks_like_subheading("Part I")
    assert not looks_like_subheading("PART II")
    assert not looks_like_subheading("The Company has also implemented changes to its")
    assert looks_like_subheading("Legal, Tax, Regulatory, Compliance, Reputational and Other Risks")
    assert looks_like_subheading("Macroeconomic and Industry Risks")
