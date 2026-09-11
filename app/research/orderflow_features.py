"""Causal features from CoinDCX futures trade and order-book snapshots."""
from __future__ import annotations

import json
import numpy as np
import pandas as pd


def _levels(value: str | dict) -> list[tuple[float, float]]:
    obj = json.loads(value) if isinstance(value, str) else value
    return sorted((float(p), float(q)) for p, q in obj.items())


def orderbook_features(orderbooks: pd.DataFrame, depth_levels: int = 10) -> pd.DataFrame:
    """Compute snapshot features without using future observations."""
    rows = []
    for ts, row in orderbooks.iterrows():
        bids = _levels(row["bids_json"])[-depth_levels:]
        asks = _levels(row["asks_json"])[:depth_levels]
        if not bids or not asks:
            continue
        bid = bids[-1][0]
        ask = asks[0][0]
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid if mid else np.nan
        bid_qty = sum(q for _, q in bids)
        ask_qty = sum(q for _, q in asks)
        total = bid_qty + ask_qty
        imbalance = (bid_qty - ask_qty) / total if total else 0.0
        microprice = (ask * bid_qty + bid * ask_qty) / total if total else mid
        rows.append({"timestamp_ms": int(row["timestamp_ms"]), "mid": mid, "spread_bps": spread * 10000, "bid_depth": bid_qty, "ask_depth": ask_qty, "depth_imbalance": imbalance, "microprice_minus_mid_bps": (microprice / mid - 1) * 10000 if mid else np.nan})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["timestamp"] = pd.to_datetime(out.timestamp_ms, unit="ms", utc=True)
    return out.set_index("timestamp").sort_index()


def trade_features(trades: pd.DataFrame, rule: str = "maker") -> pd.DataFrame:
    """Aggregate trades into one-second buckets; all fields are backward-looking."""
    x = trades.copy()
    x["timestamp"] = pd.to_datetime(x.timestamp_ms, unit="ms", utc=True)
    x["price"] = x.price.astype(float)
    x["quantity"] = x.quantity.astype(float)
    x["signed_qty"] = np.where(x.is_maker.astype(str).isin(["1", "True", "true"]), -x.quantity, x.quantity)
    x = x.set_index("timestamp").sort_index()
    g = x.resample("1s")
    out = g.agg(price=("price", "last"), trade_count=("price", "size"), volume=("quantity", "sum"), signed_volume=("signed_qty", "sum"))
    out["trade_imbalance"] = out.signed_volume / out.volume.replace(0, np.nan)
    out["volume_accel"] = out.volume / out.volume.rolling(30, min_periods=10).mean().replace(0, np.nan)
    out["ret_1s"] = out.price.pct_change()
    out["rv_30s"] = out.ret_1s.rolling(30, min_periods=10).std() * np.sqrt(30)
    return out


def merge_asof_features(trades: pd.DataFrame, orderbooks: pd.DataFrame) -> pd.DataFrame:
    """Attach the latest order-book snapshot available at each trade bucket."""
    t = trade_features(trades).reset_index()
    b = orderbook_features(orderbooks).reset_index()
    if t.empty or b.empty:
        return t
    return pd.merge_asof(t.sort_values("timestamp"), b.sort_values("timestamp"), on="timestamp", direction="backward")
