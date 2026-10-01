from __future__ import annotations
import os
import time
from pathlib import Path
import pandas as pd
import requests

BASE = "https://eodhd.com/api"

class EODHDClient:
    def __init__(self, token: str | None = None, cache_dir: str = "data/cache", timeout: int = 45):
        self.token = token or os.getenv("EODHD_API_TOKEN")
        if not self.token:
            raise RuntimeError("EODHD_API_TOKEN is not set")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.s = requests.Session()

    def _get(self, path: str, params: dict | None = None, retries: int = 5):
        p = dict(params or {})
        p["api_token"] = self.token
        url = f"{BASE}/{path.lstrip('/')}"
        for attempt in range(retries):
            r = self.s.get(url, params=p, timeout=self.timeout)
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "2"))
                time.sleep(max(wait, 1))
                continue
            if 500 <= r.status_code < 600 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"EODHD request failed after {retries} attempts: {path}")

    def symbols(self, exchange="US", delisted=False, common_only=True) -> pd.DataFrame:
        params = {"fmt": "json", "delisted": 1 if delisted else 0}
        if common_only:
            params["type"] = "common_stock"
        return pd.DataFrame(self._get(f"exchange-symbol-list/{exchange}", params))

    def eod(self, symbol: str, start: str, end: str | None = None, refresh=False) -> pd.DataFrame:
        safe = symbol.replace("/", "_")
        fp = self.cache_dir / f"{safe}_{start}_{end or 'latest'}.csv"
        if fp.exists() and not refresh:
            return pd.read_csv(fp, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
        params = {"fmt":"json", "period":"d", "order":"a", "from":start}
        if end:
            params["to"] = end
        df = pd.DataFrame(self._get(f"eod/{symbol}", params))
        if df.empty:
            return df
        df["date"] = pd.to_datetime(df["date"])
        for c in ["open","high","low","close","adjusted_close","volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["date","open","high","low","close","volume"])
        df.to_csv(fp, index=False)
        return df.sort_values("date").reset_index(drop=True)

    def splits(self, symbol: str, start: str | None = None, end: str | None = None) -> pd.DataFrame:
        params = {"fmt":"json"}
        if start:
            params["from"] = start
        if end:
            params["to"] = end
        df = pd.DataFrame(self._get(f"splits/{symbol}", params))
        if df.empty:
            return pd.DataFrame(columns=["date","split"])
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)

    def intraday_day(self, symbol: str, day: str, interval: str = "1m", refresh: bool = False) -> pd.DataFrame:
        """Fetch one US trading day of raw intraday OHLCV bars.

        EODHD intraday prices are raw/unadjusted. The execution layer applies
        the candidate day's split factor so they share the same scale as the
        split-adjusted daily research data.
        """
        from datetime import datetime, timedelta, timezone
        from zoneinfo import ZoneInfo

        safe = symbol.replace("/", "_")
        fp = self.cache_dir / "intraday" / f"{safe}_{day}_{interval}.csv"
        fp.parent.mkdir(parents=True, exist_ok=True)
        if fp.exists() and not refresh:
            return pd.read_csv(fp, parse_dates=["datetime"])

        d = pd.Timestamp(day).date()
        ny = ZoneInfo("America/New_York")
        start_local = datetime(d.year, d.month, d.day, 4, 0, tzinfo=ny)
        end_local = datetime(d.year, d.month, d.day, 20, 0, tzinfo=ny)
        params = {
            "fmt": "json",
            "interval": interval,
            "from": int(start_local.astimezone(timezone.utc).timestamp()),
            "to": int(end_local.astimezone(timezone.utc).timestamp()),
        }
        raw = self._get(f"intraday/{symbol}", params)
        df = pd.DataFrame(raw)
        if df.empty:
            return df
        if "datetime" in df:
            dt = pd.to_datetime(df["datetime"], utc=True, errors="coerce")
        elif "timestamp" in df:
            dt = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="s", utc=True)
        else:
            raise ValueError("Intraday response has no datetime/timestamp field")
        df["datetime"] = dt.dt.tz_convert("America/New_York")
        for c in ["open","high","low","close","volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["datetime","open","high","low","close"]).sort_values("datetime")
        df.to_csv(fp, index=False)
        return df.reset_index(drop=True)

def _infer_split_factor(df: pd.DataFrame, threshold: float = 0.20) -> pd.Series:
    """Infer large split-like adjustment steps from adjusted_close/close.

    Dividend adjustments normally move this ratio only slightly. We only use
    large discontinuities as a fallback when an old/delisted symbol has no
    corporate-action records.
    """
    ratio = (df["adjusted_close"] / df["close"]).replace([float("inf"), -float("inf")], pd.NA)
    ratio = ratio.ffill().bfill()
    factor = pd.Series(1.0, index=df.index, dtype=float)
    if ratio.isna().all():
        return factor
    changes = ratio / ratio.shift(1)
    for i in range(1, len(df)):
        ch = changes.iloc[i]
        if pd.notna(ch) and abs(float(ch) - 1.0) >= threshold:
            step = float(ratio.iloc[i-1] / ratio.iloc[i])
            if step > 0:
                factor.loc[:i-1] *= step
    return factor

def split_adjust_ohlc(df: pd.DataFrame, splits: pd.DataFrame | None) -> pd.DataFrame:
    """Back-adjust raw OHLC for splits only; keep EODHD split-adjusted volume.

    If no split records are available, infer only large split-like steps from
    adjusted_close/close. This fallback matters for some older delisted names.
    """
    out = df.copy().sort_values("date").reset_index(drop=True)
    factor = pd.Series(1.0, index=out.index, dtype=float)
    source = "events"
    if splits is not None and not splits.empty:
        for _, ev in splits.iterrows():
            try:
                new, old = (float(x) for x in str(ev["split"]).split("/"))
                ratio = new / old
                if ratio <= 0:
                    continue
            except Exception:
                continue
            factor.loc[out["date"] < pd.Timestamp(ev["date"])] /= ratio
    else:
        factor = _infer_split_factor(out)
        source = "inferred"
    for c in ["open","high","low","close"]:
        out[c] = out[c].astype(float) * factor
    out["split_factor"] = factor
    out["adjustment_source"] = source
    return out
