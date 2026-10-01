import pandas as pd
from src.backtest import max_runup
from src.eodhd import split_adjust_ohlc

def test_max_runup_respects_time_order():
    # A decline from 100 to 50 is not a +100% momentum move.
    x=pd.DataFrame({"low":[95,80,50],"high":[100,85,55]})
    assert max_runup(x) < 0.20

def test_split_adjustment():
    d=pd.DataFrame({
        "date":pd.to_datetime(["2020-08-28","2020-08-31"]),
        "open":[500,125],"high":[505,130],"low":[495,120],"close":[500,125],
        "adjusted_close":[121,121],"volume":[1000,1000]
    })
    s=pd.DataFrame({"date":pd.to_datetime(["2020-08-31"]),"split":["4.000000/1.000000"]})
    out=split_adjust_ohlc(d,s)
    assert abs(out.loc[0,"close"]-125) < 1e-9
    assert abs(out.loc[1,"close"]-125) < 1e-9


def test_universe_filter_excludes_otc():
    from src.research import _listed_us
    x=pd.DataFrame({"Exchange":["NASDAQ","NYSE","PINK","OTCQB","AMEX"],"Code":["A","B","C","D","E"]})
    assert _listed_us(x)["Code"].tolist() == ["A","B","E"]



def test_risk_sized_account():
    from src.capital import simulate_risk_sized_account
    x=pd.DataFrame([{
        "symbol":"TEST","entry_date":"2020-01-02","exit_date":"2020-01-03",
        "entry":100.0,"initial_stop":95.0,"pnl_per_share":10.0
    }])
    sized,s=simulate_risk_sized_account(x,10000.0,0.005,0.30)
    assert int(sized.loc[0,"shares"]) == 10
    assert abs(s["final_capital"]-10100.0) < 1e-9


def test_position_size_capped_at_30pct():
    from src.capital import simulate_risk_sized_account
    x=pd.DataFrame([{
        "symbol":"TEST","entry_date":"2020-01-02","exit_date":"2020-01-03",
        "entry":100.0,"initial_stop":99.9,"pnl_per_share":1.0
    }])
    sized,s=simulate_risk_sized_account(x,10000.0,0.005,0.30)
    assert int(sized.loc[0,"shares"]) == 30
    assert float(sized.loc[0,"notional_at_entry"]) <= 3000.0


def test_orh_stop_is_observed_low_and_no_future_lookahead():
    from src.intraday import IntradayExecution, execute_orh
    times=pd.date_range("2024-01-02 14:30:00+00:00",periods=8,freq="min")
    bars=pd.DataFrame({
        "datetime":times,
        "open":[100,100.2,100.4,100.5,100.7,101.0,101.2,100.0],
        "high":[100.4,100.5,100.6,100.8,101.0,101.5,101.4,100.5],
        "low":[99.5,99.8,100.0,100.2,100.4,100.9,100.8,99.0],
        "close":[100.2,100.4,100.5,100.7,100.9,101.3,101.0,99.5],
        "volume":[1000]*8,
    })
    r=execute_orh(bars,pivot=101.0,adr_pct=3.0,
                  cfg=IntradayExecution(opening_range_minutes=5,slippage_bps=0))
    assert r is not None
    assert abs(r["entry"]-101.0) < 1e-9
    assert abs(r["initial_stop"]-99.5) < 1e-9
    assert r["stopped_entry_day"] is True
    assert abs(r["entry_day_stop_fill"]-99.5) < 1e-9

def test_orh_rejects_stop_wider_than_one_adr():
    from src.intraday import IntradayExecution, execute_orh
    times=pd.date_range("2024-01-02 14:30:00+00:00",periods=6,freq="min")
    bars=pd.DataFrame({
        "datetime":times,
        "open":[100,100,100,100,100,105],
        "high":[101,101,101,101,101,106],
        "low":[95,96,97,98,99,104],
        "close":[100,100,100,100,100,105],
        "volume":[1000]*6,
    })
    r=execute_orh(bars,pivot=105.0,adr_pct=5.0,
                  cfg=IntradayExecution(opening_range_minutes=5,slippage_bps=0))
    assert r is None


def test_partial_then_breakeven_stop():
    from src.two_stage import manage_after_entry
    dates=pd.date_range("2024-01-02",periods=6,freq="B")
    d=pd.DataFrame({
        "date":dates,
        "open":[100,102,103,104,100,100],
        "high":[105,104,105,106,101,101],
        "low":[99,101,102,103,99,99],
        "close":[103,103,104,105,100,100],
        "sma10":[95,96,97,98,99,99],
    })
    r=manage_after_entry(d,dates[0],100.0,98.0,4,0.5,10,60,None)
    # Day 4: sell half at 105 (+2.5/share weighted). Stop moves to 100.
    # Day 5 trades through 100, so remaining half exits at 100.
    assert abs(r["pnl_per_share"]-2.5) < 1e-9
    assert r["exit_reason"]=="stop"
