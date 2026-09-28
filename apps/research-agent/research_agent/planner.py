"""Stage 1 — query decomposition. One structured call turns a broad question into
independent sub-questions a worker can research alone."""

from datetime import date

from pydantic import BaseModel, Field

from . import config
from .llm import Ledger, structured


class SubQuestion(BaseModel):
    id: str = Field(description="q1, q2, ...")
    question: str = Field(description="Specific and answerable from public sources on its own")
    why_it_matters: str = Field(description="How answering it serves the main question, one sentence")
    search_queries: list[str] = Field(description="1-3 concrete web search queries")


class Plan(BaseModel):
    restated_question: str = Field(description="The question in one precise sentence")
    assumptions: list[str] = Field(description="How you resolved anything ambiguous (scope, time, region)")
    sub_questions: list[SubQuestion]


SYSTEM = """You plan research for a brief. Break the user's question into 2-{max} sub-questions that
together answer it.

Each sub-question goes to a separate research worker who sees ONLY that sub-question, so it must:
- stand alone (no "it"/"they" that point at other sub-questions),
- be specific and answerable with facts, numbers, dates or primary sources,
- not overlap with the others.

Prefer fewer, sharper sub-questions over many vague ones. Don't ask the user anything: resolve
ambiguity yourself and record it under assumptions. Today's date is {today}; make time-sensitive
sub-questions ask for the latest information."""


def plan(ledger: Ledger, question: str, max_sub_questions: int = config.MAX_SUB_QUESTIONS) -> Plan:
    result = structured(
        ledger, "plan", model=config.ORCHESTRATOR_MODEL, schema=Plan, max_tokens=8000,
        system=SYSTEM.format(max=max_sub_questions, today=date.today().isoformat()),
        content=f"Question: {question}",
    )
    # The schema can't express "at most N" (no array-length constraints), so enforce it here.
    result.sub_questions = result.sub_questions[:max_sub_questions]
    for i, sq in enumerate(result.sub_questions, 1):
        sq.id = f"q{i}"
    return result
