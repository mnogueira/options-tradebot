"""Periodic scanner loop helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd

from options_tradebot.config import AppSettings, default_settings
from options_tradebot.data import load_snapshot_csv, snapshots_from_frame
from options_tradebot.scanner.engine import MispricingScanner, ScanResult


@dataclass(frozen=True, slots=True)
class PeriodicScanRun:
    """One persisted scan-loop iteration."""

    scanned_at: str
    rankings_path: str
    highlights_path: str


def run_csv_scan_loop(
    *,
    snapshots_path: str,
    output_dir: str,
    settings: AppSettings | None = None,
    interval_minutes: int | None = None,
    max_runs: int | None = None,
    events_path: str | None = None,
    timezone_name: str = "America/Sao_Paulo",
) -> list[PeriodicScanRun]:
    """Run the scanner on a CSV file every N minutes during B3 cash hours."""

    app_settings = settings or default_settings()
    scanner = MispricingScanner(app_settings)
    zone = ZoneInfo(timezone_name)
    sleep_seconds = max((interval_minutes or app_settings.scanner.refresh_minutes) * 60, 1)
    runs: list[PeriodicScanRun] = []
    completed = 0
    while True:
        now = datetime.now(zone)
        if _is_market_open(now, app_settings):
            frame = load_snapshot_csv(snapshots_path)
            snapshots = snapshots_from_frame(frame)
            event_calendar = None
            if events_path:
                event_calendar = pd.read_csv(events_path)
            result = scanner.scan(snapshots, event_calendar=event_calendar)
            run = persist_scan_result(result, output_dir=output_dir, prefix=now.strftime("%Y%m%d_%H%M%S"))
            runs.append(run)
            completed += 1
            if max_runs is not None and completed >= max_runs:
                break
        sleep(sleep_seconds)
    return runs


def persist_scan_result(
    result: ScanResult,
    *,
    output_dir: str,
    prefix: str | None = None,
) -> PeriodicScanRun:
    """Persist the scanner ranking and highlighted options to disk."""

    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    token = prefix or result.as_of.isoformat()
    rankings_path = directory / f"{token}_scanner_rankings.csv"
    highlights_path = directory / f"{token}_scanner_highlights.csv"
    summary_path = directory / f"{token}_scanner_summary.json"
    rankings = result.to_frame()
    highlights = result.highlights_frame()
    rankings.to_csv(rankings_path, index=False)
    highlights.to_csv(highlights_path, index=False)
    payload = {
        "as_of": result.as_of.isoformat(),
        "candidate_count": len(result.candidates),
        "top_underlyings": [candidate.underlying for candidate in result.top_candidates()],
    }
    summary_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return PeriodicScanRun(
        scanned_at=token,
        rankings_path=str(rankings_path),
        highlights_path=str(highlights_path),
    )


def _is_market_open(now: datetime, settings: AppSettings) -> bool:
    current = now.strftime("%H:%M")
    return settings.scanner.market_open <= current <= settings.scanner.market_close
