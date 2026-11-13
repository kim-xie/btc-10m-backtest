import subprocess, json, pandas as pd
msgs = []
full = pd.read_csv("trades.csv")
for t in ["2025-07-15 12:00", "2026-04-06 07:35", "2026-09-01 03:05"]:
    T = int(pd.Timestamp(t, tz="UTC").value // 10**6)
    subprocess.run(["python3", "backtest.py", "--truncate-ms", str(T)], check=True)
    tr = pd.read_csv("trades_truncated.csv")
    key = ["strategy", "open_time", "dir", "entry", "exit"]
    a = full[full.open_time <= T - 600000][key].sort_values(key).reset_index(drop=True)
    b = tr[key].sort_values(key).reset_index(drop=True)
    ok = a.equals(b)
    msgs.append(f"截断到 {t} UTC 重跑：截断前可结算的 {len(a)} 笔信号与全量版 {'完全一致' if ok else '不一致'}（截断版 {len(b)} 笔）。")
R = json.load(open("results.json")); R["lookahead"] = " ".join(msgs)
json.dump(R, open("results.json", "w"), indent=1, default=str); print(R["lookahead"])
