"""Helpers for running recurring tasks at a fixed interval."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import sys
import time
import traceback
from typing import TextIO


def repeat_with_interval(
    task: Callable[[], None],
    *,
    interval_seconds: float,
    run_once: bool = False,
    max_iterations: int | None = None,
    task_name: str = "task",
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] = datetime.now,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run a task immediately and then repeat it on a fixed cadence."""

    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    delay = max(float(interval_seconds), 0.0)
    iteration = 0

    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        started_at = now_fn().isoformat(timespec="seconds")
        print(f"[{started_at}] Starting {task_name} run {iteration}.", file=output)
        try:
            task()
        except KeyboardInterrupt:
            stopped_at = now_fn().isoformat(timespec="seconds")
            print(f"[{stopped_at}] Stopping {task_name} loop.", file=output)
            return 130
        except Exception as error:  # pragma: no cover - traceback formatting is environment-specific
            failed_at = now_fn().isoformat(timespec="seconds")
            print(f"[{failed_at}] {task_name} run {iteration} failed: {error}", file=errors)
            traceback.print_exc(file=errors)
        else:
            completed_at = now_fn().isoformat(timespec="seconds")
            print(f"[{completed_at}] Completed {task_name} run {iteration}.", file=output)

        if run_once or (max_iterations is not None and iteration >= max_iterations):
            return 0

        waiting_at = now_fn().isoformat(timespec="seconds")
        print(f"[{waiting_at}] Sleeping {delay:g}s before next {task_name} run.", file=output)
        try:
            sleep_fn(delay)
        except KeyboardInterrupt:
            stopped_at = now_fn().isoformat(timespec="seconds")
            print(f"[{stopped_at}] Stopping {task_name} loop.", file=output)
            return 130

    return 0
