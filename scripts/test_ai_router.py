import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
import ai_router as r  # noqa: E402


class RouterTests(unittest.TestCase):
    def setUp(self):
        r._registry = {task: [] for task in r.TASKS}

    def test_falls_back_when_first_provider_fails(self):
        def bad(prompt):
            raise RuntimeError("quota exceeded")

        r.register("script", r.Provider("a", "KEY_A", bad))
        r.register("script", r.Provider("b", "KEY_B", lambda p: f"ok:{p}"))
        with mock.patch.dict(os.environ, {"KEY_A": "1", "KEY_B": "2"}):
            self.assertEqual(r.route("script", "hi"), "ok:hi")

    def test_skips_providers_without_key(self):
        r.register("voice", r.Provider("a", "KEY_A_MISSING", lambda p: "should-not-run"))
        r.register("voice", r.Provider("b", "KEY_B", lambda p: "from-b"))
        with mock.patch.dict(os.environ, {"KEY_B": "2"}, clear=True):
            self.assertEqual(r.plan("voice"), ["b"])
            self.assertEqual(r.route("voice", "x"), "from-b")

    def test_raises_when_all_fail(self):
        r.register("image", r.Provider("a", "KEY_A", lambda p: (_ for _ in ()).throw(RuntimeError("down"))))
        with mock.patch.dict(os.environ, {"KEY_A": "1"}):
            with self.assertRaises(r.RouterError):
                r.route("image", "x")

    def test_no_providers_registered(self):
        with self.assertRaises(r.RouterError):
            r.route("video", "x")

    def test_unknown_task(self):
        with self.assertRaises(ValueError):
            r.plan("nope")


if __name__ == "__main__":
    unittest.main()
