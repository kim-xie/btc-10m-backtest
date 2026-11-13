"""Pre-registered AND combo: volume-spike fade ∩ fade_atr15_highvol.

Locked in PRE_REGISTERED.md at 2026-11-11 17:04 Beijing time.
Official Bonferroni m=31 locked at 17:26 Beijing time, before results.
No parameter changes. No orders. Confirmation window is the entire sample
(no IS/OOS split). Ties (exit==entry, or exit==slipped entry) count as losses.

Usage:
  python3 backtest.py
  python3 backtest.py --truncate-utc 2023-01-01T00:00:00Z
"""
from __future__ import annotations

import json
import os
import sys
from statistics import NormalDist

import numpy as np
import pandas as pd

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DIR, "data")
CSV_1M = os.path.join(DATA, "klines_btcusdt_1m.csv")
CSV_5M = os.path.join(DATA, "klines_btcusdt_5m.csv")
LOG = os.path.join(DATA, "fetch_log.json")

WIN, LOSS = 0.8, -1.0
BE = 1.0 / 1.8  # 55.55...%
STEP_5M = 300_000
DAY_MS = 86_400_000
SIGNAL_START = pd.Timestamp("2022-01-01 00:00:00", tz="UTC")
SIGNAL_END = pd.Timestamp("2024-11-11 07:35:00", tz="UTC")  # exclusive
# ATR / high-vol constants locked with fade_atr15_highvol
ATR_PERIOD = 14
ATR_FADE_TH = 1.5
NATR_LOOKBACK = 200  # median over i-200..i-1
VOL_MULT = 3.0
HIST_DAYS = (1, 2, 3, 4, 5)

Z95 = NormalDist().inv_cdf(1 - 0.025)
# Official m=31: two-sided Wilson at level 1-0.05/31
Z_M31 = NormalDist().inv_cdf(1 - (0.05 / 31) / 2)
Z_M25 = NormalDist().inv_cdf(1 - (0.05 / 25) / 2)


def _parse_truncate(argv):
    if "--truncate-utc" not in argv:
        return None
    s = argv[argv.index("--truncate-utc") + 1]
    return pd.Timestamp(s)
    # pandas accepts 2023-01-01T00:00:00Z


def load(truncate_utc=None):
    df1 = pd.read_csv(CSV_1M)
    df5 = pd.read_csv(CSV_5M)
    df1["open_time"] = pd.to_numeric(df1["open_time"])
    df5["open_time"] = pd.to_numeric(df5["open_time"])
    df1["open_time_utc"] = pd.to_datetime(df1["open_time_utc"], utc=True)
    df5["open_time_utc"] = pd.to_datetime(df5["open_time_utc"], utc=True)
    df5["close_time_utc"] = pd.to_datetime(df5["close_time_utc"], utc=True)
    if "close_ms" not in df5.columns:
        df5["close_ms"] = df5["open_time"] + STEP_5M
    if truncate_utc is not None:
        if truncate_utc.tzinfo is None:
            truncate_utc = truncate_utc.tz_localize("UTC")
        # Keep 1m bars that close at or before the cut (open < cut).
        cut_ms = int(truncate_utc.value // 10**6)
        df1 = df1[df1.open_time < cut_ms].copy()
        # Re-aggregate 5m from the truncated 1m so incomplete buckets drop.
        df5 = _agg_from_1m(df1)
    return df1.reset_index(drop=True), df5.reset_index(drop=True)


def _agg_from_1m(df1: pd.DataFrame) -> pd.DataFrame:
    d = df1.copy().sort_values("open_time")
    d["bucket"] = (d.open_time // STEP_5M) * STEP_5M
    stats = d.groupby("bucket", sort=True).agg(
        n=("open_time", "size"),
        first_open=("open_time", "min"),
        last_open=("open_time", "max"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        close_time=("close_time", "last") if "close_time" in d.columns else ("open_time", "max"),
    )
    ok = (stats["n"] == 5) & (stats["first_open"] == stats.index) & (
        stats["last_open"] == stats.index + 4 * 60_000
    )
    out = stats.loc[ok].reset_index().rename(columns={"bucket": "open_time"})
    if out.empty:
        return out
    out["open_time"] = out["open_time"].astype("int64")
    out["open_time_utc"] = pd.to_datetime(out.open_time, unit="ms", utc=True)
    out["close_ms"] = out.open_time + STEP_5M
    out["close_time_utc"] = pd.to_datetime(out.close_ms, unit="ms", utc=True)
    return out


def wilder_atr_by_segment(df5: pd.DataFrame) -> pd.Series:
    """Wilder ATR via ewm(alpha=1/14, adjust=False), same as locked fade_atr15_highvol.

    Computed separately on each contiguous 5m run so a gap does not stitch
    bars as if they were adjacent. First TR of a run is high-low.
    """
    n = len(df5)
    atr = np.full(n, np.nan)
    if n == 0:
        return pd.Series(atr, index=df5.index)
    opens = df5.open_time.to_numpy()
    high = df5.high.to_numpy(dtype=float)
    low = df5.low.to_numpy(dtype=float)
    close = df5.close.to_numpy(dtype=float)
    # segment starts
    starts = [0]
    for i in range(1, n):
        if opens[i] != opens[i - 1] + STEP_5M:
            starts.append(i)
    starts.append(n)
    alpha = 1.0 / ATR_PERIOD
    for a, b in zip(starts[:-1], starts[1:]):
        tr = np.empty(b - a)
        tr[0] = high[a] - low[a]
        for k, i in enumerate(range(a + 1, b), start=1):
            tr[k] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        run = np.empty_like(tr)
        run[0] = tr[0]
        for k in range(1, len(tr)):
            run[k] = (1 - alpha) * run[k - 1] + alpha * tr[k]
        atr[a:b] = run
    return pd.Series(atr, index=df5.index)


def natr_prior_median(natr: pd.Series, opens: np.ndarray) -> pd.Series:
    """Median of ATR14/close over bars i-200..i-1, only if those 200 bars all exist
    and are the immediately preceding 200 slots (no gap in the lookback).
    """
    x = natr.to_numpy(dtype=float)
    n = len(x)
    med = np.full(n, np.nan)
    # rolling median over previous 200 defined values is not enough: we also
    # require the prior 200 calendar 5m slots to be present as rows i-200..i-1.
    for i in range(NATR_LOOKBACK, n):
        if opens[i] != opens[i - 1] + STEP_5M:
            continue
        if opens[i - NATR_LOOKBACK] != opens[i] - NATR_LOOKBACK * STEP_5M:
            continue
        window = x[i - NATR_LOOKBACK:i]
        if np.isnan(window).any():
            continue
        med[i] = np.median(window)
    return pd.Series(med, index=natr.index)


def condition2(df5: pd.DataFrame) -> np.ndarray:
    """fade_atr15_highvol: +1 long / -1 short / 0 none. ATR may include the signal bar."""
    atr = wilder_atr_by_segment(df5)
    close = df5.close.to_numpy(dtype=float)
    open_ = df5.open.to_numpy(dtype=float)
    natr = atr.to_numpy(dtype=float) / close
    med = natr_prior_median(pd.Series(natr), df5.open_time.to_numpy()).to_numpy()
    sig = np.zeros(len(df5), dtype=int)
    ok = (~np.isnan(atr.to_numpy())) & (~np.isnan(med)) & (atr.to_numpy() != 0)
    body = (close - open_) / atr.to_numpy()
    highvol = natr > med
    short = ok & highvol & (body >= ATR_FADE_TH)
    long = ok & highvol & (body <= -ATR_FADE_TH)
    sig[short] = -1
    sig[long] = 1
    return sig, atr, med, natr


def condition1(df5: pd.DataFrame) -> np.ndarray:
    """Volume-spike fade on 5m close t, using this bar's volume as vol_5m(t)
    (equivalent to the sum of the 5 one-minute bars opening t-5m..t-1m).
    Same-clock history: vol at t-1d..t-5d, all five required.
    Direction from close(t) vs close(t-5m). Equal close -> no trigger.
    """
    n = len(df5)
    sig = np.zeros(n, dtype=int)
    opens = df5.open_time.to_numpy()
    vol = df5.volume.to_numpy(dtype=float)
    close = df5.close.to_numpy(dtype=float)
    # map open_time -> row index for O(1) history lookup
    idx_of = {int(o): i for i, o in enumerate(opens)}
    vol_hist = np.full((n, 5), np.nan)
    have_hist = np.zeros(n, dtype=bool)
    for i, o in enumerate(opens):
        vals = []
        ok = True
        for k, d in enumerate(HIST_DAYS):
            j = idx_of.get(int(o) - d * DAY_MS)
            if j is None:
                ok = False
                break
            vals.append(vol[j])
            vol_hist[i, k] = vol[j]
        if ok:
            have_hist[i] = True
            # previous 5m close must exist (adjacent slot) for a fade direction
            prev = idx_of.get(int(o) - STEP_5M)
            if prev is None:
                continue
            med = float(np.median(vals))
            if not (vol[i] > VOL_MULT * med):
                continue
            if close[i] > close[prev]:
                sig[i] = -1
            elif close[i] < close[prev]:
                sig[i] = 1
            # equal -> no trigger
    return sig, have_hist, vol_hist


def combine_and_trades(df5: pd.DataFrame, s1, s2, truncate_utc=None):
    opens = df5.open_time.to_numpy()
    close_ms = (df5["close_ms"] if "close_ms" in df5.columns else opens + STEP_5M).to_numpy()
    close = df5.close.to_numpy(dtype=float)
    n = len(df5)
    idx_of = {int(o): i for i, o in enumerate(opens)}
    rows = []
    for i in range(n):
        # signal at the 5m close instant
        t_close = int(close_ms[i])
        t_close_ts = pd.Timestamp(t_close, unit="ms", tz="UTC")
        if t_close_ts < SIGNAL_START or t_close_ts >= SIGNAL_END:
            continue
        if truncate_utc is not None:
            # signals strictly before the cut
            cut = truncate_utc if truncate_utc.tzinfo is not None else truncate_utc.tz_localize("UTC")
            if t_close_ts >= cut:
                continue
        if s1[i] == 0 or s2[i] == 0 or s1[i] != s2[i]:
            continue
        # exit = close of the 2nd 5m bar after the signal bar
        exit_open = int(opens[i]) + 2 * STEP_5M
        j = idx_of.get(exit_open)
        if j is None:
            continue  # missing exit bar: skip
        # if truncated, exit must also be available (exit close <= cut? "exits also available")
        if truncate_utc is not None:
            cut = truncate_utc if truncate_utc.tzinfo is not None else truncate_utc.tz_localize("UTC")
            exit_close_ts = pd.Timestamp(int(close_ms[j]), unit="ms", tz="UTC")
            if exit_close_ts > cut:
                # bar available in truncated file only if its 1m constituents closed by the cut
                # We re-aggregated from truncated 1m, so if j exists the exit bar is complete
                # and its close is at exit_open+5m. If that close is after the cut, the bar
                # would not have been aggregated (1m opens must be < cut, last 1m of this
                # bar opens at exit_open+4m, closes at exit_open+5m). If j exists, it's ok.
                pass
        rows.append({
            "bar": i,
            "signal_open_ms": int(opens[i]),
            "signal_close_utc": str(t_close_ts),
            "signal_open_utc": str(pd.Timestamp(int(opens[i]), unit="ms", tz="UTC")),
            "exit_open_ms": exit_open,
            "exit_close_utc": str(pd.Timestamp(int(close_ms[j]), unit="ms", tz="UTC")),
            "side": "long" if s1[i] > 0 else "short",
            "dir": int(s1[i]),
            "entry": float(close[i]),
            "exit": float(close[j]),
            "s1": int(s1[i]),
            "s2": int(s2[i]),
        })
    return pd.DataFrame(rows)


def apply_slip(trades: pd.DataFrame, bp: float) -> pd.DataFrame:
    t = trades.copy()
    # adverse entry only: long pays more, short sells cheaper
    t["entry_adj"] = t.entry * (1.0 + t.dir * (bp / 1e4))
    # win if price moved in our favor vs slipped entry; equal is a loss
    mv = (t.exit - t.entry_adj) * t.dir
    t["result"] = np.where(mv > 0, "W", "L")  # mv==0 -> L (forced loss)
    t["R"] = np.where(t.result == "W", WIN, LOSS)
    t["slip_bp"] = bp
    return t


def nonoverlap(t: pd.DataFrame) -> pd.DataFrame:
    """Next entry allowed at or after the previous exit bar's close.

    Exit bar open = signal_open + 10m; exit close = signal_open + 15m.
    A new signal whose 5m close is at that same instant (bar open = previous
    exit_open) is allowed. Implemented as next signal_open_ms >= previous exit_open_ms.
    Trades must already be in signal-time order.
    """
    if t.empty:
        t = t.copy()
        t["nonoverlap"] = pd.Series(dtype=bool)
        return t
    keep = []
    next_ok = -1
    for o, e in zip(t.signal_open_ms.to_numpy(), t.exit_open_ms.to_numpy()):
        if o >= next_ok:
            keep.append(True)
            next_ok = int(e)  # may enter on the exit bar itself
        else:
            keep.append(False)
    out = t.copy()
    out["nonoverlap"] = keep
    return out


def wilson(k, n, z):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    den = 1.0 + z * z / n
    ctr = (p + z * z / (2.0 * n)) / den
    hw = z * ((p * (1.0 - p) / n + z * z / (4.0 * n * n)) ** 0.5) / den
    return (ctr - hw, ctr + hw)


def max_drawdown_R(R: np.ndarray):
    """Equity starts at 0. Return (max DD as most-negative peak-to-trough,
    min running sum). DD is in R."""
    if len(R) == 0:
        return float("nan"), float("nan")
    eq = np.cumsum(R)
    peak = np.maximum.accumulate(np.concatenate(([0.0], eq)))[1:]
    dd = eq - peak
    return float(dd.min()), float(eq.min())


def longest_losing_streak(results) -> int:
    mx = cur = 0
    for r in results:
        if r == "L":
            cur += 1
            mx = max(mx, cur)
        else:  # W resets; there are no official ties
            cur = 0
    return mx


def metrics(t: pd.DataFrame) -> dict:
    if t is None or len(t) == 0:
        return {
            "N": 0, "W": 0, "L": 0,
            "win_rate": float("nan"),
            "wilson95_lo": float("nan"), "wilson95_hi": float("nan"),
            "bonf_m31_lo": float("nan"), "bonf_m31_hi": float("nan"),
            "bonf_m25_lo": float("nan"), "bonf_m25_hi": float("nan"),
            "exp_R": float("nan"), "total_R": 0.0,
            "max_dd_R": float("nan"), "min_running_sum_R": float("nan"),
            "max_loss_streak": 0,
        }
    w = int((t.result == "W").sum())
    l = int((t.result == "L").sum())
    n = w + l
    assert n == len(t)
    p = w / n if n else float("nan")
    lo, hi = wilson(w, n, Z95)
    lo31, hi31 = wilson(w, n, Z_M31)
    lo25, hi25 = wilson(w, n, Z_M25)
    mdd, minsum = max_drawdown_R(t.R.to_numpy(dtype=float))
    return {
        "N": n, "W": w, "L": l,
        "win_rate": p,
        "wilson95_lo": lo, "wilson95_hi": hi,
        "bonf_m31_lo": lo31, "bonf_m31_hi": hi31,
        "bonf_m25_lo": lo25, "bonf_m25_hi": hi25,
        "exp_R": float(t.R.mean()) if n else float("nan"),
        "total_R": float(t.R.sum()),
        "max_dd_R": mdd, "min_running_sum_R": minsum,
        "max_loss_streak": longest_losing_streak(t.result.to_numpy()),
    }


def verdict(m1: dict) -> dict:
    """Official pass/fail uses 1bp metrics and Bonferroni m=31.
    Also flag if the rule already fails even before Bonferroni / regardless of m.
    """
    n_ok = m1["N"] >= 200
    exp_ok = (m1["exp_R"] == m1["exp_R"]) and (m1["exp_R"] > 0)
    unadj_ok = (m1["wilson95_lo"] == m1["wilson95_lo"]) and (m1["wilson95_lo"] > BE)
    bonf31_ok = (m1["bonf_m31_lo"] == m1["bonf_m31_lo"]) and (m1["bonf_m31_lo"] > BE)
    already_fail_no_m = (not n_ok) or (not exp_ok) or (not unadj_ok)
    passed = n_ok and exp_ok and bonf31_ok
    if passed:
        status = "PASS"
    elif already_fail_no_m:
        status = "FAIL"
    else:
        # N and 1bp expectancy and unadjusted Wilson pass, but m=31 bound does not
        status = "FAIL"
    return {
        "status": status,
        "N_ge_200": n_ok,
        "exp_1bp_positive": exp_ok,
        "unadj_wilson95_lo_gt_be": unadj_ok,
        "bonf_m31_lo_gt_be": bonf31_ok,
        "already_rejected_regardless_of_m": already_fail_no_m,
        "official_m": 31,
        "breakeven": BE,
    }


def appendix_splits(t: pd.DataFrame) -> dict:
    if t.empty:
        return {}
    out = {"side": {}, "session_utc": {}}
    for side, g in t.groupby("side"):
        out["side"][side] = metrics(g)
    hr = pd.to_datetime(t.signal_close_utc, utc=True).dt.hour

    def sess(h):
        if h < 8:
            return "Asia 00-08"
        if h < 16:
            return "Europe 08-16"
        return "US 16-24"

    for s, g in t.groupby(hr.map(sess)):
        out["session_utc"][s] = metrics(g)
    return out


def run(truncate_utc=None):
    df1, df5 = load(truncate_utc)
    s1, have_hist, vol_hist = condition1(df5)
    s2, atr, med, natr = condition2(df5)
    df5 = df5.copy()
    df5["atr14"] = atr.to_numpy()
    df5["natr"] = natr
    df5["natr_med200"] = med
    df5["s1"] = s1
    df5["s2"] = s2
    trades0 = combine_and_trades(df5, s1, s2, truncate_utc=truncate_utc)
    return df1, df5, trades0, have_hist


def main():
    truncate_utc = _parse_truncate(sys.argv)
    df1, df5, trades0, have_hist = run(truncate_utc)
    if truncate_utc is not None:
        out = os.path.join(DIR, "trades_truncated.csv")
        trades0.to_csv(out, index=False)
        print(f"truncated at {truncate_utc} signals={len(trades0)} -> {out}", flush=True)
        return

    # annotate 0/1/2 bp on the primary (overlapping) trade list
    t0 = apply_slip(trades0, 0)
    t1 = apply_slip(trades0, 1)
    t2 = apply_slip(trades0, 2)
    base = t0.copy()
    base["result_0bp"] = t0.result
    base["R_0bp"] = t0.R
    base["entry_adj_0bp"] = t0.entry_adj
    base["result_1bp"] = t1.result
    base["R_1bp"] = t1.R
    base["entry_adj_1bp"] = t1.entry_adj
    base["result_2bp"] = t2.result
    base["R_2bp"] = t2.R
    base["entry_adj_2bp"] = t2.entry_adj
    no_flags = nonoverlap(trades0)
    no_trades = no_flags[no_flags.nonoverlap].copy() if len(no_flags) else no_flags
    base["nonoverlap"] = no_flags["nonoverlap"].to_numpy() if len(base) else []
    base.to_csv(os.path.join(DIR, "trades.csv"), index=False)

    sens_rows = []
    results_by_bp = {}
    for bp, t in [(0, t0), (1, t1), (2, t2)]:
        no = apply_slip(no_trades, bp) if len(no_trades) else apply_slip(trades0.iloc[0:0], bp)
        m_all = metrics(t)
        m_no = metrics(no)
        results_by_bp[bp] = {"all": m_all, "nonoverlap": m_no}
        for lab, m in [("all", m_all), ("nonoverlap", m_no)]:
            sens_rows.append({"slip_bp": bp, "set": lab, **m})
    pd.DataFrame(sens_rows).to_csv(os.path.join(DIR, "sensitivity.csv"), index=False)

    v = verdict(results_by_bp[1]["all"])
    # helper so n_flow can be documented: m = 25 + n_flow, here n_flow=6, m=31
    fetch_log = json.load(open(LOG)) if os.path.exists(LOG) else {}
    meta = {
        "rule": "combo_vol_spike_fade_AND_fade_atr15_highvol",
        "locked_at": "2026-11-11 17:04 Asia/Shanghai",
        "m_locked_at": "2026-11-11 17:26 Asia/Shanghai",
        "data_1m": CSV_1M,
        "data_5m": CSV_5M,
        "rows_1m": int(len(df1)),
        "rows_5m": int(len(df5)),
        "first_1m": str(df1.open_time_utc.iloc[0]) if len(df1) else None,
        "last_1m": str(df1.open_time_utc.iloc[-1]) if len(df1) else None,
        "first_5m": str(df5.open_time_utc.iloc[0]) if len(df5) else None,
        "last_5m": str(df5.open_time_utc.iloc[-1]) if len(df5) else None,
        "signal_window": "[2022-01-01 00:00:00 UTC, 2024-11-11 07:35:00 UTC)",
        "signal_time_is": "5m close instant (entry = that close)",
        "last_possible_signal_close": "2024-11-11 07:30:00 UTC",
        "payoff": {"win": WIN, "loss": LOSS, "tie_policy": "exit==entry (or slipped entry) counts as loss -1"},
        "breakeven": BE,
        "z95": Z95,
        "z_bonf_m31": Z_M31,
        "z_bonf_m25": Z_M25,
        "bonferroni": {
            "formula": "m = 25 + n_flow; official n_flow=6 so m=31",
            "n_flow": 6,
            "m_official": 31,
            "m_sensitivity": 25,
            "confidence_level_official": 1 - 0.05 / 31,
            "note": "m=25 (n_flow unknown) kept as a sensitivity column only; verdict uses m=31",
        },
        "role_counts_5m": df5.role.value_counts().to_dict() if "role" in df5.columns else None,
        "fetch_log": fetch_log,
        "condition1_bars_with_full_5day_hist": int(have_hist.sum()),
        "condition1_triggers": int((s1 := df5.s1.to_numpy() != 0).sum()) if "s1" in df5.columns else None,
        "condition2_triggers": int((df5.s2.to_numpy() != 0).sum()) if "s2" in df5.columns else None,
        "and_agree_in_window_before_exit_filter": None,
    }
    # count AND agrees in window regardless of exit (for transparency)
    if "role" in df5.columns:
        win = df5.role == "signal_window"
        and_agree = win & (df5.s1 != 0) & (df5.s1 == df5.s2)
        meta["and_agree_in_signal_window"] = int(and_agree.sum())
        meta["s1_in_signal_window"] = int(((df5.s1 != 0) & win).sum())
        meta["s2_in_signal_window"] = int(((df5.s2 != 0) & win).sum())

    payload = {
        "meta": meta,
        "verdict": v,
        "metrics": results_by_bp,
        "appendix_0bp": appendix_splits(t0),
        "appendix_1bp": appendix_splits(t1),
    }
    json.dump(payload, open(os.path.join(DIR, "results.json"), "w"), indent=1, default=float)
    print(json.dumps({
        "N": results_by_bp[0]["all"]["N"],
        "W": results_by_bp[0]["all"]["W"],
        "L": results_by_bp[0]["all"]["L"],
        "win_rate_0bp": results_by_bp[0]["all"]["win_rate"],
        "exp_0bp": results_by_bp[0]["all"]["exp_R"],
        "exp_1bp": results_by_bp[1]["all"]["exp_R"],
        "wilson95": [results_by_bp[0]["all"]["wilson95_lo"], results_by_bp[0]["all"]["wilson95_hi"]],
        "bonf_m31_lo_0bp": results_by_bp[0]["all"]["bonf_m31_lo"],
        "bonf_m31_lo_1bp": results_by_bp[1]["all"]["bonf_m31_lo"],
        "verdict": v,
        "nonoverlap_N": results_by_bp[0]["nonoverlap"]["N"],
    }, indent=1, default=float), flush=True)


if __name__ == "__main__":
    main()
