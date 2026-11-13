"""Pre-registered BTCUSDT 10m-settlement backtest. Parameters locked per PRE_REGISTERED.md. No trading."""
import numpy as np, pandas as pd
from statistics import NormalDist

DATA = "/workspace/btc-10m-pattern-backtest/klines_btcusdt_5m_24m.csv"
SPLIT = pd.Timestamp("2026-04-06 07:35:00+00:00")
WARMUP = 200
WIN, LOSS = 0.8, -1.0
BE = 1 / 1.8
Z95 = 1.959963984540054
Z_BONF = NormalDist().inv_cdf(1 - (0.05 / 12) / 2)  # two-sided CI at level 1-0.05/12 (~99.58%)
STRATS = ["fade_atr15", "fade_atr20", "fade_ret40", "fade_3same", "rsi_fade", "cont_atr15",
          "break_20", "ema20_cross", "inside_break", "pin_fade", "fade_atr15_lowvol", "fade_atr15_highvol"]

def load():
    d = pd.read_csv(DATA)
    d["t"] = pd.to_datetime(d.open_time_utc, utc=True)
    assert d.open_time.diff().dropna().eq(300000).all(), "gap in data"
    return d.reset_index(drop=True)

def indicators(d):
    o, h, l, c = d.open, d.high, d.low, d.close
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    tr.iloc[0] = h.iloc[0] - l.iloc[0]
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()  # Wilder smoothing
    d["ema20"] = c.ewm(alpha=2 / 21, adjust=False).mean()
    diff = c.diff()
    g = diff.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    ls = (-diff.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["rsi"] = 100 - 100 / (1 + g / ls)
    d["natr"] = d.atr / c
    d["natr_med200"] = d.natr.rolling(200).median().shift(1)  # i-200..i-1
    return d

def signals(d):
    """Return dict name -> np.array of +1 long / -1 short / 0 none; uses only data up to bar i."""
    o, h, l, c = d.open, d.high, d.low, d.close
    pc = c.shift(1)
    atr = d.atr
    z = lambda: pd.Series(0, index=d.index)
    S = {}
    bm = (c - o) / atr
    def fade(th):
        s = z(); s[bm >= th] = -1; s[bm <= -th] = 1; return s
    S["fade_atr15"] = fade(1.5)
    S["fade_atr20"] = fade(2.0)
    r = (c - pc) / pc
    s = z(); s[r >= 0.004] = -1; s[r <= -0.004] = 1; S["fade_ret40"] = s
    up3 = (c > c.shift(1)) & (c.shift(1) > c.shift(2)) & (c.shift(2) > c.shift(3))
    dn3 = (c < c.shift(1)) & (c.shift(1) < c.shift(2)) & (c.shift(2) < c.shift(3))
    big = (c - c.shift(3)).abs() / atr >= 1.5
    s = z(); s[up3 & big] = -1; s[dn3 & big] = 1; S["fade_3same"] = s
    s = z(); s[d.rsi >= 80] = -1; s[d.rsi <= 20] = 1; S["rsi_fade"] = s
    S["cont_atr15"] = -fade(1.5)
    hh = h.shift(1).rolling(20).max(); ll = l.shift(1).rolling(20).min()
    s = z(); s[c > hh] = 1; s[c < ll] = -1; S["break_20"] = s
    e = d.ema20
    s = z(); s[(pc <= e.shift(1)) & (c > e)] = 1; s[(pc >= e.shift(1)) & (c < e)] = -1; S["ema20_cross"] = s
    inside = (h.shift(1) < h.shift(2)) & (l.shift(1) > l.shift(2))
    bu = inside & (c > h.shift(1)); bd = inside & (c < l.shift(1))
    s = z(); s[bu & ~bd] = 1; s[bd & ~bu] = -1; S["inside_break"] = s
    body = (c - o).abs(); rng = h - l
    lw = pd.concat([o, c], axis=1).min(axis=1) - l
    uw = h - pd.concat([o, c], axis=1).max(axis=1)
    lo10 = l.shift(1).rolling(10).min(); hi10 = h.shift(1).rolling(10).max()
    pl = (body > 0) & (lw >= 2 * body) & (c >= l + 2 / 3 * rng) & (l <= lo10)
    ps = (body > 0) & (uw >= 2 * body) & (c <= l + 1 / 3 * rng) & (h >= hi10)
    s = z(); s[pl & ~ps] = 1; s[ps & ~pl] = -1; S["pin_fade"] = s
    S["fade_atr15_lowvol"] = S["fade_atr15"].where(d.natr < d.natr_med200, 0)
    S["fade_atr15_highvol"] = S["fade_atr15"].where(d.natr > d.natr_med200, 0)
    out = {}
    n = len(d)
    for k in STRATS:
        a = S[k].fillna(0).astype(int).to_numpy().copy()
        a[:WARMUP] = 0
        a[n - 2:] = 0  # need exit bar i+2
        out[k] = a
    return out

def make_trades(d, sig, name, slip_bps=0.0):
    idx = np.nonzero(sig)[0]
    side = sig[idx]
    entry = d.close.to_numpy()[idx]
    exitp = d.close.to_numpy()[idx + 2]
    adj = entry * (1 + side * slip_bps / 1e4)  # adverse on entry only
    mv = (exitp - adj) * side
    res = np.where(mv > 0, "W", np.where(mv < 0, "L", "T"))
    R = np.where(res == "W", WIN, np.where(res == "L", LOSS, 0.0))
    tt = d.t.reset_index(drop=True)
    t = pd.DataFrame({"strategy": name, "bar": idx,
                      "signal_open_utc": tt.iloc[idx].to_numpy(),
                      "entry_time_utc": (tt.iloc[idx] + pd.Timedelta(minutes=5)).to_numpy(),
                      "side": np.where(side > 0, "long", "short"), "entry": entry, "entry_adj": adj,
                      "exit_time_utc": (tt.iloc[idx + 2] + pd.Timedelta(minutes=5)).to_numpy(),
                      "exit": exitp, "result": res, "R": R})
    t["entry_close_utc"] = t.entry_time_utc
    t["split"] = np.where(pd.to_datetime(t.signal_open_utc, utc=True) >= SPLIT, "OOS", "IS")
    hr = pd.to_datetime(t.entry_close_utc, utc=True).dt.hour
    t["session"] = np.where(hr < 8, "Asia", np.where(hr < 16, "Europe", "US"))
    return t

def nonoverlap(t):
    keep, nxt = [], -1
    for b in t.bar.to_numpy():
        if b >= nxt:
            keep.append(True); nxt = b + 2  # hold ends at close of bar+2; may enter again at that close
        else:
            keep.append(False)
    return t[np.array(keep, dtype=bool)] if len(t) else t

def wilson(k, n, z=Z95):
    if n == 0: return (float("nan"), float("nan"))
    p = k / n; den = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / den
    hw = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (ctr - hw, ctr + hw)

def metrics(t):
    w = int((t.result == "W").sum()); l = int((t.result == "L").sum()); ti = int((t.result == "T").sum())
    dec = w + l; n = w + l + ti
    p = w / dec if dec else float("nan")
    lo, hi = wilson(w, dec); blo, bhi = wilson(w, dec, Z_BONF)
    mx = cur = 0
    for r in t.result:  # ties neither extend nor reset the losing streak
        if r == "L": cur += 1; mx = max(mx, cur)
        elif r == "W": cur = 0
    return dict(N=n, N_dec=dec, W=w, L=l, T=ti, win_rate_ex_ties=p,
                win_rate_incl_ties=(w / n if n else float("nan")),
                wilson95_lo=lo, wilson95_hi=hi, bonf_lo=blo, bonf_hi=bhi,
                exp_R_per_trade=(float(t.R.mean()) if n else float("nan")),
                total_R=float(t.R.sum()), max_consec_losses=mx)
