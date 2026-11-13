"""BTCUSDT 5m pattern backtest: Setup A (EMA50 crosses + engulfing), Setup B (impulse, weak pullback, engulfing).
Settlement 10 min after entry (close of 2nd following 5m bar). Payoff +0.8 / -1.0, scratch 0."""
import pandas as pd, numpy as np, math, json, sys
WIN, LOSS = 0.8, -1.0
BE = 1/(1+WIN)
WARMUP = 200
P = dict(dist=0.003, crosses=2, window=20, body_mult=2.0, med_win=50, imp_bars=3, pb_max=5, pb_retr=0.5)

def load(path):
    d=pd.read_csv(path)
    for c in ["open","high","low","close"]: d[c]=d[c].astype(float)
    d["t"]=pd.to_datetime(d.open_time,unit="ms",utc=True)
    d=d.reset_index(drop=True)
    d["ema"]=d.close.ewm(span=50,adjust=False).mean()   # recursive EMA, uses only data up to bar
    return d

def engulf(d):
    o,c=d.open.values,d.close.values; po,pc=np.roll(o,1),np.roll(c,1)
    bull=(c>o)&(pc<po)&(o<=pc)&(c>=po)&((c-o)>(po-pc))
    bear=(c<o)&(pc>po)&(o>=pc)&(c<=po)&((o-c)>(pc-po))
    bull[0]=bear[0]=False
    return bull,bear

def setupA(d,dist,crosses,window=20):
    c,e=d.close.values,d.ema.values
    up=np.zeros(len(d),bool); dn=np.zeros(len(d),bool)
    up[1:]=(c[:-1]<=e[:-1])&(c[1:]>e[1:]); dn[1:]=(c[:-1]>=e[:-1])&(c[1:]<e[1:])
    nup=pd.Series(up).rolling(window,min_periods=window).sum().values  # bars i-19..i (incl. signal bar)
    ndn=pd.Series(dn).rolling(window,min_periods=window).sum().values
    near=np.abs(c-e)/e<=dist
    bull,bear=engulf(d)
    return (nup>=crosses)&near&bull, (ndn>=crosses)&near&bear

def setupB(d,body_mult,med_win=50,imp_bars=3,pb_max=5,pb_retr=0.5):
    o,h,l,c=d.open.values,d.high.values,d.low.values,d.close.values
    body=np.abs(c-o)
    med=pd.Series(body).rolling(med_win).median().shift(1).values  # median of prior 50 bodies, excl. current
    sb=(c>o)&(body>=body_mult*med); sr=(c<o)&(body>=body_mult*med)
    bull,bear=engulf(d); n=len(d)
    L=np.zeros(n,bool); S=np.zeros(n,bool)
    # run lengths of consecutive strong bars ending at j
    rb=np.zeros(n,int); rr=np.zeros(n,int)
    for j in range(n):
        rb[j]=(rb[j-1]+1 if j>0 else 1) if sb[j] else 0
        rr[j]=(rr[j-1]+1 if j>0 else 1) if sr[j] else 0
    for i in range(2,n):
        if not (bull[i] or bear[i]): continue
        for k in range(1,pb_max+1):          # pullback = bars j+1..i-1, k bars
            j=i-1-k
            if j<0: break
            if bull[i] and rb[j]>=imp_bars and not sb[j+1:i].any():
                s=j-rb[j]+1; ih=h[s:j+1].max(); il=l[s:j+1].min(); rng=ih-il
                pbh=h[j+1:i].max(); pbl=l[j+1:i].min()
                if pbh<=ih and pbl>=ih-pb_retr*rng: L[i]=True
                break
            if bear[i] and rr[j]>=imp_bars and not sr[j+1:i].any():
                s=j-rr[j]+1; il=l[s:j+1].min(); ih=h[s:j+1].max(); rng=ih-il
                pbh=h[j+1:i].max(); pbl=l[j+1:i].min()
                if pbl>=il and pbh<=il+pb_retr*rng: S[i]=True
                break
    return L,S

def trades(d,L,S,setup,slip_bps=0.0):
    idx=np.where((L|S))[0]; idx=idx[(idx>=WARMUP)&(idx+2<len(d))]
    rows=[]
    for i in idx:
        side=1 if L[i] else -1
        entry=d.close[i]*(1+side*slip_bps/1e4); exitp=d.close[i+2]
        diff=(exitp-entry)*side
        res="win" if diff>0 else ("loss" if diff<0 else "scratch")
        rows.append(dict(setup=setup,bar=i,signal_open_utc=d.t[i],entry_time_utc=d.t[i]+pd.Timedelta(minutes=5),
            side="long" if side==1 else "short",entry=entry,exit=exitp,
            exit_time_utc=d.t[i+2]+pd.Timedelta(minutes=5),result=res,R={"win":WIN,"loss":LOSS,"scratch":0.0}[res]))
    t=pd.DataFrame(rows)
    if len(t):
        t["entry_time_shanghai"]=t.entry_time_utc.dt.tz_convert("Asia/Shanghai")
        h=t.entry_time_utc.dt.hour
        t["session"]=np.where(h<8,"Asia",np.where(h<16,"Europe","US"))
    return t

def wilson(k,n,z=1.96):
    if n==0: return (float("nan"),)*2
    p=k/n; den=1+z*z/n; ctr=(p+z*z/(2*n))/den; hw=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return ctr-hw,ctr+hw

def metrics(t):
    if t is None or len(t)==0: return dict(N=0)
    w=(t.result=="win").sum(); l=(t.result=="loss").sum(); s=(t.result=="scratch").sum(); dec=w+l
    p=w/dec if dec else float("nan"); lo,hi=wilson(w,dec)
    # max consecutive losses (scratches don't break or extend)
    mx=cur=0
    for r in t.sort_values("entry_time_utc").result:
        if r=="loss": cur+=1; mx=max(mx,cur)
        elif r=="win": cur=0
    eq=1000+5.0*t.sort_values("entry_time_utc").R.cumsum()   # fixed 5 USDT stake = 0.5% of 1000
    eq=pd.concat([pd.Series([1000.0]),eq.reset_index(drop=True)])
    peak=eq.cummax(); dd=(peak-eq); i=dd.idxmax()
    return dict(N=len(t),wins=int(w),losses=int(l),scratches=int(s),win_rate_decisive=p,
        win_rate_incl_scratch=w/len(t),expectancy_R=t.R.mean(),total_R=t.R.sum(),
        wilson_lo=lo,wilson_hi=hi,lo_above_BE=bool(lo>BE),max_consec_losses=mx,
        maxDD_usdt=float(dd.max()),maxDD_pct=float(dd.max()/peak[i]*100) if dd.max()>0 else 0.0,final_equity=float(eq.iloc[-1]))

def dedup_nonoverlap(t):
    """one open position at a time: skip signals while a 10-min trade is open"""
    t=t.sort_values("bar"); keep=[]; busy=-1
    for _,r in t.iterrows():
        if r.bar>busy: keep.append(r); busy=r.bar+2
    return pd.DataFrame(keep)

def run(path,params=P,slip=0.0):
    d=load(path)
    aL,aS=setupA(d,params["dist"],params["crosses"],params["window"])
    bL,bS=setupB(d,params["body_mult"],params["med_win"],params["imp_bars"],params["pb_max"],params["pb_retr"])
    tA=trades(d,aL,aS,"A",slip); tB=trades(d,bL,bS,"B",slip)
    # combined: one trade per bar; if A and B disagree on side at same bar -> skip (conflict)
    L=aL|bL; S=aS|bS; conflict=L&S
    tC=trades(d,L&~conflict,S&~conflict,"A+B",slip)
    va=np.arange(len(d))
    both=((aL|aS)&(bL|bS)); both[:WARMUP]=False; both[-2:]=False
    return d,tA,tB,tC,int(both.sum()),int((conflict[WARMUP:-2]).sum())
