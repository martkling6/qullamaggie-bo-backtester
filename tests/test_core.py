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


def test_max_initial_stop_pct_configured():
    from src.backtest import Params
    assert Params().max_initial_stop_pct == 0.06
