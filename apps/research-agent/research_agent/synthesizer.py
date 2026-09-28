"""Stage 4 — synthesis, then a programmatic citation audit.

The writer sees only validated notes with source ids and must cite [S#] on every
factual sentence. The model's output is structured (sections as fields), so code
can check it rather than trust it: unknown source ids are stripped, uncited
sentences are counted, and the numbers go in the report."""

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from . import config
from .llm import Ledger, structured

MARKER = re.compile(r"\[(S\d+)\]")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


class Section(BaseModel):
    heading: str
    body: str = Field(description="Markdown paragraphs. Every factual sentence ends with [S#] citations.")


class Brief(BaseModel):
    title: str
    bottom_line: str = Field(description="2-3 cited sentences that directly answer the question")
    sections: list[Section]
    disagreements: list[str] = Field(description="Where sources conflict, each item cited")
    gaps: list[str] = Field(description="What the evidence could not answer")
    confidence: Literal["high", "medium", "low"]
    confidence_reason: str = Field(description="One sentence: why this confidence level")


SYSTEM = """You write a research brief using ONLY the evidence notes provided. Don't use outside
knowledge, even if you're sure: if the notes don't cover something, list it under gaps.

Citations: cite source ids exactly as given, like [S2] or [S2][S5], at the end of every sentence
that states a fact. Never cite an id that isn't in the notes.
- Notes marked (partial) are only partly backed by their quote: hedge them ("reportedly",
  "according to [S4]") and don't build conclusions on them alone.
- When sources conflict, say so and put the conflict in disagreements rather than picking a side
  silently.
- Be specific: numbers with units, dates, names. No filler, no restating the question.
- Length: 600-900 words across 3-5 sections. A brief is for a busy reader: lead with what
  matters, merge overlapping points, cut minor details.
- Confidence: high only if the key claims are backed by multiple independent sources."""


@dataclass
class Audit:
    sentences: int = 0
    cited_sentences: int = 0
    uncited: list = field(default_factory=list)
    unknown_ids: list = field(default_factory=list)
    cited_ids: list = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.cited_sentences / self.sentences if self.sentences else 1.0


def write_brief(ledger: Ledger, question: str, plan, notes_text: str, conflicts_text: str) -> Brief:
    content = (f"Question: {question}\nInterpreted as: {plan.restated_question}\n"
               f"Assumptions: {'; '.join(plan.assumptions) or 'none'}\n\n"
               f"## Evidence notes\n{notes_text}\n## Conflicts flagged by the validator\n{conflicts_text or 'none'}")
    return structured(ledger, "synthesize", model=config.ORCHESTRATOR_MODEL, schema=Brief,
                      effort="medium", system=SYSTEM, content=content)


def audit(brief: Brief, sources: dict) -> Audit:
    """Strip citations to sources that don't exist; count sentences with no citation."""
    result = Audit()

    def clean(text: str) -> str:
        def keep(m):
            if m.group(1) in sources:
                if m.group(1) not in result.cited_ids:
                    result.cited_ids.append(m.group(1))
                return m.group(0)
            result.unknown_ids.append(m.group(1))
            return ""
        text = MARKER.sub(keep, text)
        for sentence in SENTENCE_END.split(text):
            words = sentence.strip()
            if len(words.split()) < 5 or words.startswith("#"):   # headings and fragments aren't claims
                continue
            result.sentences += 1
            if MARKER.search(words):
                result.cited_sentences += 1
            else:
                result.uncited.append(words)
        return text

    brief.bottom_line = clean(brief.bottom_line)
    for s in brief.sections:
        s.body = clean(s.body)
    brief.disagreements = [clean(d) for d in brief.disagreements]
    return result
