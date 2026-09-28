"""
Offline tests for the form videos: ranking, the cache, "try another", and
falling back to a YouTube link when the lookup fails. A fake stands in for
YouTube, so these use no quota.

    .venv/bin/python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent.parent
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="coach-test-"))   # test_context may have set it
os.environ["ANTHROPIC_API_KEY"] = "test-no-calls"
os.environ["APP_PASSWORD"] = ""
sys.path.insert(0, str(HERE))

import app  # noqa: E402
import httpx  # noqa: E402


def item(vid: str, title: str, channel: str, duration: str) -> dict:
    return {"id": vid, "snippet": {"title": title, "channelTitle": channel},
            "contentDetails": {"duration": duration}}


# What a real search for "back squat form Jeff Nippard" can return, in YouTube's order.
SEARCH = [
    item("short", "Squat form in 30 seconds #shorts", "Jeff Nippard", "PT45S"),         # a Short
    item("pod", "Squat talk | Podcast clips", "Some Podcast", "PT9M"),                 # junk
    item("vlog", "My leg day", "Jeff Nippard", "PT12M"),                               # channel match only
    item("long", "Back squat full workout, follow along", "Gym TV", "PT45M"),          # too long
    item("good", "How To Back Squat: Proper Form and Mistakes", "Squat University", "PT8M30S"),
]


class FakeYouTube:
    def __init__(self, items=SEARCH, fail=None):
        self.items, self.fail, self.calls = items, fail, 0

    def get(self, url, **kw):
        self.calls += 1
        if self.fail:
            raise self.fail
        body = ({"items": [{"id": {"videoId": it["id"]}} for it in self.items]} if url.endswith("/search")
                else {"items": self.items})
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: body)


class VideoTest(unittest.TestCase):
    def setUp(self):
        self.cache = Path(tempfile.mkdtemp(prefix="coach-video-")) / "video_cache.json"
        self.saved = app.VIDEO_CACHE_FILE, app.httpx.get, os.environ.get("YOUTUBE_API_KEY")
        app.VIDEO_CACHE_FILE = self.cache
        os.environ["YOUTUBE_API_KEY"] = "fake-key"

    def tearDown(self):
        app.VIDEO_CACHE_FILE, app.httpx.get, key = self.saved
        os.environ.pop("YOUTUBE_API_KEY", None) if key is None else os.environ.update(YOUTUBE_API_KEY=key)

    def use(self, fake: FakeYouTube) -> FakeYouTube:
        app.httpx.get = fake.get
        return fake

    def test_ranking_prefers_a_real_tutorial(self):
        ranked = [v["id"] for v in app.rank_videos("back squat form Jeff Nippard", SEARCH)]
        self.assertEqual(ranked[0], "good")        # names the exercise and reads like instruction
        self.assertNotIn("short", ranked)          # under a minute
        self.assertNotIn("long", ranked)           # over 15 minutes
        self.assertLess(ranked.index("vlog"), ranked.index("pod"))   # junk goes last

    def test_each_query_costs_quota_once_and_another_walks_the_list(self):
        yt = self.use(FakeYouTube())
        first = app.video(q="Back squat form Jeff Nippard")
        self.assertEqual((first["id"], first["cached"], first["n"]), ("good", False, 1))
        self.assertEqual(first["embed"], "https://www.youtube.com/embed/good")
        self.assertEqual(yt.calls, 2)              # search + durations
        again = app.video(q="back squat form jeff nippard ")
        self.assertEqual((again["id"], again["cached"]), ("good", True))
        self.assertEqual(yt.calls, 2)              # from the cache: no quota
        other = app.video(q="back squat form Jeff Nippard", another=1)
        self.assertEqual((other["n"], other["of"]), (2, first["of"]))
        self.assertNotEqual(other["id"], "good")

    def test_a_failed_lookup_falls_back_instead_of_crashing(self):
        # The page opens a YouTube search link on any error, so the endpoint must never raise.
        for failure in (httpx.ConnectError("offline"), httpx.InvalidURL("bad"), ValueError("odd JSON")):
            self.use(FakeYouTube(fail=failure))
            self.assertEqual(app.video(q=f"deadlift form {failure!r}"), {"error": "api_error"})
        self.use(FakeYouTube(items=[SEARCH[0]]))   # only a Short: nothing worth showing
        self.assertEqual(app.video(q="squat short"), {"error": "no_results"})
        os.environ.pop("YOUTUBE_API_KEY")
        self.assertEqual(app.video(q="bench press form"), {"error": "no_key"})
        self.assertEqual(app.video(q=""), {"error": "no_query"})


if __name__ == "__main__":
    unittest.main()
