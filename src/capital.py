from __future__ import annotations
import math
import pandas as pd

def simulate_risk_sized_account(
    trades: pd.DataFrame,
    initial_capital: float = 10_000.0,
    risk_per_trade: float = 0.06,
) -> tuple[pd.DataFrame, dict]:
    """Risk-size trades and compound a model account.

    shares = floor((equity * risk_per_trade) / (entry - initial_stop))

    Equity is updated when trades exit. Multiple positions may overlap; this
    first portfolio model does not impose a buying-power/notional cap, so it
    also reports peak concurrent initial risk. FX is ignored: EUR is treated
    as the account reporting unit while stock P&L is converted 1:1 for the
    purpose of testing percentage compounding.
    """
    if trades is None or trades.empty:
        return pd.DataFrame(), {
            "initial_capital": initial_capital,
            "final_capital": initial_capital,
            "return_pct": 0.0,
            "risk_per_trade": risk_per_trade,
            "max_concurrent_initial_risk_pct": 0.0,
        }

    x=trades.copy().reset_index(drop=True)
    x["entry_date"]=pd.to_datetime(x["entry_date"])
    x["exit_date"]=pd.to_datetime(x["exit_date"])
    entries={}
    exits={}
    for idx,row in x.iterrows():
        entries.setdefault(row["entry_date"],[]).append(idx)
        exits.setdefault(row["exit_date"],[]).append(idx)

    dates=sorted(set(entries)|set(exits))
    equity=float(initial_capital)
    open_pos={}
    peak_open_risk=0.0
    records={}

    for d in dates:
        # Realize positions that were already open. If entry and exit are the
        # same day, entry is processed below and closed immediately afterward.
        for idx in exits.get(d,[]):
            if idx in open_pos:
                pos=open_pos.pop(idx)
                pnl=pos["shares"]*float(x.loc[idx,"pnl_per_share"])
                equity += pnl
                records[idx]["account_pnl"] = pnl
                records[idx]["equity_after_exit"] = equity

        same_day=[]
        for idx in entries.get(d,[]):
            row=x.loc[idx]
            per_share=float(row["entry"]-row["initial_stop"])
            if per_share <= 0 or equity <= 0:
                continue
            risk_cash=equity*risk_per_trade
            shares=math.floor(risk_cash/per_share)
            if shares < 1:
                continue
            actual_risk=shares*per_share
            rec={
                "shares":shares,
                "risk_cash_target":risk_cash,
                "initial_risk_cash":actual_risk,
                "equity_at_entry":equity,
                "notional_at_entry":shares*float(row["entry"]),
            }
            records[idx]=rec
            open_pos[idx]=rec
            if row["exit_date"] == d:
                same_day.append(idx)

        for idx in same_day:
            pos=open_pos.pop(idx)
            pnl=pos["shares"]*float(x.loc[idx,"pnl_per_share"])
            equity += pnl
            records[idx]["account_pnl"]=pnl
            records[idx]["equity_after_exit"]=equity

        open_risk=sum(v["initial_risk_cash"] for v in open_pos.values())
        if initial_capital > 0:
            peak_open_risk=max(peak_open_risk,open_risk/max(equity,1e-9))

    sized=x.join(pd.DataFrame.from_dict(records,orient="index"))
    summary={
        "initial_capital":float(initial_capital),
        "final_capital":float(equity),
        "return_pct":float((equity/initial_capital-1)*100),
        "risk_per_trade":float(risk_per_trade),
        "max_concurrent_initial_risk_pct":float(peak_open_risk*100),
        "model_note":"No notional/buying-power cap; FX ignored in this research model."
    }
    return sized,summary
