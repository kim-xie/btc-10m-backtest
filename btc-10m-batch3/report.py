"""Build summary.md from results.json + lookahead check file."""
import json, os
import pandas as pd
D = os.path.dirname(os.path.abspath(__file__))
R = json.load(open(os.path.join(D, "results.json")))
BE = R["breakeven"]
ts = lambda ms: pd.to_datetime(int(ms), unit="ms", utc=True).strftime("%Y-%m-%d %H:%M UTC")
pct = lambda x: "—" if x is None else f"{100*x:.2f}%"
num = lambda x: "—" if x is None else f"{x:+.4f}"
L = ["# 第 3 批 10 分钟非形态因子回测结果", ""]
L += ["规则来源：`PRE_REGISTERED.md`（锁定于 2026-11-11 16:58 北京时间），10 条照原文实现，阈值未改。不下单，数据全部来自 Binance 公开接口。", ""]
L += ["## 数据源与覆盖", "", "| 序列 | 接口 | 行数 | 首根 | 末根 | 缺 bar 数 | 缺口段数 |", "|---|---|---|---|---|---|---|"]
for k, v in R["fetch_log"].items():
    g = R["gaps"].get(k, {})
    if v.get("ok"):
        L.append(f"| {k} | {', '.join(v['hosts'])} | {v['rows']} | {ts(v['first_open'])} | {ts(v['last_open'])} | {g.get('missing_bars')} | {len(g.get('runs', []))} |")
    else:
        L.append(f"| {k} | 失败：{v.get('error')} | | | | | |")
L += ["", f"统一时间网格：{ts(R['grid_first'])} 到 {ts(R['grid_last'])}，共 {R['grid_bars']} 根 5m（以 BTC 现货末根为终点；其他序列末根之后的部分视为缺 bar）。", ""]
L += ["缺口明细（开始–结束 open_time，根数；网格内）："]
for k, g in R["gaps"].items():
    runs = g["runs"]
    if not runs: L.append(f"- {k}：无缺口"); continue
    show = "; ".join(f"{ts(a)}–{ts(b)} ({n})" for a, b, n in runs[:15])
    L.append(f"- {k}：{len(runs)} 段 / {g['missing_bars']} 根。{show}" + (" …" if len(runs) > 15 else ""))
L += ["", "缺 bar 处理：任何所需序列缺当根、或结算根（其后第 2 根现货收盘）缺失则不出信号；滚动 z 值要求前 288 根网格全齐，否则当根不出信号（缺口后约 24 小时内依赖 z 值的策略不出信号）。", ""]
L += ["## 切分与标准", "",
      f"- 入场 = 信号 5m 现货收盘，结算 = 其后第 2 根 5m 收盘；赢 +0.8 / 输 −1 / 平 0；保本 {pct(BE)}。",
      f"- IS：信号 open_time < 2026-04-06 07:35 UTC；OOS：≥ 该切点。预热前 300 根不出信号。滚动统计用前 288 根（不含当前）。",
      "- PASS：OOS 决胜 N ≥ 200 且 OOS Wilson95 下限 > 55.56% 且 IS 点估计 > 55.56% 且 OOS 加 1bp 不利滑点后期望 > 0。WATCH：OOS 点估计 > 55.56% 但未 PASS。FAIL：其余。",
      "- Bonferroni 下限：m=10 双侧，z=2.807 的 Wilson 下限。胜率剔除平局；期望 R 按全部信号（含平局记 0）平均。最长连亏：平局不中断也不计入。",
      "- 非重叠：同一策略持仓期间（入场后 2 根内）的新信号跳过，下一笔最早在上一笔结算那根收盘入场。", ""]
la = R.get("lookahead")
if la: L += ["## 无前视检查", "", la, ""]
L += ["## 各策略结果（0bp）", ""]
hdr = "| 子集 | N | W/L/T | 胜率 | Wilson95 | Bonf 下限 | 期望 R | 最长连亏 |"
for name, r in R["strategies"].items():
    L.append(f"### {name} — **{r['status']}**")
    if r["status"] == "NOT RUN": L += [f"缺数据源：{r['missing']}", ""]; continue
    L += ["", hdr, "|---|---|---|---|---|---|---|---|"]
    for lab in ["all", "IS", "OOS", "nonoverlap_OOS"]:
        s = r[lab]
        ci = "—" if s["wilson_lo"] is None else f"{pct(s['wilson_lo'])}–{pct(s['wilson_hi'])}"
        L.append(f"| {lab} | {s['N']} | {s['W']}/{s['L']}/{s['T']} | {pct(s['win_rate'])} | {ci} | {pct(s['bonf_lo'])} | {num(s['exp_R'])} | {s['max_loss_streak']} |")
    o = r["OOS"]; no = r["nonoverlap_OOS"]
    L.append(f"\nOOS 滑点：1bp 胜率 {pct(o['win_rate_1bp'])} 期望 {num(o['exp_R_1bp'])}；2bp 胜率 {pct(o['win_rate_2bp'])} 期望 {num(o['exp_R_2bp'])}。非重叠 OOS 1bp 期望 {num(no['exp_R_1bp'])}。")
    if r["status"] == "PASS" and not r["bonf_pass"]: L.append("**经不起多重检验**（Bonferroni 下限未过保本线）。")
    if r["status"] in ("PASS", "WATCH") and (no["win_rate"] or 0) <= BE: L.append("**非重叠版本翻车**（非重叠 OOS 胜率不高于保本线）。")
    L.append("")
st = {k: v["status"] for k, v in R["strategies"].items()}
L += ["## 结论", ""]
for lab in ["PASS", "WATCH", "FAIL", "NOT RUN"]:
    L.append(f"- {lab}（{sum(1 for v in st.values() if v == lab)}）：{', '.join(k for k, v in st.items() if v == lab) or '无'}")
ran = [(k, v) for k, v in R["strategies"].items() if v["status"] != "NOT RUN" and v["OOS"]["wilson_lo"] is not None]
close3 = sorted(ran, key=lambda kv: -kv[1]["OOS"]["wilson_lo"])[:3]
L += ["", "最接近的 3 条（按 OOS Wilson95 下限排序）：", "",
      "| 策略 | OOS N(决胜) | OOS 胜率 | Wilson95 | Bonf 下限 | IS 胜率 | 非重叠 OOS 胜率(N) | OOS 1bp 胜率/期望 | OOS 2bp 胜率/期望 |", "|---|---|---|---|---|---|---|---|---|"]
for k, v in close3:
    o, i_, no = v["OOS"], v["IS"], v["nonoverlap_OOS"]
    L.append(f"| {k} | {o['decided']} | {pct(o['win_rate'])} | {pct(o['wilson_lo'])}–{pct(o['wilson_hi'])} | {pct(o['bonf_lo'])} | {pct(i_['win_rate'])} | {pct(no['win_rate'])} ({no['decided']}) | {pct(o['win_rate_1bp'])} / {num(o['exp_R_1bp'])} | {pct(o['win_rate_2bp'])} / {num(o['exp_R_2bp'])} |")
L += ["", "## 附录（仅参考，不计入通过）：OOS 多空 / 时段（UTC）", ""]
for k, v in ran:
    a = v["appendix_OOS"]
    parts = [f"{d}: {pct(s['win_rate'])} ({s['decided']})" for d, s in a["direction"].items()]
    parts += [f"{d}: {pct(s['win_rate'])} ({s['decided']})" for d, s in a["session_utc"].items()]
    L.append(f"- {k}：" + "；".join(parts))
L += ["", "月度 OOS 拆分见 `results.json` 的 `appendix_OOS.month`。文件：fetch.py、backtest.py、report.py、results.json、trades.csv（strategy 列，out_0bp/1bp/2bp：1 赢 −1 输 0 平）、sensitivity.csv。"]
open(os.path.join(D, "summary.md"), "w").write("\n".join(L) + "\n")
print("\n".join(L))
