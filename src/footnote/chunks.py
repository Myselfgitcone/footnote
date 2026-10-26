"""Cut a filing's sections into passages small enough to embed and to quote.

A passage is a run of whole paragraphs, about a third of a page. Paragraphs are
never cut unless one alone is too long, and then only between sentences. Each
passage carries the nearest subheading above it ("Supply chain risks"), so an
answer can say where in Item 1A it found something, and the next passage starts
with the end of the previous one so a point made across the break isn't lost.

What gets embedded is the passage with a one-line header naming the company,
year and section. Without it, "our products" in Apple's risk factors and in
Coca-Cola's look alike; with it, a question about Apple finds Apple.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# About 300-400 tokens of 10-K prose: room for the header inside bge-small's 512.
TARGET_CHARS = 1_200
MAX_CHARS = 1_500
OVERLAP_CHARS = 250
MIN_CHARS = 80  # a passage shorter than this is a stray heading or a page artifact

SENTENCE_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z(\"'“])")


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str  # the passage as it appears in the filing
    header: str  # company, year and section, prepended for embedding only
    item: str
    heading: str
    position: int

    @property
    def for_embedding(self) -> str:
        return f"{self.header}\n{self.text}"


def chunk_filing(filing: dict) -> list[Chunk]:
    """All passages of a clean filing, as written by ingest."""
    chunks = []
    for item, section in filing["sections"].items():
        header = (
            f"{filing['company']} ({filing['ticker']}) {filing['form']} for fiscal {filing['fiscal_year']}, "
            f"Item {item} {section['title']}"
        )
        for position, (heading, text) in enumerate(passages(section["text"])):
            chunks.append(
                Chunk(
                    id=chunk_id(filing["accession"], item, position),
                    text=text,
                    header=f"{header}: {heading}" if heading else header,
                    item=item,
                    heading=heading,
                    position=position,
                )
            )
    return chunks


def chunk_id(accession: str, item: str, position: int) -> str:
    # Accession first, so every passage of a filing can be listed and replaced by prefix.
    return f"{accession}#{item}#{position:04d}"


def passages(text: str) -> list[tuple[str, str]]:
    """(subheading, passage) pairs, in order."""
    paragraphs = _paragraphs(text)
    out: list[tuple[str, str]] = []
    heading = current_heading = ""
    current: list[str] = []  # the overlap carried in, then this passage's own paragraphs
    fresh = 0  # how many of `current` are this passage's own

    def emit() -> None:
        own = current[len(current) - fresh :]
        body = "\n".join(current)
        if _size(own) < MIN_CHARS and out and len(out[-1][1]) + _size(own) <= MAX_CHARS + OVERLAP_CHARS:
            # A short remainder ("None.") belongs with the passage before it, not on its own.
            last_heading, last = out[-1]
            out[-1] = (last_heading, last + "\n" + "\n".join(own))
        elif len(body) >= MIN_CHARS:
            out.append((current_heading, body))

    def carry() -> list[str]:
        """The last couple of sentences, to start the next passage with."""
        tail: list[str] = []
        for paragraph in reversed(current):
            room = OVERLAP_CHARS - _size(tail)
            if len(paragraph) <= room:
                tail.insert(0, paragraph)
                continue
            sentences = SENTENCE_END.split(paragraph)
            kept: list[str] = []
            while sentences and len(" ".join([sentences[-1], *kept])) <= room:
                kept.insert(0, sentences.pop())
            if kept:
                tail.insert(0, " ".join(kept))
            break
        return tail if _size(tail) < _size(current) else []

    for index, paragraph in enumerate(paragraphs):
        following = paragraphs[index + 1] if index + 1 < len(paragraphs) else ""
        # A title followed by prose. A table's row labels look like titles, but cells follow them.
        if looks_like_subheading(paragraph) and len(following) >= 200:
            if fresh:
                emit()
            current, fresh, heading = [], 0, paragraph
            continue
        if fresh and _size(current) + len(paragraph) > MAX_CHARS:
            emit()
            current, fresh = carry(), 0
        if not fresh:
            current_heading = heading
        current.append(paragraph)
        fresh += 1
        if _size(current) >= TARGET_CHARS:
            emit()
            current, fresh = carry(), 0
    if fresh:
        emit()
    return out


def looks_like_subheading(line: str) -> bool:
    """'Competition' or 'Results of Operations' could be; a sentence or a dollar amount can't.

    On its own this also passes table row labels; `passages` only counts it as a
    subheading when prose follows.
    """
    if len(line) > 120 or len(line) < 4 or line[-1] in ".,;:)" or not line[0].isupper():
        return False
    if len(line.split()) > 16:
        return False
    letters = sum(c.isalpha() for c in line)
    return letters / len(line) > 0.75


def _paragraphs(text: str) -> list[str]:
    """Lines of the section, with any line too long for one passage split between sentences."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(line) <= MAX_CHARS:
            out.append(line)
            continue
        piece = ""
        for sentence in SENTENCE_END.split(line):
            while len(sentence) > MAX_CHARS:  # no sentence breaks at all: cut at a space
                cut = sentence.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > 0 else MAX_CHARS
                if piece:
                    out.append(piece)
                    piece = ""
                out.append(sentence[:cut])
                sentence = sentence[cut:].lstrip()
            if piece and len(piece) + 1 + len(sentence) > TARGET_CHARS:
                out.append(piece)
                piece = sentence
            else:
                piece = f"{piece} {sentence}".strip()
        if piece:
            out.append(piece)
    return out


def _size(paragraphs: list[str]) -> int:
    return sum(len(p) + 1 for p in paragraphs)
