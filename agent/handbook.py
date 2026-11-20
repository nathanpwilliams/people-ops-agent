"""BM25 search over handbook sections.

Each markdown file is one document; each `## <number> <title>` heading starts a chunk.
A result cites the chunk as e.g. "PTO §2.1".
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rank_bm25 import BM25Okapi

from .config import HANDBOOK_DIR

SECTION_RE = re.compile(r"^##\s+(\d+(?:\.\d+)*)\s+(.+?)\s*$")
TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
TOKEN_RE = re.compile(r"[a-z0-9$]+")
STOPWORDS = frozenset(
    "a an the of to in on for and or is are do does we you your i my our how many much "
    "what when where which can get be it this that with at by from per as".split()
)


@dataclass(frozen=True)
class Chunk:
    doc: str
    section: str
    title: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.doc} §{self.section}"


def tokenize(text: str) -> list[str]:
    tokens = []
    for tok in TOKEN_RE.findall(text.lower()):
        # crude singularisation so "holidays" matches "holiday"
        if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        if tok not in STOPWORDS:
            tokens.append(tok)
    return tokens


def load_chunks(handbook_dir: Path = HANDBOOK_DIR) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(handbook_dir).glob("*.md")):
        doc = path.stem
        section = title = None
        body: list[str] = []

        def flush() -> None:
            if section is not None:
                text = " ".join(line.strip() for line in body if line.strip())
                chunks.append(Chunk(doc=doc, section=section, title=title, text=text))

        for line in path.read_text(encoding="utf-8").splitlines():
            if (m := TITLE_RE.match(line)) and section is None:
                doc = m.group(1)
                continue
            if m := SECTION_RE.match(line):
                flush()
                section, title, body = m.group(1), m.group(2), []
                continue
            body.append(line)
        flush()
    return chunks


class HandbookIndex:
    def __init__(self, handbook_dir: Path = HANDBOOK_DIR):
        self.chunks = load_chunks(handbook_dir)
        if not self.chunks:
            raise ValueError(f"No handbook sections found in {handbook_dir}")
        # Doc name and section title are repeated so they weigh more than body text.
        self._bm25 = BM25Okapi(
            [tokenize(f"{c.doc} {c.doc} {c.title} {c.title} {c.text}") for c in self.chunks]
        )

    def search(self, query: str, k: int = 3) -> list[dict]:
        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)[:k]
        return [
            {
                "doc": self.chunks[i].doc,
                "section": self.chunks[i].section,
                "citation": self.chunks[i].citation,
                "title": self.chunks[i].title,
                "text": self.chunks[i].text,
                "score": round(float(scores[i]), 3),
            }
            for i in ranked
        ]


@lru_cache(maxsize=4)
def get_index(handbook_dir: Path = HANDBOOK_DIR) -> HandbookIndex:
    return HandbookIndex(Path(handbook_dir))
