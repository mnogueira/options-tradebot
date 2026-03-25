"""Helpers for running recurring tasks at a fixed interval."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time as wall_time, timedelta
import sys
import time
import traceback
from typing import TextIO
from zoneinfo import ZoneInfo


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


def run_during_market_hours(
    task: Callable[[], None],
    *,
    interval_seconds: float,
    market_open: str,
    market_close: str,
    timezone: str,
    run_once: bool = False,
    max_iterations: int | None = None,
    task_name: str = "task",
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], datetime] | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Run a task on a fixed cadence, but only during the configured market session."""

    tz = ZoneInfo(timezone)
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    delay = max(float(interval_seconds), 0.0)
    current_time = now_fn or (lambda: datetime.now(tz))
    opened = _parse_wall_time(market_open)
    closed = _parse_wall_time(market_close)
    iteration = 0
    session_started = False

    while max_iterations is None or iteration < max_iterations:
        now = _coerce_now(current_time(), tz)
        open_at, close_at = _session_bounds(now, opened, closed, tz)
        if now < open_at:
            if _sleep_until(
                open_at,
                now=now,
                task_name=task_name,
                reason="market open",
                sleep_fn=sleep_fn,
                output=output,
            ) == 130:
                return 130
            continue
        if now >= close_at:
            if session_started:
                print(f"[{now.isoformat(timespec='seconds')}] {task_name} market session closed.", file=output)
                return 0
            next_open = _next_market_open(now + timedelta(seconds=1), opened, closed, tz)
            if _sleep_until(
                next_open,
                now=now,
                task_name=task_name,
                reason="next market open",
                sleep_fn=sleep_fn,
                output=output,
            ) == 130:
                return 130
            continue

        session_started = True
        iteration += 1
        started_at = _coerce_now(current_time(), tz).isoformat(timespec="seconds")
        print(f"[{started_at}] Starting {task_name} run {iteration}.", file=output)
        try:
            task()
        except KeyboardInterrupt:
            stopped_at = _coerce_now(current_time(), tz).isoformat(timespec="seconds")
            print(f"[{stopped_at}] Stopping {task_name} loop.", file=output)
            return 130
        except Exception as error:  # pragma: no cover - traceback formatting is environment-specific
            failed_at = _coerce_now(current_time(), tz).isoformat(timespec="seconds")
            print(f"[{failed_at}] {task_name} run {iteration} failed: {error}", file=errors)
            traceback.print_exc(file=errors)
        else:
            completed_at = _coerce_now(current_time(), tz).isoformat(timespec="seconds")
            print(f"[{completed_at}] Completed {task_name} run {iteration}.", file=output)

        if run_once or (max_iterations is not None and iteration >= max_iterations):
            return 0

        now = _coerce_now(current_time(), tz)
        _, close_at = _session_bounds(now, opened, closed, tz)
        if now >= close_at:
            print(f"[{now.isoformat(timespec='seconds')}] {task_name} market session closed.", file=output)
            return 0
        next_poll = min(now + timedelta(seconds=delay), close_at)
        if _sleep_until(
            next_poll,
            now=now,
            task_name=task_name,
            reason="next scan",
            sleep_fn=sleep_fn,
            output=output,
        ) == 130:
            return 130
    return 0


def is_market_session(
    when: datetime,
    *,
    market_open: str,
    market_close: str,
    timezone: str,
) -> bool:
    """Return whether a timestamp falls inside the configured trading session."""

    tz = ZoneInfo(timezone)
    current = _coerce_now(when, tz)
    open_at, close_at = _session_bounds(current, _parse_wall_time(market_open), _parse_wall_time(market_close), tz)
    return open_at <= current < close_at


def _parse_wall_time(value: str) -> wall_time:
    hour_str, minute_str = value.split(":", 1)
    return wall_time(hour=int(hour_str), minute=int(minute_str))


def _coerce_now(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def _session_bounds(
    now: datetime,
    market_open: wall_time,
    market_close: wall_time,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    session_date = now.date()
    open_at = datetime.combine(session_date, market_open, tzinfo=timezone)
    close_at = datetime.combine(session_date, market_close, tzinfo=timezone)
    return open_at, close_at


def _next_market_open(
    now: datetime,
    market_open: wall_time,
    market_close: wall_time,
    timezone: ZoneInfo,
) -> datetime:
    candidate = _coerce_now(now, timezone)
    while True:
        open_at, close_at = _session_bounds(candidate, market_open, market_close, timezone)
        if candidate.weekday() < 5 and candidate < close_at:
            return open_at if candidate < open_at else open_at + timedelta(days=1)
        candidate = datetime.combine(candidate.date() + timedelta(days=1), wall_time(0, 0), tzinfo=timezone)


def _sleep_until(
    target: datetime,
    *,
    now: datetime,
    task_name: str,
    reason: str,
    sleep_fn: Callable[[float], None],
    output: TextIO,
) -> int | None:
    delay = max((target - now).total_seconds(), 0.0)
    print(
        f"[{now.isoformat(timespec='seconds')}] Waiting {delay:g}s for {reason} before next {task_name} run.",
        file=output,
    )
    try:
        sleep_fn(delay)
    except KeyboardInterrupt:
        print(f"[{now.isoformat(timespec='seconds')}] Stopping {task_name} loop.", file=output)
        return 130
    return None
