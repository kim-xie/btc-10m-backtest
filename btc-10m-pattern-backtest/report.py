import pandas as pd, numpy as np, json, itertools
from backtest import *
F24="klines_btcusdt_5m_24m.csv"
d,tA,tB,tC,both,conf=run(F24)
n=len(d); split_bar=int(n*0.7); split_t=d.t[split_bar]
allt=pd.concat([tA,tB],ignore_index=True)
allt["is_oos"]=np.where(allt.bar<split_bar,"IS","OOS")
allt.drop(columns=["bar"]).to_csv("trades.csv",index=False)
tC.drop(columns=["bar"]).to_csv("trades_combined_dedup.csv",index=False)
out={"data":dict(rows=n,first=str(d.t.iloc[0]),last=str(d.t.iloc[-1]),split_time=str(split_t)),"overlap_bars":both,"conflict_bars":conf}
def block(t,name):
    r={"all":metrics(t)}
    if len(t)==0: return r
    r["IS"]=metrics(t[t.bar<split_bar]); r["OOS"]=metrics(t[t.bar>=split_bar])
    mid=t.bar.median(); r["H1"]=metrics(t[t.bar<=mid]); r["H2"]=metrics(t[t.bar>mid])
    for s in ["long","short"]: r[s]=metrics(t[t.side==s])
    for s in ["Asia","Europe","US"]: r[s]=metrics(t[t.session==s])
    last12=d.t.iloc[-1]-pd.Timedelta(days=365)
    r["last12m"]=metrics(t[t.signal_open_utc>=last12])
    r["nonoverlap"]=metrics(dedup_nonoverlap(t))
    return r
out["A"]=block(tA,"A"); out["B"]=block(tB,"B"); out["A+B"]=block(tC,"A+B")
# slippage
out["slip"]={}
for bps in [1,2]:
    _,a,b,c,_,_=run(F24,slip=bps)
    out["slip"][bps]={k:metrics(v) for k,v in [("A",a),("B",b),("A+B",c)]}
# sensitivity (full sample + IS/OOS)
sens=[]
for dist,cr in itertools.product([0.002,0.003,0.005],[2,3]):
    p=dict(P,dist=dist,crosses=cr); aL,aS=setupA(d,dist,cr); t=trades(d,aL,aS,"A")
    m=metrics(t); mi=metrics(t[t.bar<split_bar]) if len(t) else {}; mo=metrics(t[t.bar>=split_bar]) if len(t) else {}
    sens.append(dict(setup="A",param=f"dist={dist*100:.1f}%,crosses>={cr}",N=m["N"],wr=m.get("win_rate_decisive"),lo=m.get("wilson_lo"),expR=m.get("expectancy_R"),IS_N=mi.get("N"),IS_wr=mi.get("win_rate_decisive"),OOS_N=mo.get("N"),OOS_wr=mo.get("win_rate_decisive")))
for bm in [1.5,2.0,2.5]:
    bL,bS=setupB(d,bm); t=trades(d,bL,bS,"B")
    m=metrics(t); mi=metrics(t[t.bar<split_bar]) if len(t) else {}; mo=metrics(t[t.bar>=split_bar]) if len(t) else {}
    sens.append(dict(setup="B",param=f"body>={bm}x median50",N=m["N"],wr=m.get("win_rate_decisive"),lo=m.get("wilson_lo"),expR=m.get("expectancy_R"),IS_N=mi.get("N"),IS_wr=mi.get("win_rate_decisive"),OOS_N=mo.get("N"),OOS_wr=mo.get("win_rate_decisive")))
for pr in [0.382]:
    bL,bS=setupB(d,2.0,pb_retr=pr); t=trades(d,bL,bS,"B"); m=metrics(t)
    mi=metrics(t[t.bar<split_bar]); mo=metrics(t[t.bar>=split_bar])
    sens.append(dict(setup="B",param=f"body>=2.0x, retrace<=38.2%",N=m["N"],wr=m.get("win_rate_decisive"),lo=m.get("wilson_lo"),expR=m.get("expectancy_R"),IS_N=mi.get("N"),IS_wr=mi.get("win_rate_decisive"),OOS_N=mo.get("N"),OOS_wr=mo.get("win_rate_decisive")))
pd.DataFrame(sens).to_csv("sensitivity.csv",index=False)
out["sens"]=sens
json.dump(out,open("results.json","w"),indent=1,default=str)
print(json.dumps(out,indent=1,default=lambda x: round(x,4) if isinstance(x,float) else str(x)))
