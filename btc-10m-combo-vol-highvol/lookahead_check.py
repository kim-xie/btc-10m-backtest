"""Re-run the backtest on 1m series truncated at three dates.

Signals strictly before each cut whose exits are also available in the
truncated file must match the full-file run exactly.
"""
import json
import os
import subprocess

import pandas as pd

DIR = os.path.dirname(os.path.abspath(__file__))
CUTS = ["2023-01-01T00:00:00Z", "2023-07-01T00:00:00Z", "2024-01-01T00:00:00Z"]
KEY = ["signal_open_ms", "dir", "entry", "exit"]


def main():
    full = pd.read_csv(os.path.join(DIR, "trades.csv"))
    full["signal_close_utc"] = pd.to_datetime(full["signal_close_utc"], utc=True)
    full["exit_close_utc"] = pd.to_datetime(full["exit_close_utc"], utc=True)
    msgs = []
    all_ok = True
    for cut_s in CUTS:
        cut = pd.Timestamp(cut_s)
        subprocess.run(
            ["python3", "backtest.py", "--truncate-utc", cut_s],
            cwd=DIR, check=True,
        )
        tr_path = os.path.join(DIR, "trades_truncated.csv")
        tr = pd.read_csv(tr_path) if os.path.getsize(tr_path) > 0 else pd.DataFrame(columns=KEY)
        # Full-run trades with signal close strictly before the cut AND exit close
        # at or before the cut (so the exit 5m bar can be completed from 1m opens < cut).
        # A 5m bar closing at exactly `cut` has last 1m open = cut-1m < cut, so it is available.
        a = full[(full.signal_close_utc < cut) & (full.exit_close_utc <= cut)][KEY].sort_values(KEY).reset_index(drop=True)
        b = tr[KEY].sort_values(KEY).reset_index(drop=True) if len(tr) else tr
        # truncated run should only emit those same trades (signals before cut with exit available)
        ok = len(a) == len(b) and (len(a) == 0 or a.equals(b))
        all_ok = all_ok and ok
        msgs.append(
            f"截断到 {cut_s} 重跑：全量版中信号收盘早于切点且结算根在截断文件内的 {len(a)} 笔，"
            f"与截断重跑的 {len(b)} 笔{'完全一致' if ok else '不一致'}。"
        )
        if not ok:
            # small debug, not used to change the rule
            print("MISMATCH at", cut_s, "full_eligible", len(a), "truncated", len(b), flush=True)
            if len(a) and len(b):
                # show first differing key
                merged = a.merge(b, on=KEY, how="outer", indicator=True)
                print(merged[merged._merge != "both"].head(10).to_string(), flush=True)
            elif len(a) != len(b):
                print("count mismatch only", flush=True)
    text = " ".join(msgs)
    text += " 结论：无前视检查" + ("通过。" if all_ok else "未通过，截断重跑与全量版不一致。")
    R = json.load(open(os.path.join(DIR, "results.json")))
    R["lookahead"] = text
    R["lookahead_pass"] = all_ok
    json.dump(R, open(os.path.join(DIR, "results.json"), "w"), indent=1, default=str)
    print(text, flush=True)
    # cleanup truncated file
    p = os.path.join(DIR, "trades_truncated.csv")
    if os.path.exists(p):
        os.remove(p)


if __name__ == "__main__":
    main()
