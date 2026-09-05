import pandas as pd
from app.utils.indicators import add_indicators

def test_indicators_columns():
    n=220
    df=pd.DataFrame({"open":range(1,n+1),"high":range(2,n+2),"low":range(1,n+1),"close":range(2,n+2),"volume":[1000+i for i in range(n)]})
    x=add_indicators(df)
    for c in ["ema9","ema20","ema50","ema200","rsi","atr","macd","vwap","rvol"]: assert c in x.columns
