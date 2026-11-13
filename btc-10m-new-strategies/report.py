import json, numpy as np, pandas as pd
from backtest import *

def verdict(m):
    if m["N_dec"] >= 100 and m["wilson95_lo"] > BE: return "PASS"
    if m["win_rate_ex_ties"] > BE: return "WATCH"
    return "FAIL"

def f(x, pct=True):
    if x != x: return "–"
    return f"{x*100:.2f}%" if pct else f"{x:+.4f}"

def main():
    d = indicators(load()); S = signals(d)
    res, all_tr, sens = {}, [], []
    for k in STRATS:
        t = make_trades(d, S[k], k); no = nonoverlap(t)
        all_tr.append(t)
        r = {"all": metrics(t), "IS": metrics(t[t.split == "IS"]), "OOS": metrics(t[t.split == "OOS"]),
             "nonoverlap_all": metrics(no), "nonoverlap_IS": metrics(no[no.split == "IS"]),
             "nonoverlap_OOS": metrics(no[no.split == "OOS"])}
        oos = t[t.split == "OOS"]
        r["appendix_OOS"] = {"long": metrics(oos[oos.side == "long"]), "short": metrics(oos[oos.side == "short"]),
                             **{s: metrics(oos[oos.session == s]) for s in ["Asia", "Europe", "US"]}}
        r["verdict"] = verdict(r["OOS"])
        r["nonoverlap_OOS_verdict"] = verdict(r["nonoverlap_OOS"])
        r["bonferroni_pass"] = bool(r["OOS"]["N_dec"] >= 100 and r["OOS"]["bonf_lo"] > BE)
        r["overfit_flag"] = bool(r["IS"]["win_rate_ex_ties"] > BE and r["OOS"]["win_rate_ex_ties"] <= BE)
        if r["verdict"] in ("PASS", "WATCH"):
            for bp in (0, 1, 2):
                tt = make_trades(d, S[k], k, bp)
                for lab, sub in [("OOS", tt[tt.split == "OOS"]), ("nonoverlap_OOS", nonoverlap(tt).query("split=='OOS'"))]:
                    m = metrics(sub); sens.append({"strategy": k, "slip_bp": bp, "set": lab, **m})
        res[k] = r
    tr = pd.concat(all_tr); tr.to_csv("trades.csv", index=False)
    pd.DataFrame(sens, columns=["strategy", "slip_bp", "set", "N", "N_dec", "W", "L", "T", "win_rate_ex_ties",
                 "win_rate_incl_ties", "wilson95_lo", "wilson95_hi", "bonf_lo", "bonf_hi", "exp_R_per_trade",
                 "total_R", "max_consec_losses"]).to_csv("sensitivity.csv", index=False)
    meta = {"data": DATA, "bars": len(d), "first_bar_utc": str(d.t.iloc[0]), "last_bar_utc": str(d.t.iloc[-1]),
            "split_utc": str(SPLIT), "breakeven": BE, "z_bonferroni": Z_BONF}
    json.dump({"meta": meta, "results": res}, open("results.json", "w"), indent=1, default=float)

    L = ["# 预注册新策略回测（BTCUSDT，10 分钟结算）", "",
         f"- 数据来源：Binance 现货 BTCUSDT 5m，`{DATA}`，{len(d)} 根，{d.t.iloc[0]} → {d.t.iloc[-1]}（UTC 开盘时间；北京时间 +8h）。无缺口、无重复。",
         f"- 结算：入场=信号 K 线收盘；出场=其后第 2 根 5m 收盘。赢 +0.8R / 输 −1R / 平 0。无手续费、无滑点（主情形）。",
         f"- 切分：IS = 信号K线开盘 < {SPLIT}（北京 2026-04-06 15:35），OOS = 之后。预热前 {WARMUP} 根不出信号。",
         f"- 保本胜率 {BE*100:.2f}%。**PASS**：OOS 剔除平局胜率 Wilson 95% 下限 > 55.56% 且 OOS 决胜笔数 ≥100；**WATCH**：OOS 点估计 > 55.56% 但未达 PASS；其余 **FAIL**。IS 好 OOS 差 = 过拟合 = FAIL。",
         f"- 多重检验：12 条，Bonferroni 双侧 Wilson CI 水平 1−0.05/12（z={Z_BONF:.3f}）。多空/时段拆分仅附录。",
         "- 非重叠版本：持仓未结束（入场后 2 根内）不开新单，在出场那根收盘可再入场。", ""]
    hdr = "| 集合 | N | 胜/负/平 | 胜率(剔平) | Wilson95% | Bonf CI 下限 | 期望R/笔 | 总R | 最大连亏 |"
    for k in STRATS:
        r = res[k]
        L += [f"## {k} — **{r['verdict']}**" + ("（过拟合标记）" if r["overfit_flag"] else ""), "", hdr, "|---|---|---|---|---|---|---|---|---|"]
        for lab in ["all", "IS", "OOS", "nonoverlap_OOS"]:
            m = r[lab]
            L.append(f"| {lab} | {m['N']} | {m['W']}/{m['L']}/{m['T']} | {f(m['win_rate_ex_ties'])} | [{f(m['wilson95_lo'])}, {f(m['wilson95_hi'])}] | {f(m['bonf_lo'])} | {f(m['exp_R_per_trade'], False)} | {m['total_R']:.1f} | {m['max_consec_losses']} |")
        ap = r["appendix_OOS"]
        L += ["", "附录（OOS，探索性，非正式）：" + "；".join(f"{s} {ap[s]['N_dec']}笔 {f(ap[s]['win_rate_ex_ties'])}" for s in ["long", "short", "Asia", "Europe", "US"]), ""]
    p = [k for k in STRATS if res[k]["verdict"] == "PASS"]; w = [k for k in STRATS if res[k]["verdict"] == "WATCH"]
    L += ["## 结论", "", f"- PASS：{', '.join(p) or '无'}", f"- WATCH：{', '.join(w) or '无'}",
          f"- FAIL：{len(STRATS)-len(p)-len(w)} / 12" + ("（全部不通过）" if not p and not w else ""),
          f"- Bonferroni 调整后通过：{', '.join(k for k in STRATS if res[k]['bonferroni_pass']) or '无'}", "",
          "### 最接近门槛的 3 条（按 OOS Wilson 95% 下限排序）", "", "| 策略 | OOS 决胜N | 胜率 | Wilson95% | 期望R/笔 | 非重叠OOS 胜率 (N) |", "|---|---|---|---|---|---|"]
    top = sorted(STRATS, key=lambda k: -res[k]["OOS"]["wilson95_lo"])[:3]
    for k in top:
        m = res[k]["OOS"]; nm = res[k]["nonoverlap_OOS"]
        L.append(f"| {k} | {m['N_dec']} | {f(m['win_rate_ex_ties'])} | [{f(m['wilson95_lo'])}, {f(m['wilson95_hi'])}] | {f(m['exp_R_per_trade'], False)} | {f(nm['win_rate_ex_ties'])} ({nm['N_dec']}) |")
    if sens:
        L += ["", "### 滑点敏感性（PASS/WATCH）", "", "见 `sensitivity.csv`。"]
        for s in sens:
            L.append(f"- {s['strategy']} {s['set']} {s['slip_bp']}bp：N_dec {s['N_dec']}，胜率 {f(s['win_rate_ex_ties'])}，CI [{f(s['wilson95_lo'])}, {f(s['wilson95_hi'])}]，期望 {s['exp_R_per_trade']:+.4f}R")
    open("summary.md", "w").write("\n".join(L) + "\n")
    print("\n".join(L[-30:]))

if __name__ == "__main__":
    main()
