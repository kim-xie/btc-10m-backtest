# BTC 10 分钟方向预测回测

这是用 Binance BTCUSDT K 线做 10 分钟方向预测的回测代码（赢 +0.8R / 输 −1R，保本胜率 55.56%）。各批次规则与结论以对应目录里的 `PRE_REGISTERED.md`、`summary.md` 为准。

K 线和成交 CSV 不在本仓库里。`.gitignore` 排除了 `*.csv`、`*.parquet`、`*.feather`、`*.zip` 和 `data/`（因此 `data/` 下的 `fetch_log.json` 也不会入库）。需要先在本机准备各脚本写明的行情文件，再跑回测。

## 目录

- `btc-10m-pattern-backtest/`：第 1 批形态。Setup A 是 20 根内 EMA50 穿越至少 2 次且贴近均线的吞没；Setup B 是至少 3 根强势推进、弱回调后再顺势吞没。脚本为 `fetch.py`、`backtest.py`、`report.py`。`summary.md` 结论是两条都不合格。
- `btc-10m-new-strategies/`：第 2 批预注册，共 12 条。均值回归：`fade_atr15`、`fade_atr20`、`fade_ret40`、`fade_3same`、`rsi_fade`。趋势对照：`cont_atr15`、`break_20`、`ema20_cross`。形态：`inside_break`、`pin_fade`。波动过滤：`fade_atr15_lowvol`、`fade_atr15_highvol`。脚本为 `backtest.py`、`report.py`。`summary.md` 写明 PASS 无，WATCH 为 `fade_ret40`、`rsi_fade`、`fade_atr15_highvol`。
- `btc-10m-batch3/`：第 3 批预注册，10 条非形态因子（主动买比极端顺/逆、放量尖峰顺/逆、吸收、期现主动买比背离、溢价 z、基差 z、ETH 领先、成交笔数尖峰）。脚本为 `fetch.py`、`backtest.py`、`report.py`、`lookahead_check.py`。`summary.md` 写明 PASS 无，WATCH 为 `prem_z_fade`。
- `flow-10m/bt.py`：订单流 R1–R5（2026-11-11 16:59 锁定）。R1/R2 是 5 分钟主动买比顺势与反向，R3 是 15 分钟主动买比顺势，R4/R5 是放量 3 倍后的顺势与逆势。读取 `morning-star-backtest/ytd/kl/` 下的 1 分钟 CSV。
- `btc-10m-combo-vol-highvol/`：看过前面结果后的组合，放量逆势与高波动 ATR 逆势（`fade_atr15_highvol`）必须同向同时触发。确认样本是 2022-01-01 至 2024-11-11 07:35 UTC，不与此前 24 个月样本重叠。脚本为 `fetch.py`、`backtest.py`、`report.py`、`lookahead_check.py`。`summary.md` 的正式判定是不通过。
- `btc-10m-ema50-3cross/`：`ema50_3cross_18`。18 根窗口内阳线上穿 EMA50 至少 3 次则做多，阴线下穿对称做空；第 3 次及之后每次满足都进场。脚本为 `backtest.py`、`report.py`。`summary.md` 判定为 FAIL。
- `ema50-engulf-backtest/bt.py`：早期、较短样本上的 EMA50 穿越加吞没，以及强推进后弱回调再吞没（与第 1 批相近，回调限制为 1–3 根）。读取 `morning-star-backtest/ytd/kl/BTCUSDT-1m-*.csv`。
- `morning-star-backtest/`：早晨之星对 Polymarket BTC 5 分钟 / 15 分钟 Up/Down。脚本为 `backtest_morning_star.py`，说明见该目录 `README.md`。依赖的 `kl/` 与市场 CSV 不在本仓库。
- `btc-10m-backtest-viewer/`：回测查看器。`build.py` 读取上面各批的成交 CSV 和 5 分钟 K 线（路径写在脚本里，例如组合目录与第 3 批 `data/` 下的 K 线，以及各目录的 `trades.csv`），生成同目录的 `data.js`（另写出 `data.bin.gz` 和 `meta.json`）。然后用浏览器直接打开同目录的 `index.html`（需与 `data.js` 放在一起；页面会解压 `data.js` 里的数据）。这些 CSV 不在仓库里时，`build.py` 无法重新生成 `data.js`。

各脚本里的数据路径多写为 `/workspace/...`，换机器时要按实际目录调整，或把仓库放在该路径下。
