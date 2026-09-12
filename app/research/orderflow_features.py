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
    if depth_levels < 1:
        raise ValueError("depth_levels must be positive")
    rows = []
    for _, row in orderbooks.iterrows():
        levels = _levels(row["bids_json"]), _levels(row["asks_json"])
        bids = sorted(levels[0], reverse=True)[:depth_levels]
        asks = sorted(levels[1])[:depth_levels]
        if not bids or not asks:
            continue
        bid, bid_qty_top = bids[0]
        ask, ask_qty_top = asks[0]
        mid = (bid + ask) / 2
        spread = (ask - bid) / mid if mid else np.nan
        bid_qty = sum(q for _, q in bids)
        ask_qty = sum(q for _, q in asks)
        total = bid_qty + ask_qty
        imbalance = (bid_qty - ask_qty) / total if total else 0.0
        microprice = (ask * bid_qty_top + bid * ask_qty_top) / (bid_qty_top + ask_qty_top) if bid_qty_top + ask_qty_top else mid
        rows.append({"timestamp_ms": int(row["timestamp_ms"]), "mid": mid, "spread_bps": spread * 10000,
                     "best_bid": bid, "best_ask": ask, "bid_depth": bid_qty, "ask_depth": ask_qty,
                     "depth_imbalance": imbalance, "microprice_minus_mid_bps": (microprice / mid - 1) * 10000 if mid else np.nan})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["timestamp"] = pd.to_datetime(out.timestamp_ms, unit="ms", utc=True)
    return out.set_index("timestamp").sort_index()


def trade_features(trades: pd.DataFrame, freq: str = "1s") -> pd.DataFrame:
    """Aggregate trades into time buckets; all fields are backward-looking.

    CoinDCX's maker flag is preserved as metadata. The signed volume below is
    a proxy and must not be treated as a verified aggressor-side label until
    exchange semantics are independently confirmed.
    """
    if trades.empty:
        return pd.DataFrame()
    x = trades.copy()
    required = {"timestamp_ms", "price", "quantity", "is_maker"}
    missing = required - set(x.columns)
    if missing:
        raise ValueError(f"Missing trade columns: {sorted(missing)}")
    x["timestamp"] = pd.to_datetime(x["timestamp_ms"], unit="ms", utc=True)
    x["price"] = pd.to_numeric(x["price"], errors="coerce")
    x["quantity"] = pd.to_numeric(x["quantity"], errors="coerce")
    x = x.dropna(subset=["timestamp", "price", "quantity"]).sort_values("timestamp").set_index("timestamp")
    maker = x["is_maker"].astype(str).str.lower().isin(["1", "true"])
    x["signed_qty_proxy"] = np.where(maker, -x["quantity"], x["quantity"])
    g = x.resample(freq)
    out = g.agg(price=("price", "last"), trade_count=("price", "count"), volume=("quantity", "sum"), signed_volume_proxy=("signed_qty_proxy", "sum"))
    out["trade_imbalance_proxy"] = out["signed_volume_proxy"] / out["volume"].replace(0, np.nan)
    out["volume_accel"] = out["volume"] / out["volume"].rolling(30, min_periods=10).mean().replace(0, np.nan)
    out["ret_1s"] = out["price"].pct_change()
    out["rv_30s"] = out["ret_1s"].rolling(30, min_periods=10).std() * np.sqrt(30)
    return out


def merge_asof_features(trades: pd.DataFrame, orderbooks: pd.DataFrame) -> pd.DataFrame:
    """Attach only the latest order-book snapshot available at each trade bucket."""
    t = trade_features(trades).reset_index()
    b = orderbook_features(orderbooks).reset_index()
    if t.empty or b.empty:
        return t
    return pd.merge_asof(t.sort_values("timestamp"), b.sort_values("timestamp"), on="timestamp", direction="backward")
