import glob, pandas as pd, numpy as np, math
files=sorted(glob.glob('/workspace/morning-star-backtest/ytd/kl/BTCUSDT-1m-*.csv'))
dfs=[]
for f in files:
    d=pd.read_csv(f,header=None)
    if not str(d.iloc[0,0]).isdigit(): d=d.iloc[1:]
    dfs.append(d.iloc[:,:6])
df=pd.concat(dfs); df.columns=['t','o','h','l','c','v']
df=df.astype(float); df['t']=df['t'].astype('int64')
if df['t'].max()>1e14: df['t']//=1000
df=df.drop_duplicates('t').sort_values('t')
df['dt']=pd.to_datetime(df['t'],unit='ms',utc=True); df=df.set_index('dt')
print('1m range',df.index.min(),df.index.max(),len(df))
k=df.resample('5min').agg({'o':'first','h':'max','l':'min','c':'last'}).dropna()
c1=df['c']
k['ema']=k['c'].ewm(span=50,adjust=False).mean()
k['body']=(k['c']-k['o']).abs()
k['med']=k['body'].rolling(20).median().shift(1)
above=(k['c']>k['ema']).astype(int)
up=(above.diff()==1).astype(int); dn=(above.diff()==-1).astype(int)
k['upx']=up.rolling(20).sum(); k['dnx']=dn.rolling(20).sum()
po,pc=k['o'].shift(1),k['c'].shift(1)
bull_eng=(k['c']>k['o'])&(pc<po)&(k['o']<=pc)&(k['c']>=po)
bear_eng=(k['c']<k['o'])&(pc>po)&(k['o']>=pc)&(k['c']<=po)
near=((k['c']-k['ema']).abs()/k['ema']<=0.003)
sigA_L=bull_eng&(k['upx']>=2)&near
sigA_S=bear_eng&(k['dnx']>=2)&near
# rule B: >=3 strong bulls, then 1-3 weak pullback bars (opposite color, body<med), then engulf
strongbull=((k['c']>k['o'])&(k['body']>=2*k['med'])).values
strongbear=((k['c']<k['o'])&(k['body']>=2*k['med'])).values
weakbear=((k['c']<k['o'])&(k['body']<k['med'])).values
weakbull=((k['c']>k['o'])&(k['body']<k['med'])).values
be=bull_eng.values; se=bear_eng.values
n=len(k); BL=np.zeros(n,bool); BS=np.zeros(n,bool)
for i in range(8,n):
    for pb in (1,2,3):
        j=i-1  # last pullback bar index i-1 .. i-pb
        if be[i] and all(weakbear[i-m] for m in range(1,pb+1)):
            s=i-pb-1; cnt=0
            while s>=0 and strongbull[s]: cnt+=1; s-=1
            if cnt>=3: BL[i]=True
        if se[i] and all(weakbull[i-m] for m in range(1,pb+1)):
            s=i-pb-1; cnt=0
            while s>=0 and strongbear[s]: cnt+=1; s-=1
            if cnt>=3: BS[i]=True
k['BL']=BL; k['BS']=BS
# entry at signal bar close time = bar start+5min; exit 10 min later using 1m close
rows=[]
for name,col,d in [('A_long',sigA_L,1),('A_short',sigA_S,-1),('B_long',k['BL'],1),('B_short',k['BS'],-1)]:
    idx=k.index[col.fillna(False).values]
    idx=idx[idx>=k.index[0]+pd.Timedelta(minutes=5*250)]  # ema warmup
    for t in idx:
        te=t+pd.Timedelta(minutes=5); tx=te+pd.Timedelta(minutes=10)
        # close of 1m bar ending at te -> bar starting te-1m
        try:
            pe=c1.loc[te-pd.Timedelta(minutes=1)]; px=c1.loc[tx-pd.Timedelta(minutes=1)]
        except KeyError: continue
        win=(px-pe)*d>0
        rows.append((name,t,win))
r=pd.DataFrame(rows,columns=['rule','t','win']).sort_values('t')
cut=r['t'].quantile(0.6) if len(r) else None
cut=r['t'].iloc[int(len(r)*0.6)]
def stats(s):
    n=len(s); w=s['win'].sum(); p=w/n if n else float('nan')
    z=1.96; den=1+z*z/n; ctr=(p+z*z/(2*n))/den; hw=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    pnl=np.where(s['win'],0.8,-1.0); eq=np.cumsum(pnl); dd=(np.maximum.accumulate(np.concatenate([[0],eq]))[1:]-eq).max() if n else 0
    return n,round(p*100,1),round((ctr-hw)*100,1),round((ctr+hw)*100,1),round(pnl.mean()*100,1),round(dd,1)
print('split at',cut)
out=[]
groups=[('ALL',r)]+[(g,r[r.rule==g]) for g in ['A_long','A_short','B_long','B_short']]+[('A',r[r.rule.str.startswith('A')]),('B',r[r.rule.str.startswith('B')])]
for g,s in groups:
    for part,ss in [('all',s),('train',s[s.t<cut]),('test',s[s.t>=cut])]:
        if len(ss): out.append((g,part)+stats(ss))
res=pd.DataFrame(out,columns=['group','part','n','win%','ci_lo','ci_hi','EV_per100','maxDD_units'])
print(res.to_string(index=False))
res.to_csv('results.csv',index=False); r.to_csv('trades.csv',index=False)
