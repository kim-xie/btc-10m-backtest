#!/usr/bin/env python3
"""Pack all 10-minute strategy trades + 5m klines into a compact gzip payload for the viewer."""
import base64, gzip, io, json, os, struct, math
from pathlib import Path
import numpy as np
import pandas as pd

OUT = Path("/workspace/btc-10m-backtest-viewer")
STEP = 300_000
CUT_MS = int(pd.Timestamp("2026-04-06 07:35:00", tz="UTC").value // 10**6)
WIN, LOSS = 0.8, -1.0

STRATS = []  # filled in order; id is index

def add_strat(key, label, group, desc, status="", note=""):
    sid = len(STRATS)
    STRATS.append({
        "id": sid, "key": key, "label": label, "group": group,
        "desc": desc, "status": status, "note": note,
    })
    return sid

# ---- catalog (display only; trades attached later) ----
G1, G2, G3, G4, G5, G6, G7 = (
    "第 1 批 · 形态",
    "第 2 批 · 均值回归与突破",
    "第 3 批 · 资金流与溢价",
    "订单流 R1–R5",
    "事后组合（看完结果后）",
    "kim 提出",
    "其他早期测试",
)

# Pattern
add_strat("pattern_A", "Setup A：20 根内 EMA50 穿越≥2 次 + 近均线吞没", G1,
          "窗口 20 根，向上穿越≥2 次且 |close-EMA50|/EMA50≤0.30%，本根看涨吞没做多；向下穿越+看跌吞没做空。",
          "FAIL")
add_strat("pattern_B", "Setup B：强势推进后弱回调 + 顺势吞没", G1,
          "连续≥3 根强阳（实体≥2×前50根实体中位数），1–5 根弱回调（不创新高、回撤≤50%）后看涨吞没做多；阴线对称做空。",
          "FAIL")

# New 12
NEW = [
    ("fade_atr15", "fade_atr15：实体 ≥1.5×ATR 逆势", "（close-open）/ATR14 ≥ +1.5 做空，≤ −1.5 做多。"),
    ("fade_atr20", "fade_atr20：实体 ≥2.0×ATR 逆势", "同上，阈值改为 2.0。"),
    ("fade_ret40", "fade_ret40：单根涨跌 ≥0.40% 逆势", "相对前收涨跌≥+0.40% 做空，≤ −0.40% 做多。"),
    ("fade_3same", "fade_3same：连续 3 根同向后逆势", "连续 3 根收盘同向，且幅度/ATR14≥1.5，逆这 3 根方向。"),
    ("rsi_fade", "rsi_fade：RSI14 超买超卖逆势", "RSI14≥80 做空，≤20 做多。"),
    ("cont_atr15", "cont_atr15：实体 ≥1.5×ATR 顺势（对照）", "与 fade_atr15 方向相反。"),
    ("break_20", "break_20：突破前 20 根高低点", "收盘高于前 20 根最高价做多，低于最低价做空。"),
    ("ema20_cross", "ema20_cross：收盘上穿/下穿 EMA20", "上一根收盘≤EMA20、本根收盘>EMA20 做多；下穿做空。"),
    ("inside_break", "inside_break：内包线后突破", "上一根为内包线，本根收盘突破其最高做多、最低做空。"),
    ("pin_fade", "pin_fade：针形反转", "下影≥2×实体且收在上 1/3、并刷新前 10 根低点做多；上影对称做空。"),
    ("fade_atr15_lowvol", "fade_atr15_lowvol：低波动下的 ATR 逆势", "仅当 ATR14/close 低于过去 200 根中位数时执行 fade_atr15。"),
    ("fade_atr15_highvol", "fade_atr15_highvol：高波动下的 ATR 逆势", "仅当 ATR14/close 高于过去 200 根中位数时执行 fade_atr15。"),
]
NEW_STATUS = {}
try:
    ns = json.loads(Path("/workspace/btc-10m-new-strategies/results.json").read_text())
    for k, v in ns["results"].items():
        NEW_STATUS[k] = v.get("verdict") or v.get("status") or "FAIL"
except Exception:
    pass
for k, lab, desc in NEW:
    add_strat(k, lab, G2, desc, NEW_STATUS.get(k, "FAIL"))

# Batch 3
B3 = [
    ("tbr_extreme_fade", "tbr_extreme_fade：主动买比极端逆势", "主动买占比≥0.70 做空，≤0.30 做多。", "WATCH" if False else "FAIL"),
    ("tbr_extreme_follow", "tbr_extreme_follow：主动买比极端顺势（对照）", "主动买占比≥0.70 做多，≤0.30 做空。", "FAIL"),
    ("vol_spike_fade", "vol_spike_fade：成交量尖峰逆势", "成交量 z≥4 且 |收益|≥0.15%，逆这根方向。", "FAIL"),
    ("vol_spike_follow", "vol_spike_follow：成交量尖峰顺势（对照）", "成交量 z≥4 且 |收益|≥0.15%，顺这根方向。", "FAIL"),
    ("absorption", "absorption：放量但几乎不涨跌（吸收）", "成交量 z≥3 且 |收益|≤0.03%；买盘占比≥0.60 做空，≤0.40 做多。", "FAIL"),
    ("perp_tbr_div", "perp_tbr_div：期现主动买比背离", "永续买比≥0.60 且现货≤0.45 做空；反向做多。", "FAIL"),
    ("prem_z_fade", "prem_z_fade：溢价 z 值极端逆势", "溢价 z≥3 做空，≤ −3 做多。", "WATCH"),
    ("basis_z_fade", "basis_z_fade：期现基差 z 值极端逆势", "基差 z≥3 做空，≤ −3 做多。", "FAIL"),
    ("eth_lead", "eth_lead：ETH 大动、BTC 没跟上则跟 ETH", "ETH 5 分钟涨跌≥0.30% 且 BTC 没跟上或反向，顺 ETH 方向做 BTC。", "FAIL"),
    ("trades_spike_fade", "trades_spike_fade：成交笔数尖峰逆势", "笔数 z≥4 且 |收益|≥0.10%，逆这根方向。", "FAIL"),
]
for k, lab, desc, st in B3:
    add_strat(k, lab, G3, desc, st)

# Flow
FLOW = [
    ("flow_R1", "R1 主动买比 5m 顺势", "5 分钟主动买比≥0.60 做多，≤0.40 做空。"),
    ("flow_R2", "R2 主动买比 5m 逆势", "与 R1 方向相反。"),
    ("flow_R3", "R3 主动买比 15m 顺势", "15 分钟主动买比≥0.57 做多，≤0.43 做空。"),
    ("flow_R4", "R4 放量 3 倍 + 5 分钟涨跌顺势", "5 分钟成交量大于 24 小时中位数的 3 倍，顺 5 分钟涨跌。"),
    ("flow_R5", "R5 放量 3 倍 + 5 分钟涨跌逆势", "同上，逆 5 分钟涨跌。"),
]
for k, lab, desc in FLOW:
    add_strat(k, lab, G4, desc, "FAIL", "样本为 2026 年 1 月至 11 月的 1 分钟数据合成 5 分钟。")

add_strat("combo_vol_highvol", "量能尖峰逆势 且 高波动 ATR 逆势", G5,
          "同时满足 vol_spike_fade 与 fade_atr15_highvol。看完前两批结果后组合，样本外用 2022–2024 未触碰数据验证，未过关。",
          "FAIL", "事后组合，不计入正式通过。")

add_strat("ema50_3cross", "18 根内 EMA50 阳线上穿≥3 次做多 / 阴线下穿做空", G6,
          "EMA50（5 分钟收盘）。本根阳线收盘上穿，且最近 18 根里阳线上穿次数≥3，在收盘做多；阴线下穿对称做空。第 3、4、5 次及之后都进场。持有 10 分钟。",
          "FAIL")

add_strat("engulf_A", "早期 EMA50 穿越+吞没（较短样本）", G7,
          "与 Setup A 类似，样本主要是 2026 年 1 月至 11 月，参数略有不同。", "FAIL", "早期测试，样本短于正式第 1 批。")
add_strat("engulf_B", "早期强推进+弱回调+吞没（较短样本）", G7,
          "与 Setup B 类似，回调限制为 1–3 根。样本主要是 2026 年 1 月至 11 月。", "FAIL", "早期测试，样本短于正式第 1 批。")
add_strat("morning_star", "下跌趋势中的早晨之星（Polymarket 5 分钟）", G7,
          "在 Polymarket BTC 5 分钟涨跌市场，按 5 分钟 K 线早晨之星形态买入涨。赔付与入场价按当时盘口，与币安 10 分钟二元赔率不完全相同。",
          "FAIL", "市场与结算不同，图表用同步的 BTC 5 分钟 K 线，输赢按原回测记录。")

KEY2ID = {s["key"]: s["id"] for s in STRATS}


def load_klines():
    print("loading klines...")
    cols = ["open_time", "open", "high", "low", "close"]
    a = pd.read_csv("/workspace/btc-10m-combo-vol-highvol/data/klines_btcusdt_5m.csv", usecols=cols)
    b = pd.read_csv("/workspace/btc-10m-batch3/data/btc_spot.csv", usecols=cols)
    df = pd.concat([a, b], ignore_index=True)
    df = df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)
    print("  klines", len(df),
          pd.to_datetime(df.open_time.iloc[0], unit="ms", utc=True),
          "->",
          pd.to_datetime(df.open_time.iloc[-1], unit="ms", utc=True))
    return df


def to_ms(series):
    s = pd.Series(series)
    if pd.api.types.is_numeric_dtype(s):
        v = pd.to_numeric(s, errors="coerce")
        mx = float(v.max()) if v.notna().any() else 0
        if mx > 1e16:
            v = v // 1_000_000
        elif mx > 1e14:
            v = v // 1000
        elif mx > 0 and mx < 1e11:
            v = v * 1000
        return v.fillna(0).astype("int64").to_numpy()
    ts = pd.to_datetime(s, utc=True, errors="coerce")
    arr = np.array(ts, dtype="datetime64[ms]").astype(np.int64)
    arr[np.isnat(np.array(ts, dtype="datetime64[ms]"))] = 0
    return arr


def result_from_row(side_or_dir, entry, exit_, result=None, R=None, out=None, win=None):
    if out is not None and pd.notna(out):
        return int(out)
    if win is not None and pd.notna(win):
        if isinstance(win, str):
            w = win.strip().lower()
            if w in ("1", "true", "win", "w", "yes"):
                return 1
            if w in ("0", "false", "loss", "l", "no", "-1"):
                return -1
            return 0
        if isinstance(win, (int, float, np.integer, np.floating)) and not isinstance(win, (bool, np.bool_)):
            if win > 0: return 1
            if win < 0: return -1
            return 0
        return 1 if bool(win) else -1
    if result is not None and pd.notna(result):
        r = str(result).strip().lower()
        if r in ("w", "win", "1"):
            return 1
        if r in ("l", "loss", "-1"):
            return -1
        if r in ("t", "tie", "scratch", "0", "平"):
            return 0
    if R is not None and pd.notna(R):
        if float(R) > 0:
            return 1
        if float(R) < 0:
            return -1
        return 0
    d = side_to_dir(side_or_dir)
    if d == 0 or pd.isna(entry) or pd.isna(exit_):
        return 0
    if exit_ > entry:
        return 1 if d > 0 else -1
    if exit_ < entry:
        return 1 if d < 0 else -1
    return 0


def side_to_dir(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return 0
    if isinstance(v, (int, np.integer, float, np.floating)) and not isinstance(v, bool):
        if v > 0:
            return 1
        if v < 0:
            return -1
        return 0
    s = str(v).strip().lower()
    if s in ("long", "l", "1", "buy", "多"):
        return 1
    if s in ("short", "s", "-1", "sell", "空"):
        return -1
    return 0


class Pack:
    def __init__(self, klines):
        self.k = klines
        self.times = klines.open_time.to_numpy(dtype=np.int64)
        self.index = {int(t): i for i, t in enumerate(self.times)}
        self.rows = []  # list of tuples
        self.dropped = {}

    def kidx(self, ms):
        if ms is None or (isinstance(ms, float) and math.isnan(ms)):
            return -1
        return self.index.get(int(ms), -1)

    def add(self, key, open_ms, dir_, entry, exit_, result, oos=False, nonoverlap=False, extra_flag=0):
        sid = KEY2ID[key]
        if dir_ not in (1, -1):
            self.dropped[key] = self.dropped.get(key, 0) + 1
            return
        if pd.isna(entry) or pd.isna(exit_):
            self.dropped[key] = self.dropped.get(key, 0) + 1
            return
        ki = self.kidx(open_ms)
        flags = (1 if oos else 0) | (2 if nonoverlap else 0) | extra_flag
        tsec = int(open_ms) // 1000 if open_ms and open_ms > 0 else 0
        self.rows.append((ki if ki >= 0 else 0xFFFFFFFF, tsec, float(entry), float(exit_), sid, int(dir_), int(result), flags))

    def add_df(self, key, df, open_ms, dir_col=None, side_col=None, entry="entry", exit_="exit",
               result=None, R=None, out=None, win=None, oos_col=None, oos_value=None, sample_col=None,
               nonoverlap_col=None, extra_flag=0):
        n0 = len(self.rows)
        for rec in df.itertuples(index=False):
            d = rec._asdict() if False else None
        # faster: arrays
        oms = open_ms if isinstance(open_ms, np.ndarray) else to_ms(df[open_ms]) if isinstance(open_ms, str) else open_ms
        if isinstance(oms, (int, np.integer)):
            raise ValueError("open_ms should be series")
        oms = np.asarray(oms, dtype=np.int64)
        if dir_col:
            dirs = np.array([side_to_dir(x) for x in df[dir_col].to_numpy()])
        else:
            dirs = np.array([side_to_dir(x) for x in df[side_col].to_numpy()])
        ent = df[entry].to_numpy(dtype=float)
        exi = df[exit_].to_numpy(dtype=float)
        if out and out in df.columns:
            res = df[out].to_numpy()
            res = np.array([int(x) if pd.notna(x) else 0 for x in res], dtype=np.int8)
        elif result and result in df.columns:
            res = np.array([result_from_row(dirs[i], ent[i], exi[i], result=df[result].iloc[i]) for i in range(len(df))], dtype=np.int8)
        elif R and R in df.columns:
            res = np.array([result_from_row(dirs[i], ent[i], exi[i], R=df[R].iloc[i]) for i in range(len(df))], dtype=np.int8)
        elif win and win in df.columns:
            res = np.array([result_from_row(dirs[i], ent[i], exi[i], win=df[win].iloc[i]) for i in range(len(df))], dtype=np.int8)
        else:
            res = np.array([result_from_row(dirs[i], ent[i], exi[i]) for i in range(len(df))], dtype=np.int8)
        if oos_col and oos_col in df.columns:
            if oos_value is not None:
                oos = (df[oos_col].to_numpy() == oos_value)
            else:
                oos = df[oos_col].to_numpy()
                oos = np.array([str(x) in ("1", "True", "true", "OOS") or x is True for x in oos])
        elif sample_col and sample_col in df.columns:
            oos = np.array([str(x) == "OOS" for x in df[sample_col].to_numpy()])
        else:
            oos = oms >= CUT_MS
        if nonoverlap_col and nonoverlap_col in df.columns:
            no = df[nonoverlap_col].to_numpy()
            no = np.array([bool(x) and str(x) not in ("False", "0", "false") for x in no])
        else:
            no = np.zeros(len(df), dtype=bool)
        for i in range(len(df)):
            self.add(key, int(oms[i]), int(dirs[i]), ent[i], exi[i], int(res[i]), bool(oos[i]), bool(no[i]), extra_flag)
        print(f"  {key}: +{len(self.rows)-n0}")


def load_pattern(pack):
    df = pd.read_csv("/workspace/btc-10m-pattern-backtest/trades.csv")
    # signal_open_utc is the signal bar open
    for setup, key in (("A", "pattern_A"), ("B", "pattern_B")):
        sub = df[df.setup == setup].copy()
        pack.add_df(key, sub, "signal_open_utc", side_col="side", entry="entry", exit_="exit",
                    result="result", oos_col="is_oos", oos_value=True)


def load_new(pack):
    df = pd.read_csv("/workspace/btc-10m-new-strategies/trades.csv",
                     usecols=["strategy", "signal_open_utc", "side", "entry", "exit", "result", "split"])
    for key, _, _ in NEW:
        sub = df[df.strategy == key]
        if len(sub) == 0:
            print("  missing", key)
            continue
        pack.add_df(key, sub, "signal_open_utc", side_col="side", entry="entry", exit_="exit",
                    result="result", sample_col="split")


def load_batch3(pack):
    df = pd.read_csv("/workspace/btc-10m-batch3/trades.csv")
    for key, *_ in B3:
        sub = df[df.strategy == key]
        pack.add_df(key, sub, "open_time", dir_col="dir", entry="entry", exit_="exit",
                    out="out_0bp", sample_col="sample", nonoverlap_col="nonoverlap")


def load_combo(pack):
    df = pd.read_csv("/workspace/btc-10m-combo-vol-highvol/trades.csv")
    # only 0bp rows? file has one row per trade with result_0bp
    pack.add_df("combo_vol_highvol", df, "signal_open_ms", dir_col="dir", entry="entry", exit_="exit",
                result="result_0bp", nonoverlap_col="nonoverlap", extra_flag=0)
    # all historical 2022-2024 = OOS; override flags: force oos=1
    # Fix last added rows: they're before CUT so oos computed False. Mark all combo as OOS (flag bit0).
    sid = KEY2ID["combo_vol_highvol"]
    n = 0
    for i in range(len(pack.rows)):
        r = pack.rows[i]
        if r[4] == sid:
            flags = r[7] | 1
            pack.rows[i] = (r[0], r[1], r[2], r[3], r[4], r[5], r[6], flags)
            n += 1
    print("  combo forced OOS", n)


def load_ema50_3cross(pack):
    df = pd.read_csv("/workspace/btc-10m-ema50-3cross/trades.csv")
    df = df[df.strategy == "at_least_3"].copy()
    hist = df[df.window == "historical_oos"]
    rec = df[df.window == "recent_confirm"]
    pack.add_df("ema50_3cross", hist, "open_time", dir_col="dir", entry="entry", exit_="exit",
                nonoverlap_col="nonoverlap")
    sid = KEY2ID["ema50_3cross"]
    # historical all OOS
    for i in range(len(pack.rows)):
        r = pack.rows[i]
        if r[4] == sid:
            pack.rows[i] = (r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7] | 1)
    pack.add_df("ema50_3cross", rec, "open_time", dir_col="dir", entry="entry", exit_="exit",
                nonoverlap_col="nonoverlap", extra_flag=4)  # bit2 = recent window


def load_engulf(pack):
    df = pd.read_csv("/workspace/ema50-engulf-backtest/trades.csv")
    # t is signal bar open (5m)
    tms = to_ms(df["t"])
    # lookup entry/exit from klines
    closes = pack.k.set_index("open_time")["close"]
    entries, exits, dirs, keys, oms, wins = [], [], [], [], [], []
    miss = 0
    for rec, ms in zip(df.itertuples(), tms):
        key = "engulf_A" if rec.rule.startswith("A") else "engulf_B"
        d = 1 if rec.rule.endswith("long") else -1
        if int(ms) not in pack.index:
            miss += 1
            continue
        i = pack.index[int(ms)]
        if i + 2 >= len(pack.times) or pack.times[i + 2] != pack.times[i] + 2 * STEP:
            miss += 1
            continue
        entries.append(float(pack.k.close.iloc[i]))
        exits.append(float(pack.k.close.iloc[i + 2]))
        dirs.append(d)
        keys.append(key)
        oms.append(int(ms))
        # use recorded win if prices disagree slightly
        px_win = (exits[-1] - entries[-1]) * d > 0
        recorded = bool(rec.win) if not isinstance(rec.win, str) else str(rec.win).lower() in ("true", "1")
        wins.append(1 if recorded else -1)
    print(f"  engulf mapped {len(keys)} miss {miss}")
    tmp = pd.DataFrame({"key": keys, "open_time": oms, "dir": dirs, "entry": entries, "exit": exits, "win": wins})
    for key in ("engulf_A", "engulf_B"):
        sub = tmp[tmp.key == key]
        if len(sub):
            pack.add_df(key, sub, "open_time", dir_col="dir", entry="entry", exit_="exit", win="win")


def load_morning_star(pack):
    path = Path("/workspace/morning-star-backtest/trades.csv")
    if not path.exists():
        return
    df = pd.read_csv(path)
    # c3_open is unix seconds of 5m bar? "c3_open 1792702500" — seconds
    # eventStartTime is market T
    # morning star: typically long Up. won column.
    # Use c3_open as signal bar if it looks like seconds/ms
    if "c3_open" in df.columns:
        raw = df["c3_open"].to_numpy()
        # if seconds (~1.7e9)
        oms = []
        for x in raw:
            if pd.isna(x):
                oms.append(0)
            elif x > 1e14:
                oms.append(int(x) // 1000 if x > 1e12 and x < 1e14 else int(x))
            elif x > 1e12:
                oms.append(int(x))
            else:
                oms.append(int(x) * 1000)
        df = df.copy()
        df["_oms"] = oms
    else:
        df = df.copy()
        df["_oms"] = to_ms(df["eventStartTime"] if "eventStartTime" in df.columns else df["T"])
    # dir: morning star in downtrend is long (buy up)
    df["_dir"] = 1
    # need entry/exit on BTC for the chart; result from 'won'
    closes = pack.index
    entries, exits = [], []
    for ms in df["_oms"]:
        i = pack.index.get(int(ms), -1)
        if i >= 0 and i + 2 < len(pack.times) and pack.times[i + 2] == pack.times[i] + 2 * STEP:
            entries.append(float(pack.k.close.iloc[i]))
            exits.append(float(pack.k.close.iloc[i + 2]))
        elif i >= 0:
            entries.append(float(pack.k.close.iloc[i]))
            exits.append(float(pack.k.close.iloc[i]))
        else:
            entries.append(np.nan)
            exits.append(np.nan)
    df["entry"] = entries
    df["exit"] = exits
    pack.add_df("morning_star", df, "_oms", dir_col="_dir", entry="entry", exit_="exit", win="won")


def load_flow(pack):
    import glob
    fs = sorted(glob.glob("/workspace/morning-star-backtest/ytd/kl/BTCUSDT-1m-*.csv"))
    if not fs:
        print("  flow: no 1m files")
        return
    print(f"  flow: reading {len(fs)} 1m files...")
    dfs = []
    for f in fs:
        d = pd.read_csv(f, header=None)
        if not str(d.iloc[0, 0]).isdigit():
            d = d.iloc[1:]
        dfs.append(d.iloc[:, :11])
    df = pd.concat(dfs, ignore_index=True)
    df.columns = ["t", "o", "h", "l", "c", "v", "ct", "qv", "n", "tbv", "tbq"]
    for col in ["t", "o", "h", "l", "c", "v", "tbv"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["t"] = df["t"].astype("int64")
    if df["t"].max() > 1e14:
        df["t"] //= 1000
    df = df.drop_duplicates("t").sort_values("t")
    df["dt"] = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df.set_index("dt")
    c = df["c"]
    # evaluate at each 5m boundary (minute % 5 == 4), entry = that 1m close, exit = close 10 min later
    # The 5m bar that closes at 00:05 has open 00:00; signal open_time for our klines is 00:00.
    idx = df.index[(df.index.minute % 5 == 4)]
    fut = c.shift(-10)
    r5 = df.tbv.rolling(5).sum() / df.v.rolling(5).sum()
    r15 = df.tbv.rolling(15).sum() / df.v.rolling(15).sum()
    vol5 = df.v.rolling(5).sum()
    volmed = vol5.rolling(288 * 5).median()
    ret5 = c / c.shift(5) - 1
    spike = vol5 > 3 * volmed
    rules = {
        "flow_R1": (r5 >= 0.60, r5 <= 0.40),
        "flow_R2": (r5 <= 0.40, r5 >= 0.60),
        "flow_R3": (r15 >= 0.57, r15 <= 0.43),
        "flow_R4": (spike & (ret5 > 0), spike & (ret5 < 0)),
        "flow_R5": (spike & (ret5 < 0), spike & (ret5 > 0)),
    }
    # 60/40 time split as in original script, for oos flag
    cut = idx[int(len(idx) * 0.6)] if len(idx) else idx
    print("  flow split", cut, "idx", len(idx))
    for key, (L, S) in rules.items():
        recs = []
        for d, m in ((1, L), (-1, S)):
            sel = idx[m.reindex(idx).fillna(False).values]
            entry = c.reindex(sel)
            exitp = fut.reindex(sel)
            ok = entry.notna() & exitp.notna()
            for t, pe, px in zip(sel[ok], entry[ok], exitp[ok]):
                # 5m bar open = close time of this minute bar minus 5 minutes + 1ms? 
                # minute ending 00:04:00-00:04:59.999 is last minute of 00:00 5m bar.
                # Binance 5m open_time = 00:00:00.000
                open_ms = int(t.value // 1_000_000) - 4 * 60_000
                recs.append((open_ms, d, float(pe), float(px), t >= cut))
        sub = pd.DataFrame(recs, columns=["open_time", "dir", "entry", "exit", "oos"])
        if len(sub) == 0:
            print("  ", key, "empty")
            continue
        pack.add_df(key, sub, "open_time", dir_col="dir", entry="entry", exit_="exit", oos_col="oos")


def summarize(pack):
    rows = pack.rows
    by = {}
    for r in rows:
        by.setdefault(r[4], []).append(r)
    for s in STRATS:
        rs = by.get(s["id"], [])
        s["n"] = len(rs)
        if not rs:
            s["win_rate"] = None
            s["exp_R"] = None
            s["first"] = None
            s["last"] = None
            continue
        dec = [r for r in rs if r[6] != 0]
        w = sum(1 for r in dec if r[6] == 1)
        s["win_rate"] = (w / len(dec)) if dec else None
        ev = (WIN * sum(1 for r in rs if r[6] == 1) + LOSS * sum(1 for r in rs if r[6] == -1)) / len(rs)
        s["exp_R"] = ev
        s["wins"] = w
        s["losses"] = sum(1 for r in rs if r[6] == -1)
        s["ties"] = sum(1 for r in rs if r[6] == 0)
        s["first"] = min(r[1] for r in rs if r[1])
        s["last"] = max(r[1] for r in rs if r[1])
        oos = [r for r in rs if (r[7] & 1) and r[6] != 0]
        if oos:
            wo = sum(1 for r in oos if r[6] == 1)
            s["oos_n"] = len(oos)
            s["oos_win_rate"] = wo / len(oos)
        else:
            s["oos_n"] = 0
            s["oos_win_rate"] = None


def write_payload(pack):
    k = pack.k
    nbar = len(k)
    times = (k.open_time.to_numpy(dtype=np.int64) // 1000).astype(np.uint32)
    o = k.open.to_numpy(dtype=np.float32)
    h = k.high.to_numpy(dtype=np.float32)
    l = k.low.to_numpy(dtype=np.float32)
    c = k.close.to_numpy(dtype=np.float32)
    meta = {
        "barCount": nbar,
        "tradeCount": len(pack.rows),
        "stepSec": 300,
        "cutUtc": "2026-04-06T07:35:00Z",
        "breakeven": 1 / 1.8,
        "winR": WIN,
        "lossR": LOSS,
        "strategies": STRATS,
        "groups": [G1, G2, G3, G4, G5, G6, G7],
        "dropped": pack.dropped,
        "klines": {
            "firstUtc": pd.to_datetime(int(k.open_time.iloc[0]), unit="ms", utc=True).isoformat(),
            "lastUtc": pd.to_datetime(int(k.open_time.iloc[-1]), unit="ms", utc=True).isoformat(),
        },
    }
    jb = json.dumps(meta, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    buf = io.BytesIO()
    buf.write(b"BT10")
    buf.write(struct.pack("<I", len(jb)))
    buf.write(jb)
    pad = (4 - (buf.tell() % 4)) % 4
    buf.write(bytes(pad))
    buf.write(struct.pack("<I", nbar))
    buf.write(times.tobytes())
    buf.write(o.tobytes())
    buf.write(h.tobytes())
    buf.write(l.tobytes())
    buf.write(c.tobytes())
    buf.write(struct.pack("<I", len(pack.rows)))
    # pack trades
    rec = struct.Struct("<II ff bbbb")  # kidx u32, tsec u32, entry f32, exit f32, sid i8, dir i8, result i8, flags u8 but i8 ok
    # actually sid is 0-40, use B for flags and b for signed
    rec = struct.Struct("<I I f f b b b B")
    for r in pack.rows:
        kidx, tsec, ent, exi, sid, d, res, flags = r
        if kidx < 0:
            kidx = 0xFFFFFFFF
        buf.write(rec.pack(int(kidx) & 0xFFFFFFFF, int(tsec) & 0xFFFFFFFF, float(ent), float(exi),
                           sid, d, res, flags & 0xFF))
    raw = buf.getvalue()
    gz = gzip.compress(raw, compresslevel=9)
    print(f"payload raw {len(raw)/1e6:.2f} MB  gzip {len(gz)/1e6:.2f} MB  trades {len(pack.rows)}")
    b64 = base64.b64encode(gz).decode("ascii")
    (OUT / "data.js").write_text("window.__BT_GZ__=" + json.dumps(b64) + ";\n")
    print("wrote", OUT / "data.js", "chars", len(b64) + 30)
    # also raw gz for debugging
    (OUT / "data.bin.gz").write_bytes(gz)
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return meta


def main():
    klines = load_klines()
    pack = Pack(klines)
    print("loading trades...")
    load_pattern(pack)
    load_new(pack)
    load_batch3(pack)
    load_combo(pack)
    load_ema50_3cross(pack)
    load_engulf(pack)
    load_morning_star(pack)
    load_flow(pack)
    print("dropped", pack.dropped)
    summarize(pack)
    write_payload(pack)
    for s in STRATS:
        wr = f"{s['win_rate']*100:.1f}%" if s.get("win_rate") is not None else "—"
        print(f"  [{s['status']}] {s['key']:24} n={s.get('n',0):6} wr={wr}")


if __name__ == "__main__":
    main()
