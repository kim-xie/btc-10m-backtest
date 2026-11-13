"""Locked rule ema50_3cross_18. Do not change parameters after seeing results.
Usage: python3 backtest.py [--truncate-ms T]
"""
import json, os, math
import numpy as np
import pandas as pd
from statistics import NormalDist

D = os.path.dirname(os.path.abspath(__file__))
STEP = 300_000
WIN, LOSS = 0.8, -1.0
BE = 1 / 1.8
WARMUP = 300
EMA_SPAN = 50
WINDOW = 18
Z95 = NormalDist().inv_cdf(1 - 0.025)
ZBONF = NormalDist().inv_cdf(1 - 0.05 / (2 * 32))  # m=32 two-sided
HIST_CSV = "/workspace/btc-10m-combo-vol-highvol/data/klines_btcusdt_5m.csv"
RECENT_CSV = "/workspace/btc-10m-batch3/data/btc_spot.csv"
HIST_FIRST = int(pd.Timestamp("2022-01-01 00:00:00", tz="UTC").value // 10**6)
HIST_LAST_SIGNAL = int(pd.Timestamp("2024-11-11 07:30:00", tz="UTC").value // 10**6)
RECENT_FIRST = int(pd.Timestamp("2024-11-11 07:35:00", tz="UTC").value // 10**6)


def load_ohlc(path):
    df = pd.read_csv(path, usecols=["open_time", "open", "high", "low", "close"])
    df = df.sort_values("open_time").drop_duplicates("open_time").reset_index(drop=True)
    return df


def contiguous_runs(times):
    times = np.asarray(times, dtype=np.int64)
    if len(times) == 0:
        return []
    brk = np.where(np.diff(times) != STEP)[0] + 1
    bounds = np.concatenate(([0], brk, [len(times)]))
    return list(zip(bounds[:-1], bounds[1:]))


def signals_on_run(run, mode="at_least_3"):
    """Return dir series aligned to run index: +1 long, -1 short, 0 none."""
    n = len(run)
    direc = np.zeros(n, dtype=int)
    if n < WARMUP + 2:
        return direc
    close = run["close"].to_numpy(dtype=float)
    opn = run["open"].to_numpy(dtype=float)
    ema = pd.Series(close).ewm(span=EMA_SPAN, adjust=False, min_periods=EMA_SPAN).mean().to_numpy()
    prev_c = np.roll(close, 1)
    prev_e = np.roll(ema, 1)
    prev_c[0] = np.nan
    prev_e[0] = np.nan
    yang_up = (prev_c <= prev_e) & (close > ema) & (close > opn) & np.isfinite(ema) & np.isfinite(prev_e)
    yin_dn = (prev_c >= prev_e) & (close < ema) & (close < opn) & np.isfinite(ema) & np.isfinite(prev_e)
    up_cnt = pd.Series(yang_up.astype(int)).rolling(WINDOW, min_periods=WINDOW).sum().to_numpy()
    dn_cnt = pd.Series(yin_dn.astype(int)).rolling(WINDOW, min_periods=WINDOW).sum().to_numpy()
    if mode == "at_least_3":
        longc = yang_up & (up_cnt >= 3)
        shortc = yin_dn & (dn_cnt >= 3)
    elif mode == "exactly_3":
        longc = yang_up & (up_cnt == 3)
        shortc = yin_dn & (dn_cnt == 3)
    else:
        raise ValueError(mode)
    longc[:WARMUP] = False
    shortc[:WARMUP] = False
    both = longc & shortc
    longc = longc & ~both
    shortc = shortc & ~both
    direc[longc] = 1
    direc[shortc] = -1
    return direc


def build_trades(df, mode, sig_lo, sig_hi):
    """df must be sorted raw bars (gaps allowed). Signals only if 18-bar window and +10m exit exist."""
    rows = []
    times = df["open_time"].to_numpy(dtype=np.int64)
    for a, b in contiguous_runs(times):
        run = df.iloc[a:b].reset_index(drop=True)
        direc = signals_on_run(run, mode)
        t = run["open_time"].to_numpy(dtype=np.int64)
        close = run["close"].to_numpy(dtype=float)
        n = len(run)
        for i in np.where(direc != 0)[0]:
            if t[i] < sig_lo or t[i] > sig_hi:
                continue
            # need exit bar exactly +10 minutes; in a contiguous 5m run, i+2 is that bar if it exists
            j = i + 2
            if j >= n or t[j] != t[i] + 2 * STEP:
                continue
            rows.append((mode, int(t[i]), int(direc[i]), float(close[i]), float(close[j]), int(i)))
    T = pd.DataFrame(rows, columns=["strategy", "open_time", "dir", "entry", "exit", "bar"])
    if T.empty:
        T["time_utc"] = pd.Series(dtype="datetime64[ns, UTC]")
        T["nonoverlap"] = pd.Series(dtype=bool)
        return T
    T["time_utc"] = pd.to_datetime(T.open_time, unit="ms", utc=True)
    T["nonoverlap"] = False
    last = -10
    keep = []
    # bar index is per-run, so nonoverlap must use time, not bar
    last_exit = -10**18
    for j, ot in zip(T.index, T.open_time):
        if ot >= last_exit:
            keep.append(j)
            last_exit = ot + 2 * STEP  # next allowed at the exit bar's close (same open_time as exit)
    T.loc[keep, "nonoverlap"] = True
    return T


def outcome(T, bp):
    adj = T.entry * (1 + T.dir * bp / 1e4)
    d = np.sign(T.exit - adj) * T.dir
    return d.astype(int)


def wilson(w, n, z):
    if n == 0:
        return (None, None)
    p = w / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def stats(o):
    o = np.asarray(o)
    if len(o) == 0:
        return {"N": 0, "decided": 0, "W": 0, "L": 0, "T": 0,
                "win_rate": None, "wilson_lo": None, "wilson_hi": None,
                "bonf_lo": None, "bonf_hi": None, "exp_R": None, "max_loss_streak": 0}
    W, L, Tt = int((o == 1).sum()), int((o == -1).sum()), int((o == 0).sum())
    n = W + L
    streak = mx = 0
    for v in o:
        if v == -1:
            streak += 1
            mx = max(mx, streak)
        elif v == 1:
            streak = 0
    lo, hi = wilson(W, n, Z95)
    blo, bhi = wilson(W, n, ZBONF)
    return {"N": int(len(o)), "decided": n, "W": W, "L": L, "T": Tt,
            "win_rate": W / n if n else None, "wilson_lo": lo, "wilson_hi": hi,
            "bonf_lo": blo, "bonf_hi": bhi,
            "exp_R": (WIN * W + LOSS * L) / len(o) if len(o) else None,
            "max_loss_streak": mx}


def session(h):
    return "Asia 00-08" if h < 8 else ("Europe 08-13" if h < 13 else ("US 13-21" if h < 21 else "Late 21-24"))


def pack(T):
    if T.empty:
        empty = stats([])
        r = {lab: {**empty, "exp_R_0bp": None, "exp_R_1bp": None, "exp_R_2bp": None,
                   "win_rate_0bp": None, "win_rate_1bp": None, "win_rate_2bp": None,
                   "wilson_lo_0bp": None, "wilson_lo_1bp": None, "wilson_lo_2bp": None}
             for lab in ("all", "nonoverlap")}
        r["appendix"] = {"direction": {}, "session_utc": {}, "year": {}, "month": {}}
        return r, []
    for bp in (0, 1, 2):
        T[f"out_{bp}bp"] = outcome(T, bp)
    r = {}
    sens = []
    for lab, sub in (("all", T), ("nonoverlap", T[T.nonoverlap])):
        r[lab] = stats(sub.out_0bp)
        for bp in (0, 1, 2):
            st = stats(sub[f"out_{bp}bp"])
            sens.append({"subset": lab, "slippage_bp": bp, **st})
            r[lab][f"exp_R_{bp}bp"] = st["exp_R"]
            r[lab][f"win_rate_{bp}bp"] = st["win_rate"]
            r[lab][f"wilson_lo_{bp}bp"] = st["wilson_lo"]
    r["appendix"] = {
        "direction": {("long" if k == 1 else "short"): stats(v.out_0bp) for k, v in T.groupby("dir")},
        "session_utc": {k: stats(v.out_0bp) for k, v in T.groupby(T.time_utc.dt.hour.map(session))},
        "year": {str(k): stats(v.out_0bp) for k, v in T.groupby(T.time_utc.dt.year)},
        "month": {str(k): stats(v.out_0bp) for k, v in T.groupby(T.time_utc.dt.strftime("%Y-%m"))},
    }
    return r, sens


def describe_data(path, df):
    times = df["open_time"].to_numpy(dtype=np.int64)
    missing_runs = []
    if len(times):
        grid = np.arange(times[0], times[-1] + STEP, STEP)
        have = set(times.tolist())
        miss = [int(x) for x in grid if int(x) not in have]
        i = 0
        while i < len(miss):
            j = i
            while j + 1 < len(miss) and miss[j + 1] == miss[j] + STEP:
                j += 1
            missing_runs.append([miss[i], miss[j], j - i + 1])
            i = j + 1
    return {
        "path": path,
        "rows": int(len(df)),
        "first_open": int(times[0]) if len(times) else None,
        "last_open": int(times[-1]) if len(times) else None,
        "missing_bars": int(sum(r[2] for r in missing_runs)),
        "missing_runs": missing_runs,
        "contiguous_runs": int(len(contiguous_runs(times))),
    }


def classify(main):
    o = main["all"]
    wr = o["win_rate"]
    passed = (o["decided"] >= 200 and o["wilson_lo"] is not None and o["wilson_lo"] > BE
              and o["exp_R_1bp"] is not None and o["exp_R_1bp"] > 0)
    status = "PASS" if passed else ("WATCH" if (wr or 0) > BE else "FAIL")
    return status, bool(o["bonf_lo"] is not None and o["bonf_lo"] > BE)


def main():
    import sys
    trunc = None
    if "--truncate-ms" in sys.argv:
        trunc = int(sys.argv[sys.argv.index("--truncate-ms") + 1])

    hist_raw = load_ohlc(HIST_CSV)
    recent_raw = load_ohlc(RECENT_CSV)
    if trunc is not None:
        hist_raw = hist_raw[hist_raw.open_time <= trunc].copy()
        recent_raw = recent_raw[recent_raw.open_time <= trunc].copy()

    # last recent signal must have +10m exit in file
    recent_last_signal = int(recent_raw["open_time"].max() - 2 * STEP) if len(recent_raw) else 0

    windows = {
        "historical_oos": (hist_raw, HIST_FIRST, HIST_LAST_SIGNAL, HIST_CSV),
        "recent_confirm": (recent_raw, RECENT_FIRST, recent_last_signal, RECENT_CSV),
    }

    res = {
        "locked_at": "2026-11-11 23:14 Asia/Shanghai",
        "rule": "ema50_3cross_18 at_least_3",
        "breakeven": BE,
        "z95": Z95,
        "zbonf_m32": ZBONF,
        "windows": {},
        "status": None,
        "bonf_pass": None,
    }
    all_trades = []
    all_sens = []

    for wname, (raw, lo, hi, path) in windows.items():
        meta = describe_data(path, raw)
        meta["signal_first"] = lo
        meta["signal_last"] = hi
        official = build_trades(raw, "at_least_3", lo, hi)
        exact = build_trades(raw, "exactly_3", lo, hi)
        off_r, off_s = pack(official.copy())
        ex_r, ex_s = pack(exact.copy())
        for s in off_s:
            s.update({"window": wname, "strategy": "at_least_3"})
        for s in ex_s:
            s.update({"window": wname, "strategy": "exactly_3"})
        all_sens.extend(off_s + ex_s)
        if not official.empty:
            tmp = official.copy()
            tmp["window"] = wname
            all_trades.append(tmp)
        if not exact.empty:
            tmp = exact.copy()
            tmp["window"] = wname
            tmp["strategy"] = "exactly_3_appendix"
            all_trades.append(tmp)
        res["windows"][wname] = {
            "data": meta,
            "at_least_3": off_r,
            "exactly_3_appendix": ex_r,
        }

    main = res["windows"]["historical_oos"]["at_least_3"]
    status, bonf_pass = classify(main)
    res["status"] = status
    res["bonf_pass"] = bonf_pass

    if trunc is not None:
        out = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
        out.to_csv(os.path.join(D, "trades_truncated.csv"), index=False)
        json.dump(res, open(os.path.join(D, "results_truncated.json"), "w"), indent=1, default=str)
        print({"truncated": True, "n": 0 if out.empty else int(len(out[out.strategy == "at_least_3"]))})
        return

    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    if not trades.empty:
        trades.drop(columns=["bar"], errors="ignore").to_csv(os.path.join(D, "trades.csv"), index=False)
    else:
        pd.DataFrame().to_csv(os.path.join(D, "trades.csv"), index=False)
    pd.DataFrame(all_sens).to_csv(os.path.join(D, "sensitivity.csv"), index=False)
    json.dump(res, open(os.path.join(D, "results.json"), "w"), indent=1, default=str)
    print(json.dumps({
        "status": status,
        "bonf_pass": bonf_pass,
        "historical_N": main["all"]["N"],
        "historical_decided": main["all"]["decided"],
        "historical_win_rate": main["all"]["win_rate"],
        "historical_wilson_lo": main["all"]["wilson_lo"],
        "historical_exp_R_1bp": main["all"]["exp_R_1bp"],
        "recent_N": res["windows"]["recent_confirm"]["at_least_3"]["all"]["N"],
        "recent_win_rate": res["windows"]["recent_confirm"]["at_least_3"]["all"]["win_rate"],
    }, default=str, indent=2))


if __name__ == "__main__":
    main()
