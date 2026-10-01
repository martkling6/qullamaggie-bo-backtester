from __future__ import annotations
import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import duckdb
import pandas as pd
import yaml

from .backtest import Params, setup_ok
from .capital import simulate_risk_sized_account
from .eodhd import EODHDClient, split_adjust_ohlc
from .intraday import IntradayExecution, execute_orh
from .research import _listed_us, load_one
from .indicators import add_indicators
from .stats import summarize, split_summary, yearly_summary

def load_one_fast(symbol: str, cfg: dict):
    """One-call universe loader.

    Full-universe discovery uses EOD only and infers large split steps from
    adjusted_close/close. Exact split-event calls are deferred until a symbol
    actually becomes a top-2% candidate, which keeps a full historical run
    within the API budget.
    """
    client=EODHDClient(cache_dir=cfg["data"]["cache_dir"])
    raw=client.eod(symbol,cfg["data"]["start"],cfg["data"].get("end"))
    if raw.empty or len(raw)<160:
        return symbol,None,"insufficient_history"
    adj=split_adjust_ohlc(raw,None)
    return symbol,add_indicators(adj),None

def full_universe(client: EODHDClient, include_delisted: bool = True) -> pd.DataFrame:
    active=_listed_us(client.symbols("US",delisted=False,common_only=True)).copy()
    active["universe_status"]="active"
    parts=[active]
    if include_delisted:
        dead=_listed_us(client.symbols("US",delisted=True,common_only=True)).copy()
        dead["universe_status"]="delisted"
        parts.append(dead)
    u=pd.concat(parts,ignore_index=True)
    u=u[u["Code"].notna()].copy()
    u["symbol"]=u["Code"].astype(str)+".US"
    # EODHD normally disambiguates reused historical tickers with _old.
    u=u.drop_duplicates(["symbol","universe_status"]).reset_index(drop=True)
    return u

def pre_candidates(df: pd.DataFrame, symbol: str, p: Params) -> pd.DataFrame:
    """Find chart/setup candidates without using the cross-sectional leader rank."""
    q=replace(p,require_leader_scan=False)
    rows=[]
    i=max(140,q.base_len+q.momentum_lookback+5)
    while i < len(df)-1:
        ok,f=setup_ok(df,i,q)
        if ok:
            j=i+1
            pivot=float(f["pivot"])
            if float(df.iloc[j]["high"]) >= pivot:
                rows.append({
                    "symbol":symbol,
                    "setup_date":pd.Timestamp(df.iloc[i]["date"]),
                    "breakout_date":pd.Timestamp(df.iloc[j]["date"]),
                    "pivot":pivot,
                    "adr20":float(df.iloc[i]["adr20"]),
                    "prior_move":float(f["prior_move"]),
                    "contraction":float(f["contraction"]),
                    "base_depth":float(f["base_depth"]),
                    "split_factor":float(df.iloc[j].get("split_factor",1.0)),
                })
        i+=1
    return pd.DataFrame(rows)

def manage_after_entry(
    daily: pd.DataFrame,
    breakout_date,
    entry: float,
    initial_stop: float,
    partial_day: int,
    partial_fraction: float,
    trail_ma: int,
    max_hold_days: int,
    entry_day_stop_fill: float | None = None,
) -> dict:
    risk=entry-initial_stop
    if risk <= 0:
        raise ValueError("initial stop must be below entry")
    if entry_day_stop_fill is not None and pd.notna(entry_day_stop_fill):
        pnl=float(entry_day_stop_fill)-entry
        return {"pnl_per_share":pnl,"r_multiple":pnl/risk,
                "exit_date":pd.Timestamp(breakout_date),"hold_days":1,
                "exit_reason":"intraday_initial_stop"}

    x=daily.reset_index(drop=True)
    hits=x.index[pd.to_datetime(x["date"]).dt.normalize()==pd.Timestamp(breakout_date).normalize()]
    if len(hits)==0:
        raise ValueError("breakout date missing from daily data")
    j=int(hits[0])
    stop=float(initial_stop)
    remaining=1.0
    realized=0.0
    partial_done=False
    exit_i=j
    reason="max_hold"

    # Entry day has already been sequenced minute-by-minute. Start at next day.
    for k in range(j+1,min(len(x),j+max_hold_days)):
        r=x.iloc[k]
        if float(r["low"]) <= stop:
            fill=float(r["open"]) if float(r["open"]) < stop else stop
            realized += remaining*(fill-entry)
            remaining=0.0
            exit_i=k
            reason="stop"
            break

        held=k-j+1
        if (not partial_done) and held >= partial_day:
            frac=min(float(partial_fraction),remaining)
            realized += frac*(float(r["close"])-entry)
            remaining -= frac
            partial_done=True
            # Published rule: after the partial sale, remaining shares go to
            # the ORIGINAL ENTRY price (breakeven for the remaining tranche).
            stop=max(stop,entry)

        ma_col=f"sma{trail_ma}"
        if partial_done and pd.notna(r[ma_col]) and float(r["close"]) < float(r[ma_col]):
            realized += remaining*(float(r["close"])-entry)
            remaining=0.0
            exit_i=k
            reason=f"close_below_sma{trail_ma}"
            break
        exit_i=k

    if remaining>0:
        realized += remaining*(float(x.iloc[exit_i]["close"])-entry)
    return {
        "pnl_per_share":realized,
        "r_multiple":realized/risk,
        "exit_date":pd.Timestamp(x.iloc[exit_i]["date"]),
        "hold_days":exit_i-j+1,
        "exit_reason":reason,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--config",default="config.yaml")
    ap.add_argument("--max-symbols",type=int,default=0,
                    help="0 = full eligible US common-stock universe")
    ap.add_argument("--workers",type=int,default=12)
    ap.add_argument("--include-delisted",action="store_true")
    ap.add_argument("--opening-range",type=int,default=None,choices=[1,5,30])
    args=ap.parse_args()

    cfg=yaml.safe_load(Path(args.config).read_text())
    p=Params(**cfg["strategy"])
    icfg=cfg.get("intraday",{})
    opening_range=int(args.opening_range or icfg.get("opening_range_minutes",5))
    ex_cfg=IntradayExecution(
        opening_range_minutes=opening_range,
        slippage_bps=float(p.slippage_bps),
        max_stop_adr_multiple=float(icfg.get("max_stop_adr_multiple",1.0)),
    )

    Path("results").mkdir(exist_ok=True)
    db_path=Path("results/research.duckdb")
    if db_path.exists():
        db_path.unlink()
    con=duckdb.connect(str(db_path))
    con.execute("CREATE TABLE daily_rank(date DATE, symbol VARCHAR, perf21 DOUBLE, perf63 DOUBLE, perf126 DOUBLE)")
    con.execute("""CREATE TABLE candidates_pre(
        symbol VARCHAR, setup_date DATE, breakout_date DATE, pivot DOUBLE,
        adr20 DOUBLE, prior_move DOUBLE, contraction DOUBLE, base_depth DOUBLE,
        split_factor DOUBLE, universe_status VARCHAR)""")

    client=EODHDClient(cache_dir=cfg["data"]["cache_dir"])
    u=full_universe(client,args.include_delisted)
    if args.max_symbols and args.max_symbols>0:
        # deterministic development subset only; production uses max_symbols=0
        u=u.sort_values("symbol").head(args.max_symbols).copy()
    u.to_csv("results/universe.csv",index=False)
    status=dict(zip(u["symbol"],u["universe_status"]))

    errors=[]
    with ThreadPoolExecutor(max_workers=max(1,args.workers)) as ex:
        futs={ex.submit(load_one_fast,s,cfg):s for s in u["symbol"]}
        for n,f in enumerate(as_completed(futs),1):
            sym=futs[f]
            try:
                symbol,df,err=f.result()
                if df is None:
                    errors.append({"symbol":symbol,"stage":"daily","error":err})
                else:
                    ranks=df[["date","perf21","perf63","perf126"]].copy()
                    ranks["symbol"]=symbol
                    con.register("rank_chunk",ranks)
                    con.execute("INSERT INTO daily_rank SELECT date,symbol,perf21,perf63,perf126 FROM rank_chunk")
                    con.unregister("rank_chunk")
                    c=pre_candidates(df,symbol,p)
                    if not c.empty:
                        c["universe_status"]=status.get(symbol,"unknown")
                        con.register("cand_chunk",c)
                        con.execute("""INSERT INTO candidates_pre
                            SELECT symbol,setup_date,breakout_date,pivot,adr20,prior_move,
                                   contraction,base_depth,split_factor,universe_status
                            FROM cand_chunk""")
                        con.unregister("cand_chunk")
            except Exception as e:
                errors.append({"symbol":sym,"stage":"daily","error":str(e)})
            if n%100==0 or n==len(futs):
                print(f"Daily universe {n}/{len(futs)} errors={len(errors)}")

    pd.DataFrame(errors).to_csv("results/data_errors.csv",index=False)

    # Rank against ALL available stocks on each candidate setup date, not just
    # against candidates and not against a random sample.
    leader_sql="""
    WITH candidate_dates AS (
      SELECT DISTINCT setup_date AS date FROM candidates_pre
    ),
    universe_on_dates AS (
      SELECT d.*
      FROM daily_rank d JOIN candidate_dates c USING(date)
      WHERE perf21 IS NOT NULL OR perf63 IS NOT NULL OR perf126 IS NOT NULL
    ),
    ranked AS (
      SELECT *,
        percent_rank() OVER(PARTITION BY date ORDER BY perf21 DESC NULLS LAST) AS rank21,
        percent_rank() OVER(PARTITION BY date ORDER BY perf63 DESC NULLS LAST) AS rank63,
        percent_rank() OVER(PARTITION BY date ORDER BY perf126 DESC NULLS LAST) AS rank126
      FROM universe_on_dates
    )
    SELECT c.*,r.rank21,r.rank63,r.rank126
    FROM candidates_pre c
    JOIN ranked r ON r.date=c.setup_date AND r.symbol=c.symbol
    WHERE least(coalesce(r.rank21,1),coalesce(r.rank63,1),coalesce(r.rank126,1)) <= 0.02
    ORDER BY c.breakout_date,c.symbol
    """
    candidates=con.execute(leader_sql).df()
    candidates.to_csv("results/candidates_top2pct.csv",index=False)
    print(f"Top-2% candidates: {len(candidates)}")

    trades=[]
    intraday_errors=[]
    # Re-load only candidate symbols from the local EOD cache; no new EOD calls.
    daily_cache={}
    for n,row in candidates.iterrows():
        sym=row["symbol"]
        try:
            if sym not in daily_cache:
                _,daily,err=load_one(sym,cfg)
                if daily is None:
                    raise RuntimeError(err or "daily reload failed")
                daily_cache[sym]=daily
            daily=daily_cache[sym]
            bdate=pd.Timestamp(row["breakout_date"])
            dmatch=daily[pd.to_datetime(daily["date"]).dt.normalize()==bdate.normalize()]
            if dmatch.empty:
                raise RuntimeError("breakout date missing after exact split reload")
            exact_sf=float(dmatch.iloc[0].get("split_factor",1.0))
            inferred_sf=float(row["split_factor"]) if pd.notna(row["split_factor"]) else 1.0
            exact_pivot=float(row["pivot"])*(exact_sf/inferred_sf if inferred_sf>0 else 1.0)
            # EODHD documents intraday history for delisted companies only
            # when they were delisted after 2021. Avoid pretending older
            # delisted names have exact ORH execution data.
            if row.get("universe_status")=="delisted" and bdate < pd.Timestamp("2021-01-01"):
                raise RuntimeError("intraday_unavailable_for_pre2021_delisted")
            bars=client.intraday_day(sym,str(bdate.date()),"1m")
            if bars.empty:
                raise RuntimeError("no intraday bars")
            exr=execute_orh(
                bars,pivot=exact_pivot,adr_pct=float(row["adr20"]),
                split_factor=exact_sf,cfg=ex_cfg
            )
            if exr is None:
                continue
            m=manage_after_entry(
                daily,row["breakout_date"],float(exr["entry"]),float(exr["initial_stop"]),
                p.partial_day,p.partial_fraction,p.trail_ma,p.max_hold_days,
                exr["entry_day_stop_fill"],
            )
            rec=row.to_dict()
            rec.update(exr)
            rec.update(m)
            rec["entry_date"]=pd.Timestamp(row["breakout_date"])
            rec["initial_stop_pct"]=(rec["entry"]-rec["initial_stop"])/rec["entry"]
            trades.append(rec)
        except Exception as e:
            intraday_errors.append({"symbol":sym,"breakout_date":row["breakout_date"],
                                    "stage":"intraday","error":str(e)})
        if (n+1)%100==0 or n+1==len(candidates):
            print(f"Intraday candidates {n+1}/{len(candidates)} trades={len(trades)}")

    t=pd.DataFrame(trades)
    t.to_csv("results/trades.csv",index=False)
    pd.DataFrame(intraday_errors).to_csv("results/intraday_errors.csv",index=False)

    summary=summarize(t)
    summary.update({
        "universe_symbols":int(len(u)),
        "top2pct_candidates":int(len(candidates)),
        "intraday_trades":int(len(t)),
        "opening_range_minutes":opening_range,
        "intraday_errors":int(len(intraday_errors)),
        "execution_note":"ORH entry; stop = observed session low before trigger; reject if wider than 1x ADR.",
    })
    Path("results/summary.json").write_text(json.dumps(summary,indent=2,default=str))

    oos=cfg.get("research",{}).get("out_of_sample_start","2023-01-01")
    split_summary(t,oos).to_csv("results/sample_summary.csv",index=False)
    yearly_summary(t).to_csv("results/yearly_summary.csv",index=False)

    pcfg=cfg.get("portfolio",{})
    sized,acct=simulate_risk_sized_account(
        t,
        initial_capital=float(pcfg.get("initial_capital",10000.0)),
        risk_per_trade=float(pcfg.get("risk_per_trade",0.005)),
        max_position_pct=float(pcfg.get("max_position_pct",0.30)),
    )
    sized.to_csv("results/trades_risk_sized.csv",index=False)
    Path("results/account_summary.json").write_text(json.dumps(acct,indent=2,default=str))
    con.close()
    print(json.dumps(summary,indent=2,default=str))

if __name__=="__main__":
    main()
