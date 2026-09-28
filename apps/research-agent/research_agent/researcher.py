"""Stage 2 — parallel research. Each worker gets ONE sub-question and a fresh context
(no shared history), runs server-side web search, and returns cited text. Code then
turns every citation into an Evidence record: claim + the quote that backs it + URL.

Why not ask the worker for JSON? Citations and structured outputs can't be combined in
one request (the API returns 400). So workers write cited prose, and extraction is
plain code, which is also cheaper and deterministic."""

import html
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import config
from .evidence import Evidence
from .llm import Ledger, web_search_turn

SYSTEM = """You are a research worker. You research ONE sub-question for a larger brief and can't see
the other sub-questions.

- Use web search. You have at most {max_searches} searches, so make each query specific.
- Write findings as short standalone factual statements, one fact per paragraph, each grounded in
  the search results. Keep numbers, dates and names exactly as the source states them.
- Prefer primary and authoritative sources (official sites, filings, papers, statistics agencies,
  established outlets) over blogs, forums and SEO pages.
- If sources disagree, state each version with its source.
- If you can't find evidence for part of the sub-question, write one paragraph starting with
  "GAP:" that says what's missing. Never fill a gap from memory.
- No introduction, conclusion or recommendations."""


@dataclass
class WorkerResult:
    sub_question_id: str
    evidence: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    uncited: list = field(default_factory=list)    # prose with no citation: kept out of the brief
    queries: list = field(default_factory=list)
    search_errors: list = field(default_factory=list)
    error: str = ""


SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])|\n")
MAX_CLAIM_CHARS = 500


def sentence_around(text: str, start: int, end: int) -> str:
    """The API attaches a citation to the exact quoted span, which is often a fragment
    ("increases in total Mini-Z (…)"). Widen it to the whole sentence so the claim
    stands on its own, but stay close to the quote so a 150-char excerpt can verify it."""
    left = 0
    for m in SENTENCE_BREAK.finditer(text, 0, start):
        left = m.end()
    m = SENTENCE_BREAK.search(text, end)
    right = m.start() if m else len(text)
    sentence = text[left:right].strip()
    sentence = sentence if len(sentence) <= MAX_CLAIM_CHARS else text[start:end].strip()
    return MARKDOWN_LEAD.sub("", sentence).replace("**", "").strip()


MARKDOWN_LEAD = re.compile(r"^(?:[-*•]\s+|\d+\.\s+|#+\s+)?(?:\*\*[^*]{1,60}:\*\*\s*)?")   # "- **Label:** " prefixes


def extract(sub_question_id: str, blocks: list) -> WorkerResult:
    """Deterministic: content blocks in, evidence records out. No model call.

    Text arrives split into blocks at citation boundaries, so first stitch the blocks back
    into one document (remembering where each block sits), then read claims out of it."""
    result = WorkerResult(sub_question_id)
    doc, cited_spans = "", []
    for block in blocks:
        if block.type == "server_tool_use" and block.name == "web_search":
            result.queries.append(block.input.get("query", ""))
            doc += "\n\n"                      # text before and after a search isn't one sentence
        elif block.type == "web_search_tool_result":
            # Errors come back as HTTP 200 with an error object instead of a result list.
            if not isinstance(block.content, list):
                result.search_errors.append(getattr(block.content, "error_code", "unknown"))
        elif block.type == "text":
            start = len(doc)
            doc += block.text
            for c in getattr(block, "citations", None) or []:
                if c.type == "web_search_result_location":
                    cited_spans.append((start, len(doc), c))

    for start, end, c in cited_spans:
        result.evidence.append(Evidence(
            sub_question_id=sub_question_id, claim=sentence_around(doc, start, end),
            quote=html.unescape(c.cited_text or "").strip(),
            url=c.url, title=html.unescape(c.title or ""),
        ))

    cited_ranges = [(s, e) for s, e, _ in cited_spans]
    offset = 0
    for para in doc.split("\n\n"):
        p_start, p_end = offset, offset + len(para)
        offset = p_end + 2
        text = para.strip()
        if text.startswith("GAP:"):
            result.gaps.append(text[4:].strip())
        elif len(text.split()) >= 8 and not any(s < p_end and e > p_start for s, e in cited_ranges):
            result.uncited.append(text)          # opinion or memory: kept out of the brief
    return result


def research_one(ledger: Ledger, sub_question, max_searches: int) -> WorkerResult:
    try:
        blocks, stop = web_search_turn(
            ledger, "research",
            system=SYSTEM.format(max_searches=max_searches),
            content=f"Sub-question: {sub_question.question}\n\nSuggested searches (adapt freely): "
                    + "; ".join(sub_question.search_queries),
            max_searches=max_searches,
        )
    except Exception as e:   # one failed worker shouldn't sink the whole brief
        return WorkerResult(sub_question.id, error=f"{type(e).__name__}: {e}")
    result = extract(sub_question.id, blocks)
    if stop == "refusal":
        result.error = "worker declined this sub-question"
    elif stop == "pause_turn":
        result.error = "search loop still paused after max continuations; results may be partial"
    return result


def research_all(ledger: Ledger, sub_questions: list, max_searches: int = config.SEARCHES_PER_WORKER,
                 on_done=None) -> list:
    """Fan out: every sub-question in parallel. Progress reports as each worker finishes;
    results come back in plan order."""
    with ThreadPoolExecutor(max_workers=config.MAX_PARALLEL_WORKERS) as pool:
        futures = {pool.submit(research_one, ledger, sq, max_searches): sq for sq in sub_questions}
        results = {}
        for fut in as_completed(futures):
            r = fut.result()
            results[r.sub_question_id] = r
            if on_done:
                on_done(futures[fut], r)
    return [results[sq.id] for sq in sub_questions]
