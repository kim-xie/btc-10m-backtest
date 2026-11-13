"""Write summary.md from results.json (and lookahead notes already in results)."""
from __future__ import annotations

import json
import os

import pandas as pd

DIR = os.path.dirname(os.path.abspath(__file__))
BE = 1.0 / 1.8


def pct(x):
    if x is None or x != x:
        return "—"
    return f"{100 * x:.2f}%"


def num(x, digits=4):
    if x is None or x != x:
        return "—"
    return f"{x:+.{digits}f}"


def ci(lo, hi):
    if lo is None or lo != lo:
        return "—"
    return f"[{pct(lo)}, {pct(hi)}]"


def ts_bj(s):
    """Render a UTC timestamp string also as Asia/Shanghai."""
    if not s:
        return "—"
    t = pd.Timestamp(s)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    bj = t.tz_convert("Asia/Shanghai")
    return f"{t.strftime('%Y-%m-%d %H:%M:%S UTC')}（北京时间 {bj.strftime('%Y-%m-%d %H:%M:%S')}）"


def main():
    R = json.load(open(os.path.join(DIR, "results.json")))
    meta = R["meta"]
    v = R["verdict"]
    m0 = R["metrics"]["0"]["all"] if "0" in R["metrics"] else R["metrics"][0]["all"]
    # json keys may be ints written as strings
    metrics = R["metrics"]
    def getbp(bp, lab="all"):
        key = str(bp) if str(bp) in metrics else bp
        return metrics[key][lab]

    m0 = getbp(0, "all")
    m1 = getbp(1, "all")
    m2 = getbp(2, "all")
    n0 = getbp(0, "nonoverlap")
    n1 = getbp(1, "nonoverlap")
    n2 = getbp(2, "nonoverlap")
    fl = meta.get("fetch_log") or {}

    status = v["status"]
    already = v["already_rejected_regardless_of_m"]
    if status == "PASS":
        verdict_line = (
            f"**正式判定（m=31）：通过。** 确认样本 N={m1['N']} ≥ 200，"
            f"1bp 期望 {num(m1['exp_R'])}R > 0，"
            f"Bonferroni（m=31）Wilson 下限 {pct(m1['bonf_m31_lo'])} > 55.56%。"
        )
    elif already:
        reasons = []
        if not v["N_ge_200"]:
            reasons.append(f"N={m1['N']} < 200")
        if not v["exp_1bp_positive"]:
            reasons.append(f"1bp 期望 {num(m1['exp_R'])}R ≤ 0")
        if not v["unadj_wilson95_lo_gt_be"]:
            reasons.append(f"未校正 Wilson 95% 下限 {pct(m1['wilson95_lo'])} ≤ 55.56%")
        verdict_line = (
            f"**正式判定（m=31）：不通过。** 即使不看多重检验、也不论 n_flow / m 取多少，"
            f"本条已经不能过线（{ '；'.join(reasons) }）。不下单。"
        )
    else:
        verdict_line = (
            f"**正式判定（m=31）：不通过。** N 与 1bp 期望、未校正 Wilson 95% 下限均已检查；"
            f"官方 Bonferroni（m=31）Wilson 下限 {pct(m1['bonf_m31_lo'])} 未高于 55.56%。不下单。"
        )

    L = []
    L += ["# 预注册组合规则回测：放量逆势 ∩ 高波动逆势（10 分钟结算）", ""]
    L += [
        "规则于 2026-11-11 17:04 北京时间写入 `PRE_REGISTERED.md` 后锁定，看结果前未改任何阈值或验证段。"
        "多重检验 m 于 2026-11-11 17:26 北京时间、打开结果前锁定为 **31**（25 + n_flow=6）。"
        "本段整段都是确认样本，不再内切 IS/OOS。不下单，不编造数据。",
        "",
        "## 数据源与窗口",
        "",
        f"- 来源：Binance 现货 REST `{fl.get('source_url', 'https://api.binance.com/api/v3/klines')}`，"
        f"symbol=BTCUSDT，interval=1m。实际请求主机：{', '.join(fl.get('hosts_used') or []) or '—'}。",
        f"- 1 分钟 K 线：{meta.get('rows_1m')} 根，{ts_bj(meta.get('first_1m'))} → {ts_bj(meta.get('last_1m'))}。",
        f"- 其中结算专用 1 分钟 K 线（open_time ≥ 2024-11-11 07:35:00 UTC，不作信号）："
        f"{fl.get('settlement_only_1m', '—')} 根。",
        f"- 由 1 分钟聚合的完整 5 分钟 K 线（5 根 1 分钟必须齐全，否则该 5 分钟槽位视为缺失）："
        f"{meta.get('rows_5m')} 根，{ts_bj(meta.get('first_5m'))} → {ts_bj(meta.get('last_5m'))}。"
        f"角色计数：{meta.get('role_counts_5m')}。",
        f"- 信号入场时间（5 分钟收盘时刻）：{meta.get('signal_window')}。"
        "最后一笔可能的信号收盘为 2024-11-11 07:30:00 UTC；07:35 及之后的 5 分钟收盘只允许作结算价，不发新信号。",
        f"- 1 分钟缺口：{fl.get('gaps_1m_bars', '—')} 根，共 {len(fl.get('gaps_1m_runs') or [])} 段。",
        f"- 5 分钟网格缺失/不完整槽位：{(fl.get('grid_5m') or {}).get('missing_or_incomplete_5m', '—')}。"
        "缺失的 5 分钟槽位当根无信号，且会破坏后续需要它的 5 日同口径成交量历史。",
        "",
    ]
    # gap detail
    runs = fl.get("gaps_1m_runs") or []
    if runs:
        L.append("1 分钟缺口明细（open 时间，UTC）：")
        for r in runs[:30]:
            L.append(f"- {r.get('start_utc')} → {r.get('end_utc')}（{r.get('bars')} 根）")
        if len(runs) > 30:
            L.append(f"- … 另有 {len(runs) - 30} 段，见 `data/fetch_log.json`")
        L.append("")
    miss5 = (fl.get("grid_5m") or {}).get("missing_5m_opens_utc") or []
    if miss5:
        shown = ", ".join(miss5[:25])
        more = " …" if (fl.get("grid_5m") or {}).get("missing_5m_opens_utc_truncated") or len(miss5) > 25 else ""
        L += [f"不完整/缺失的 5 分钟 open：{shown}{more}", ""]

    L += [
        "## 锁定规则（简述）",
        "",
        "在每个有效 5 分钟收盘 t，两个条件必须同时触发且方向相同才开仓：",
        "",
        "1. **放量逆势**：`vol_5m(t)`（t−5min 至 t−1min 共 5 根 1 分钟成交量之和）"
        "大于过去 5 个自然日同一时钟时刻 `vol_5m` 中位数的 3 倍（5 个值必须齐全）；"
        "若 close(t) > close(t−5min) 做空，若 close(t) < close(t−5min) 做多，相等不触发。",
        "2. **fade_atr15_highvol**（5 分钟）：Wilder ATR14（信号根可用截至该根的 ATR）；"
        "ATR14/close 高于过去 200 根（i−200…i−1，不含当前）该比值的中位数；"
        "若 (close−open)/ATR14 ≥ +1.5 做空，≤ −1.5 做多；ATR14=0 不触发。不足 200 根预热不出信号。",
        "",
        "入场价 = 信号 5 分钟收盘价；结算价 = 其后第 2 根 5 分钟收盘价（持有 10 分钟）。"
        "赢 +0.8，输 −1；结算价等于入场价（或滑点后入场价）视为输（−1），不记平局。"
        "主表统计全部信号；非重叠版本在持仓未结束前不开新单，上一笔结算那根收盘可以再入场。"
        "滑点只调入场价（多头 ×1.0001 / 1.0002，空头 ×0.9999 / 0.9998），结算价不调。",
        "",
        "通过标准（事先锁定）：确认样本 N≥200；1bp 滑点后期望每笔 > 0；"
        "Bonferroni m=31（Wilson 置信水平 1−0.05/31）校正下限 > 55.56%。"
        "公式：`m = 25 + n_flow`，已锁定 n_flow=6，故官方 m=31。m=25 仅作敏感性备注。",
        "",
        "## 主结果",
        "",
        verdict_line,
        "",
        "| 集合 | 滑点 | N | 胜/负 | 胜率 | Wilson 95% CI | Bonf 下限 m=31 | Bonf 下限 m=25（备注） | 期望 R/笔 | 总 R | 最大回撤 (R) | 最长连亏 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lab, bp, m in [
        ("全部信号（主表）", 0, m0),
        ("全部信号（主表）", 1, m1),
        ("全部信号（主表）", 2, m2),
        ("非重叠", 0, n0),
        ("非重叠", 1, n1),
        ("非重叠", 2, n2),
    ]:
        L.append(
            f"| {lab} | {bp}bp | {m['N']} | {m['W']}/{m['L']} | {pct(m['win_rate'])} | "
            f"{ci(m['wilson95_lo'], m['wilson95_hi'])} | {pct(m['bonf_m31_lo'])} | {pct(m['bonf_m25_lo'])} | "
            f"{num(m['exp_R'])} | {m['total_R']:.1f} | {num(m['max_dd_R'], 2)} | {m['max_loss_streak']} |"
        )

    L += [
        "",
        f"- 未校正 Wilson 95% 下限（0bp）：{pct(m0['wilson95_lo'])}；1bp：{pct(m1['wilson95_lo'])}。",
        f"- 官方 Bonferroni 下限 m=31（0bp）：{pct(m0['bonf_m31_lo'])}；1bp：{pct(m1['bonf_m31_lo'])}。"
        f"z={meta.get('z_bonf_m31')}。",
        f"- 敏感性 Bonferroni 下限 m=25（1bp）：{pct(m1['bonf_m25_lo'])}（不作为正式判定）。",
        f"- 最大回撤按累计 R、权益从 0 起算的峰值回撤（最负值）；同期累计 R 最低点为 {num(m0['min_running_sum_R'], 2)}R（0bp）。",
        f"- 条件 1 在确认窗口内触发 {meta.get('s1_in_signal_window')} 次；条件 2 触发 {meta.get('s2_in_signal_window')} 次；"
        f"同向同时满足 {meta.get('and_agree_in_signal_window')} 次（再剔除缺失结算根后的成交见上表 N）。",
        f"- 即使看 0bp 主表：胜率 {pct(m0['win_rate'])}，期望 {num(m0['exp_R'])}R，Wilson 95% 下限 {pct(m0['wilson95_lo'])}，已经不过线。",
        f"- 非重叠 0bp 点估计 {pct(n0['win_rate'])}、期望 {num(n0['exp_R'])}R，略好于主表，但 Wilson 95% 下限 {pct(n0['wilson95_lo'])} 仍不过 55.56%，且 1bp 后期望 {num(n1['exp_R'])}R 为负，不能改变不通过的结论。",
        "",
    ]

    la = R.get("lookahead")
    if la:
        L += ["## 无前视检查", "", la if isinstance(la, str) else " ".join(la) if isinstance(la, list) else str(la), ""]

    # appendix
    app = R.get("appendix_0bp") or {}
    if app:
        L += ["## 附录（仅参考，不计入通过）：多空 / 时段（0bp，信号收盘时刻 UTC）", ""]
        if app.get("side"):
            parts = [f"{s}: {pct(mm['win_rate'])}（N={mm['N']}）" for s, mm in app["side"].items()]
            L.append("- 方向：" + "；".join(parts))
        if app.get("session_utc"):
            parts = [f"{s}: {pct(mm['win_rate'])}（N={mm['N']}）" for s, mm in app["session_utc"].items()]
            L.append("- 时段：" + "；".join(parts))
        L.append("")

    L += [
        "## 文件",
        "",
        "- `PRE_REGISTERED.md`、`fetch.py`、`backtest.py`、`report.py`、`lookahead_check.py`",
        "- `data/klines_btcusdt_1m.csv`、`data/klines_btcusdt_5m.csv`、`data/fetch_log.json`",
        "- `results.json`、`trades.csv`、`sensitivity.csv`（0/1/2 bp）",
        "",
        verdict_line,
        "",
    ]
    open(os.path.join(DIR, "summary.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L), flush=True)


if __name__ == "__main__":
    main()
