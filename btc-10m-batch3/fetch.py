"""Fetch Binance public 5m data for batch 3 (no invented data). Saves CSVs in data/ and data/fetch_log.json."""
import json, time, os, sys
import requests, pandas as pd

START_MS = 1731310500000  # 2024-11-11 07:35:00 UTC
STEP = 300_000
COLS = ["open_time","open","high","low","close","volume","close_time","quote_volume",
        "trades","taker_buy_base","taker_buy_quote","ignore"]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)

def get(hosts, path, params, tries=6):
    last = None
    for h in hosts:
        for i in range(tries):
            try:
                r = requests.get(h + path, params=params, timeout=20)
                if r.status_code == 200:
                    return r.json(), h
                last = f"{h} HTTP {r.status_code} {r.text[:200]}"
                if r.status_code in (418, 429):
                    time.sleep(10 * (i + 1))
                else:
                    time.sleep(1 + i)
            except Exception as e:
                last = f"{h} {e!r}"; time.sleep(1 + i)
    raise RuntimeError(last)

def fetch(name, hosts, path, symbol, limit):
    now_ms = int(time.time() * 1000)
    end_ms = (now_ms // STEP) * STEP - 1  # last closed bar close_time
    rows, start, used = [], START_MS, set()
    while start <= end_ms:
        data, h = get(hosts, path, {"symbol": symbol, "interval": "5m", "startTime": start,
                                     "endTime": end_ms, "limit": limit})
        used.add(h)
        if not data:
            break
        rows.extend(data)
        nxt = int(data[-1][0]) + STEP
        if nxt <= start: break
        start = nxt
        time.sleep(0.12)
    df = pd.DataFrame(rows, columns=COLS).drop(columns=["ignore"])
    for c in df.columns:
        df[c] = pd.to_numeric(df[c])
    df = df.drop_duplicates("open_time").sort_values("open_time")
    df = df[df.close_time <= end_ms]  # closed bars only
    df.to_csv(os.path.join(OUT, f"{name}.csv"), index=False)
    return {"rows": len(df), "first_open": int(df.open_time.iloc[0]), "last_open": int(df.open_time.iloc[-1]),
            "hosts": sorted(used), "fetched_at_ms": now_ms}

SOURCES = {
    "btc_spot": (["https://api.binance.com", "https://data-api.binance.vision"], "/api/v3/klines", "BTCUSDT", 1000),
    "btc_perp": (["https://fapi.binance.com"], "/fapi/v1/klines", "BTCUSDT", 1500),
    "eth_spot": (["https://api.binance.com", "https://data-api.binance.vision"], "/api/v3/klines", "ETHUSDT", 1000),
    "btc_prem": (["https://fapi.binance.com"], "/fapi/v1/premiumIndexKlines", "BTCUSDT", 1500),
}

if __name__ == "__main__":
    log = {}
    for name, (hosts, path, sym, lim) in SOURCES.items():
        try:
            log[name] = {"ok": True, **fetch(name, hosts, path, sym, lim)}
        except Exception as e:
            log[name] = {"ok": False, "error": str(e)}
        print(name, log[name], flush=True)
    json.dump(log, open(os.path.join(OUT, "fetch_log.json"), "w"), indent=1)
