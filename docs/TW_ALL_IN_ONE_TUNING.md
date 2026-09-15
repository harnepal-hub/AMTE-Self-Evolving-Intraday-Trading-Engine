# TW All in One — filtered research result

## Source signal
The supplied Pine indicator uses EHMA, `HULL[2]`, and crossover/crossunder signals. AMTE preserves that signal direction and does not use future bars.

## Historical test
Dataset: existing CoinDCX BTC/USDT futures 5-minute dataset, 105,102 rows.

The raw TW signal generated too many entries and was strongly negative. Filtering was therefore tested on the historical development portion first.

Filter families tested included:
- Hull length: 8/12/16/20/24
- EMA trend: none/100/200
- Hull slope confirmation
- RSI directional confirmation
- volume ratio thresholds
- ATR expansion
- minimum distance from EMA
- stop/target variations

### Result
No configuration with a meaningful sample produced a robust profitable development + validation result.

The best development configuration with at least 30 trades was:
- Hull length: 8
- EMA: 200
- EMA trend filter: ON
- Hull slope filter: ON
- volume ratio >= 2.0
- ATR expansion >= 1.2x its 50-bar prior mean
- close at least 1.0% beyond EMA200 in signal direction
- stop: 0.5%
- target: 1.0%

Development result:
- trades: 44
- net P&L: approximately -₹1,181
- profit factor: 0.864
- win rate: 38.6%

A more aggressively selected development candidate could show positive P&L, but its validation sample was only 3 trades and was negative (approximately -₹182, PF 0.706). It is rejected as overfit.

## Decision
**NO-GO for live money.**

The filtered profile is retained only as an experimental paper-trading candidate so that real-time behavior can be measured without risking capital.

Paper profile:
- virtual capital ₹1,00,000
- risk 0.25% per trade
- maximum 5 trades/day
- daily loss lock 2%
- bid/ask-aware fills
- 5 bps/side fees
- 2 bps slippage
- no exchange order placement

The old final holdout is treated as burned and is not used for parameter selection.
