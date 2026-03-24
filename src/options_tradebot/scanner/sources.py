"""Live and public-data helpers for the dynamic scanner."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from io import TextIOWrapper
from pathlib import Path
from time import sleep
from typing import Iterable
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zipfile import ZipFile

import pandas as pd


_BRAPI_BASE_URL = "https://brapi.dev/api/quote/list"
_OPCOES_NET_URL = "https://opcoes.net.br/v1"


@dataclass(frozen=True, slots=True)
class BrapiListedAsset:
    """One asset returned by brapi's quote list endpoint."""

    symbol: str
    name: str | None
    sector: str | None
    asset_type: str | None
    volume: float | None


def fetch_brapi_quote_list(
    *,
    token: str,
    asset_type: str = "stock",
    page: int = 1,
    limit: int = 100,
    search: str | None = None,
    sector: str | None = None,
) -> tuple[list[BrapiListedAsset], dict[str, object]]:
    """Fetch one page of B3-listed assets from brapi."""

    params = {
        "type": asset_type,
        "page": page,
        "limit": limit,
        "token": token,
    }
    if search:
        params["search"] = search
    if sector:
        params["sector"] = sector
    url = f"{_BRAPI_BASE_URL}?{urlencode(params)}"
    payload = _http_json(url)
    assets = [
        BrapiListedAsset(
            symbol=str(item.get("stock", "")),
            name=item.get("name"),
            sector=item.get("sector"),
            asset_type=item.get("type"),
            volume=None if item.get("volume") is None else float(item["volume"]),
        )
        for item in payload.get("stocks", [])
        if item.get("stock")
    ]
    metadata = {
        "currentPage": payload.get("currentPage"),
        "totalPages": payload.get("totalPages"),
        "totalCount": payload.get("totalCount"),
        "hasNextPage": payload.get("hasNextPage", False),
    }
    return assets, metadata


def fetch_all_brapi_tickers(*, token: str, asset_type: str = "stock") -> list[str]:
    """Fetch the full listed-equity universe from brapi."""

    page = 1
    symbols: list[str] = []
    while True:
        assets, metadata = fetch_brapi_quote_list(token=token, asset_type=asset_type, page=page)
        symbols.extend(asset.symbol for asset in assets if asset.symbol)
        if not metadata.get("hasNextPage"):
            break
        page += 1
    return sorted(set(symbols))


def scrape_opcoes_net_optionable_assets(html: str | None = None) -> list[str]:
    """Return tickers exposed on the public Opcoes.Net.Br landing page."""

    if html is None:
        html = _http_text(_OPCOES_NET_URL)
    matches = set(re.findall(r"\b[A-Z]{4}\d{1,2}\b|\b[A-Z]{5}\d{1,2}\b", html))
    return sorted(matches)


def discover_optionable_assets(
    *,
    snapshot_frame: pd.DataFrame | None = None,
    mt5_symbols: Iterable[str] | None = None,
    cotahist_frame: pd.DataFrame | None = None,
    opcoes_net_assets: Iterable[str] | None = None,
    brapi_assets: Iterable[str] | None = None,
) -> list[str]:
    """Combine multiple sources into a single optionable-asset universe."""

    discovered: set[str] = set()
    if snapshot_frame is not None and "underlying" in snapshot_frame:
        discovered.update(snapshot_frame["underlying"].dropna().astype(str))
    if mt5_symbols is not None:
        discovered.update(_extract_mt5_underlyings(mt5_symbols))
    if cotahist_frame is not None and not cotahist_frame.empty:
        options = cotahist_frame.loc[cotahist_frame["tmerc"].isin([70, 80])]
        discovered.update(options["codneg"].astype(str).str[:4].str.strip())
    if opcoes_net_assets is not None:
        discovered.update(str(item).strip().upper() for item in opcoes_net_assets if item)
    if brapi_assets is not None:
        discovered.update(str(item).strip().upper() for item in brapi_assets if item)
    return sorted(item for item in discovered if item)


def load_b3_cotahist(path: str) -> pd.DataFrame:
    """Load a B3 COTAHIST fixed-width file or zipped archive."""

    source = Path(path)
    if source.suffix.lower() == ".zip":
        with ZipFile(source) as archive:
            member = next(
                (
                    name
                    for name in archive.namelist()
                    if name.upper().endswith(".TXT") or name.upper().endswith(".DAT")
                ),
                None,
            )
            if member is None:
                raise ValueError(f"No COTAHIST text file found inside {source}.")
            with archive.open(member) as handle:
                text = TextIOWrapper(handle, encoding="latin-1")
                return _parse_cotahist_lines(text)
    return _parse_cotahist_lines(source.open("r", encoding="latin-1"))


def _parse_cotahist_lines(lines: Iterable[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for raw_line in lines:
        line = raw_line.rstrip("\n")
        if not line.startswith("01") or len(line) < 245:
            continue
        rows.append(
            {
                "trade_date": pd.to_datetime(line[2:10], format="%Y%m%d", errors="coerce"),
                "codbdi": line[10:12].strip(),
                "codneg": line[12:24].strip(),
                "tmerc": int(line[24:27]),
                "nomres": line[27:39].strip(),
                "especi": line[39:49].strip(),
                "preabe": _scaled_int(line[56:69]),
                "premax": _scaled_int(line[69:82]),
                "premin": _scaled_int(line[82:95]),
                "premed": _scaled_int(line[95:108]),
                "preult": _scaled_int(line[108:121]),
                "preofc": _scaled_int(line[121:134]),
                "preofv": _scaled_int(line[134:147]),
                "totneg": int(line[147:152]),
                "quatot": int(line[152:170]),
                "voltot": _scaled_int(line[170:188]),
                "preexe": _scaled_int(line[188:201]),
                "indopc": line[201:202].strip(),
                "datven": pd.to_datetime(line[202:210], format="%Y%m%d", errors="coerce"),
                "fatcot": int(line[210:217]),
                "codisi": line[230:242].strip(),
            }
        )
    return pd.DataFrame(rows)


def _scaled_int(value: str) -> float:
    stripped = value.strip()
    if not stripped:
        return 0.0
    return int(stripped) / 100.0


def _extract_mt5_underlyings(symbols: Iterable[str]) -> set[str]:
    underlyings: set[str] = set()
    for symbol in symbols:
        cleaned = str(symbol).strip().upper()
        if not cleaned:
            continue
        match = re.match(r"([A-Z]{4,5}\d{0,2})", cleaned)
        if match:
            underlyings.add(match.group(1))
    return underlyings


def _http_json(url: str) -> dict[str, object]:
    payload = _http_text(url)
    return json.loads(payload)


def _http_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "options-tradebot/0.1"})
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="ignore")
        except HTTPError as error:
            last_error = error
            if error.code not in {408, 425, 429, 500, 502, 503, 504} or attempt == 2:
                raise
        except URLError as error:
            last_error = error
            if attempt == 2:
                raise
        sleep(1.5 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Unable to fetch {url}")
