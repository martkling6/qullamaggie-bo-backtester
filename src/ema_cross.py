from __future__ import annotations
import argparse, json, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import pandas as pd
from .eodhd import EODHDClient, split_adjust_ohlc

def nasdaq_technology(universe):
    x=universe.copy()
    exchange=x.get("Exchange",pd.Series("",index=x.index)).fillna("").astype(str).str.upper()
    typ=x.get("Type",pd.Series("",index=x.index)).fillna("").astype(str).str.lower()
    return x[exchange.str.contains("NASDAQ",regex=False) & (~typ.str.contains("etf|fund|preferred|warrant",regex=True)) & x["Code"].notna()].drop_duplicates("Code")

def is_technology(fundamentals):
    general=fundamentals.get("General",{}) if isinstance(fundamentals,dict) else {}
    return str(general.get("Sector","")).lower() in ("technology","information technology")

def signals(df):
    x=df.sort_values("date").copy().reset_index(drop=True)
    x["ema10"]=x.close.ewm(span=10,adjust=False,min_periods=20).mean()
    x["ema20"]=x.close.ewm(span=20,adjust=False,min_periods=20).mean()
    x["ema200"]=x.close.ewm(span=200,adjust=False,min_periods=200).mean()
    x["ema200_rising"]=x.ema200>x.ema200.shift(1)
    x["cross"]=(x.ema10>x.ema20)&(x.ema10.shift(1)<=x.ema20.shift(1))
    return x

def max_90d_runup(x, signal_idx):
    """Largest low-to-later-high rise within last 90 completed trading days."""
    window=x.iloc[signal_idx-89:signal_idx+1]
    if len(window)!=90:return float("nan")
    lows=window["low"].to_numpy(dtype=float)
    highs=window["high"].to_numpy(dtype=float)
    prior_min=pd.Series(lows).cummin().shift(1).to_numpy()
    return float(max(highs[1:]/prior_min[1:]-1))

def trade_signals(x,symbol,buffer_pct=.001):
    trades=[]
    # Entries are next-session opens; entry signal uses only previous close.
    for i in range(201,len(x)-6):
        if not bool(x.at[i-1,"cross"]): continue
        if not bool(x.at[i-1,"ema200_rising"]): continue
        move90=max_90d_runup(x,i-1)
        if not (0.30<=move90<=1.00): continue
        entry=float(x.at[i,"open"])
        stop=float(x.at[i-1,"low"])*(1-buffer_pct)
        if not (math.isfinite(entry) and math.isfinite(stop) and 0<stop<entry): continue
        risk_pct=(entry-stop)/entry
        remaining=1.; pnl=0.; partial=False; exit_idx=None; reason=None
        current_stop=stop
        for j in range(i,len(x)):
            bar=x.iloc[j]; held=j-i+1
            # Day-one opening is already the entry; later gaps can slip below stop.
            if j>i and float(bar.open)<=current_stop:
                pnl+=remaining*(float(bar.open)-entry); exit_idx=j; reason="gap_stop";break
            if float(bar.low)<=current_stop:
                # Daily OHLC cannot determine whether stop or another event occurred first.
                pnl+=remaining*(current_stop-entry);exit_idx=j;reason="stop";break
            if held==3 and float(bar.close)<=entry:
                pnl+=remaining*(float(bar.close)-entry);exit_idx=j;reason="day3_not_profitable";break
            if held==5:
                pnl+=.5*(float(bar.close)-entry);remaining=.5;partial=True
            if held>=5 and j+1<len(x):
                # EMA is known at this close; applies starting NEXT session.
                ma=float(bar.ema10)
                if math.isfinite(ma):
                    current_stop=max(current_stop,ma*(1-buffer_pct))
            if j==len(x)-1:
                pnl+=remaining*(float(bar.close)-entry);exit_idx=j;reason="end_of_data"
        if exit_idx is None:continue
        trades.append(dict(symbol=symbol,signal_date=str(x.at[i-1,"date"].date()),entry_date=str(x.at[i,"date"].date()),
            exit_date=str(x.at[exit_idx,"date"].date()),entry=entry,initial_stop=stop,stop_pct=risk_pct,
            pnl_per_share=pnl,partial_day5=partial,exit_reason=reason,
            move90_pct=round(move90*100,3),ema200_signal=float(x.at[i-1,"ema200"])))
    return trades

def simulate(trades,risk_fraction,initial=10000.,leverage=5.,fee_bps=10.,max_stop_pct=None):
    capital=initial; open_positions=[]; out=[]; skipped={"stop_limit":0,"margin":0,"overlap":0}
    # Independent strategy paths for each risk setting; do not use future realized profits to fund earlier entries.
    for t in sorted(trades,key=lambda z:(z["entry_date"],z["symbol"])):
        entry_date=pd.Timestamp(t["entry_date"])
        still=[]
        for p in open_positions:
            if pd.Timestamp(p["exit_date"])<entry_date:capital+=p["margin"]+p["pnl_eur"]
            else:still.append(p)
        open_positions=still
        # Cash / margin is reserved until exit. Conservative: unrealized PnL not counted.
        equity=capital+sum(p["margin"] for p in open_positions)
        if max_stop_pct is not None and t["stop_pct"]>max_stop_pct:
            skipped["stop_limit"]+=1;continue
        if any(p["symbol"]==t["symbol"] for p in open_positions):
            skipped["overlap"]+=1;continue
        budget=max(0,equity*risk_fraction)
        shares=math.floor(budget/(t["entry"]-t["initial_stop"]))
        available=capital
        shares=min(shares,math.floor(max(0,available)*leverage/t["entry"]))
        if shares<1:
            skipped["margin"]+=1;continue
        margin=shares*t["entry"]/leverage
        # PnL in USD, account currency assumed USD; fees both entry and exit notionals approx.
        pnl=shares*t["pnl_per_share"]-shares*t["entry"]*fee_bps/10000*2
        capital-=margin
        row=dict(t,scenario_risk_pct=int(risk_fraction*100),shares=shares,margin=margin,
                 planned_risk=shares*(t["entry"]-t["initial_stop"]),pnl_eur=pnl,
                 equity_at_entry=equity)
        open_positions.append(row);out.append(row)
    for p in open_positions:capital+=p["margin"]+p["pnl_eur"]
    return out,dict(risk_pct=int(risk_fraction*100),initial_capital=initial,ending_capital=round(capital,2),
                    return_pct=round((capital/initial-1)*100,2),trades=len(out),skipped=skipped)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--start",default="2015-01-01")
    ap.add_argument("--end",default=None)
    ap.add_argument("--max-symbols",type=int,default=200,help="0 = all matching technology stocks")
    ap.add_argument("--include-delisted",action="store_true")
    ap.add_argument("--workers",type=int,default=8)
    ap.add_argument("--leverage",type=float,default=5.)
    args=ap.parse_args()
    client=EODHDClient()
    u=[client.symbols("US",delisted=False,common_only=True)]
    if args.include_delisted:u.append(client.symbols("US",delisted=True,common_only=True))
    uni=nasdaq_technology(pd.concat(u,ignore_index=True))
    if uni.empty:raise RuntimeError("No NASDAQ technology symbols: inspect EODHD Exchange/Sector metadata; do not silently run an empty backtest.")
    # Curated NASDAQ-listed technology stock sample. EODHD Fundamentals is
    # unavailable on EOD+Intraday subscriptions (HTTP 403). Do not pretend
    # this static watchlist is the complete or point-in-time sector universe.
    tech_codes = {
        "AAPL","MSFT","NVDA","AMD","INTC","AVGO","ADBE","CSCO","QCOM",
        "AMAT","LRCX","KLAC","MU","MRVL","ADI","NXPI","MCHP","ON",
        "CDNS","SNPS","FTNT","PANW","CRWD","ZS","OKTA","DDOG",
        "MDB","NET","TEAM","WDAY","PAYC","PAYX","ADP","INTU",
        "ORCL","APP","PLTR","SMCI","DELL","HPQ","HPE","NTAP",
        "WDC","STX","AKAM","ANSS","CTSH","EPAM","GDDY","DOCU",
        "ZM","S","CYBR","CHKP","GEN","FSLY","RPD","TENB",
        "SPLK","VMW","XLNX","ATVI","SWKS","QRVO","MPWR","TER",
        "ENTG","LSCC","COHR","OLED","ALGM","ASML","ARM","GFS"
    }
    uni=uni[uni.Code.astype(str).isin(tech_codes)].sort_values("Code").copy()
    if args.max_symbols>0:uni=uni.head(args.max_symbols)
    if uni.empty:raise RuntimeError("No curated technology tickers match the NASDAQ exchange list")
    sector_errors=[]
    print(f"Curated technology watchlist: {len(uni)} matching NASDAQ symbols; NOT a complete historical sector universe",flush=True)
    Path("results/ema_cross").mkdir(parents=True,exist_ok=True)
    uni.to_csv("results/ema_cross/universe.csv",index=False)
    def load(sym):
        c=EODHDClient()
        raw=c.eod(sym,args.start,args.end)
        if len(raw)<30:return [],None
        try:splits=c.splits(sym,args.start,args.end)
        except Exception:splits=None
        x=signals(split_adjust_ohlc(raw,splits))
        return trade_signals(x,sym),None
    trades=[];errors=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        fut={pool.submit(load,str(code)+".US"):str(code) for code in uni.Code}
        for f in as_completed(fut):
            try:
                rows,_=f.result();trades.extend(rows)
            except Exception as e:errors.append(dict(symbol=fut[f],error=str(e)))
    pd.DataFrame(trades).to_csv("results/ema_cross/signals.csv",index=False)
    errors.extend(sector_errors)
    pd.DataFrame(errors,columns=["symbol","error"]).to_csv("results/ema_cross/errors.csv",index=False)
    summaries=[]
    for risk in (.05,.07,.10):
        rows,s=simulate(trades,risk,leverage=args.leverage,max_stop_pct=risk)
        pd.DataFrame(rows).to_csv(f"results/ema_cross/trades_{int(risk*100)}pct.csv",index=False)
        summaries.append(s)
    pd.DataFrame(summaries).to_csv("results/ema_cross/comparison.csv",index=False)
    Path("results/ema_cross/summary.json").write_text(json.dumps(dict(symbols=len(uni),raw_signals=len(trades),errors=len(errors),scenarios=summaries,
      caveats=["USD prices and USD capital: no EUR/USD conversion","EOD daily-bar fills; no broker CFD spreads, overnight financing or real margin rules","Daily bars cannot resolve intraday stop sequencing","Curated technology watchlist only; NOT all NASDAQ tech stocks; historical sector membership not point-in-time verified","5:1 leverage is an assumption, not broker validation"]),indent=2))
    print(json.dumps(summaries,indent=2))
if __name__=="__main__":main()
