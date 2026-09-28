"""Stage 4 — validation, the main hallucination defense before writing.

Every evidence item pairs a CLAIM (the worker's sentence) with the QUOTE the API says
backs it. A worker can still over-generalize, mix up numbers, or attach a citation to
the wrong sentence. So a separate, stronger model audits claim-vs-quote and anything
unsupported is dropped before the writer ever sees it.

Two layers: cheap deterministic checks first (free), model judgment second."""

from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from . import config
from .llm import Ledger, structured


class Verdict(BaseModel):
    evidence_id: str
    verdict: Literal["supported", "partial", "unsupported"]
    reason: str = Field(description="At most 15 words")


class Conflict(BaseModel):
    evidence_ids: list[str]
    description: str = Field(description="What disagrees, in one sentence")


class Validation(BaseModel):
    verdicts: list[Verdict]
    conflicts: list[Conflict]


SYSTEM = """You audit research evidence before it goes into a brief. Each item has a CLAIM written by a
research assistant and the QUOTE it cites: usually a full sentence from the source. A quote that
ends in "..." was cut off, and nothing after the cut counts as support.

Judge only whether the quote supports the claim's KEY FACTS: the finding itself, every number,
date and name, the direction of an effect, and its scope.
- supported: the quote backs the key facts. Context the claim adds around them (who said it,
  where, a lead-in phrase) doesn't need to appear in the quote.
- partial: a key fact in the claim is missing from the quote or stretched (a bigger number, a
  broader scope, "causes" where the quote says "is associated with").
- unsupported: the quote doesn't back the claim, contradicts it, or is unrelated.

Be strict about numbers and dates: a number that isn't in the quote can't be supported. Don't use your own knowledge of whether the claim is true: this
check is about whether THIS quote supports THIS claim.

Also list conflicts: groups of items whose claims contradict each other. Return one verdict for
every item."""


def precheck(ev) -> str:
    """Free, deterministic rejections before any model call. Returns a reason or ''."""
    if len(ev.quote) < 15:
        return "quote too short to support anything"
    if not ev.url.startswith(("http://", "https://")):
        return "no usable source URL"
    return ""


def validate(ledger: Ledger, evidence: list, sources: dict) -> list:
    """Sets verdict + reason on every item; returns the conflicts found."""
    to_judge = []
    for ev in evidence:
        reason = precheck(ev)
        if reason:
            ev.verdict, ev.reason = "unsupported", reason
        else:
            to_judge.append(ev)

    def judge(batch):
        items = "\n\n".join(
            f"[{ev.id}] source: {sources[ev.source_id].title} ({sources[ev.source_id].domain})\n"
            f"CLAIM: {ev.claim}\nQUOTE: \"{ev.quote}\""
            for ev in batch
        )
        # effort=low: this is classification with a clear rubric, not open-ended reasoning.
        # Measured: medium spent $0.54 and 150 s on 155 items, mostly on thinking tokens.
        return batch, structured(ledger, "validate", model=config.ORCHESTRATOR_MODEL, schema=Validation,
                                 effort="low", system=SYSTEM, content=items)

    batches = [to_judge[i:i + config.VALIDATION_BATCH] for i in range(0, len(to_judge), config.VALIDATION_BATCH)]
    conflicts = []
    with ThreadPoolExecutor(max_workers=config.MAX_PARALLEL_WORKERS) as pool:   # batches are independent
        judged = list(pool.map(judge, batches))
    for batch, result in judged:
        by_id = {v.evidence_id: v for v in result.verdicts}
        for ev in batch:
            v = by_id.get(ev.id)
            if v is None:   # the model skipped it: fail closed, never pass unaudited evidence
                ev.verdict, ev.reason = "unsupported", "not judged by validator"
            else:
                ev.verdict, ev.reason = v.verdict, v.reason
        known = {ev.id for ev in batch}
        conflicts += [c for c in result.conflicts if len(set(c.evidence_ids) & known) >= 2]
    return conflicts
