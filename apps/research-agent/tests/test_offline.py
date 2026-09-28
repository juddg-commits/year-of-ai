"""Offline tests: every deterministic step, no API calls, no cost.
Run: .venv/bin/python -m unittest discover tests"""

import unittest
from types import SimpleNamespace as NS

from research_agent import evidence as ev
from research_agent.llm import Ledger
from research_agent.researcher import extract
from research_agent.synthesizer import Brief, Section, audit


def cite(url, quote, title="T"):
    return NS(type="web_search_result_location", url=url, cited_text=quote, title=title)


class Extraction(unittest.TestCase):
    def test_citations_become_evidence_and_uncited_text_is_kept_out(self):
        blocks = [
            NS(type="server_tool_use", name="web_search", input={"query": "ai scribes burnout"}),
            NS(type="web_search_tool_result", content=[]),
            NS(type="web_search_tool_result", content=NS(error_code="max_uses_exceeded")),
            # Real responses split one text into blocks at citation boundaries:
            NS(type="text", text="In a 2025 trial, ", citations=None),
            NS(type="text", text="scribes cut note time by 20%", citations=[cite("https://a.org/x", "cut &quot;note&quot; time 20%")]),
            NS(type="text", text=" across 6 systems. Adoption keeps growing.\n\n", citations=None),
            NS(type="text", text="I think this is very promising for most hospitals overall.\n\n", citations=None),
            NS(type="text", text="GAP: no data on rural hospitals.", citations=None),
        ]
        r = extract("q1", blocks)
        self.assertEqual(r.queries, ["ai scribes burnout"])
        self.assertEqual(r.search_errors, ["max_uses_exceeded"])
        self.assertEqual(len(r.evidence), 1)
        self.assertEqual(r.evidence[0].quote, 'cut "note" time 20%')   # HTML entities decoded
        # the fragment widens to its sentence, and stops at the sentence end
        self.assertEqual(r.evidence[0].claim, "In a 2025 trial, scribes cut note time by 20% across 6 systems.")
        self.assertEqual(r.uncited, ["I think this is very promising for most hospitals overall."])
        self.assertEqual(r.gaps, ["no data on rural hospitals."])


    def test_markdown_scaffolding_is_stripped_from_claims(self):
        blocks = [NS(type="text", text="- **Scale:** ", citations=None),
                  NS(type="text", text="Abridge serves 250 health systems", citations=[cite("https://b.com", "Abridge serves 250 health systems")]),
                  NS(type="text", text=".\n\n", citations=None)]
        self.assertEqual(extract("q1", blocks).evidence[0].claim, "Abridge serves 250 health systems.")


class Sources(unittest.TestCase):
    def test_same_page_different_url_is_one_source(self):
        a = ev.normalize_url("https://www.Example.com/report/?utm_source=x&id=7#top")
        b = ev.normalize_url("https://example.com/report?id=7")
        self.assertEqual(a, b)

    def test_dedupe_keeps_corroboration_drops_repeats(self):
        items = [ev.Evidence("q1", "Claim A.", "quote a", "https://a.com/1", "A"),
                 ev.Evidence("q1", "Claim A.", "quote a", "https://www.a.com/1/", "A"),   # repeat
                 ev.Evidence("q1", "Claim A.", "quote b", "https://b.com/2", "B")]        # corroboration
        sources = ev.number_sources(items)
        kept = ev.dedupe(items)
        self.assertEqual(len(sources), 2)
        self.assertEqual([e.id for e in kept], ["E1", "E2"])

    def test_notes_merge_sources_and_full_support_clears_partial(self):
        items = [ev.Evidence("q1", "Claim A.", "qa", "https://a.com", "A", source_id="S1", verdict="partial"),
                 ev.Evidence("q1", "Claim A.", "qb", "https://b.com", "B", source_id="S2", verdict="supported")]
        notes = ev.to_notes(items)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].source_ids, ["S1", "S2"])
        self.assertFalse(notes[0].partial)


class CitationAudit(unittest.TestCase):
    def test_unknown_ids_stripped_and_uncited_sentences_counted(self):
        sources = {"S1": ev.Source("S1", "https://a.com", "A", "a.com")}
        brief = Brief(title="t", bottom_line="Scribes save about an hour a day [S1]. They also fix everything forever [S9].",
                      sections=[Section(heading="h", body="Adoption grew quickly across large systems in 2025 [S1]. "
                                                          "Nobody knows why this happened in practice.")],
                      disagreements=[], gaps=[], confidence="medium", confidence_reason="r")
        a = audit(brief, sources)
        self.assertEqual(a.unknown_ids, ["S9"])
        self.assertNotIn("[S9]", brief.bottom_line)
        self.assertEqual(a.sentences, 4)
        self.assertEqual(a.cited_sentences, 2)            # the [S9] sentence lost its only citation
        self.assertEqual(a.cited_ids, ["S1"])


class Cost(unittest.TestCase):
    def test_cost_includes_cache_and_searches(self):
        usage = NS(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=1_000_000,
                   cache_creation_input_tokens=0, server_tool_use=NS(web_search_requests=3))
        rec = Ledger().record("research", NS(usage=usage, model="claude-sonnet-5"), 1.0)
        # $2 input + $1 output + $0.20 cache read + $0.03 searches
        self.assertAlmostEqual(rec.cost, 3.23, places=4)


if __name__ == "__main__":
    unittest.main()
