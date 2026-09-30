# Qullamaggie BO Backtester

Systematic research project for Kristjan Kullamägi's **Breakout / Momentum Continuation** setup using EODHD historical data.

## Source-defined structure

The published setup has three core phases: (1) a strong move higher during the prior 1–3 months, commonly 30–100%+, (2) an orderly consolidation lasting roughly 2 weeks to 2 months with higher lows and tightening ranges while price surfs rising 10/20-day moving averages (sometimes 50-day), and (3) range expansion out of the consolidation. Kullamägi's FAQ defines ADR20 as the 20-session average of `High/Low - 1` in percent.

Published execution is intraday: entry on an opening-range high (1-, 5- or 60-minute), stop at the low of day with width no greater than ADR/ATR, sell roughly 1/3–1/2 after 3–5 days, move stop to breakeven, and trail the remainder on the 10- or 20-day moving average.

## Daily-data approximation

The current EODHD plan/backtest uses daily bars. Daily OHLC cannot reveal whether the breakout high or the low occurred first, so it cannot honestly reproduce an ORH entry plus same-day low stop. The research engine therefore uses a conservative, explicit approximation based only on information available before entry. This prevents intraday look-ahead. Exact ORH testing remains a later intraday module.

## Data integrity

EODHD EOD OHLC is raw; adjusted close includes splits and dividends; volume is split-adjusted. We fetch split events and back-adjust OHLC for splits. If corporate-action rows are unavailable for an older delisted name, the loader can infer only large split-like adjustment steps from the adjusted/raw close ratio as a fallback.

The broad-research universe is built from EODHD's US `common_stock` symbol lists. With delisted enabled it requests active and delisted lists separately, then uses a deterministic 50/50 sample. This is a validation step toward a larger survivorship-bias-aware universe, not yet a complete point-in-time CRSP-style universe.

## Workflows

### BO Backtest
Small five-symbol smoke test.

### BO Broad Research
Default validation run:
- 200 deterministic US common stocks
- 100 active + 100 delisted
- history from 2005
- baseline strategy
- in-sample vs out-of-sample split at 2021-01-01
- yearly statistics
- tail-dependence statistics
- one-factor-at-a-time robustness checks

Outputs:
- `trades.csv`
- `summary.json`
- `sample_summary.csv`
- `yearly_summary.csv`
- `sensitivity.csv`
- `universe.csv`
- `data_errors.csv`

The sensitivity run is deliberately **not** a grid-search winner picker. Each rule is moved one dimension at a time so we can see whether the edge is stable rather than overfit a single parameter combination.

## Research hypotheses vs published rules

Thresholds such as minimum ADR, exact base length, liquidity floor, contraction ratio and pivot distance are research hypotheses. They must not be presented as exact Kullamägi rules unless explicitly supported by his published material.

## Roadmap

1. Broad active + delisted validation and data-quality audit.
2. Robustness by year, market regime, liquidity and volatility bucket.
3. Walk-forward parameter selection with untouched out-of-sample periods.
4. Portfolio simulation with overlapping positions, capital constraints and risk sizing.
5. Larger/full historical universe in API-safe batches.
6. Exact Opening Range High execution if suitable intraday data is added.


## Published Qullamaggie BO profile used by this research

The baseline is constrained to Kristjan Kullamägi's published Breakout framework:
- leaders from the top 1-2% over 1-, 3- or 6-month performance;
- a 30-100%+ prior move during roughly the prior 1-3 months;
- an orderly 2-week to 2-month consolidation with higher lows, tightening ranges, and rising 10/20-day moving averages;
- breakout entry; published execution prefers 1-, 5- or 60-minute opening-range highs;
- stop at the low of day, no wider than one ADR/ATR;
- sell 1/3 to 1/2 after 3-5 days, move the remainder stop to breakeven, and trail the rest with the 10- or 20-day moving average (10-day baseline);
- account-risk baseline 0.5%, with a hard 30% single-position overnight cap.

Important: Kullamägi publishes ranges and discretionary chart-selection language, not one fully deterministic algorithm. The current EOD-only test therefore uses mechanical proxies for qualitative setup selection and a daily execution approximation. In particular, exact ORH entry and the low-of-day stop cannot be reconstructed from daily OHLC bars without intraday sequencing. Sample runs also rank leaders relative to the sampled universe; literal whole-market top-2% ranking requires a full-universe run.
