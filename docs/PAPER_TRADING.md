# AMTE Live Paper Trading

Research-only BTC/USDT futures paper execution. The runner never submits exchange orders.

## Controls

- Virtual capital: ₹100,000
- Risk per trade: 0.25%
- Maximum entries/day: 5
- Daily loss lock: 2%
- Stop: 0.5%
- Target: 1%
- Fees: 5 bps/side
- Slippage: 2 bps
- Long and short fills use bid/ask rather than last trade

## Data

The runner consumes CoinDCX public futures WebSocket `depth-snapshot` and `new-trade` events. The initial experimental signal combines top-of-book imbalance with a signed trade-flow proxy. CoinDCX's maker flag is treated only as a proxy for direction; it is not assumed to be verified aggressor-side classification.

## Evidence policy

This is an **experimental paper signal**, not a validated profitable strategy. A paper run with few trades cannot establish an edge. Do not move to live money based on a positive short run. Promotion requires sustained after-cost paper evidence, operational checks, and exchange execution controls.

## Run

```bash
python scripts/run_paper_trading.py --duration 900
```

Output is written to `data/paper/paper_trades.csv`.
