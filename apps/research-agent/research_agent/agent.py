"""The pipeline: plan → research (parallel) → validate → fit context → write → audit.

This is a workflow with agentic steps, not one open-ended agent loop: the ORDER is
fixed in code, and the model decides inside each step (what to search, what counts
as support, how to write). That makes cost, latency and failure modes predictable,
and every stage is testable on its own."""

import time
from dataclasses import dataclass, field

from . import config, evidence as ev_mod, planner, researcher, synthesizer, validator
from .llm import AgentError, Ledger


@dataclass
class Run:
    question: str
    plan: object = None
    workers: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    compressed: bool = False
    brief: object = None
    audit: object = None
    ledger: Ledger = field(default_factory=Ledger)
    timings: dict = field(default_factory=dict)


def run(question: str, *, max_sub_questions: int = config.MAX_SUB_QUESTIONS,
        searches: int = config.SEARCHES_PER_WORKER, log=print) -> Run:
    r = Run(question)
    try:
        return _run(r, max_sub_questions, searches, log)
    except AgentError as e:
        e.run = r            # a failed run still has a cost and a story: keep both for the trace
        raise


def _run(r: Run, max_sub_questions: int, searches: int, log) -> Run:
    question = r.question
    t = time.time()

    def lap(stage):
        nonlocal t
        r.timings[stage] = round(time.time() - t, 1)
        t = time.time()
        return r.timings[stage]

    log("[1/5] Planning…")
    r.plan = planner.plan(r.ledger, question, max_sub_questions)
    log(f"      {len(r.plan.sub_questions)} sub-questions ({lap('plan')}s)")
    for sq in r.plan.sub_questions:
        log(f"      {sq.id}. {sq.question}")

    log(f"[2/5] Researching in parallel (≤{searches} searches each)…")

    def done(sq, w):
        status = f"✗ {w.error}" if w.error and not w.evidence else f"✓ {len(w.evidence)} cited claims, {len(w.queries)} searches"
        log(f"      {sq.id} {status}" + (f", {len(w.gaps)} gap(s)" if w.gaps else ""))

    r.workers = researcher.research_all(r.ledger, r.plan.sub_questions, searches, on_done=done)
    raw = [e for w in r.workers for e in w.evidence]
    if not raw:
        raise AgentError("no worker found any cited evidence; try rephrasing the question")
    r.sources = ev_mod.number_sources(raw)
    r.evidence = ev_mod.dedupe(raw)
    log(f"      {len(raw)} citations → {len(r.evidence)} unique evidence items from {len(r.sources)} sources ({lap('research')}s)")

    log(f"[3/5] Validating {len(r.evidence)} claims against their quotes…")
    r.conflicts = validator.validate(r.ledger, r.evidence, r.sources)
    counts = {v: sum(e.verdict == v for e in r.evidence) for v in ("supported", "partial", "unsupported")}
    log(f"      {counts['supported']} supported, {counts['partial']} partial, "
        f"{counts['unsupported']} dropped, {len(r.conflicts)} conflict(s) ({lap('validate')}s)")
    kept = [e for e in r.evidence if e.verdict in ("supported", "partial")]
    if not kept:
        raise AgentError("the validator rejected every claim; nothing trustworthy to write from")

    r.notes = ev_mod.to_notes(kept)
    before = ev_mod.estimate_tokens(ev_mod.render_notes(r.notes, r.plan.sub_questions))
    r.notes, r.compressed = ev_mod.compress(r.ledger, r.notes, r.plan.sub_questions)
    notes_text = ev_mod.render_notes(r.notes, r.plan.sub_questions)
    after = ev_mod.estimate_tokens(notes_text)
    log(f"[4/5] Context: ~{before:,} tokens of notes (budget {config.EVIDENCE_TOKEN_BUDGET:,})"
        + (f" → compressed to ~{after:,} ({lap('compress')}s)" if r.compressed else " → fits, no compression"))

    log("[5/5] Writing the brief…")
    by_id = {e.id: e for e in r.evidence}
    conflicts_text = "\n".join(
        f"- {c.description} (sources: {', '.join(sorted({by_id[i].source_id for i in c.evidence_ids if i in by_id}))})"
        for c in r.conflicts)
    r.brief = synthesizer.write_brief(r.ledger, question, r.plan, notes_text, conflicts_text)
    r.audit = synthesizer.audit(r.brief, r.sources)
    log(f"      {r.audit.cited_sentences}/{r.audit.sentences} sentences cited, "
        f"{len(r.audit.unknown_ids)} invalid citation(s) removed ({lap('synthesize')}s)")
    return r
