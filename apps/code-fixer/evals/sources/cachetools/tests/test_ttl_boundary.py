"""Not upstream: added for the code-fixer eval (a hidden test in one case).
An item expires exactly when its TTL is reached, whichever way the cache is read."""

import unittest

from cachetools import TTLCache


class Clock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class TTLBoundaryTest(unittest.TestCase):
    def test_item_is_gone_exactly_at_its_ttl(self):
        clock = Clock()
        cache = TTLCache(maxsize=10, ttl=5, timer=clock)
        cache["a"] = 1
        clock.now = 4
        self.assertEqual(len(cache), 1)
        self.assertEqual(list(cache), ["a"])
        clock.now = 5
        self.assertNotIn("a", cache)
        self.assertEqual(len(cache), 0)
        self.assertEqual(list(cache), [])
        self.assertEqual(cache.currsize, 0)

    def test_expire_returns_the_items_that_reached_their_ttl(self):
        clock = Clock()
        cache = TTLCache(maxsize=10, ttl=2, timer=clock)
        cache["a"] = 1
        cache["b"] = 2
        self.assertEqual(cache.expire(1), [])
        self.assertEqual(cache.expire(2), [("a", 1), ("b", 2)])
        self.assertEqual(len(cache), 0)
