# AMTE Historical Data Contract

AMTE backtests consume canonical OHLCV data with these columns:

`timestamp, open, high, low, close, volume`

## Rules

- `timestamp` is unique and sorted ascending.
- Timestamps should be timezone-aware; crypto data is stored in UTC.
- NSE data is normalized to `Asia/Kolkata`.
- OHLC prices must be positive.
- `high >= max(open, close)`.
- `low <= min(open, close)`.
- Volume cannot be negative.
- Raw downloaded data belongs under `data/raw/` and is ignored by Git.
- Processed datasets belong under `data/processed/` and are also ignored by Git.
- Backtests must record the exact instrument, timeframe, source, date range and cost assumptions used.

## Delta Exchange V1 source

The first real-data adapter uses Delta Exchange India's public historical candle endpoint. It supports 5-minute candles and automatically splits longer requests because the API returns a maximum of 2000 candles per response.

No API key is required for public historical candles. Credentials must never be committed to the repository.

## First dataset

The first AMTE real-data validation dataset is **BTCUSD, 5-minute candles** from Delta Exchange India. This is a data-engineering benchmark, not a claim that BTCUSD is the best trading instrument.
