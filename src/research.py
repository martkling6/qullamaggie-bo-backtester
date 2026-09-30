from __future__ import annotations
import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import pandas as pd
import yaml

from .backtest import Params, backtest_symbol
from .capital import simulate_risk_sized_account
from .eodhd import EODHDClient, split_adjust_ohlc
from .indicators import add_indicators
from .stats import split_summary, summarize, yearly_summary

def stable_sample(df: pd.DataFrame, n: int, seed: int, status: str) -> pd.DataFrame:
    x = df.copy()
    x = x[x["Code"].notna()].drop_duplicates("Code")
    x["_rank"] = x["Code"].astype(str).map(
        lambda s: hashlib.sha256(f"{seed}:{status}:{s}".encode()).hexdigest()
    )
    x = x.sort_values("_rank").head(n).drop(columns="_rank")
    x["universe_status"] = status
    return x

def _listed_us(df: pd.DataFrame) -> pd.DataFrame:
    """Keep primary US listed venues; exclude OTC/Pink/foreign OTC lines."""
    allowed = {"NASDAQ", "NYSE", "AMEX", "NYSE MKT"}
    x = df.copy()
    if "Exchange" in x:
        x = x[x["Exchange"].astype(str).str.upper().isin(allowed)]
    return x

def build_universe(client: EODHDClient, sample_size: int, seed: int, include_delisted: bool) -> pd.DataFrame:
    active = _listed_us(client.symbols("US", delisted=False, common_only=True))
    if not include_delisted:
        u = stable_sample(active, sample_size, seed, "active")
    else:
        dead = _listed_us(client.symbols("US", delisted=True, common_only=True))
        n_dead = sample_size // 2
        n_active = sample_size - n_dead
        u = pd.concat([
            stable_sample(active, n_active, seed, "active"),
            stable_sample(dead, n_dead, seed, "delisted")
        ], ignore_index=True)
    u["symbol"] = u["Code"].astype(str) + ".US"
    return u

def load_one(symbol: str, cfg: dict):
    client = EODHDClient(cache_dir=cfg["data"]["cache_dir"])
    raw = client.eod(symbol, cfg["data"]["start"], cfg["data"].get("end"))
    if raw.empty or len(raw) < 160:
        return symbol, None, "insufficient_history"
    try:
        splits = client.splits(symbol, cfg["data"]["start"], cfg["data"].get("end"))
    except Exception:
        splits = pd.DataFrame(columns=["date","split"])
    adj = split_adjust_ohlc(raw, splits)
    return symbol, add_indicators(adj), None

def sensitivity_params(base: Params):
    """One-factor-at-a-time robustness checks, not a best-parameter optimizer."""
    seen=set()
    def emit(name, p):
        key=repr(p)
        if key not in seen:
            seen.add(key)
            return (name,p)
    candidates=[("baseline",base)]
    for v in [10,15,20,30,40]:
        candidates.append((f"base_len={v}", replace(base, base_len=v)))
    for v in [42,63]:
        candidates.append((f"momentum_days={v}", replace(base, momentum_lookback=v)))
    for v in [0.30,0.50,0.70,1.00]:
        candidates.append((f"prior_move={v}", replace(base, min_prior_move=v)))
    for v in [3.0,4.0,5.0,6.0]:
        candidates.append((f"adr={v}", replace(base, min_adr=v)))
    for v in [0.65,0.80,0.95]:
        candidates.append((f"contraction={v}", replace(base, max_contraction=v)))
    for v in [10,20]:
        candidates.append((f"trail_sma={v}", replace(base, trail_ma=v)))
    for v in [3,4,5]:
        candidates.append((f"partial_day={v}", replace(base, partial_day=v)))
    for v in [1/3,0.50]:
        candidates.append((f"partial_fraction={v:.3f}", replace(base, partial_fraction=v)))
    candidates.append(("entry_day_stop=ignore", replace(base, entry_day_stop_mode="ignore")))
    out=[]
    for name,p in candidates:
        item=emit(name,p)
        if item:
            out.append(item)
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--sample-size", type=int, default=200)
    ap.add_argument("--include-delisted", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--sensitivity", action="store_true")
    args=ap.parse_args()

    cfg=yaml.safe_load(Path(args.config).read_text())
    base=Params(**cfg["strategy"])
    seed=int(cfg.get("research",{}).get("seed",42))
    oos=cfg.get("research",{}).get("out_of_sample_start","2021-01-01")
    client=EODHDClient(cache_dir=cfg["data"]["cache_dir"])
    universe=build_universe(client,args.sample_size,seed,args.include_delisted)

    Path("results").mkdir(exist_ok=True)
    universe.to_csv("results/universe.csv",index=False)

    prepared={}
    errors=[]
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as ex:
        futs={ex.submit(load_one,s,cfg):s for s in universe["symbol"]}
        for n,f in enumerate(as_completed(futs),1):
            sym=futs[f]
            try:
                symbol,df,err=f.result()
                if df is not None:
                    prepared[symbol]=df
                else:
                    errors.append({"symbol":symbol,"error":err})
            except Exception as e:
                errors.append({"symbol":sym,"error":str(e)})
            if n % 25 == 0 or n == len(futs):
                print(f"Downloaded {n}/{len(futs)}; usable={len(prepared)} errors={len(errors)}")

    pd.DataFrame(errors).to_csv("results/data_errors.csv",index=False)

    all_trades=[]
    for n,(sym,df) in enumerate(prepared.items(),1):
        t=backtest_symbol(df,sym,base,prepared=True)
        if not t.empty:
            status=universe.loc[universe["symbol"]==sym,"universe_status"].iloc[0]
            t["universe_status"]=status
            all_trades.append(t)
        if n % 25 == 0 or n == len(prepared):
            print(f"Baseline backtest {n}/{len(prepared)}")

    trades=pd.concat(all_trades,ignore_index=True) if all_trades else pd.DataFrame()
    trades.to_csv("results/trades.csv",index=False)
    summary=summarize(trades)
    summary.update({
        "requested_symbols":int(len(universe)),
        "usable_symbols":int(len(prepared)),
        "data_errors":int(len(errors)),
        "include_delisted":bool(args.include_delisted),
        "oos_start":oos
    })
    Path("results/summary.json").write_text(json.dumps(summary,indent=2))
    pcfg=cfg.get("portfolio",{})
    sized,account_summary=simulate_risk_sized_account(
        trades,
        initial_capital=float(pcfg.get("initial_capital",10000.0)),
        risk_per_trade=float(pcfg.get("risk_per_trade",0.005)),
        max_position_pct=float(pcfg.get("max_position_pct",0.30)),
    )
    sized.to_csv("results/trades_risk_sized.csv",index=False)
    Path("results/account_summary.json").write_text(json.dumps(account_summary,indent=2))
    split_summary(trades,oos).to_csv("results/sample_summary.csv",index=False)
    yearly_summary(trades).to_csv("results/yearly_summary.csv",index=False)
    if not trades.empty and "universe_status" in trades:
        status_rows=[]
        for status,g in trades.groupby("universe_status"):
            row={"universe_status":status}
            row.update(summarize(g))
            status_rows.append(row)
        pd.DataFrame(status_rows).to_csv("results/universe_status_summary.csv",index=False)

    if args.sensitivity:
        rows=[]
        for label,p in sensitivity_params(base):
            chunks=[]
            for sym,df in prepared.items():
                t=backtest_symbol(df,sym,p,prepared=True)
                if not t.empty:
                    chunks.append(t)
            z=pd.concat(chunks,ignore_index=True) if chunks else pd.DataFrame()
            row={"variant":label}
            row.update(summarize(z))
            if not z.empty:
                z["entry_date"]=pd.to_datetime(z["entry_date"])
                o=z[z["entry_date"]>=pd.Timestamp(oos)]
                osum=summarize(o)
                row["oos_trades"]=osum.get("trades",0)
                row["oos_mean_R"]=osum.get("mean_R")
                row["oos_profit_factor_R"]=osum.get("profit_factor_R")
            rows.append(row)
            print(label,row.get("trades",0),row.get("mean_R"))
        pd.DataFrame(rows).to_csv("results/sensitivity.csv",index=False)

    print(json.dumps(summary,indent=2))

if __name__=="__main__":
    main()
