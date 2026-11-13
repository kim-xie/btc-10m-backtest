"""Fetch Binance spot BTCUSDT 1m klines and aggregate valid 5m bars.

Window: 2021-12-20 00:00:00 UTC through the 1m bar that closes at
2024-11-11 07:45:00 UTC (open 07:44). Bars with open_time >= 2024-11-11
07:35:00 UTC are settlement-only and must not be used as signals.

Official API: https://api.binance.com/api/v3/klines
Fallback host: https://data-api.binance.vision (same public kline schema)
No invented bars. Resume-safe; remaining range is fetched in parallel.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import requests

DIR = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(DIR, "data")
os.makedirs(DATA, exist_ok=True)

START_MS = int(datetime(2021, 12, 20, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
SETTLE_END_OPEN_MS = int(datetime(2024, 11, 11, 7, 44, 0, tzinfo=timezone.utc).timestamp() * 1000)
SIGNAL_CUT_MS = int(datetime(2024, 11, 11, 7, 35, 0, tzinfo=timezone.utc).timestamp() * 1000)
SIGNAL_START_MS = int(datetime(2022, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)

HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]
PATH = "/api/v3/klines"
COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]
STEP_1M = 60_000
STEP_5M = 300_000
RAW_CSV = os.path.join(DATA, "klines_btcusdt_1m.csv")
AGG_CSV = os.path.join(DATA, "klines_btcusdt_5m.csv")
CKPT = os.path.join(DATA, "fetch_checkpoint.csv")
LOG = os.path.join(DATA, "fetch_log.json")
N_WORKERS = 8


def utc_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def get(params, tries=8):
    last = None
    for host in HOSTS:
        for i in range(tries):
            try:
                r = requests.get(host + PATH, params=params, timeout=30)
                if r.status_code == 200:
                    return r.json(), host
                last = f"{host} HTTP {r.status_code} {r.text[:200]}"
                if r.status_code in (418, 429, 500, 502, 503, 504):
                    time.sleep(min(60, 5 * (2 ** i)))
                else:
                    time.sleep(1 + i)
            except Exception as e:
                last = f"{host} {e!r}"
                time.sleep(1 + i)
    raise RuntimeError(last)


def fetch_range(start_ms: int, end_open_ms: int):
    rows = []
    used = set()
    pages = 0
    cur = start_ms
    while cur <= end_open_ms:
        data, host = get({
            "symbol": "BTCUSDT",
            "interval": "1m",
            "startTime": cur,
            "endTime": end_open_ms,
            "limit": 1000,
        })
        used.add(host)
        pages += 1
        if not data:
            break
        rows.extend(data)
        last_open = int(data[-1][0])
        nxt = last_open + STEP_1M
        if nxt <= cur:
            break
        cur = nxt
        time.sleep(0.05)
    return rows, used, pages


def load_checkpoint():
    if not os.path.exists(CKPT):
        return None
    prev = pd.read_csv(CKPT)
    if not len(prev):
        return None
    return prev


def fetch_1m():
    prev = load_checkpoint()
    start = START_MS
    frames = []
    if prev is not None:
        frames.append(prev)
        start = int(prev.open_time.iloc[-1]) + STEP_1M
        print(f"resuming after {utc_ms(int(prev.open_time.iloc[-1]))} ({len(prev)} rows)", flush=True)
    used = set()
    pages = 0
    if start <= SETTLE_END_OPEN_MS:
        # Split the remaining window into N contiguous ranges.
        span = SETTLE_END_OPEN_MS - start + STEP_1M
        chunk = max(STEP_1M, ((span // N_WORKERS) // STEP_1M) * STEP_1M)
        ranges = []
        cur = start
        while cur <= SETTLE_END_OPEN_MS:
            end = min(cur + chunk - STEP_1M, SETTLE_END_OPEN_MS)
            ranges.append((cur, end))
            cur = end + STEP_1M
        print(f"parallel fetch {len(ranges)} ranges x ~{chunk // STEP_1M} bars, workers={N_WORKERS}", flush=True)
        with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
            futs = {ex.submit(fetch_range, a, b): (a, b) for a, b in ranges}
            done_n = 0
            for fut in as_completed(futs):
                a, b = futs[fut]
                rows, hosts, pgs = fut.result()
                used |= hosts
                pages += pgs
                if rows:
                    frames.append(pd.DataFrame(rows, columns=COLS))
                done_n += 1
                last = rows[-1][0] if rows else b
                print(
                    f"range {done_n}/{len(ranges)} {utc_ms(a)} -> {utc_ms(b)} "
                    f"rows={len(rows)} last={utc_ms(int(last))} hosts={sorted(hosts)}",
                    flush=True,
                )
                # incremental checkpoint of everything gathered so far
                tmp = pd.concat(frames, ignore_index=True)
                tmp = tmp.drop_duplicates("open_time").sort_values("open_time")
                tmp.to_csv(CKPT, index=False)

    if not frames:
        raise RuntimeError("no klines returned")
    df = pd.concat(frames, ignore_index=True)
    df = _finalize_1m(df)
    if os.path.exists(CKPT):
        os.remove(CKPT)
    return df, sorted(used), pages


def _finalize_1m(df: pd.DataFrame):
    # Drop helper columns that may exist from a previous finalized write; keep raw fields.
    drop = [c for c in ("open_time_utc", "close_time_utc", "settlement_only") if c in df.columns]
    if drop:
        df = df.drop(columns=drop)
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="raise")
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    df = df[(df.open_time >= START_MS) & (df.open_time <= SETTLE_END_OPEN_MS)]
    end_close = SETTLE_END_OPEN_MS + STEP_1M - 1
    df = df[df.close_time <= end_close].copy()
    df["open_time_utc"] = pd.to_datetime(df.open_time, unit="ms", utc=True)
    df["close_time_utc"] = pd.to_datetime(df.close_time, unit="ms", utc=True)
    df["settlement_only"] = df.open_time >= SIGNAL_CUT_MS
    return df


def gap_runs(open_times, step):
    if len(open_times) == 0:
        return [], 0
    expected = set(range(int(open_times[0]), int(open_times[-1]) + step, step))
    have = set(int(x) for x in open_times)
    missing = sorted(expected - have)
    runs, i = [], 0
    while i < len(missing):
        j = i
        while j + 1 < len(missing) and missing[j + 1] == missing[j] + step:
            j += 1
        runs.append({
            "start_open_ms": missing[i],
            "end_open_ms": missing[j],
            "start_utc": utc_ms(missing[i]),
            "end_utc": utc_ms(missing[j]),
            "bars": (missing[j] - missing[i]) // step + 1,
        })
        i = j + 1
    return runs, len(missing)


def aggregate_5m(df1: pd.DataFrame) -> pd.DataFrame:
    """A 5m bar is valid only if all 5 one-minute bars exist."""
    d = df1[["open_time", "open", "high", "low", "close", "volume",
             "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]].copy()
    d["bucket"] = (d.open_time // STEP_5M) * STEP_5M
    # Unique 1m opens already guaranteed. A bucket is complete iff it has 5 bars
    # starting at the bucket open and ending at bucket+4m.
    stats = d.groupby("bucket", sort=True).agg(
        n=("open_time", "size"),
        first_open=("open_time", "min"),
        last_open=("open_time", "max"),
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        close_time=("close_time", "max"),
        quote_volume=("quote_volume", "sum"),
        trades=("trades", "sum"),
        taker_buy_base=("taker_buy_base", "sum"),
        taker_buy_quote=("taker_buy_quote", "sum"),
    )
    # first/last require rows in time order; groupby order follows appearance.
    # Recompute open/close from time-sorted data to be safe.
    d = d.sort_values("open_time")
    first_rows = d.groupby("bucket", sort=True).first()
    last_rows = d.groupby("bucket", sort=True).last()
    stats["open"] = first_rows["open"]
    stats["close"] = last_rows["close"]
    stats["close_time"] = last_rows["close_time"]
    ok = (stats["n"] == 5) & (stats["first_open"] == stats.index) & (
        stats["last_open"] == stats.index + 4 * STEP_1M
    )
    out = stats.loc[ok].reset_index().rename(columns={"bucket": "open_time"})
    out["n_1m"] = 5
    if out.empty:
        raise RuntimeError("no complete 5m bars")
    out["open_time"] = out["open_time"].astype("int64")
    out["open_time_utc"] = pd.to_datetime(out.open_time, unit="ms", utc=True)
    out["close_time_utc"] = pd.to_datetime(out.close_time, unit="ms", utc=True)
    close_ms = out.open_time + STEP_5M
    out["close_ms"] = close_ms
    role = pd.Series("settlement_only", index=out.index)
    role = role.mask(close_ms < SIGNAL_CUT_MS, "signal_window")
    role = role.mask(close_ms < SIGNAL_START_MS, "warmup")
    out["role"] = role
    cols = [
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "n_1m",
        "open_time_utc", "close_time_utc", "close_ms", "role",
    ]
    return out[cols]


def main():
    print(f"fetch 1m BTCUSDT {utc_ms(START_MS)} -> last open {utc_ms(SETTLE_END_OPEN_MS)}", flush=True)
    t0 = time.time()
    df1, hosts, pages = fetch_1m()
    keep_cols = [
        "open_time", "open_time_utc", "open", "high", "low", "close", "volume",
        "close_time", "close_time_utc", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "settlement_only",
    ]
    df1[keep_cols].to_csv(RAW_CSV, index=False)
    gaps_1m, nmiss_1m = gap_runs(df1.open_time.to_numpy(), STEP_1M)
    print(
        f"1m rows={len(df1)} first={df1.open_time_utc.iloc[0]} "
        f"last={df1.open_time_utc.iloc[-1]} gaps={nmiss_1m}",
        flush=True,
    )

    df5 = aggregate_5m(df1)
    df5.to_csv(AGG_CSV, index=False)
    first_b = int(df1.open_time.iloc[0] // STEP_5M * STEP_5M)
    last_b = int(df1.open_time.iloc[-1] // STEP_5M * STEP_5M)
    grid = list(range(first_b, last_b + STEP_5M, STEP_5M))
    have5 = set(int(x) for x in df5.open_time)
    missing5 = [t for t in grid if t not in have5]
    runs5, nmiss5 = gap_runs(list(df5.open_time.to_numpy()) if len(df5) else [], STEP_5M)
    intended_missing = {
        "grid_first_open_ms": first_b,
        "grid_last_open_ms": last_b,
        "grid_slots": len(grid),
        "valid_5m": int(len(df5)),
        "missing_or_incomplete_5m": len(missing5),
        "missing_5m_opens_utc": [utc_ms(t) for t in missing5[:80]],
        "missing_5m_opens_utc_truncated": len(missing5) > 80,
    }
    log = {
        "source_url": "https://api.binance.com/api/v3/klines",
        "fallback_url": "https://data-api.binance.vision/api/v3/klines",
        "hosts_used": hosts,
        "symbol": "BTCUSDT",
        "interval": "1m",
        "params": {
            "startTime": START_MS,
            "start_utc": utc_ms(START_MS),
            "last_1m_open_ms": SETTLE_END_OPEN_MS,
            "last_1m_open_utc": utc_ms(SETTLE_END_OPEN_MS),
            "limit": 1000,
        },
        "pages": pages,
        "workers": N_WORKERS,
        "elapsed_sec": round(time.time() - t0, 1),
        "rows_1m": int(len(df1)),
        "first_1m_open_utc": str(df1.open_time_utc.iloc[0]),
        "last_1m_open_utc": str(df1.open_time_utc.iloc[-1]),
        "settlement_only_1m": int(df1.settlement_only.sum()),
        "gaps_1m_bars": nmiss_1m,
        "gaps_1m_runs": gaps_1m,
        "rows_5m_valid": int(len(df5)),
        "first_5m_open_utc": str(df5.open_time_utc.iloc[0]),
        "last_5m_open_utc": str(df5.open_time_utc.iloc[-1]),
        "role_counts": df5.role.value_counts().to_dict(),
        "gaps_5m_interior_runs": runs5,
        "gaps_5m_interior_bars": nmiss5,
        "grid_5m": intended_missing,
        "signal_window": "close in [2022-01-01 00:00:00 UTC, 2024-11-11 07:35:00 UTC)",
        "settlement_only_note": (
            "1m/5m bars with open_time >= 2024-11-11 07:35:00 UTC are settlement-only; "
            "no signals at close 07:35 or later. Last possible signal close is 07:30; "
            "its exit is the 07:40 close. The 07:45 1m close is fetched only as a buffer."
        ),
        "raw_csv": RAW_CSV,
        "agg_csv": AGG_CSV,
    }
    json.dump(log, open(LOG, "w"), indent=1, default=str)
    print(json.dumps({k: log[k] for k in [
        "rows_1m", "first_1m_open_utc", "last_1m_open_utc", "gaps_1m_bars",
        "rows_5m_valid", "role_counts", "hosts_used", "elapsed_sec",
    ]}, indent=1), flush=True)


if __name__ == "__main__":
    main()
