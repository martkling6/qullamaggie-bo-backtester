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


## Two-stage production backtest

Use the GitHub Actions workflow **Qullamaggie Two Stage Backtest** for the serious test.

Stage 1 scans the full eligible US common-stock universe (active + delisted), computes 1/3/6-month point-in-time momentum ranks across the whole universe, and retains only setup candidates that are in the top 2% on at least one horizon.

Stage 2 downloads 1-minute bars only for those candidate breakout days and executes the opening-range entry. The default is the 5-minute ORH; 1-minute and the 9:30-10:00 opening range can be run as source-supported variants.

### Stop model

The initial stop is the **lowest regular-session price observed before the ORH trigger**. This is the information that would actually have been known when sizing the trade. A completed day's final low is never used to size the position because that would be look-ahead bias. If entry-to-stop distance is wider than 1x ADR20, the setup is rejected rather than moving the stop closer.

After entry, later 1-minute bars can trigger the initial stop. A gap/open below the stop fills at the bar open. After the configured partial sale (baseline: 50% on trading day 4), the stop on the remaining shares moves to the original entry price. The remainder exits on the first daily **close** below SMA10; SMA10 is not treated as an intraday stop order.

### Data-coverage caveat

EODHD documents US 1-minute history from 2004 for NYSE/NASDAQ, but delisted-company intraday availability is materially narrower: delisted before 2018 are EOD-only, 2018-2021 add fundamentals/dividends/splits, and only post-2021 delistings include intraday. Therefore pre-2021 exact-intraday results cannot be fully survivorship-bias-free. The workflow records those missing executions explicitly instead of silently replacing them with daily approximations.
