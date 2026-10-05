"""The network guard in conftest.py is on, and it stops a live call before anything leaves this machine.
Run: .venv/bin/python -m unittest discover tests"""

import importlib.util
import socket
import unittest

from tests import conftest   # unittest doesn't load conftest.py by itself; this import turns the guard on


class NetworkGuardTest(unittest.TestCase):
    def test_a_connection_to_another_host_is_refused(self):
        with self.assertRaises(conftest.LiveNetworkCall):
            socket.create_connection(("api.anthropic.com", 443), timeout=1)

    @unittest.skipUnless(importlib.util.find_spec("anthropic"), "the anthropic SDK isn't installed here")
    def test_the_real_sdk_cannot_reach_the_api(self):
        import anthropic
        client = anthropic.Anthropic(api_key="not-a-real-key", max_retries=0)
        with self.assertRaises(conftest.LiveNetworkCall):   # not an APIConnectionError: the SDK can't catch it
            client.messages.create(model="claude-opus-5-5", max_tokens=1, messages=[{"role": "user", "content": "hi"}])

    def test_this_machine_is_still_reachable(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            with socket.create_connection(server.getsockname(), timeout=1):
                pass
