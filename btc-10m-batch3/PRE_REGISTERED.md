# 预注册第 3 批：10 分钟预测，非形态因子（锁定于 2026-11-11 16:58 北京时间，跑数前不得改）

背景：前两批 10 分钟规则（形态、冲击逆势、突破、RSI、针形等）全部没过 55.56%。这批改用资金流、成交量、期货溢价、ETH 领先这类非形态信息。

## 数据（全部为 Binance 公开接口，5m，与之前相同的 24 个月窗口：2024-11-11 07:35 UTC 到拉取时最近已收盘的一根）
- BTCUSDT 现货 5m klines，保留全部字段（含 quote_volume、trades、taker_buy_base、taker_buy_quote）。
- BTCUSDT USDⓈ-M 永续 5m klines（`/fapi/v1/klines`，同样保留 taker 字段）。
- ETHUSDT 现货 5m klines。
- BTCUSDT 永续溢价指数 5m（`/fapi/v1/premiumIndexKlines`），取 close 作为溢价。
- 拉不到某个数据源就把依赖它的策略标 NOT RUN，绝不造数据。按 open_time 对齐，任何一侧缺 bar 的时刻不出信号。

## 结算（与面板一致，不得改）
- 入场 = 信号 5m K 线收盘价（BTC 现货）；结算 = 其后第 2 根 5m 收盘（10 分钟）。多：exit>entry 赢；空：exit<entry 赢；相等算平。
- 赢 +0.8、输 −1、平 0。保本胜率 55.56%。
- 切分：时间前 70% IS / 后 30% OOS，切点 2026-04-06 07:35:00 UTC（与前批相同；若数据终点不同，仍用此切点）。
- 预热 300 根不出信号。无前视：只用截至信号 K 线收盘的数据。
- 对 PASS/WATCH 加测 1bp、2bp 入场不利滑点。

## 定义
- 现货买盘占比 `tbr = taker_buy_base / volume`（volume=0 跳过）。
- 成交量 z 值 `vz = (volume − 前 288 根均值) / 前 288 根标准差`（不含当前）。
- 收益 `r = close/open − 1`（当前 5m 根）。
- 溢价 `prem` = 永续溢价指数 5m close；`prem_z` = (prem − 前 288 根均值) / 前 288 根标准差。
- 期现价差 `basis = perp_close / spot_close − 1`；`basis_z` 同上用前 288 根。
- ETH 领先：`eth_r` = ETH 当前 5m 的 close/open − 1，`btc_r` 同理。

## 正式策略（10 条，m=10）
1. `tbr_extreme_fade`：tbr ≥ 0.70 做空，≤ 0.30 做多。
2. `tbr_extreme_follow`：tbr ≥ 0.70 做多，≤ 0.30 做空（与 1 方向相反，作对照）。
3. `vol_spike_fade`：vz ≥ 4 且 |r| ≥ 0.15%：逆 r 方向。
4. `vol_spike_follow`：vz ≥ 4 且 |r| ≥ 0.15%：顺 r 方向。
5. `absorption`：vz ≥ 3 且 |r| ≤ 0.03%：若 tbr ≥ 0.60 做空（买盘被吸收），tbr ≤ 0.40 做多。
6. `perp_tbr_div`：永续 tbr ≥ 0.60 且现货 tbr ≤ 0.45 做空；永续 tbr ≤ 0.40 且现货 tbr ≥ 0.55 做多。
7. `prem_z_fade`：prem_z ≥ 3 做空，≤ −3 做多。
8. `basis_z_fade`：basis_z ≥ 3 做空，≤ −3 做多。
9. `eth_lead`：|eth_r| ≥ 0.30%，且 btc_r × sign(eth_r) ≤ 0.5 × |eth_r|（BTC 没跟上或反向）：顺 eth_r 方向做 BTC。（跑数前措辞澄清，非事后修改。）
10. `trades_spike_fade`：成交笔数 z（前 288 根）≥ 4 且 |r| ≥ 0.10%：逆 r 方向。

## 通过标准（锁定）
- 主胜率剔除平局。PASS：OOS 决胜 N ≥ 200，OOS Wilson 95% 下限 > 55.56%，IS 点估计 > 55.56%，且加 1bp 滑点后 OOS 期望 > 0。
- WATCH：OOS 点估计 > 55.56% 但未达 PASS。FAIL：其余。
- Bonferroni（m=10，双侧，z≈2.807）下限另报；只有未调整通过时写“经不起多重检验”。
- 多空、时段、年份只作附录。非重叠版本另报，非重叠若翻车须写明。
- 看完结果后的新规则只能列为事后探索，不计入通过。不下单，不编造数据。

## 交付
`/workspace/btc-10m-batch3/`：fetch.py、backtest.py、report.py、summary.md、results.json、trades.csv、sensitivity.csv。summary 开头写数据源与覆盖、缺口、切点、标准，然后每条策略 all/IS/OOS/非重叠 OOS 表，最后 PASS/WATCH/FAIL 清单与最接近的 3 条。
