"""Feature engineering for collected CoinDCX microstructure records."""
from __future__ import annotations
import json
import numpy as np
import pandas as pd


def _book_levels(value):
    if isinstance(value, dict):
        return [(float(p), float(q)) for p, q in value.items()]
    try:
        value = json.loads(value)
        return [(float(p), float(q)) for p, q in value.items()]
    except Exception:
        return []


def orderbook_features(book: pd.DataFrame, levels: int = 10) -> pd.DataFrame:
    rows = []
    for _, r in book.iterrows():
        bids = sorted(_book_levels(r["bids_json"]), reverse=True)[:levels]
        asks = sorted(_book_levels(r["asks_json"]))[:levels]
        if not bids or not asks:
            continue
        bp, bq = bids[0]; ap, aq = asks[0]
        mid = (bp + ap) / 2.0
        spread_bps = (ap - bp) / mid * 10000 if mid else np.nan
        total_b = sum(q for _, q in bids); total_a = sum(q for _, q in asks)
        imbalance = (total_b - total_a) / (total_b + total_a) if total_b + total_a else np.nan
        micro = (ap * bq + bp * aq) / (bq + aq) if bq + aq else mid
        rows.append({"timestamp_ms": r["timestamp_ms"], "mid": mid, "best_bid": bp, "best_ask": ap,
                     "spread_bps": spread_bps, "book_imbalance": imbalance,
                     "microprice_edge_bps": (micro-mid)/mid*10000 if mid else np.nan,
                     "bid_depth": total_b, "ask_depth": total_a})
    return pd.DataFrame(rows)


def trade_features(trades: pd.DataFrame, freq: str = "1s") -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    x = trades.copy()
    x["timestamp"] = pd.to_datetime(x["timestamp_ms"], unit="ms", utc=True)
    x["price"] = pd.to_numeric(x["price"], errors="coerce")
    x["quantity"] = pd.to_numeric(x["quantity"], errors="coerce")
    # CoinDCX maker flag is retained; aggressor-side inference must be verified
    # against exchange semantics before being used as a directional label.
    x["signed_proxy"] = np.where(x["is_maker"].astype(str).str.lower().isin(["true", "1"]), -1.0, 1.0)
    x = x.dropna(subset=["timestamp", "price", "quantity"]).set_index("timestamp")
    g = x.resample(freq)
    out = g.agg(price=("price", "last"), trade_count=("price", "size"), volume=("quantity", "sum"),
                signed_volume=("signed_proxy", lambda s: 0.0))
    signed = (x["signed_proxy"] * x["quantity"]).resample(freq).sum()
    out["signed_volume"] = signed
    out["trade_imbalance"] = signed / out["volume"].replace(0, np.nan)
    out["trade_intensity"] = out["trade_count"]
    return out
