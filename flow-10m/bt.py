# Pre-registered 2026-11-11 16:59 CST. Rules fixed before running.
import pandas as pd,glob,numpy as np,math
fs=sorted(glob.glob('/workspace/morning-star-backtest/ytd/kl/*.csv'))
df=pd.concat(pd.read_csv(f,header=None) for f in fs)
df.columns=['t','o','h','l','c','v','ct','qv','n','tbv','tbq','x']
df['t']=pd.to_datetime(df.t//1000,unit='ms');df=df.set_index('t').sort_index()
c=df.c
res={}
# evaluate at each 5m boundary (entry = close of minute ending there), exit = close 10 min later
idx=df.index[(df.index.minute%5==4)]
fut=c.shift(-10)
r5=df.tbv.rolling(5).sum()/df.v.rolling(5).sum()
r15=df.tbv.rolling(15).sum()/df.v.rolling(15).sum()
vol5=df.v.rolling(5).sum(); volmed=vol5.rolling(288*5).median()
ret5=c/c.shift(5)-1
spike=vol5>3*volmed
rules={
 'R1 主动买比5m>=0.60做多/<=0.40做空(顺)':(r5>=0.60, r5<=0.40),
 'R2 同R1但反向':(r5<=0.40, r5>=0.60),
 'R3 主动买比15m>=0.57做多/<=0.43做空(顺)':(r15>=0.57, r15<=0.43),
 'R4 放量3倍+5m涨跌顺势':(spike&(ret5>0), spike&(ret5<0)),
 'R5 放量3倍+5m涨跌逆势':(spike&(ret5<0), spike&(ret5>0)),
}
cut=idx[int(len(idx)*0.6)]
print('split',cut)
for k,(L,S) in rules.items():
    out=[]
    for side,m in (('L',L),('S',S)):
        sel=idx[m.reindex(idx).fillna(False).values]
        d=(fut.reindex(sel)-c.reindex(sel)).dropna()
        w=(d>0) if side=='L' else (d<0)
        out.append(pd.Series(w.values,index=w.index))
    s=pd.concat(out).sort_index()
    for name,part in (('训练',s[s.index<cut]),('测试',s[s.index>=cut])):
        n=len(part);p=part.mean() if n else float('nan')
        lo=p-1.96*math.sqrt(p*(1-p)/n) if n else float('nan')
        ev=(p*0.8-(1-p))*100 if n else float('nan')
        print(f'{k} {name}: n={n} 胜率={p:.3%} 下限={lo:.3%} 每100期望={ev:.1f}')
