"""Build summary.md from results.json."""
import json, os
import pandas as pd

D = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(D, "results.json")))
BE = R["breakeven"]
ts = lambda ms: "—" if ms is None else pd.to_datetime(int(ms), unit="ms", utc=True).strftime("%Y-%m-%d %H:%M UTC")
pct = lambda x: "—" if x is None else f"{100*x:.2f}%"
num = lambda x: "—" if x is None else f"{x:+.4f}"

L = ["# ema50_3cross_18 回测结果", ""]
L += [
    f"规则来源：`PRE_REGISTERED.md`（锁定于 {R['locked_at']}）。正式规则是 18 根窗口内阳线上穿次数 ≥ 3 则在当根收盘做多，阴线下穿对称做空；第 3、第 4、第 5 次及之后都进场。阈值未改。不下单。",
    "",
    "## 数据源与覆盖",
    "",
    "| 窗口 | 文件 | 行数 | 首根 | 末根 | 缺 bar | 缺口段数 | 信号区间 |",
    "|---|---|---|---|---|---|---|---|",
]
for wname, label in (("historical_oos", "主样本（正式判定，全部样本外）"), ("recent_confirm", "近端确认（只报，不单独通过）")):
    d = R["windows"][wname]["data"]
    L.append(
        f"| {label} | `{d['path']}` | {d['rows']} | {ts(d['first_open'])} | {ts(d['last_open'])} | "
        f"{d['missing_bars']} | {len(d['missing_runs'])} | {ts(d['signal_first'])}–{ts(d['signal_last'])} |"
    )
L += ["", "缺口处理：连续段分别计算 EMA；每段前 300 根不出信号；18 根窗口或 +10 分钟结算根不连续则丢弃该信号。不补造 K 线。", ""]
for wname in ("historical_oos", "recent_confirm"):
    runs = R["windows"][wname]["data"]["missing_runs"]
    if not runs:
        L.append(f"- {wname}：无缺口")
        continue
    show = "; ".join(f"{ts(a)}–{ts(b)} ({n})" for a, b, n in runs[:10])
    L.append(f"- {wname}：{len(runs)} 段 / {R['windows'][wname]['data']['missing_bars']} 根。{show}" + (" …" if len(runs) > 10 else ""))
L += [
    "",
    "## 切分与标准",
    "",
    f"- 入场 = 信号 5m 现货收盘，结算 = 正好 10 分钟后的 5m 收盘；赢 +0.8 / 输 −1 / 平 0；保本 {pct(BE)}。",
    "- 主样本 2022-01-01 00:00 UTC 至 2024-11-11 07:30 UTC 的信号全部视为样本外（规则于 2026-11-11 提出，未在这段拟合）。",
    "- 近端样本 2024-11-11 07:35 UTC 起只作确认，不得单独宣布通过。",
    "- PASS：主样本决胜 N ≥ 200 且 Wilson95 下限 > 55.56% 且加 1bp 不利滑点后期望 > 0。WATCH：主样本点估计 > 55.56% 但未 PASS。FAIL：其余。",
    f"- Bonferroni 下限：m=32 双侧，z={R['zbonf_m32']:.4f}。胜率剔除平局；期望 R 按全部信号（平局记 0）平均。",
    "- 非重叠：持仓 10 分钟内的新信号跳过。",
    "",
    f"## 正式规则判定：**{R['status']}**",
    "",
]

def table(r, labs):
    lines = [
        "| 子集 | N | W/L/T | 胜率 | Wilson95 | Bonf 下限 | 期望 R | 最长连亏 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for lab in labs:
        s = r[lab]
        ci = "—" if s["wilson_lo"] is None else f"{pct(s['wilson_lo'])}–{pct(s['wilson_hi'])}"
        lines.append(
            f"| {lab} | {s['N']} | {s['W']}/{s['L']}/{s['T']} | {pct(s['win_rate'])} | {ci} | "
            f"{pct(s['bonf_lo'])} | {num(s['exp_R'])} | {s['max_loss_streak']} |"
        )
    return lines

main = R["windows"]["historical_oos"]["at_least_3"]
L += ["### 主样本 at_least_3", ""]
L += table(main, ["all", "nonoverlap"])
o, no = main["all"], main["nonoverlap"]
L.append(
    f"\n滑点：1bp 胜率 {pct(o['win_rate_1bp'])} 期望 {num(o['exp_R_1bp'])}；"
    f"2bp 胜率 {pct(o['win_rate_2bp'])} 期望 {num(o['exp_R_2bp'])}。"
    f"非重叠 1bp 期望 {num(no['exp_R_1bp'])}。"
)
if R["status"] == "PASS" and not R["bonf_pass"]:
    L.append("**经不起多重检验**（Bonferroni 下限未过保本线）。")
if R["status"] in ("PASS", "WATCH") and (no["win_rate"] or 0) <= BE:
    L.append("**非重叠版本翻车**（非重叠胜率不高于保本线）。")
L.append("")

rec = R["windows"]["recent_confirm"]["at_least_3"]
L += ["### 近端确认 at_least_3（不计入通过）", ""]
L += table(rec, ["all", "nonoverlap"])
ro = rec["all"]
L.append(
    f"\n近端滑点：1bp 胜率 {pct(ro['win_rate_1bp'])} 期望 {num(ro['exp_R_1bp'])}；"
    f"2bp 胜率 {pct(ro['win_rate_2bp'])} 期望 {num(ro['exp_R_2bp'])}。"
)
L += ["", "## 附录（不计入通过）：仅第 3 次进场（exactly_3）", ""]
exh = R["windows"]["historical_oos"]["exactly_3_appendix"]
exr = R["windows"]["recent_confirm"]["exactly_3_appendix"]
L += ["### 主样本 exactly_3", ""]
L += table(exh, ["all", "nonoverlap"])
L += ["", "### 近端 exactly_3", ""]
L += table(exr, ["all", "nonoverlap"])
L += ["", "## 附录：主样本正式规则的多空 / 时段 / 年份", ""]
a = main["appendix"]
parts = [f"{d}: {pct(s['win_rate'])} ({s['decided']})" for d, s in a["direction"].items()]
parts += [f"{d}: {pct(s['win_rate'])} ({s['decided']})" for d, s in a["session_utc"].items()]
parts += [f"{d}: {pct(s['win_rate'])} ({s['decided']})" for d, s in a["year"].items()]
L.append("- " + "；".join(parts) if parts else "- 无交易")
L += ["", "月度拆分见 `results.json`。文件：backtest.py、report.py、results.json、trades.csv、sensitivity.csv。"]
open(os.path.join(D, "summary.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))
