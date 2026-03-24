"""Small brapi.dev client for dynamic B3 universe discovery."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


@dataclass(frozen=True, slots=True)
class BrapiClientConfig:
    """HTTP settings for brapi."""

    token: str | None = None
    base_url: str = "https://brapi.dev"
    timeout_seconds: int = 30


class BrapiClient:
    """Minimal wrapper around the public brapi quote endpoints."""

    def __init__(self, config: BrapiClientConfig):
        self.config = config

    def list_assets(
        self,
        *,
        page: int = 1,
        limit: int = 100,
        search: str | None = None,
        asset_type: str | None = None,
        sector: str | None = None,
        sort_by: str = "volume",
        sort_order: str = "desc",
    ) -> dict[str, Any]:
        """Return one page from `/api/quote/list`."""

        params: dict[str, object] = {
            "page": page,
            "limit": limit,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        if search:
            params["search"] = search
        if asset_type:
            params["type"] = asset_type
        if sector:
            params["sector"] = sector
        return self._request_json("/api/quote/list", params=params)

    def list_all_assets(
        self,
        *,
        limit: int = 100,
        max_pages: int = 10,
        asset_type: str | None = None,
    ) -> pd.DataFrame:
        """Page through the B3 asset list and return a DataFrame."""

        rows: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            payload = self.list_assets(page=page, limit=limit, asset_type=asset_type)
            rows.extend(payload.get("stocks", []))
            if not payload.get("hasNextPage"):
                break
        return pd.DataFrame(rows)

    def quote_history(
        self,
        symbol: str,
        *,
        range_: str = "6mo",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """Fetch daily history for one B3 ticker."""

        payload = self._request_json(
            f"/api/quote/{symbol}",
            params={"range": range_, "interval": interval},
        )
        results = payload.get("results", [])
        if not results:
            return pd.DataFrame()
        history = results[0].get("historicalDataPrice", [])
        frame = pd.DataFrame(history)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"], unit="s", utc=True).dt.tz_localize(None)
        return frame

    def _request_json(self, path: str, *, params: dict[str, object] | None = None) -> dict[str, Any]:
        query = dict(params or {})
        if self.config.token and "token" not in query:
            query["token"] = self.config.token
        encoded = urlencode(query)
        url = f"{self.config.base_url}{path}"
        if encoded:
            url = f"{url}?{encoded}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "options-tradebot/0.1.0",
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        request = Request(url, headers=headers)
        with urlopen(request, timeout=self.config.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
