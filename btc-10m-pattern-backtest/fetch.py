"""Download BTCUSDT 5m klines from Binance spot public REST (paginated). No synthetic bars."""
import requests, time, pandas as pd, sys
BASE=["https://api.binance.com/api/v3/klines","https://data-api.binance.vision/api/v3/klines"]
def get(params):
    for b in BASE:
        for _ in range(3):
            try:
                r=requests.get(b,params=params,timeout=20); r.raise_for_status(); return r.json(),b
            except Exception as e: err=e; time.sleep(1)
    raise RuntimeError(err)
now=int(time.time()*1000)
last_closed_open = (now//300000)*300000 - 300000   # open time of most recent CLOSED 5m bar
end_close = last_closed_open + 299999
start = last_closed_open - 730*86400*1000  # ~24 months
rows=[]; cur=start; src=None
while cur<=last_closed_open:
    data,src=get({"symbol":"BTCUSDT","interval":"5m","startTime":cur,"endTime":end_close,"limit":1000})
    if not data: break
    rows+=data; cur=data[-1][0]+300000
cols=["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]
df=pd.DataFrame(rows,columns=cols).drop_duplicates("open_time").sort_values("open_time")
df=df[df.open_time<=last_closed_open]
df["open_time_utc"]=pd.to_datetime(df.open_time,unit="ms",utc=True)
df["close_time_utc"]=pd.to_datetime(df.close_time,unit="ms",utc=True)
out=df[["open_time","open_time_utc","open","high","low","close","volume","close_time","close_time_utc"]]
out.to_csv("klines_btcusdt_5m_24m.csv",index=False)
cut=last_closed_open-365*86400*1000
out[out.open_time>=cut].to_csv("klines_btcusdt_5m_12m.csv",index=False)
gaps=(df.open_time.diff().dropna()!=300000).sum()
print("source",src,"rows",len(df),"first",df.open_time_utc.iloc[0],"last",df.open_time_utc.iloc[-1],"gaps",gaps)
