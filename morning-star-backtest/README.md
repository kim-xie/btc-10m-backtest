# Morning Star → Polymarket BTC Up/Down 回测（2026-11-04，Asia/Shanghai）

严谨保守回测：经典三根 K 线「早晨之星」是否对 Polymarket 短周期 BTC Up/Down 有扣费后边际。

## 数据

- `kl/` → 软链到 `/workspace/fisher-backtest/kl/`：Binance BTCUSDT 1m（data.binance.vision，2026-09-02..2026-11-03）
- `markets_5m.csv` / `markets_15m.csv` → 软链到 fisher-backtest：Gamma slug `btc-updown-{5m,15m}-<ts>` + CLOB prices-history
- 裁决真相：Polymarket resolution（Chainlink BTC/USD），**不是** Binance OHLC 方向
- Binance vs Chainlink 同窗涨跌一致率：5m **86.4%**，15m **94.1%**（方向错配会稀释任何纯 K 线信号）

## 早晨之星定义（固定教材规则，未在测试集调参）

在选定周期 TF 上取连续三根已收盘 K 线 C1、C2、C3：

1. **C1 长阴**：`close < open`，实体 ≥ `max(1.0 × 近20根实体中位数, 0.6 × ATR14)`（阈值取 C1 时点滚动统计，避免用 C2/C3 信息）
2. **C2 星线小实体**：实体 ≤ `0.5 × 近20根实体中位数`；加密无真正跳空，用替代规则：`open2 ≤ close1` **或** C2 实体完全落在 C1 振幅下半区；且 `close2 ≤ C1 实体中点`（星线未提前大幅反包）
3. **C3 长阳确认**：`close > open`，实体同样 ≥ 上述「长实体」阈值；`close3 ≥ C1 实体中点`（至少收回 C1 实体 50%）；且 `close3 > close2`

**无前视**：信号仅用收盘时刻 ≤ 市场开盘 T 的 K 线。主设定 `aligned`：C3 收盘恰在 T（开盘瞬间可知；主评估入场为开盘后 ~20s mid）。对照 `strict`：C3 收盘在 T−TF。

主周期：与市场窗对齐的 5m / 15m；另试 1m→5m 市场、5m→15m 市场。

## 趋势标签（在 C1 收盘判定，不含 C2/C3）

- SMA(20)、ATR(14)、近 10 根高低点 vs 再往前 10 根
- **下跌**：C1 收盘 < SMA20，且（更低低点 或 价格低于 10 根前），且距 SMA > 0.25·ATR → `下跌趋势中的早晨之星`
- **上涨**：对称 → `上涨趋势中的早晨之星`
- 其余（含贴近 SMA）→ `震荡中的早晨之星`

## 交易与成本

- 信号方向：早晨之星 → **买 Up**（经典看涨反转）
- 主入场：`up_px_post`（(T, T+60] 内首个 CLOB mid，约 +20s）+ **0.005** 半价差 + taker fee `0.07·p·(1−p)`（feesEnabled 时；约在 p≈0.50 时盈亏平衡胜率 ~52.3%）
- 时间切分：按市场开盘时间 **60% train / 40% test**（不打乱）
- 对照：always Up、上一根同 TF 动量、抛硬币

## 主结果（TEST，aligned，市场同周期，post 入场）

| 市场 | 趋势 | n | 胜率 | 95% CI | 盈亏平衡 | 每 $100 均 PnL | 最大回撤 |
|------|------|--:|-----:|--------|----------|---------------:|---------:|
| 5m | ALL | 22 | 45.5% | 26.9–65.3% | 55.6% | −29.7 | 654 |
| 5m | 下跌 | 14 | 35.7% | 16.3–61.2% | 54.2% | −47.2 | 660 |
| 5m | 上涨 | 6 | 66.7% | 30.0–90.3% | 61.0% | +7.4 | 200 |
| 5m | 震荡 | 2 | 50.0% | — | — | — | — |
| 15m | ALL | 15 | 53.3% | 30.1–75.2% | 55.0% | +2.7 | 400 |
| 15m | 下跌 | 7 | 85.7% | 48.7–97.4% | 52.7% | +68.5 | 100 |
| 15m | 上涨 | 6 | 33.3% | 9.7–70.0% | 56.4% | −39.9 | 300 |

Train 对照（同设定）：5m ALL n=45、胜率 66.7%、均 PnL +19.3（**样本内好看，样本外翻车**）；15m ALL n=24、胜率 33.3%、均 PnL −41.6。

全样本信号数（aligned）：5m 67（跌 42 / 涨 17 / 震 8）；15m 39（跌 20 / 涨 13 / 震 6）。形态本身很稀。

Baseline TEST 均 PnL 亦为负（always Up 约 −5~−7 / $100），但早晨之星在 5m 主设定上更差，且 **下跌趋势子集并未更好**（反而最差）。

15m「下跌」TEST 7 笔里 6 胜看起来亮眼，但 train 同子集仅 30.8%、全样本下跌胜率 50%，属小样本噪音，**不能**当作可交易优势。

## 结论（保守）

**扣费后，早晨之星对 Polymarket BTC 5m/15m Up/Down 没有稳健边际。**  
尤其「下跌趋势中的早晨之星」在主设定（5m aligned TEST）胜率与 PnL 均差，未验证教材「跌势反转」假设。Train/Test 不一致 + n 很小 + 信号出现后 Up 入场价常被买贵（breakeven 常 >55%）→ 拒绝该策略。

## 文件

- `backtest_morning_star.py` — 定义、趋势、回测、统计
- `results.csv` — 全配置 × train/test/all × 趋势切片
- `trades.csv` — 主设定（aligned + 市场 TF）成交明细
- `examples.csv` — 各趋势示例时间戳（UTC + Asia/Shanghai）
- `summary.txt` — 控制台摘要

## 复现

```bash
cd /workspace/morning-star-backtest
python3 backtest_morning_star.py
```
