"""Knowledge-base search: BM25 over a tenant's Markdown files.

No embeddings and no API cost; for a few dozen policy pages BM25 is accurate enough and fully
deterministic, which keeps evals stable. A vector backend can sit behind the same tool later.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from .base import Tool, ToolContext

_STOP = set(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or our "
    "so that the their there this to was we what when where which who will with you your".split()
)
_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    out = []
    for tok in _TOKEN.findall(text.lower()):
        if tok in _STOP:
            continue
        if len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]  # crude plural folding: claims -> claim
        out.append(tok)
    return out


@dataclass
class Chunk:
    source: str
    section: str
    text: str


def chunk_markdown(text: str, source: str, max_chars: int = 1200) -> list[Chunk]:
    chunks: list[Chunk] = []
    title, section, buf = source, source, []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if not body:
            return
        paragraphs, current = body.split("\n\n"), ""
        for p in paragraphs:
            if current and len(current) + len(p) > max_chars:
                chunks.append(Chunk(source, section, current.strip()))
                current = ""
            current += p + "\n\n"
        if current.strip():
            chunks.append(Chunk(source, section, current.strip()))

    for line in text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)", line)
        if m:
            flush()
            buf = []
            if len(m.group(1)) == 1:
                title = m.group(2).strip()
                section = title
            else:
                section = f"{title} > {m.group(2).strip()}"
            continue
        buf.append(line)
    flush()
    return chunks


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(c.section + " " + c.text)) for c in chunks]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 3) -> list[tuple[float, Chunk]]:
        terms = tokenize(query)
        scored = []
        for i, doc in enumerate(self.docs):
            score = 0.0
            for t in terms:
                tf = doc.get(t, 0)
                if not tf:
                    continue
                norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lengths[i] / (self.avg_len or 1)))
                score += self.idf.get(t, 0.0) * norm
            if score > 0:
                scored.append((score, self.chunks[i]))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]


def load_index(docs_dir: Path) -> BM25Index:
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("**/*.md")):
        chunks.extend(chunk_markdown(path.read_text(encoding="utf-8"), path.stem))
    return BM25Index(chunks)


class SearchArgs(BaseModel):
    query: str = Field(min_length=2, description="What to look up, in plain words, e.g. 'damage claim deadline'")
    top_k: int = Field(default=3, ge=1, le=5)


def make_kb_tool(docs_dir: Path, company: str) -> Tool:
    index = load_index(docs_dir)

    def handler(args: SearchArgs, ctx: ToolContext) -> dict:
        hits = index.search(args.query, args.top_k)
        if not hits:
            return {"results": [], "note": "No matching policy text. Do not invent a policy; escalate if it matters."}
        return {"results": [{"source": c.source, "section": c.section, "text": c.text} for _, c in hits]}

    return Tool(
        name="search_knowledge_base",
        description=f"Search {company}'s policies, service descriptions and FAQs. Use it before stating any policy, fee or deadline.",
        args_model=SearchArgs,
        handler=handler,  # type: ignore[arg-type]
    )
