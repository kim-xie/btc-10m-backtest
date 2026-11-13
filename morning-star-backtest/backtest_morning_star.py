#!/usr/bin/env python3
"""Morning Star candlestick pattern -> Polymarket BTC Up/Down backtest.

Data: Binance BTCUSDT 1m (data.binance.vision) + Polymarket gamma/CLOB markets CSVs
      reused from /workspace/fisher-backtest.

Truth: Polymarket resolution (Chainlink BTC/USD), NOT Binance OHLC direction.
Entry (primary): first CLOB mid in (T, T+60] (~20s after open) + 0.005 half-spread + taker fee.
Fee: 0.07 * p * (1-p) when feesEnabled (breakeven ~52.3% near p=0.50).

No lookahead: pattern uses only candles whose close time <= market open T.
Primary alignment: last 3 TF-bars ending at T (C3 closes at T; known by ~T+20s entry).
Also report strict lag: last 3 TF-bars ending at T-TF (C3 closes one bar before open).
"""
from __future__ import annotations
import glob, os, json
import numpy as np
import pandas as pd
from scipy.stats import binomtest

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
rng = np.random.default_rng(42)

# -------------------- load 1m klines --------------------
fs = sorted(glob.glob('kl/BTCUSDT-1m-*.csv'))
k = pd.concat([pd.read_csv(f, header=None, usecols=[0, 1, 2, 3, 4]) for f in fs])
k.columns = ['t', 'o', 'h', 'l', 'c']
k['t'] = (k.t // 1_000_000).astype(int)
k = k.drop_duplicates('t').set_index('t').sort_index()
KMIN, KMAX = int(k.index.min()), int(k.index.max()) + 60

def resample(sec: int) -> pd.DataFrame:
    g = k.copy()
    g['b'] = (g.index // sec) * sec
    r = g.groupby('b').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('c', 'size'))
    return r[r.n == sec // 60]

# -------------------- Morning Star definition (fixed textbook) --------------------
# Documented in README. Thresholds fixed a priori (not tuned on test).
# C1: long bearish — body >= max(MED_BODY_MULT * median_body_20, ATR_BODY_MULT * ATR14)
# C2: small body   — body <= SMALL_BODY_FRAC * median_body_20
#      crypto gap substitute: C2 open <= C1 close  OR  C2 body entirely in lower half of C1 range
#      and C2 close is not a strong recovery into C1 (C2.close <= C1 body midpoint)
# C3: long bullish — body >= max(MED_BODY_MULT * median_body_20, ATR_BODY_MULT * ATR14)
#      closes well into C1: C3.close >= C1 body midpoint (50% recovery into C1 body)
#      and C3.close > C2.close (confirmation)
MED_BODY_MULT = 1.0          # "long" >= 1x recent median body
ATR_BODY_MULT = 0.6          # and/or >= 0.6 * ATR14
SMALL_BODY_FRAC = 0.5        # star body <= 0.5x median body
SMA_N = 20
TREND_LOOKBACK = 10          # bars before C1 for HH/HL / level change
NEAR_SMA_ATR = 0.25          # |close-SMA| < 0.25*ATR -> 震荡 if not clear HH/HL

def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_c = df.c.shift(1)
    tr = pd.concat([(df.h - df.l), (df.h - prev_c).abs(), (df.l - prev_c).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()

def detect_morning_star(bars: pd.DataFrame) -> pd.DataFrame:
    """Return DataFrame indexed by C3 open time with pattern flags + trend label.
    Pattern is attributed to the open time of candle 3 (completion bar).
    """
    o, h, l, c = bars.o, bars.h, bars.l, bars.c
    body = (c - o).abs()
    med20 = body.rolling(20, min_periods=20).median()
    a14 = atr(bars, 14)
    long_thr = np.maximum(MED_BODY_MULT * med20, ATR_BODY_MULT * a14)
    small_thr = SMALL_BODY_FRAC * med20

    # shift so row i corresponds to C3 at i; C2=i-1; C1=i-2
    o1, h1, l1, c1 = o.shift(2), h.shift(2), l.shift(2), c.shift(2)
    o2, h2, l2, c2 = o.shift(1), h.shift(1), l.shift(1), c.shift(1)
    o3, h3, l3, c3 = o, h, l, c
    b1, b2, b3 = body.shift(2), body.shift(1), body
    thr_long = long_thr  # use rolling stats as of C3 (includes C1-C3; conservative enough)
    # Prefer thresholds as of C1 end to avoid peeking via C2/C3 into med/ATR — use shift(2)
    thr_long_c1 = long_thr.shift(2)
    thr_small_c1 = small_thr.shift(2)
    # If early NaN, fall back
    thr_long_use = thr_long_c1.fillna(thr_long)
    thr_small_use = thr_small_c1.fillna(small_thr)

    bear1 = (c1 < o1) & (b1 >= thr_long_use)
    small2 = b2 <= thr_small_use
    # crypto gap rule: open2 <= close1 OR body2 entirely in lower half of C1 range
    c1_mid_range = (h1 + l1) / 2.0
    c1_body_lo = np.minimum(o1, c1)
    c1_body_hi = np.maximum(o1, c1)
    c1_body_mid = (o1 + c1) / 2.0
    body2_hi = np.maximum(o2, c2)
    body2_lo = np.minimum(o2, c2)
    gap_or_lower = (o2 <= c1) | (body2_hi <= c1_mid_range)
    # star should not already reverse deep into C1
    star_not_recovered = c2 <= c1_body_mid
    bull3 = (c3 > o3) & (b3 >= thr_long_use)
    recovery = c3 >= c1_body_mid
    confirm = c3 > c2

    is_ms = bear1 & small2 & gap_or_lower & star_not_recovered & bull3 & recovery & confirm

    # Trend as of C1 close (before star/confirmation) — no lookahead into C2/C3 for regime
    sma = c.rolling(SMA_N, min_periods=SMA_N).mean()
    sma1 = sma.shift(2)
    a1 = a14.shift(2)
    c_ago = c.shift(2 + TREND_LOOKBACK)
    # HH/HL over lookback window ending at C1
    hh = h.rolling(TREND_LOOKBACK, min_periods=TREND_LOOKBACK).max().shift(2)
    ll = l.rolling(TREND_LOOKBACK, min_periods=TREND_LOOKBACK).min().shift(2)
    prev_hh = h.shift(TREND_LOOKBACK).rolling(TREND_LOOKBACK, min_periods=TREND_LOOKBACK).max().shift(2)
    prev_ll = l.shift(TREND_LOOKBACK).rolling(TREND_LOOKBACK, min_periods=TREND_LOOKBACK).min().shift(2)

    below_sma = c1 < sma1
    above_sma = c1 > sma1
    near = (c1 - sma1).abs() <= (NEAR_SMA_ATR * a1)
    lower_lows = ll < prev_ll
    higher_highs = hh > prev_hh
    price_down = c1 < c_ago
    price_up = c1 > c_ago

    # Priority: clear downtrend / uptrend / else 震荡
    trend = np.where(
        below_sma & (lower_lows | price_down) & ~near, '下跌',
        np.where(above_sma & (higher_highs | price_up) & ~near, '上涨', '震荡')
    )
    # If near SMA, force 震荡 even if other flags
    trend = np.where(near.fillna(True), '震荡', trend)

    out = pd.DataFrame({
        'is_ms': is_ms.fillna(False).astype(bool),
        'trend': trend,
        'c1_o': o1, 'c1_c': c1, 'c2_o': o2, 'c2_c': c2, 'c3_o': o3, 'c3_c': c3,
        'c1_body': b1, 'c2_body': b2, 'c3_body': b3,
        'c1_body_mid': c1_body_mid,
        'sma20_c1': sma1,
    }, index=bars.index)
    return out

# -------------------- PnL / stats --------------------
def pnl_per100(side_up, up_px, won, half_spread, fee_rate=0.07):
    p = np.where(side_up, up_px, 1.0 - up_px) + half_spread
    p = np.clip(p.astype(float), 0.01, 0.99)
    cost = p + fee_rate * p * (1.0 - p)
    shares = 100.0 / cost
    pnl = np.where(won, shares - 100.0, -100.0)
    return pnl, p, cost

def wilson_ci(w, n):
    if n == 0:
        return (np.nan, np.nan)
    ci = binomtest(w, n).proportion_ci(method='wilson')
    return ci.low, ci.high

def summarize(name, d, side_up_mask, hs=0.005, fee_rate=0.07):
    """side_up_mask: boolean array, True = take Up trade on that row; length = len(d)."""
    m = np.asarray(side_up_mask, dtype=bool)
    n = int(m.sum())
    if n == 0:
        return dict(strategy=name, n=0, win_rate=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                    p_vs_50=np.nan, breakeven_wr=np.nan, p_vs_breakeven_greater=np.nan,
                    avg_pnl_per_100=np.nan, total_pnl_per_100stake=0.0, max_dd=0.0, avg_entry=np.nan)
    sub = d.loc[m]
    won = (sub.res.values == 'Up')
    # Always buy Up for morning star (bullish reversal)
    pnl, p, cost = pnl_per100(np.ones(n, dtype=bool), sub.up_px_entry.values, won, hs, fee_rate)
    # For baselines that short, caller passes different mask via separate path — see summarize_signed
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.r_[0.0, cum])
    dd = (peak[1:] - cum).max() if n else 0.0
    w = int(won.sum())
    ci_lo, ci_hi = wilson_ci(w, n)
    be = float(np.mean(cost))
    return dict(
        strategy=name, n=n, win_rate=w / n, ci_lo=ci_lo, ci_hi=ci_hi,
        p_vs_50=float(binomtest(w, n, 0.5).pvalue),
        breakeven_wr=be,
        p_vs_breakeven_greater=float(binomtest(w, n, min(be, 0.999), alternative='greater').pvalue),
        avg_pnl_per_100=float(pnl.mean()), total_pnl_per_100stake=float(pnl.sum()),
        max_dd=float(dd), avg_entry=float(p.mean()),
    )

def summarize_signed(name, d, sig, hs=0.005, fee_rate=0.07):
    """sig: +1 Up, -1 Down, 0 skip."""
    sig = np.asarray(sig, dtype=float)
    m = sig != 0
    n = int(m.sum())
    if n == 0:
        return dict(strategy=name, n=0, win_rate=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                    p_vs_50=np.nan, breakeven_wr=np.nan, p_vs_breakeven_greater=np.nan,
                    avg_pnl_per_100=np.nan, total_pnl_per_100stake=0.0, max_dd=0.0, avg_entry=np.nan)
    up = sig[m] > 0
    won = up == (d.res.values[m] == 'Up')
    pnl, p, cost = pnl_per100(up, d.up_px_entry.values[m], won, hs, fee_rate)
    cum = np.cumsum(pnl)
    peak = np.maximum.accumulate(np.r_[0.0, cum])
    dd = (peak[1:] - cum).max()
    w = int(won.sum())
    ci_lo, ci_hi = wilson_ci(w, n)
    be = float(np.mean(cost))
    return dict(
        strategy=name, n=n, win_rate=w / n, ci_lo=ci_lo, ci_hi=ci_hi,
        p_vs_50=float(binomtest(w, n, 0.5).pvalue),
        breakeven_wr=be,
        p_vs_breakeven_greater=float(binomtest(w, n, min(be, 0.999), alternative='greater').pvalue),
        avg_pnl_per_100=float(pnl.mean()), total_pnl_per_100stake=float(pnl.sum()),
        max_dd=float(dd), avg_entry=float(p.mean()),
    )

# -------------------- run one market kind --------------------
def load_markets(kind: str, step: int, entry: str) -> pd.DataFrame:
    d = pd.read_csv(f'markets_{kind}.csv')
    if entry == 'post':
        d = d.assign(up_px_entry=d.up_px_post)
    d = d[d.res.isin(['Up', 'Down']) & d.up_px_entry.notna()].copy()
    # need enough history for SMA20 + lookback on TF bars + pattern
    warm = 40 * max(step, 900)
    d = d[(d['T'] - warm >= KMIN) & (d['T'] + step <= KMAX)].sort_values('T').reset_index(drop=True)
    # fee: use actual fee rate if present else 0.07 when feesEnabled
    d['fee_rate'] = d.apply(
        lambda r: float(r.feeRate) if pd.notna(r.get('feeRate')) and bool(r.get('feesEnabled'))
        else (0.07 if bool(r.get('feesEnabled')) else 0.0), axis=1)
    return d

def attach_signals(d: pd.DataFrame, ms: pd.DataFrame, tf: int, lag_bars: int) -> pd.DataFrame:
    """Map each market T to the pattern completing at key = T - lag_bars*tf.
    lag_bars=0 => C3 closes at T; lag_bars=1 => C3 closes at T-tf.
    """
    key = ((d['T'].values - lag_bars * tf) // tf) * tf
    # For lag_bars=0, key=T which is C3 open time if bars are aligned to T.
    # Our ms index is bar open time; C3 at open=key means C3 covers [key, key+tf) and closes at key+tf.
    # So for C3 to close at T: key+tf == T => key = T - tf. THAT is lag conceptually of one bar open.
    #
    # Clarification:
    # Bar open time B covers [B, B+tf), closes at B+tf.
    # Last 3 bars ending at T (C3 closes at T): C3 open = T-tf, C2 open = T-2tf, C1 open = T-3tf.
    # So we look up ms at index (T - tf).
    #
    # Strict before open (C3 closes at T-tf): C3 open = T-2tf.
    # lag_bars in this function: 1 => C3 closes at T; 2 => C3 closes at T-tf.
    c3_open = ((d['T'].values - lag_bars * tf) // tf) * tf
    hit = ms.reindex(c3_open)
    out = d.copy()
    out['c3_open'] = c3_open
    out['is_ms'] = hit['is_ms'].fillna(False).values
    out['trend'] = hit['trend'].fillna('震荡').values
    out['c1_c'] = hit['c1_c'].values
    out['c3_c'] = hit['c3_c'].values
    out['c1_body_mid'] = hit['c1_body_mid'].values
    out['sma20_c1'] = hit['sma20_c1'].values
    return out

def binance_agree(d: pd.DataFrame, step: int) -> float:
    bo = k.o.reindex(d['T']).values
    bc = k.c.reindex(d['T'] + step - 60).values
    return float(np.mean((bc >= bo) == (d.res.values == 'Up')))

def run_kind(kind: str, step: int, entry: str = 'post'):
    d0 = load_markets(kind, step, entry)
    agree = binance_agree(d0, step)
    split = int(len(d0) * 0.6)
    print(f"\n{'='*80}")
    print(f"### {kind} entry={entry}: {len(d0)} markets "
          f"{pd.to_datetime(d0['T'].min(), unit='s')} .. {pd.to_datetime(d0['T'].max(), unit='s')} UTC; "
          f"Up share {np.mean(d0.res=='Up'):.4f}; Binance-vs-Chainlink agree {agree:.4f}; "
          f"train_end_excl {pd.to_datetime(d0['T'].iloc[split], unit='s')}")

    rows = []
    trade_rows = []
    # Primary TF = market window; also try 1m for 5m markets
    tfs = [step]
    if step == 300:
        tfs.append(60)   # 1m candles, last 3 closed before open
    if step == 900:
        tfs.append(300)  # also 5m pattern into 15m market

    bars_cache = {tf: resample(tf) for tf in sorted(set(tfs + [60, 300, 900]))}
    ms_cache = {tf: detect_morning_star(bars_cache[tf]) for tf in tfs}

    configs = []
    for tf in tfs:
        # lag_bars=1: C3 closes at T (aligned); lag_bars=2: C3 closes at T-tf (strict before)
        for lag_bars, lag_name in ((1, 'aligned_C3_close_at_T'), (2, 'strict_C3_close_at_T_minus_tf')):
            configs.append((tf, lag_bars, lag_name))

    for tf, lag_bars, lag_name in configs:
        ms = ms_cache[tf]
        d = attach_signals(d0, ms, tf, lag_bars)
        # drop rows where we lacked enough history for detection (NaN c1)
        valid = d['c1_c'].notna()
        d = d[valid].reset_index(drop=True)
        split_i = int(len(d) * 0.6)
        parts = {'train': d.iloc[:split_i], 'test': d.iloc[split_i:], 'all': d}

        base_name = f"morning_star tf={tf//60}m {lag_name}"

        for part, sub in parts.items():
            # overall MS -> buy Up
            mask = sub.is_ms.values
            # per-row fee
            def stats_ms(name, mask_, sub_):
                # use per-row fees via loop aggregation
                m = np.asarray(mask_, dtype=bool)
                n = int(m.sum())
                if n == 0:
                    return dict(strategy=name, n=0, win_rate=np.nan, ci_lo=np.nan, ci_hi=np.nan,
                                p_vs_50=np.nan, breakeven_wr=np.nan, p_vs_breakeven_greater=np.nan,
                                avg_pnl_per_100=np.nan, total_pnl_per_100stake=0.0, max_dd=0.0, avg_entry=np.nan)
                ss = sub_.loc[m]
                won = (ss.res.values == 'Up')
                fees = ss.fee_rate.values.astype(float)
                # vectorized with potentially varying fee — compute per row
                p_entry = ss.up_px_entry.values.astype(float) + 0.005
                p_entry = np.clip(p_entry, 0.01, 0.99)
                cost = p_entry + fees * p_entry * (1 - p_entry)
                shares = 100.0 / cost
                pnl = np.where(won, shares - 100.0, -100.0)
                cum = np.cumsum(pnl)
                dd = (np.maximum.accumulate(np.r_[0.0, cum])[1:] - cum).max()
                w = int(won.sum())
                ci_lo, ci_hi = wilson_ci(w, n)
                be = float(cost.mean())
                return dict(strategy=name, n=n, win_rate=w/n, ci_lo=ci_lo, ci_hi=ci_hi,
                            p_vs_50=float(binomtest(w, n, 0.5).pvalue),
                            breakeven_wr=be,
                            p_vs_breakeven_greater=float(binomtest(w, n, min(be, 0.999), alternative='greater').pvalue),
                            avg_pnl_per_100=float(pnl.mean()), total_pnl_per_100stake=float(pnl.sum()),
                            max_dd=float(dd), avg_entry=float(p_entry.mean()))

            r = stats_ms(base_name, mask, sub)
            r.update(part=part, market=kind, scenario=entry, trend='ALL', tf_sec=tf, lag=lag_name)
            rows.append(r)

            for tr_lab, tr_cn in (('下跌', '下跌趋势中的早晨之星'), ('上涨', '上涨趋势中的早晨之星'), ('震荡', '震荡中的早晨之星')):
                m2 = mask & (sub.trend.values == tr_lab)
                r2 = stats_ms(f"{base_name} | {tr_cn}", m2, sub)
                r2.update(part=part, market=kind, scenario=entry, trend=tr_lab, tf_sec=tf, lag=lag_name)
                rows.append(r2)

            # Optional: short AFTER morning star in uptrend (does the pattern fail?)
            m_up = mask & (sub.trend.values == '上涨')
            if m_up.any():
                # fade: buy Down
                ss = sub.loc[m_up]
                won = (ss.res.values == 'Down')
                fees = ss.fee_rate.values.astype(float)
                # buy Down at 1 - up_px
                p_entry = (1.0 - ss.up_px_entry.values.astype(float)) + 0.005
                p_entry = np.clip(p_entry, 0.01, 0.99)
                cost = p_entry + fees * p_entry * (1 - p_entry)
                shares = 100.0 / cost
                pnl = np.where(won, shares - 100.0, -100.0)
                n = len(ss); w = int(won.sum())
                cum = np.cumsum(pnl)
                dd = (np.maximum.accumulate(np.r_[0.0, cum])[1:] - cum).max() if n else 0
                ci_lo, ci_hi = wilson_ci(w, n)
                be = float(cost.mean()) if n else np.nan
                rows.append(dict(
                    strategy=f"{base_name} | FADE uptrend MS (buy Down)",
                    n=n, win_rate=w/n if n else np.nan, ci_lo=ci_lo, ci_hi=ci_hi,
                    p_vs_50=float(binomtest(w, n, 0.5).pvalue) if n else np.nan,
                    breakeven_wr=be,
                    p_vs_breakeven_greater=float(binomtest(w, n, min(be, 0.999), alternative='greater').pvalue) if n else np.nan,
                    avg_pnl_per_100=float(pnl.mean()) if n else np.nan,
                    total_pnl_per_100stake=float(pnl.sum()) if n else 0.0,
                    max_dd=float(dd), avg_entry=float(p_entry.mean()) if n else np.nan,
                    part=part, market=kind, scenario=entry, trend='上涨_fade', tf_sec=tf, lag=lag_name,
                ))

            # save trades for test/all primary aligned
            if part in ('test', 'all') and lag_name == 'aligned_C3_close_at_T' and tf == step:
                ss = sub[sub.is_ms].copy()
                ss['part'] = part
                ss['market'] = kind
                ss['tf_sec'] = tf
                ss['lag'] = lag_name
                ss['trend_cn'] = ss.trend.map({'下跌': '下跌趋势中的早晨之星', '上涨': '上涨趋势中的早晨之星', '震荡': '震荡中的早晨之星'})
                # pnl
                won = (ss.res.values == 'Up')
                fees = ss.fee_rate.values.astype(float)
                p_entry = np.clip(ss.up_px_entry.values.astype(float) + 0.005, 0.01, 0.99)
                cost = p_entry + fees * p_entry * (1 - p_entry)
                pnl = np.where(won, 100.0 / cost - 100.0, -100.0)
                ss['entry_px_with_spread'] = p_entry
                ss['pnl_per_100'] = pnl
                ss['won'] = won
                trade_rows.append(ss)

        # baselines on same d universe (use train/test of this d)
        d_base = d
        split_i = int(len(d_base) * 0.6)
        for part, sub in (('train', d_base.iloc[:split_i]), ('test', d_base.iloc[split_i:]), ('all', d_base)):
            # always Up
            sig = np.ones(len(sub))
            rows.append({**summarize_signed('baseline always Up', sub, sig),
                         'part': part, 'market': kind, 'scenario': entry, 'trend': '-', 'tf_sec': tf, 'lag': lag_name})
            # last candle momentum on same TF
            pb = bars_cache[tf]
            kk = ((sub['T'].values - tf) // tf) * tf  # last closed bar ending at T
            prev = np.sign(pb.c.reindex(kk).values - pb.o.reindex(kk).values)
            rows.append({**summarize_signed(f'baseline last-{tf//60}m momentum', sub, np.nan_to_num(prev)),
                         'part': part, 'market': kind, 'scenario': entry, 'trend': '-', 'tf_sec': tf, 'lag': lag_name})
            # coin flip
            rows.append({**summarize_signed('baseline coin flip', sub, rng.choice([-1.0, 1.0], len(sub))),
                         'part': part, 'market': kind, 'scenario': entry, 'trend': '-', 'tf_sec': tf, 'lag': lag_name})

        # print primary test summary
        te = parts['test']
        n_ms = int(te.is_ms.sum())
        print(f"\n-- tf={tf//60}m {lag_name}: test MS signals={n_ms} / {len(te)}")
        for tr in ('ALL', '下跌', '上涨', '震荡'):
            subr = [r for r in rows if r.get('tf_sec') == tf and r.get('lag') == lag_name
                    and r.get('part') == 'test' and r.get('market') == kind and r.get('trend') == tr
                    and str(r.get('strategy', '')).startswith('morning_star')]
            if subr:
                x = subr[0]
                print(f"   trend={tr}: n={x['n']} wr={x['win_rate'] if x['n'] else float('nan'):.4f} "
                      f"CI=({x['ci_lo'] if x['n'] else float('nan'):.3f},{x['ci_hi'] if x['n'] else float('nan'):.3f}) "
                      f"avgPnL={x['avg_pnl_per_100'] if x['n'] else float('nan'):.2f} "
                      f"be={x['breakeven_wr'] if x['n'] else float('nan'):.4f}")

    return pd.DataFrame(rows), (pd.concat(trade_rows) if trade_rows else pd.DataFrame()), agree, d0

def main():
    all_rows = []
    all_trades = []
    agrees = {}
    for kind, step in (('5m', 300), ('15m', 900)):
        if not os.path.exists(f'markets_{kind}.csv'):
            continue
        res, trades, agree, _ = run_kind(kind, step, entry='post')
        all_rows.append(res)
        if len(trades):
            all_trades.append(trades)
        agrees[kind] = agree

    results = pd.concat(all_rows, ignore_index=True)
    results.to_csv('results.csv', index=False)

    # Example timestamps table (test set, primary aligned, market TF)
    examples = []
    if all_trades:
        trades = pd.concat(all_trades, ignore_index=True)
        trades.to_csv('trades.csv', index=False)
        for kind in ('5m', '15m'):
            for tr in ('下跌', '上涨', '震荡'):
                sub = trades[(trades.market == kind) & (trades.part == 'test') & (trades.trend == tr)]
                take = sub.head(5)
                for _, r in take.iterrows():
                    examples.append({
                        'market': kind,
                        'trend': r.trend_cn,
                        'T_utc': pd.to_datetime(r['T'], unit='s').strftime('%Y-%m-%d %H:%M:%S'),
                        'T_shanghai': (pd.to_datetime(r['T'], unit='s') + pd.Timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S'),
                        'c3_open': int(r.c3_open),
                        'res': r.res,
                        'up_px_post': r.up_px_entry,
                        'won': bool(r.won),
                        'pnl_per_100': round(float(r.pnl_per_100), 2),
                        'slug': r.slug,
                    })
        pd.DataFrame(examples).to_csv('examples.csv', index=False)

    # Compact summary for README / parent
    summary_lines = []
    summary_lines.append(f"Binance vs Chainlink agreement: {agrees}")
    focus = results[
        results.strategy.str.startswith('morning_star')
        & results.strategy.str.contains('aligned_C3_close_at_T')
        & ~results.strategy.str.contains('FADE')
        & (results.part == 'test')
        & (
            ((results.market == '5m') & (results.tf_sec == 300))
            | ((results.market == '15m') & (results.tf_sec == 900))
        )
    ]
    summary_lines.append('\nPRIMARY TEST (aligned, market TF, post entry):')
    cols = ['market', 'trend', 'n', 'win_rate', 'ci_lo', 'ci_hi', 'breakeven_wr',
            'avg_pnl_per_100', 'total_pnl_per_100stake', 'max_dd', 'p_vs_50', 'p_vs_breakeven_greater']
    summary_lines.append(focus[cols].round(4).to_string(index=False))

    # also train for same
    focus_tr = results[
        results.strategy.str.startswith('morning_star')
        & results.strategy.str.contains('aligned_C3_close_at_T')
        & ~results.strategy.str.contains('FADE')
        & (results.part == 'train')
        & (
            ((results.market == '5m') & (results.tf_sec == 300))
            | ((results.market == '15m') & (results.tf_sec == 900))
        )
    ]
    summary_lines.append('\nPRIMARY TRAIN (aligned, market TF, post entry):')
    summary_lines.append(focus_tr[cols].round(4).to_string(index=False))

    # baselines test
    base = results[
        results.strategy.str.startswith('baseline')
        & (results.part == 'test')
        & results.lag.eq('aligned_C3_close_at_T')
        & (
            ((results.market == '5m') & (results.tf_sec == 300))
            | ((results.market == '15m') & (results.tf_sec == 900))
        )
    ]
    summary_lines.append('\nBASELINES TEST:')
    summary_lines.append(base[['market', 'strategy', 'n', 'win_rate', 'avg_pnl_per_100', 'max_dd']].round(4).to_string(index=False))

    # strict + alt TF brief
    alt = results[
        results.strategy.str.startswith('morning_star')
        & ~results.strategy.str.contains('FADE')
        & (results.part == 'test')
        & (results.trend == 'ALL')
    ]
    summary_lines.append('\nALL configs TEST trend=ALL:')
    summary_lines.append(alt[['market', 'strategy', 'n', 'win_rate', 'avg_pnl_per_100', 'breakeven_wr']].round(4).to_string(index=False))

    # downtrend focus across configs
    dn = results[
        results.strategy.str.startswith('morning_star')
        & ~results.strategy.str.contains('FADE')
        & (results.part == 'test')
        & (results.trend == '下跌')
    ]
    summary_lines.append('\nDOWNTREND TEST:')
    summary_lines.append(dn[['market', 'strategy', 'n', 'win_rate', 'ci_lo', 'ci_hi', 'avg_pnl_per_100', 'breakeven_wr']].round(4).to_string(index=False))

    text = '\n'.join(summary_lines)
    with open('summary.txt', 'w') as f:
        f.write(text)
    print('\n\n' + text)
    print('\nWrote results.csv, trades.csv, examples.csv, summary.txt')

if __name__ == '__main__':
    main()
