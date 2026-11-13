"""Batch-3 backtest: exactly the 10 locked strategies in PRE_REGISTERED.md. No parameter changes.
Usage: python3 backtest.py [--truncate-ms T]  (truncation mode only used for the lookahead check)"""
import json, os, sys, math
import numpy as np, pandas as pd

D = os.path.dirname(os.path.abspath(__file__))
START_MS = 1731310500000
CUT_MS = int(pd.Timestamp("2026-04-06 07:35:00", tz="UTC").value // 10**6)
STEP = 300_000
WIN, LOSS = 0.8, -1.0
BE = 1 / 1.8  # 0.5556
WARMUP, ROLL = 300, 288
Z95, ZBONF = 1.959964, 2.807034  # Bonferroni m=10 two-sided: z_{1-0.05/20}

DEPS = {
    "tbr_extreme_fade": ["btc_spot"], "tbr_extreme_follow": ["btc_spot"],
    "vol_spike_fade": ["btc_spot"], "vol_spike_follow": ["btc_spot"], "absorption": ["btc_spot"],
    "perp_tbr_div": ["btc_spot", "btc_perp"], "prem_z_fade": ["btc_spot", "btc_prem"],
    "basis_z_fade": ["btc_spot", "btc_perp"], "eth_lead": ["btc_spot", "eth_spot"],
    "trades_spike_fade": ["btc_spot"],
}
STRATS = list(DEPS)

def load(truncate_ms=None):
    log = json.load(open(os.path.join(D, "data", "fetch_log.json")))
    ser = {}
    for k, v in log.items():
        if not v.get("ok"): continue
        df = pd.read_csv(os.path.join(D, "data", f"{k}.csv"))
        if truncate_ms is not None: df = df[df.open_time <= truncate_ms]
        ser[k] = df.set_index("open_time")
    last = ser["btc_spot"].index.max()
    grid = np.arange(START_MS, last + STEP, STEP)
    return {k: v.reindex(grid) for k, v in ser.items()}, grid, log

def prior_z(x):
    """z vs the prior 288 grid bars excluding current; requires all 288 present (gap -> NaN -> no signal)."""
    m = x.rolling(ROLL, min_periods=ROLL).mean().shift(1)
    s = x.rolling(ROLL, min_periods=ROLL).std().shift(1)
    return (x - m) / s.replace(0, np.nan)

def features(S):
    sp = S["btc_spot"]
    F = pd.DataFrame(index=sp.index)
    vol = sp.volume
    F["tbr"] = (sp.taker_buy_base / vol).where(vol > 0)
    F["vz"] = prior_z(vol)
    F["r"] = sp.close / sp.open - 1
    F["trz"] = prior_z(sp.trades)
    if "btc_perp" in S:
        pp = S["btc_perp"]
        F["ptbr"] = (pp.taker_buy_base / pp.volume).where(pp.volume > 0)
        F["basis"] = pp.close / sp.close - 1
        F["basis_z"] = prior_z(F["basis"])
    if "btc_prem" in S:
        F["prem"] = S["btc_prem"].close
        F["prem_z"] = prior_z(F["prem"])
    if "eth_spot" in S:
        e = S["eth_spot"]
        F["eth_r"] = e.close / e.open - 1
    return F

def signals(F):
    """+1 long / -1 short / 0 none. NaN comparisons are False -> no signal when data missing."""
    sig = {}
    t, vz, r = F.tbr, F.vz, F.r
    def mk(longc, shortc):
        s = np.zeros(len(F), dtype=int)
        s[np.asarray(longc.fillna(False), bool)] = 1
        s[np.asarray(shortc.fillna(False), bool)] = -1
        return s
    sig["tbr_extreme_fade"] = mk(t <= 0.30, t >= 0.70)
    sig["tbr_extreme_follow"] = mk(t >= 0.70, t <= 0.30)
    spike = (vz >= 4) & (r.abs() >= 0.0015)
    sig["vol_spike_fade"] = mk(spike & (r < 0), spike & (r > 0))
    sig["vol_spike_follow"] = mk(spike & (r > 0), spike & (r < 0))
    ab = (vz >= 3) & (r.abs() <= 0.0003)
    sig["absorption"] = mk(ab & (t <= 0.40), ab & (t >= 0.60))
    if "ptbr" in F:
        sig["perp_tbr_div"] = mk((F.ptbr <= 0.40) & (t >= 0.55), (F.ptbr >= 0.60) & (t <= 0.45))
        sig["basis_z_fade"] = mk(F.basis_z <= -3, F.basis_z >= 3)
    if "prem_z" in F:
        sig["prem_z_fade"] = mk(F.prem_z <= -3, F.prem_z >= 3)
    if "eth_r" in F:
        er, br = F.eth_r, F.r
        lag = (er.abs() >= 0.0030) & (br * np.sign(er) <= 0.5 * er.abs()) & br.notna()
        sig["eth_lead"] = mk(lag & (er > 0), lag & (er < 0))
    tsp = (F.trz >= 4) & (r.abs() >= 0.0010)
    sig["trades_spike_fade"] = mk(tsp & (r < 0), tsp & (r > 0))
    return sig

def build_trades(S, grid, sig):
    close = S["btc_spot"].close.values
    n = len(grid)
    exitc = np.full(n, np.nan); exitc[:-2] = close[2:]
    rows = []
    for name, s in sig.items():
        s = s.copy(); s[:WARMUP] = 0
        idx = np.where((s != 0) & ~np.isnan(close) & ~np.isnan(exitc))[0]
        for i in idx:
            rows.append((name, int(grid[i]), int(s[i]), close[i], exitc[i], int(i)))
    T = pd.DataFrame(rows, columns=["strategy", "open_time", "dir", "entry", "exit", "bar"])
    T["time_utc"] = pd.to_datetime(T.open_time, unit="ms", utc=True)
    T["sample"] = np.where(T.open_time >= CUT_MS, "OOS", "IS")
    # non-overlap: greedy, next entry allowed at bar >= previous bar + 2 (previous exit close)
    T["nonoverlap"] = False
    for name, g in T.groupby("strategy"):
        last = -10; keep = []
        for j, b in zip(g.index, g.bar):
            if b >= last + 2: keep.append(j); last = b
        T.loc[keep, "nonoverlap"] = True
    return T

def outcome(T, bp):
    adj = T.entry * (1 + T.dir * bp / 1e4)
    d = np.sign(T.exit - adj) * T.dir
    return d.astype(int)  # 1 win, -1 loss, 0 tie

def wilson(w, n, z):
    if n == 0: return (None, None)
    p = w / n; den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)

def stats(o):
    o = np.asarray(o)
    W, L, Tt = int((o == 1).sum()), int((o == -1).sum()), int((o == 0).sum())
    n = W + L
    streak = mx = 0
    for v in o:
        if v == -1: streak += 1; mx = max(mx, streak)
        elif v == 1: streak = 0          # ties neither extend nor reset
    lo, hi = wilson(W, n, Z95); blo, bhi = wilson(W, n, ZBONF)
    return {"N": int(len(o)), "decided": n, "W": W, "L": L, "T": Tt,
            "win_rate": W / n if n else None, "wilson_lo": lo, "wilson_hi": hi, "bonf_lo": blo, "bonf_hi": bhi,
            "exp_R": (WIN * W + LOSS * L) / len(o) if len(o) else None, "max_loss_streak": mx}

def session(h):
    return "Asia 00-08" if h < 8 else ("Europe 08-13" if h < 13 else ("US 13-21" if h < 21 else "Late 21-24"))

def main():
    trunc = None
    if "--truncate-ms" in sys.argv: trunc = int(sys.argv[sys.argv.index("--truncate-ms") + 1])
    S, grid, log = load(trunc)
    F = features(S); sig = signals(F)
    T = build_trades(S, grid, sig)
    if trunc is not None:
        T.to_csv(os.path.join(D, "trades_truncated.csv"), index=False); return
    for bp in (0, 1, 2): T[f"out_{bp}bp"] = outcome(T, bp)
    res = {"cut_ms": CUT_MS, "breakeven": BE, "grid_first": int(grid[0]), "grid_last": int(grid[-1]),
           "grid_bars": int(len(grid)), "strategies": {}}
    # gaps per series
    gaps = {}
    for k, v in S.items():
        miss = v.close.isna().values
        runs, i = [], 0
        while i < len(miss):
            if miss[i]:
                j = i
                while j < len(miss) and miss[j]: j += 1
                runs.append([int(grid[i]), int(grid[j - 1]), j - i]); i = j
            else: i += 1
        gaps[k] = {"missing_bars": int(miss.sum()), "runs": runs}
    res["gaps"] = gaps; res["fetch_log"] = log
    sens = []
    for name in STRATS:
        missing = [d for d in DEPS[name] if not log.get(d, {}).get("ok")]
        if missing or name not in sig:
            res["strategies"][name] = {"status": "NOT RUN", "missing": missing}; continue
        g = T[T.strategy == name]
        r = {}
        for lab, sub in [("all", g), ("IS", g[g["sample"] == "IS"]), ("OOS", g[g["sample"] == "OOS"]),
                         ("nonoverlap_all", g[g.nonoverlap]), ("nonoverlap_IS", g[g.nonoverlap & (g["sample"] == "IS")]),
                         ("nonoverlap_OOS", g[g.nonoverlap & (g["sample"] == "OOS")])]:
            r[lab] = stats(sub.out_0bp)
            for bp in (0, 1, 2):
                st = stats(sub[f"out_{bp}bp"])
                sens.append({"strategy": name, "subset": lab, "slippage_bp": bp, **st})
                r[lab][f"exp_R_{bp}bp"] = st["exp_R"]; r[lab][f"win_rate_{bp}bp"] = st["win_rate"]
                r[lab][f"wilson_lo_{bp}bp"] = st["wilson_lo"]
        o = r["OOS"]; i_ = r["IS"]
        passed = (o["decided"] >= 200 and o["wilson_lo"] is not None and o["wilson_lo"] > BE
                  and i_["win_rate"] is not None and i_["win_rate"] > BE and o["exp_R_1bp"] is not None and o["exp_R_1bp"] > 0)
        status = "PASS" if passed else ("WATCH" if (o["win_rate"] or 0) > BE else "FAIL")
        r["status"] = status
        r["bonf_pass"] = bool(o["bonf_lo"] is not None and o["bonf_lo"] > BE)
        # appendix splits (OOS, 0bp)
        go = g[g["sample"] == "OOS"]
        r["appendix_OOS"] = {
            "direction": {("long" if k == 1 else "short"): stats(v.out_0bp) for k, v in go.groupby("dir")},
            "session_utc": {k: stats(v.out_0bp) for k, v in go.groupby(go.time_utc.dt.hour.map(session))},
            "month": {str(k): stats(v.out_0bp) for k, v in go.groupby(go.time_utc.dt.strftime("%Y-%m"))},
        }
        res["strategies"][name] = r
    T.drop(columns=["bar"]).to_csv(os.path.join(D, "trades.csv"), index=False)
    pd.DataFrame(sens).to_csv(os.path.join(D, "sensitivity.csv"), index=False)
    json.dump(res, open(os.path.join(D, "results.json"), "w"), indent=1, default=str)
    print({k: v.get("status") for k, v in res["strategies"].items()})

if __name__ == "__main__":
    main()
