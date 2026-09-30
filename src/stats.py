from __future__ import annotations
import math
import pandas as pd

def summarize(trades: pd.DataFrame) -> dict:
    if trades is None or trades.empty:
        return {"trades": 0}
    r = pd.to_numeric(trades["r_multiple"], errors="coerce").dropna()
    if r.empty:
        return {"trades": 0}
    pos = r[r > 0]
    neg = r[r < 0]
    gross_win = float(pos.sum())
    gross_loss = float(-neg.sum())
    ordered = r.sort_values(ascending=False)
    top1n = max(1, math.ceil(len(r)*0.01))
    top5n = max(1, math.ceil(len(r)*0.05))
    total = float(r.sum())
    return {
        "trades": int(len(r)),
        "win_rate": float((r > 0).mean()),
        "mean_R": float(r.mean()),
        "median_R": float(r.median()),
        "total_R": total,
        "profit_factor_R": gross_win/gross_loss if gross_loss > 0 else None,
        "avg_winner_R": float(pos.mean()) if len(pos) else None,
        "avg_loser_R": float(neg.mean()) if len(neg) else None,
        "max_R": float(r.max()),
        "min_R": float(r.min()),
        "top_1pct_R": float(ordered.iloc[:top1n].sum()),
        "top_5pct_R": float(ordered.iloc[:top5n].sum()),
        "top_1pct_share_of_total": float(ordered.iloc[:top1n].sum()/total) if total != 0 else None,
        "symbols": int(trades["symbol"].nunique()) if "symbol" in trades else None,
    }

def split_summary(trades: pd.DataFrame, oos_start: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame([{"sample":"all","trades":0}])
    x = trades.copy()
    x["entry_date"] = pd.to_datetime(x["entry_date"])
    cut = pd.Timestamp(oos_start)
    rows = []
    for name, frame in [
        ("all", x),
        ("in_sample", x[x["entry_date"] < cut]),
        ("out_of_sample", x[x["entry_date"] >= cut]),
    ]:
        row = {"sample":name}
        row.update(summarize(frame))
        rows.append(row)
    return pd.DataFrame(rows)

def yearly_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    x = trades.copy()
    x["year"] = pd.to_datetime(x["entry_date"]).dt.year
    rows=[]
    for year,g in x.groupby("year"):
        row={"year":int(year)}
        row.update(summarize(g))
        rows.append(row)
    return pd.DataFrame(rows)
