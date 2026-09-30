from __future__ import annotations
from dataclasses import dataclass
import pandas as pd
from .indicators import add_indicators, base_features

@dataclass
class Params:
    base_len: int = 15
    momentum_lookback: int = 63
    min_prior_move: float = 0.30
    min_adr: float = 4.0
    max_contraction: float = 0.80
    max_base_depth: float = 0.30
    max_dist_to_pivot: float = 0.08
    min_price: float = 5.0
    min_dollarvol20: float = 5_000_000.0
    partial_day: int = 4
    partial_fraction: float = 0.50
    trail_ma: int = 10
    max_hold_days: int = 60
    slippage_bps: float = 10.0

def setup_ok(x: pd.DataFrame, i: int, p: Params):
    f = base_features(x, i, p.base_len)
    if not f:
        return False, None
    pre = i - p.base_len
    if pre - p.momentum_lookback < 0:
        return False, None
    w = x.iloc[pre-p.momentum_lookback:pre+1]
    prior_move = float(w["high"].max()/w["low"].min()-1.0)
    row = x.iloc[i]
    checks = [
        prior_move >= p.min_prior_move,
        row["close"] >= p.min_price,
        row["adr20"] >= p.min_adr,
        row["dollarvol20"] >= p.min_dollarvol20,
        row["sma10"] > row["sma20"],
        row["sma10"] > x.iloc[i-5]["sma10"],
        row["sma20"] > x.iloc[i-5]["sma20"],
        f["contraction"] <= p.max_contraction,
        f["base_depth"] <= p.max_base_depth,
        f["low_slope"] >= 0,
        (f["pivot"] / row["close"] - 1.0) <= p.max_dist_to_pivot,
    ]
    f["prior_move"] = prior_move
    return bool(all(checks)), f

def backtest_symbol(df: pd.DataFrame, symbol: str, p: Params) -> pd.DataFrame:
    x = add_indicators(df).reset_index(drop=True)
    trades = []
    i = max(140, p.base_len + p.momentum_lookback + 5)
    while i < len(x)-1:
        ok, f = setup_ok(x, i, p)
        if not ok:
            i += 1
            continue
        pivot = f["pivot"]
        j = i + 1
        if x.iloc[j]["high"] < pivot:
            i += 1
            continue
        entry = max(float(x.iloc[j]["open"]), pivot) * (1 + p.slippage_bps/10000)
        # Daily approximation: true Qullamaggie low-of-day stop requires intraday sequencing.
        adr_frac = float(x.iloc[i]["adr20"]) / 100.0
        stop = max(float(x.iloc[i]["low"]), entry*(1-adr_frac))
        if stop >= entry:
            i += 1
            continue
        initial_stop = stop
        risk = entry - initial_stop
        remaining = 1.0
        realized = 0.0
        partial_done = False
        exit_i = j
        reason = "max_hold"
        k = j
        while k < min(len(x), j+p.max_hold_days):
            r = x.iloc[k]
            if float(r["low"]) <= stop:
                fill = float(r["open"]) if float(r["open"]) < stop else stop
                realized += remaining*(fill-entry)
                remaining = 0
                exit_i = k
                reason = "stop"
                break
            held = k-j+1
            if (not partial_done) and held >= p.partial_day:
                frac = min(p.partial_fraction, remaining)
                realized += frac*(float(r["close"])-entry)
                remaining -= frac
                partial_done = True
                stop = max(stop, entry)
            ma_col = f"sma{p.trail_ma}"
            if partial_done and pd.notna(r[ma_col]) and float(r["close"]) < float(r[ma_col]):
                realized += remaining*(float(r["close"])-entry)
                remaining = 0
                exit_i = k
                reason = f"close_below_sma{p.trail_ma}"
                break
            exit_i = k
            k += 1
        if remaining > 0:
            realized += remaining*(float(x.iloc[exit_i]["close"])-entry)
        trades.append({
            "symbol":symbol, "setup_date":x.iloc[i]["date"], "entry_date":x.iloc[j]["date"],
            "exit_date":x.iloc[exit_i]["date"], "entry":entry, "initial_stop":initial_stop,
            "pivot":pivot, "adr20":x.iloc[i]["adr20"], "prior_move":f["prior_move"],
            "contraction":f["contraction"], "base_depth":f["base_depth"],
            "pnl_per_share":realized, "r_multiple":realized/risk,
            "hold_days":exit_i-j+1, "exit_reason":reason
        })
        i = exit_i + 1
    return pd.DataFrame(trades)
