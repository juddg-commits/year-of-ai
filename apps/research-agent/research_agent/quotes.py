"""Finding a cut-off quote inside the page it came from, and completing its sentence.

Pure string work, no network: pages.py downloads the page, turns it into text, and calls
extend_quote() for every cut-off quote. Each rule below has a test in
tests/test_offline.py (QuoteExtension), modeled on a real quote from a run."""

import re

# A sentence ends at . ! or ? followed by whitespace or the end of the text (so the dot in
# "13.4" doesn't count), or at a line break: html_to_text puts each paragraph on its own line.
SENTENCE_END = re.compile(r"[.!?](?=\s|$)|\n")
# The API's quotes are markdown, the page text isn't: link targets like
# "[Apache License](https://en.wikipedia.org/wiki/Apache_License)" (maybe cut off, maybe
# with parentheses inside) and footnote markers like [12], which html_to_text drops too.
LINK_TARGET = re.compile(r"\((?:https?://|\./)(?:[^()\s]|\([^()\s]*\))*(?:\)|$)")
FOOTNOTE = re.compile(r"\[(?:\d{1,3}|[a-z]|citation needed)\]")
# Where the API joined separate excerpts of the page into one quote.
EXCERPT_GAP = re.compile(r"\.{3}|…|\n\s*\n")
MIN_WORDS = 4     # fewer words than this could match the wrong spot on the page


def extend_quote(page_text: str, quote: str, max_chars: int = 600) -> str | None:
    r"""Find `quote` in `page_text` and return it extended to the end of its sentence.

    The quote is the API's excerpt: at most ~150 characters, usually ending in "..." and
    often cut mid-word ("...16.0 fewer minutes of documentatio...").

    1. The result runs from where the quote starts to the end of the sentence it was cut
       off in.
    2. Only the quote's words have to match, in order, ignoring case and whatever sits
       between them: whitespace, punctuation, markdown ("**Findings:**\n\nThis study"
       matches "FINDINGS:\nThis study"). Link targets and [12] markers are ignored.
    3. The last word is skipped: it may be half a word ("documentatio").
    4. At most `max_chars` characters are taken from the page, cut at a whole word.
    5. Whitespace is collapsed to single spaces.
    6. If the API joined separate excerpts ("...", blank lines), the cut is in the last
       one: that one is extended and the earlier ones are kept as given.
    7. None if the quote isn't on the page (or is too short to locate safely); the caller
       then keeps the API's quote.
    """
    quote = FOOTNOTE.sub(" ", LINK_TARGET.sub("", quote))
    extended = _extend(page_text, quote, max_chars)
    if extended is not None:
        return extended
    pieces = [" ".join(p.split()) for p in EXCERPT_GAP.split(quote) if p.strip()]
    if len(pieces) < 2:
        return None
    last = _extend(page_text, pieces[-1], max_chars)     # rule 6: the cut is in the last excerpt
    return " … ".join(pieces[:-1] + [last]) if last else None


def _extend(page_text: str, quote: str, max_chars: int) -> str | None:
    words = re.findall(r"\w+", quote)[:-1]
    if len(words) < MIN_WORDS:
        return None
    match = re.search(r"\W+".join(map(re.escape, words)), page_text, re.IGNORECASE)
    if not match:
        return None

    end = SENTENCE_END.search(page_text, match.end())
    if end is None:
        stop = len(page_text)
    elif end.group() == "\n":
        stop = end.start()           # the line break isn't part of the sentence
    else:
        stop = end.end()             # keep the . ! or ?

    result = " ".join(page_text[match.start():stop].split())
    if len(result) > max_chars:
        cut = result.rfind(" ", 0, max_chars + 1)      # the last space within the limit
        result = result[:cut] if cut > 0 else result[:max_chars]
    return result
