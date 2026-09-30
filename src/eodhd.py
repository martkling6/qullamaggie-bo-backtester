from __future__ import annotations
import os
from pathlib import Path
import pandas as pd
import requests

BASE = "https://eodhd.com/api"

class EODHDClient:
    def __init__(self, token: str | None = None, cache_dir: str = "data/cache", timeout: int = 30):
        self.token = token or os.getenv("EODHD_API_TOKEN")
        if not self.token:
            raise RuntimeError("EODHD_API_TOKEN is not set")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.s = requests.Session()

    def _get(self, path: str, params: dict | None = None):
        p = dict(params or {})
        p["api_token"] = self.token
        r = self.s.get(f"{BASE}/{path.lstrip('/')}", params=p, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

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

def split_adjust_ohlc(df: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """Back-adjust raw OHLC for splits only. EODHD volume is already split-adjusted."""
    out = df.copy().sort_values("date").reset_index(drop=True)
    factor = pd.Series(1.0, index=out.index)
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
    for c in ["open","high","low","close"]:
        out[c] = out[c].astype(float) * factor
    out["split_factor"] = factor
    return out
