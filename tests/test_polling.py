from __future__ import annotations

from datetime import datetime
from io import StringIO
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from options_tradebot.utils.polling import repeat_with_interval, run_during_market_hours


class PollingTests(unittest.TestCase):
    def test_repeat_with_interval_runs_multiple_iterations(self) -> None:
        events: list[tuple[str, float] | str] = []

        def task() -> None:
            events.append("scan")

        def sleep_fn(seconds: float) -> None:
            events.append(("sleep", seconds))

        stdout = StringIO()
        exit_code = repeat_with_interval(
            task,
            interval_seconds=300.0,
            max_iterations=2,
            task_name="live market scan",
            sleep_fn=sleep_fn,
            now_fn=lambda: datetime(2026, 3, 24, 9, 30, 0),
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(events, ["scan", ("sleep", 300.0), "scan"])
        self.assertIn("Starting live market scan run 1.", stdout.getvalue())
        self.assertIn("Sleeping 300s before next live market scan run.", stdout.getvalue())

    def test_repeat_with_interval_continues_after_failure(self) -> None:
        attempts = 0

        def task() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("boom")

        stdout = StringIO()
        stderr = StringIO()
        exit_code = repeat_with_interval(
            task,
            interval_seconds=60.0,
            max_iterations=2,
            task_name="live market scan",
            sleep_fn=lambda _: None,
            now_fn=lambda: datetime(2026, 3, 24, 9, 35, 0),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(attempts, 2)
        self.assertIn("failed: boom", stderr.getvalue())
        self.assertIn("Completed live market scan run 2.", stdout.getvalue())

    def test_run_during_market_hours_waits_until_open_then_runs(self) -> None:
        events: list[tuple[str, float] | str] = []
        clock = iter(
            [
                datetime(2026, 3, 25, 9, 55, 0),
                datetime(2026, 3, 25, 10, 0, 0),
                datetime(2026, 3, 25, 10, 0, 0),
                datetime(2026, 3, 25, 10, 0, 0),
            ]
        )

        def task() -> None:
            events.append("scan")

        def sleep_fn(seconds: float) -> None:
            events.append(("sleep", seconds))

        stdout = StringIO()
        exit_code = run_during_market_hours(
            task,
            interval_seconds=300.0,
            market_open="10:00",
            market_close="18:00",
            timezone="America/Sao_Paulo",
            max_iterations=1,
            task_name="short-vol scan",
            sleep_fn=sleep_fn,
            now_fn=lambda: next(clock),
            stdout=stdout,
            stderr=StringIO(),
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(events, [("sleep", 300.0), "scan"])
        self.assertIn("Waiting 300s for market open", stdout.getvalue())
        self.assertIn("Starting short-vol scan run 1.", stdout.getvalue())
