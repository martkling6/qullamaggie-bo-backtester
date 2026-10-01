from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

@dataclass(frozen=True)
class IntradayExecution:
    opening_range_minutes: int = 5
    slippage_bps: float = 10.0
    max_stop_adr_multiple: float = 1.0

def regular_session(bars: pd.DataFrame) -> pd.DataFrame:
    if bars is None or bars.empty:
        return pd.DataFrame()
    x=bars.copy()
    dt=pd.to_datetime(x["datetime"], utc=True, errors="coerce")
    x["datetime"]=dt.dt.tz_convert("America/New_York")
    t=x["datetime"].dt.time
    return x[(t >= pd.Timestamp("09:30").time()) & (t < pd.Timestamp("16:00").time())].reset_index(drop=True)

def execute_orh(
    bars: pd.DataFrame,
    pivot: float,
    adr_pct: float,
    split_factor: float = 1.0,
    cfg: IntradayExecution = IntradayExecution(),
) -> dict | None:
    """Execute a Qullamaggie-style ORH entry without future-low lookahead.

    ORH is formed from the first N regular-session minutes. Entry requires a
    later 1-minute bar to trade through both ORH and the daily breakout pivot.
    The initial stop is the session low already OBSERVED before the trigger
    bar. If that stop is wider than 1x ADR from entry, the trade is rejected.

    This is deliberately stricter than using the completed day's final low:
    the latter would leak future information into position sizing.
    """
    x=regular_session(bars)
    if x.empty or len(x) <= cfg.opening_range_minutes:
        return None
    sf=float(split_factor) if pd.notna(split_factor) and float(split_factor)>0 else 1.0
    for c in ["open","high","low","close"]:
        x[c]=x[c].astype(float)*sf

    n=int(cfg.opening_range_minutes)
    opening=x.iloc[:n]
    orh=float(opening["high"].max())
    trigger=max(orh,float(pivot))
    prior_low=float(opening["low"].min())

    for k in range(n,len(x)):
        r=x.iloc[k]
        if float(r["high"]) < trigger:
            prior_low=min(prior_low,float(r["low"]))
            continue

        entry=max(float(r["open"]),trigger)*(1.0+cfg.slippage_bps/10000.0)
        initial_stop=prior_low
        if initial_stop >= entry:
            return None
        stop_width=(entry-initial_stop)/entry
        adr_frac=float(adr_pct)/100.0
        if adr_frac <= 0 or stop_width > adr_frac*cfg.max_stop_adr_multiple:
            return None

        # Trigger-bar OHLC ordering is unknown even with 1m bars. We do not
        # use its low to set the stop. After the trigger, every later bar can
        # unambiguously stop the position; a gap/open below stop fills at open.
        stopped=False
        stop_fill=None
        stop_time=None
        for m in range(k+1,len(x)):
            q=x.iloc[m]
            if float(q["low"]) <= initial_stop:
                stop_fill=float(q["open"]) if float(q["open"]) < initial_stop else initial_stop
                stop_time=q["datetime"]
                stopped=True
                break
        return {
            "entry":entry,
            "entry_time":r["datetime"],
            "initial_stop":initial_stop,
            "initial_risk_per_share":entry-initial_stop,
            "initial_stop_pct":stop_width,
            "orh":orh,
            "opening_range_minutes":n,
            "stopped_entry_day":stopped,
            "entry_day_stop_fill":stop_fill,
            "entry_day_stop_time":stop_time,
        }
    return None
