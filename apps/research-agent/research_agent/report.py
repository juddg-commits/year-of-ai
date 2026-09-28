"""Turn a Run into the things people read: the brief (markdown), the cost table,
and a JSON trace that replays exactly what happened (the raw material for evals)."""

import json
import re
from dataclasses import asdict
from datetime import datetime

from . import config


def brief_markdown(run) -> str:
    b, a = run.brief, run.audit
    lines = [f"# {b.title}", "", f"**Bottom line:** {b.bottom_line}", ""]
    for s in b.sections:
        lines += [f"## {s.heading}", "", s.body.strip(), ""]
    if b.disagreements:
        lines += ["## Where sources disagree", ""] + [f"- {d}" for d in b.disagreements] + [""]
    if b.gaps:
        lines += ["## Open questions", ""] + [f"- {g}" for g in b.gaps] + [""]
    lines += [f"**Confidence:** {b.confidence}. {b.confidence_reason}", "", "## Sources", ""]
    for sid in sorted(a.cited_ids, key=lambda s: int(s[1:])):
        src = run.sources[sid]
        lines.append(f"- **[{sid}]** {src.title or src.domain}: {src.url}")
    dropped = sum(e.verdict == "unsupported" for e in run.evidence)
    lines += ["", "---",
              f"_{a.cited_sentences}/{a.sentences} sentences cited · {dropped} claims dropped by validation · "
              f"{len(run.sources)} sources read · ${run.ledger.total():.2f} · "
              f"{sum(run.timings.values()):.0f}s_"]
    return "\n".join(lines)


def cost_table(run) -> str:
    stages = run.ledger.by_stage()
    rows = [f"{'stage':<12}{'calls':>6}{'searches':>10}{'in tok':>10}{'out tok':>9}{'cost':>9}"]
    for name, s in stages.items():
        rows.append(f"{name:<12}{s['calls']:>6}{s['searches']:>10}{s['input_tokens']:>10,}"
                    f"{s['output_tokens']:>9,}{'$' + format(s['cost'], '.3f'):>9}")
    rows.append(f"{'total':<12}{'':>6}{'':>10}{'':>10}{'':>9}{'$' + format(run.ledger.total(), '.3f'):>9}")
    return "\n".join(rows)


def save(run) -> tuple:
    config.RUNS_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", run.question.lower()).strip("-")[:50]
    stem = config.RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}"
    md = stem.with_suffix(".md")
    md.write_text(brief_markdown(run))
    trace = {
        "question": run.question,
        "models": {"orchestrator": config.ORCHESTRATOR_MODEL, "worker": config.WORKER_MODEL},
        "plan": run.plan.model_dump(),
        "workers": [{"sub_question_id": w.sub_question_id, "queries": w.queries, "gaps": w.gaps,
                     "uncited": w.uncited, "search_errors": w.search_errors, "error": w.error}
                    for w in run.workers],
        "sources": {k: asdict(v) for k, v in run.sources.items()},
        "quote_stats": asdict(run.quote_stats) if run.quote_stats else None,
        "evidence": [asdict(e) for e in run.evidence],
        "conflicts": [c.model_dump() for c in run.conflicts],
        "notes": [asdict(n) for n in run.notes],
        "compressed": run.compressed,
        "brief": run.brief.model_dump(),
        "audit": {**asdict(run.audit), "coverage": run.audit.coverage},
        "calls": run.ledger.as_dicts(),
        "cost_by_stage": run.ledger.by_stage(),
        "total_cost": run.ledger.total(),
        "timings_s": run.timings,
    }
    js = stem.with_suffix(".json")
    js.write_text(json.dumps(trace, indent=2))
    return md, js


def save_failed(run, error: str):
    """What a failed run did before it stopped, and what it cost."""
    config.RUNS_DIR.mkdir(exist_ok=True)
    path = config.RUNS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-FAILED.json"
    path.write_text(json.dumps({
        "question": run.question, "error": error,
        "plan": run.plan.model_dump() if run.plan else None,
        "workers": [{"sub_question_id": w.sub_question_id, "queries": w.queries, "evidence": len(w.evidence),
                     "uncited": w.uncited, "gaps": w.gaps, "error": w.error} for w in run.workers],
        "calls": run.ledger.as_dicts(), "total_cost": run.ledger.total(),
    }, indent=2))
    return path
