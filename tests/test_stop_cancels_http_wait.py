import threading
import time
import unittest

from core import http_rate_limiter as limiter


class StopCancelsHttpWaitTests(unittest.TestCase):
    def setUp(self):
        with limiter._LOCK:
            limiter._LAST_BY_HOST.clear()
            limiter._WINDOW_BY_HOST.clear()

    def test_stop_interrupts_pending_rate_limit_wait(self):
        stopped = threading.Event()
        settings = {
            "_cancel_callback": stopped.is_set,
            "http_min_interval_by_host": {"example.invalid": 10.0},
            "http_requests_per_minute_by_host": {"example.invalid": 0},
        }
        with limiter._LOCK:
            limiter._LAST_BY_HOST["example.invalid"] = time.monotonic()

        captured = []

        def run_wait():
            try:
                limiter.wait_for("https://example.invalid/posts.json", settings)
            except Exception as exc:
                captured.append(exc)

        thread = threading.Thread(target=run_wait)
        started = time.monotonic()
        thread.start()
        time.sleep(0.05)
        stopped.set()
        thread.join(timeout=0.6)

        self.assertFalse(thread.is_alive(), "STOP must not leave a lane sleeping in the limiter")
        self.assertTrue(captured)
        self.assertIsInstance(captured[0], limiter.RequestCancelled)
        self.assertLess(time.monotonic() - started, 0.6)

    def test_already_cancelled_request_is_never_admitted(self):
        settings = {"_cancel_callback": lambda: True}
        with self.assertRaises(limiter.RequestCancelled):
            limiter.wait_for("https://example.invalid/posts.json", settings)
        with limiter._LOCK:
            self.assertNotIn("example.invalid", limiter._LAST_BY_HOST)


if __name__ == "__main__":
    unittest.main()
