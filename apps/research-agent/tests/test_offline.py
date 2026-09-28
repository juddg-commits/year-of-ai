"""Offline tests: every deterministic step, no API calls, no cost.
Run: .venv/bin/python -m unittest discover tests"""

import unittest
from types import SimpleNamespace as NS

from research_agent import evidence as ev
from research_agent import pages
from research_agent.llm import Ledger
from research_agent.quotes import extend_quote
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


class QuoteExtension(unittest.TestCase):
    """quotes.extend_quote, one test per rule in its docstring. Each case is modeled on a
    real cut-off quote from the runs."""

    def test_cut_off_quote_extends_to_the_end_of_its_sentence(self):
        # Rules 1 + 3: the cut word "documentatio" completes, the decimals don't end the
        # sentence, and the result stops at the first real sentence end.
        page = ("Key findings\nThe Baseline ROI: Across the board, AI scribe adoption was associated with "
                "13.4 fewer minutes of total EHR time and 16.0 fewer minutes of documentation time per 8 "
                "scheduled patient hours. Clinicians also saw 0.49 more visits per week.")
        quote = ("The Baseline ROI: Across the board, AI scribe adoption was associated with 13.4 fewer "
                 "minutes of total EHR time and 16.0 fewer minutes of documentatio...")
        self.assertEqual(extend_quote(page, quote),
                         "The Baseline ROI: Across the board, AI scribe adoption was associated with 13.4 "
                         "fewer minutes of total EHR time and 16.0 fewer minutes of documentation time per 8 "
                         "scheduled patient hours.")

    def test_starts_where_the_quote_starts_not_at_the_sentence_before(self):
        page = ("Meta shipped two models that day. Llama 4 Maverick was released on April 5, 2025 under the "
                "Llama 4 Community License, with 17B active parameters. Behemoth was never released.")
        quote = "Llama 4 Maverick was released on April 5, 2025 under the Llama 4 Commu…"
        self.assertEqual(extend_quote(page, quote),
                         "Llama 4 Maverick was released on April 5, 2025 under the Llama 4 Community License, "
                         "with 17B active parameters.")

    def test_markdown_line_breaks_and_case_dont_block_the_match(self):
        # Rules 2 + 5: the quote has markdown and blank lines, the page has a heading in
        # capitals on its own line. The result is the PAGE's text, whitespace collapsed.
        page = ("Key Points\nQuestion: Is an ambient AI scribe associated with less burnout?\nFINDINGS:\n"
                "This quality improvement study of 263 physicians and advance practice practitioners across "
                "6 health care systems found that after 30 days, burnout decreased from 51.9% to 38.8%.\n"
                "Meaning: ambient scribes may reduce burnout.")
        quote = ("**Findings:**\n\nThis quality improvement study of 263 physicians and advance practice "
                 "practitioners across 6 health care systems found that after 30 da...")
        self.assertEqual(extend_quote(page, quote),
                         "FINDINGS: This quality improvement study of 263 physicians and advance practice "
                         "practitioners across 6 health care systems found that after 30 days, burnout "
                         "decreased from 51.9% to 38.8%.")

    def test_a_line_break_ends_the_sentence(self):
        # List items and table cells often have no final period.
        page = ("Deployments\nAbridge serves more than 150 health systems including Johns Hopkins and Duke\n"
                "Pricing is not public.")
        quote = "Abridge serves more than 150 health systems including Johns Hop..."
        self.assertEqual(extend_quote(page, quote),
                         "Abridge serves more than 150 health systems including Johns Hopkins and Duke")

    def test_long_sentence_is_capped_at_a_whole_word(self):
        # Rule 4.
        page = "Intro.\nQwen3 ships eight models " + "with more words " * 60 + "and ends here."
        quote = "Qwen3 ships eight models with more words with more words with mo..."
        result = extend_quote(page, quote, max_chars=120)
        rest = page[page.index("Qwen3"):]
        self.assertLessEqual(len(result), 120)
        self.assertTrue(rest.startswith(result))
        self.assertEqual(rest[len(result)], " ")          # stopped between words, not inside one

    def test_markdown_links_and_footnote_markers_in_the_quote_are_ignored(self):
        # Also rule 2: Wikipedia quotes carry link targets (even cut-off ones) and [7] markers.
        page = ("Qwen3.8 has 2.4T parameters. The distilled Qwen3.8 27B was released under a more permissive "
                "Apache License 2.0 than the flagship.\nHistory")
        quote = ("The [distilled](https://en.wikipedia.org/wiki/Knowledge_distillation) Qwen3.8 27B was "
                 "released under a more permissive [Apache License](https://en.wik...")
        self.assertEqual(extend_quote(page, quote),
                         "The distilled Qwen3.8 27B was released under a more permissive Apache License 2.0 "
                         "than the flagship.")
        page = ("On April 24, 2026, DeepSeek released the preview of DeepSeek V4: DeepSeek-V4-Pro and "
                "DeepSeek-V4-Flash. The release came as open weights on Hugging Face.")
        quote = ("On April 24, 2026, DeepSeek released the preview of DeepSeek V4: DeepSeek-V4-Pro and "
                 "DeepSeek-V4-Flash.[7] The releas...")
        self.assertEqual(extend_quote(page, quote), page)

    def test_joined_excerpts_extend_the_last_one(self):
        # Rule 6: the API glued two separate spots of the page into one quote.
        page = ("R1\nIt matches or beats OpenAI's o1 on key reasoning benchmarks.\nV4\nDeepSeek released the "
                "V4 paper and models (V4-Pro and V4-Flash), a 1.6T-parameter MoE with 1M context.\nMore")
        quote = ("It matches or beats OpenAI's o1 on key reasoning benchma\n\nDeepSeek released the V4 paper "
                 "and models (V4-Pro and V4-Flash), a 1.6T-para...")
        self.assertEqual(extend_quote(page, quote),
                         "It matches or beats OpenAI's o1 on key reasoning benchma … DeepSeek released the V4 "
                         "paper and models (V4-Pro and V4-Flash), a 1.6T-parameter MoE with 1M context.")

    def test_quote_not_on_the_page_returns_none(self):
        # Rule 7.
        page = "A page about something else entirely. It never mentions the model."
        self.assertIsNone(extend_quote(page, "Llama 4 Maverick was released on April 5, 2025 under the Lla..."))

    def test_too_short_to_locate_safely_returns_none(self):
        # Also rule 7: "The model..." would match the first "the model" anywhere on the page.
        page = "A page about something else entirely. It never mentions the model."
        self.assertIsNone(extend_quote(page, "The model..."))


class QuoteRecovery(unittest.TestCase):
    """The plumbing around extend_quote: page → text, and which quotes get replaced."""

    def test_html_becomes_one_line_per_block(self):
        raw = ("<html><head><title>T</title><script>var x = 1;</script></head><body><h1>Findings</h1>"
               "<p>Burnout fell from\n     51.9% to <b>38.8%</b>[12] in 30&nbsp;days.</p>"
               "<ul><li>One</li><li>Two</li></ul><!-- ad --></body></html>")
        self.assertEqual(pages.html_to_text(raw), "Findings\nBurnout fell from 51.9% to 38.8% in 30 days.\nOne\nTwo")

    def test_a_bad_url_is_a_failed_fetch_not_a_crash(self):
        # InvalidURL isn't an httpx.HTTPError; uncaught, it would crash the run from inside the thread pool.
        import httpx
        with httpx.Client() as client:
            self.assertEqual(pages.fetch_page(client, "http://[::1"), (None, "InvalidURL"))   # fails before any network

    def test_only_cut_off_quotes_are_fetched_and_failures_keep_the_api_quote(self):
        page = "Intro.\nAbridge serves more than 150 health systems including Johns Hopkins and Duke.\n"
        items = [ev.Evidence("q1", "c1", "Abridge serves more than 150 health systems including Johns Hop...",
                             "https://a.com/x", "A"),
                 ev.Evidence("q1", "c2", "Nabla raised $70M in its Series C round...", "https://b.com/y", "B"),
                 ev.Evidence("q1", "c3", "A complete quote that was not cut off.", "https://c.com/z", "C")]
        fetched = []

        def fake_fetch(url):
            fetched.append(url)
            return (page, "ok") if "a.com" in url else (None, "http 403")

        stats = pages.extend_quotes(items, fetch=fake_fetch)
        self.assertEqual(sorted(fetched), ["https://a.com/x", "https://b.com/y"])   # c.com was never fetched
        self.assertEqual(items[0].quote, "Abridge serves more than 150 health systems including Johns Hopkins and Duke.")
        self.assertEqual(items[0].api_quote, "Abridge serves more than 150 health systems including Johns Hop...")
        self.assertEqual(items[1].quote, "Nabla raised $70M in its Series C round...")   # 403: unchanged
        self.assertEqual((stats.truncated, stats.extended, stats.pages), (2, 1, 2))
        self.assertEqual(stats.page_failures, {"http 403": 1})


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
