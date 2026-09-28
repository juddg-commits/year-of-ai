"""Stage 3 — recover cut-off quotes from the source page.

The API caps each citation's quote at ~150 characters and ends it with "...", so the
number a claim relies on is often just past the cut. Measured on three runs, 76-88%
of "partial" verdicts had a cut-off quote. The worker read the whole page; we only
got the excerpt.

Fix, in plain code with no tokens spent: download the page, find the quote in it, and
extend the quote to the end of its sentence (quotes.extend_quote). A page that can't
be fetched (403, PDF, timeout) keeps the API's quote, so this can only add evidence."""

import html
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import httpx

from . import config
from .evidence import normalize_url
from .quotes import FOOTNOTE, extend_quote

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126 Safari/537.36"}
MAX_PAGE_BYTES = 3_000_000
TRUNCATED = ("...", "…")

DROP = re.compile(r"(?is)<(script|style|noscript|svg|template|head)\b.*?</\1\s*>|<!--.*?-->")
BLOCK = re.compile(r"(?i)</?(p|div|li|ul|ol|h[1-6]|br|tr|td|th|table|section|article|header|footer|"
                   r"nav|aside|blockquote|pre|figure|figcaption|dt|dd|hr|main)\b[^>]*>")
TAG = re.compile(r"<[^>]+>")


def is_truncated(quote: str) -> bool:
    return quote.rstrip().endswith(TRUNCATED)


def html_to_text(raw: str) -> str:
    """Readable text with one line per block (paragraph, list item, heading), so a
    newline always means "a sentence can't continue past here"."""
    raw = DROP.sub(" ", raw)
    raw = re.sub(r"\s+", " ", raw)        # source-code line wraps are just spaces
    raw = BLOCK.sub("\n", raw)
    text = html.unescape(TAG.sub("", raw)).replace("\xa0", " ")
    text = FOOTNOTE.sub("", text)
    lines = (" ".join(line.split()) for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def fetch_page(client: httpx.Client, url: str) -> tuple:
    """Returns (text or None, status). Never raises: a failed fetch just means we keep
    the API's quote."""
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                return None, f"http {r.status_code}"
            ctype = r.headers.get("content-type", "").lower()
            if "html" not in ctype and "text/plain" not in ctype:
                return None, "pdf" if "pdf" in ctype else "not html"
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_PAGE_BYTES:      # huge pages: the quote is almost always near the top
                    break
            text = body.decode(r.encoding or "utf-8", errors="replace")
    except Exception as e:   # timeouts, bad URLs, broken streams: any of them just keeps the API quote
        return None, type(e).__name__
    return (html_to_text(text) if "html" in ctype else text), "ok"


@dataclass
class QuoteStats:
    truncated: int = 0          # evidence items whose quote was cut off
    extended: int = 0           # … and now carry the full sentence from the page
    pages: int = 0              # distinct pages we tried to download
    page_failures: dict = field(default_factory=dict)   # status → count (http 403, pdf, …)
    not_found: int = 0          # page downloaded, but the quote wasn't located in it
    errors: list = field(default_factory=list)   # extend_quote raised: the item keeps its API quote


def extend_quotes(evidence: list, fetch=None) -> QuoteStats:
    """Replace cut-off quotes with the full sentence from the source page, in place.
    The API's original excerpt is kept on the item as api_quote."""
    stats = QuoteStats()
    todo = [ev for ev in evidence if is_truncated(ev.quote)]
    stats.truncated = len(todo)
    urls = {normalize_url(ev.url): ev.url for ev in todo}
    stats.pages = len(urls)
    if not todo:
        return stats

    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=config.PAGE_FETCH_TIMEOUT) as client:
        get = fetch or (lambda url: fetch_page(client, url))
        with ThreadPoolExecutor(max_workers=config.MAX_PAGE_FETCHES) as pool:
            downloaded = dict(zip(urls, pool.map(get, urls.values())))

    for text, status in downloaded.values():
        if text is None:
            stats.page_failures[status] = stats.page_failures.get(status, 0) + 1
    for ev in todo:
        text, _ = downloaded[normalize_url(ev.url)]
        if text is None:
            continue
        try:
            full = extend_quote(text, ev.quote)
        except Exception as e:   # a bug here mustn't sink a run whose research is already paid for
            stats.errors.append(f"{ev.id}: {type(e).__name__}: {e}")
            continue
        if full:
            ev.api_quote, ev.quote = ev.quote, full
            stats.extended += 1
        else:
            stats.not_found += 1
    return stats
