from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd
import yaml
from .eodhd import EODHDClient, split_adjust_ohlc
from .backtest import Params, backtest_symbol

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--symbols", default="AAPL.US,NVDA.US,TSLA.US,AMD.US,PLTR.US")
    ap.add_argument("--include-delisted", action="store_true")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text())
    client = EODHDClient(cache_dir=cfg["data"]["cache_dir"])
    p = Params(**cfg["strategy"])
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.include_delisted:
        active = client.symbols("US", False)
        dead = client.symbols("US", True)
        symbols = [f"{c}.US" for c in pd.concat([active, dead], ignore_index=True)["Code"].dropna().drop_duplicates().head(args.limit)]
    out = []
    for n, sym in enumerate(symbols, 1):
        print(f"[{n}/{len(symbols)}] {sym}")
        try:
            raw = client.eod(sym, cfg["data"]["start"], cfg["data"].get("end"))
            if raw.empty:
                continue
            sp = client.splits(sym, cfg["data"]["start"], cfg["data"].get("end"))
            adj = split_adjust_ohlc(raw, sp)
            t = backtest_symbol(adj, sym, p)
            if not t.empty:
                out.append(t)
        except Exception as e:
            print(f"WARN {sym}: {e}")
    trades = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    Path("results").mkdir(exist_ok=True)
    trades.to_csv("results/trades.csv", index=False)
    if trades.empty:
        summary = {"trades": 0}
    else:
        r = trades["r_multiple"]
        summary = {
            "trades": int(len(r)), "win_rate": float((r>0).mean()), "mean_R": float(r.mean()),
            "median_R": float(r.median()), "total_R": float(r.sum()),
            "max_R": float(r.max()), "min_R": float(r.min())
        }
    Path("results/summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
