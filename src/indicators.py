from __future__ import annotations
import numpy as np
import pandas as pd

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    for n in (10,20,50):
        x[f"sma{n}"] = x["close"].rolling(n).mean()
    x["range_pct"] = (x["high"] / x["low"] - 1.0) * 100.0
    x["adr20"] = x["range_pct"].rolling(20).mean()
    x["avgvol20"] = x["volume"].rolling(20).mean()
    x["dollarvol20"] = (x["close"] * x["volume"]).rolling(20).mean()
    for n in (21,63,126):
        x[f"perf{n}"] = x["close"] / x["close"].shift(n) - 1.0
    return x

def base_features(x: pd.DataFrame, end_i: int, base_len: int) -> dict | None:
    start = end_i - base_len + 1
    if start < 1:
        return None
    b = x.iloc[start:end_i+1]
    if len(b) < base_len:
        return None
    pivot = float(b["high"].max())
    floor = float(b["low"].min())
    early = b.iloc[:max(3, base_len//2)]
    late = b.iloc[-max(3, base_len//3):]
    early_range = float(early["range_pct"].mean())
    late_range = float(late["range_pct"].mean())
    contraction = late_range / early_range if early_range > 0 else np.nan
    lows = b["low"].to_numpy(float)
    slope = np.polyfit(np.arange(len(lows)), lows, 1)[0] / max(np.mean(lows), 1e-9)
    return {"pivot":pivot, "base_floor":floor, "contraction":contraction, "low_slope":slope,
            "base_depth": pivot/floor-1.0 if floor > 0 else np.nan}
