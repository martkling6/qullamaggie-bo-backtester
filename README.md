# Qullamaggie BO Backtester

Research backtester for Kristjan Kullamägi's **Breakout / Momentum Continuation** setup using EODHD data.

## What v0.1 implements

Primary-source structure: a strong prior move (roughly 30–100%+ over 1–3 months), an orderly 2-week-to-2-month consolidation with tightening ranges/higher lows around rising 10/20-day moving averages, followed by range expansion. ADR20 follows Kullamägi's FAQ definition: average of `High/Low - 1` over 20 sessions.

This first version is intentionally **daily-data only**. Kullamägi's published execution uses opening-range highs and a low-of-day stop. Those cannot be reproduced faithfully from daily bars because the intraday order of high/low is unknown. Therefore v0.1 uses a documented approximation: breakout above the daily pivot on the next session and a stop based only on information available before entry, capped to one ADR. This avoids look-ahead bias. Intraday ORH testing is a later module.

## Data integrity

EODHD EOD OHLC is raw while `adjusted_close` includes splits and dividends. The backtester downloads historical split events and back-adjusts OHLC **for splits only** so stock splits do not create fake breakouts. EODHD's volume is already split-adjusted. The universe loader can request both active and delisted US common stocks to reduce survivorship bias.

## GitHub Actions

Create repository secret `EODHD_API_TOKEN`, then open **Actions → BO Backtest → Run workflow**. Start with the default five symbols. Results are uploaded as an artifact containing `trades.csv` and `summary.json`.

## Current test parameters

The values in `config.yaml` are **research hypotheses**, not claims that Kullamägi published those exact thresholds. We will sweep and validate them rather than optimizing one hand-picked setting.

## Roadmap

1. Validate data/split handling and daily event logic on known charts.
2. Add parameter-grid and walk-forward/out-of-sample testing.
3. Scale to active + delisted US common stocks.
4. Add market-regime variants and portfolio-level capital/risk constraints.
5. Add intraday Opening Range High execution only when suitable intraday data is available.
