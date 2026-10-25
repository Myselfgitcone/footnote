from footnote import sections

FILLER = "The company designs, makes and sells things. " * 20

TEN_K = f"""
<html><head><title>10-K</title></head><body>
<div style="display:none"><ix:header>dei:EntityRegistrantName Hidden Corp 0000123456</ix:header></div>
<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p>
<p><b>TABLE OF CONTENTS</b></p>
<table>
  <tr><td>Item 1.</td><td>Business</td><td>3</td></tr>
  <tr><td>Item 1A.</td><td>Risk Factors</td><td>9</td></tr>
  <tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>20</td></tr>
  <tr><td>Item 7.</td><td>Management's Discussion and Analysis</td><td>30</td></tr>
  <tr><td>Item 7A.</td><td>Quantitative and Qualitative Disclosures About Market Risk</td><td>45</td></tr>
  <tr><td>Item 8.</td><td>Financial Statements</td><td>47</td></tr>
</table>
<p>PART I</p>
<p><b>Item 1. Business</b></p>
<p>Business starts here. {FILLER}</p>
<p>2</p>
<p><a href="#toc">Table of Contents</a></p>
<p>Business continues after the page break. {FILLER}</p>
<p><b>ITEM 1A. RISK FACTORS</b></p>
<p>Risk one: supply chains. {FILLER}</p>
<p>Item 7 of this report discusses results in more detail, but it is not a heading.</p>
<p>Risk two: competition. {FILLER}</p>
<p><b>Item 1B.</b></p><p><b>Unresolved Staff Comments</b></p>
<p>None.</p>
<p>PART II</p>
<p><b>Item 7.</b></p>
<p><b>Management's Discussion and Analysis of Financial Condition and Results of Operations</b></p>
<p>Revenue grew. {FILLER}</p>
<p><b>Item 7A. Quantitative and Qualitative Disclosures About Market Risk</b></p>
<p>Interest rates and currencies. {FILLER}</p>
<p><b>Item 8. Financial Statements and Supplementary Data</b></p>
<table><tr><td>Revenue</td><td>100</td></tr></table>
</body></html>
"""

BANK_STYLE = f"""
<html><body>
<h2>Letter to shareholders</h2><p>{FILLER}</p>
<h2>Management's discussion and analysis</h2>
<p>Net interest income rose. {FILLER}</p>
<h2>Risk Factors</h2>
<p>Credit risk is the main risk. {FILLER}</p>
<h2>Legal Proceedings</h2>
<p>Various matters are pending. {FILLER}</p>
<h2>Consolidated Financial Statements</h2>
<table><tr><td>Loans</td><td>1,000</td></tr></table>
<h2>Form 10-K: Cross-reference index</h2>
<table><tr><td>Part I, Item 1A</td><td>Risk factors</td><td>9-30</td></tr></table>
</body></html>
"""


def test_finds_the_real_sections_not_the_table_of_contents():
    found = sections.split(TEN_K)
    assert set(found) == {"1", "1A", "7", "7A"}
    assert found["1"].text.startswith("Business starts here.")
    assert "Business continues after the page break." in found["1"].text
    assert found["1A"].text.startswith("Risk one: supply chains.")
    assert found["7"].text.startswith("Revenue grew.")
    assert found["7A"].text.startswith("Interest rates and currencies.")
    assert all(s.found_by == "item" for s in found.values())


def test_a_sentence_starting_with_item_is_not_a_heading():
    risk = sections.split(TEN_K)["1A"].text
    assert "Item 7 of this report discusses results" in risk
    assert "Risk two: competition." in risk  # the section didn't stop at the sentence


def test_sections_end_where_the_next_item_starts():
    found = sections.split(TEN_K)
    assert "Unresolved Staff Comments" not in found["1A"].text
    assert "Revenue grew." not in found["1A"].text
    assert "Interest rates" not in found["7"].text


def test_page_numbers_hidden_headers_and_contents_links_are_dropped():
    text = "\n".join(s.text for s in sections.split(TEN_K).values())
    assert "Hidden Corp" not in text
    assert "Table of Contents" not in text
    assert "\n2\n" not in text


def test_annual_report_style_filings_are_split_by_title():
    found = sections.split(BANK_STYLE)
    assert {"1A", "7", "3"} <= set(found)
    assert found["1A"].text.startswith("Credit risk is the main risk.")
    assert found["7"].text.startswith("Net interest income rose.")
    assert "Credit risk" not in found["7"].text
    assert found["1A"].found_by == "title"


def test_too_short_to_be_a_section_is_ignored():
    html = "<p>Item 1A. Risk Factors</p><p>See page 10.</p><p>Item 1B. Unresolved Staff Comments</p>"
    assert sections.split(html) == {}
