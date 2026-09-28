"""Evidence records, source numbering, de-duplication, and the context budget.

Context management in one sentence: the orchestrator never sees raw search pages
(~20k tokens per search), only compact evidence notes (claim + one-sentence quote +
source id). When even those outgrow the budget, they're condensed per sub-question
while keeping which sources back each note, so citations survive compression."""

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, Field

from . import config
from .llm import Ledger, structured

TRACKING_PARAMS = re.compile(r"^(utm_\w+|ref|ref_src|fbclid|gclid|mc_cid|mc_eid)$", re.I)


@dataclass
class Evidence:
    sub_question_id: str
    claim: str
    quote: str
    url: str
    title: str
    id: str = ""
    source_id: str = ""
    verdict: str = ""        # supported | partial | unsupported (set by the validator)
    reason: str = ""
    api_quote: str = ""      # the API's cut-off excerpt, when pages.py replaced it with the full sentence


@dataclass
class Source:
    id: str
    url: str
    title: str
    domain: str


@dataclass
class Note:
    """What the brief is written from: a claim plus the sources that back it."""
    sub_question_id: str
    text: str
    source_ids: list = field(default_factory=list)
    partial: bool = False
    quote: str = ""


def normalize_url(url: str) -> str:
    """Same page, different URL (tracking params, www, trailing slash) → one source."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not TRACKING_PARAMS.match(k)])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower() or "https", host, path, query, ""))


def number_sources(evidence: list) -> dict:
    """Give each distinct page a stable id (S1, S2, …) in order of first appearance."""
    sources: dict = {}
    for ev in evidence:
        key = normalize_url(ev.url)
        if key not in sources:
            sources[key] = Source(f"S{len(sources) + 1}", ev.url, ev.title, urlsplit(key).netloc)
        ev.source_id = sources[key].id
    return {s.id: s for s in sources.values()}


def dedupe(evidence: list) -> list:
    """Same claim backed by the same quote from the same page, once. Different pages
    backing the same claim are kept: that's corroboration, not duplication."""
    seen, out = set(), []
    for ev in evidence:
        key = (ev.source_id, " ".join(ev.claim.lower().split()), " ".join(ev.quote.lower().split()))
        if key not in seen:
            seen.add(key)
            out.append(ev)
    for i, ev in enumerate(out, 1):
        ev.id = f"E{i}"
    return out


def estimate_tokens(text: str) -> int:
    return len(text) // 4    # ~4 characters per token; good enough to decide whether to compress


def to_notes(evidence: list) -> list:
    """Validated evidence → notes. Items sharing a claim merge their sources."""
    notes: dict = {}
    for ev in evidence:
        key = (ev.sub_question_id, ev.claim)
        note = notes.get(key)
        if note is None:
            note = notes[key] = Note(ev.sub_question_id, ev.claim, [], ev.verdict == "partial", ev.quote)
        if ev.source_id not in note.source_ids:
            note.source_ids.append(ev.source_id)
        note.partial = note.partial and ev.verdict == "partial"   # any full support clears the flag
    return list(notes.values())


def render_notes(notes: list, sub_questions: list) -> str:
    lines = []
    for sq in sub_questions:
        mine = [n for n in notes if n.sub_question_id == sq.id]
        lines.append(f"### {sq.id}: {sq.question}")
        if not mine:
            lines.append("- (no validated evidence)")
        for n in mine:
            cites = "".join(f"[{s}]" for s in n.source_ids)
            quote = f' (quote: "{n.quote}")' if n.quote else ""
            lines.append(f"- {n.text} {cites}{' (partial)' if n.partial else ''}{quote}")
        lines.append("")
    return "\n".join(lines)


# ── Compression, only when the evidence outgrows the budget ────────────────────

class CondensedNote(BaseModel):
    text: str = Field(description="One precise factual statement merged from the notes")
    source_ids: list[str] = Field(description="Every source id (S#) from the notes this statement came from")
    partial: bool = Field(description="True if any merged note was marked (partial)")


class Condensed(BaseModel):
    notes: list[CondensedNote]


COMPRESS_SYSTEM = """You condense research notes for ONE sub-question so they fit a context budget.
Merge notes that say the same thing, drop redundancy, keep every distinct fact, number, date and
disagreement. Each output note must list the source ids (S#) of every input note it came from; never
invent ids. Don't add facts that aren't in the notes."""


def compress(ledger: Ledger, notes: list, sub_questions: list, budget: int = config.EVIDENCE_TOKEN_BUDGET) -> tuple:
    """Returns (notes, compressed?). Condenses per sub-question with the cheap model."""
    if estimate_tokens(render_notes(notes, sub_questions)) <= budget:
        return notes, False
    def condense(sq):
        mine = [n for n in notes if n.sub_question_id == sq.id]
        if len(mine) <= 3:
            return mine
        condensed = structured(
            ledger, "compress", model=config.WORKER_MODEL, schema=Condensed, effort="low",
            system=COMPRESS_SYSTEM, content=render_notes(mine, [sq]),
        )
        valid = {s for n in mine for s in n.source_ids}
        kept = []
        for c in condensed.notes:
            ids = [s for s in c.source_ids if s in valid]   # provenance check: no invented sources
            if ids:
                kept.append(Note(sq.id, c.text, ids, c.partial))
        return kept

    with ThreadPoolExecutor(max_workers=config.MAX_PARALLEL_WORKERS) as pool:
        out = [n for group in pool.map(condense, sub_questions) for n in group]
    return out, True
