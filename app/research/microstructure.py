"""Causal candle/volume microstructure proxies and walk-forward ML signals."""
from __future__ import annotations
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

FEATURE_COLUMNS = [
    "ret_1", "ret_3", "ret_6", "body_pct", "upper_wick_pct", "lower_wick_pct",
    "range_pct", "atr_pct", "volume_z", "volume_ratio", "ema_fast_slope",
    "ema_slow_slope", "trend_strength", "vwap_distance", "rsi_14",
    "range_expansion", "close_location",
]


def make_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Create features using only information available on the current bar."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    df = bars.copy().sort_index()
    o, h, l, c, v = [df[x].astype(float) for x in ("open", "high", "low", "close", "volume")]
    prev = c.shift(1)
    tr = pd.concat([(h-l), (h-prev).abs(), (l-prev).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean()
    ema20 = c.ewm(span=20, adjust=False, min_periods=20).mean()
    ema50 = c.ewm(span=50, adjust=False, min_periods=50).mean()
    vmean = v.rolling(50, min_periods=50).mean()
    vstd = v.rolling(50, min_periods=50).std(ddof=0)
    typical = (h+l+c)/3.0
    day = pd.Series(df.index.date, index=df.index)
    vwap = (typical*v).groupby(day).cumsum()/v.groupby(day).cumsum().replace(0, np.nan)
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    rsi = 100-100/(1+gain/loss.replace(0,np.nan))
    rng = (h-l).replace(0,np.nan)
    out = pd.DataFrame(index=df.index)
    out["ret_1"] = c.pct_change(1)
    out["ret_3"] = c.pct_change(3)
    out["ret_6"] = c.pct_change(6)
    out["body_pct"] = (c-o)/c
    out["upper_wick_pct"] = (h-np.maximum(o,c))/c
    out["lower_wick_pct"] = (np.minimum(o,c)-l)/c
    out["range_pct"] = rng/c
    out["atr_pct"] = atr/c
    out["volume_z"] = (v-vmean)/vstd.replace(0,np.nan)
    out["volume_ratio"] = v/vmean.replace(0,np.nan)
    out["ema_fast_slope"] = ema20.pct_change(3)
    out["ema_slow_slope"] = ema50.pct_change(6)
    out["trend_strength"] = (ema20-ema50)/atr.replace(0,np.nan)
    out["vwap_distance"] = (c-vwap)/c
    out["rsi_14"] = rsi
    out["range_expansion"] = rng/rng.shift(1).rolling(20,min_periods=20).mean()
    out["close_location"] = (c-l)/rng
    return out.replace([np.inf,-np.inf],np.nan)


def make_target(bars: pd.DataFrame, horizon_bars: int=3, min_move_bps: float=4.0) -> pd.Series:
    """Binary future-direction label; neutral moves are excluded from training."""
    if horizon_bars <= 0 or min_move_bps < 0:
        raise ValueError("invalid target parameters")
    future = bars["close"].astype(float).shift(-horizon_bars)/bars["close"].astype(float)-1
    threshold = min_move_bps/10_000
    y = pd.Series(np.nan,index=bars.index,name="target")
    y[future > threshold] = 1
    y[future < -threshold] = 0
    return y


def fit_classifier(train_x: pd.DataFrame, train_y: pd.Series) -> Pipeline|None:
    mask = train_y.notna() & train_x.notna().all(axis=1)
    if mask.sum() < 500 or train_y.loc[mask].nunique() < 2:
        return None
    model = Pipeline([("scale",StandardScaler()),("clf",LogisticRegression(C=0.25,max_iter=1000,class_weight="balanced"))])
    model.fit(train_x.loc[mask,FEATURE_COLUMNS],train_y.loc[mask].astype(int))
    return model


def walk_forward_probabilities(bars: pd.DataFrame, train_bars: int=20_000, test_bars: int=2_000,
                               step_bars: int=2_000, horizon_bars: int=3, min_move_bps: float=4.0,
                               long_threshold: float=0.62, short_threshold: float=0.38) -> pd.DataFrame:
    """Generate out-of-sample probabilities/signals using rolling chronological training."""
    if min(train_bars,test_bars,step_bars) <= 0:
        raise ValueError("window sizes must be positive")
    x,y = make_features(bars),make_target(bars,horizon_bars,min_move_bps)
    out = pd.DataFrame(index=bars.index,data={"prob_up":np.nan,"ml_signal":0})
    start=train_bars
    while start < len(bars):
        stop=min(start+test_bars,len(bars)); train_start=max(0,start-train_bars)
        model=fit_classifier(x.iloc[train_start:start],y.iloc[train_start:start])
        if model is not None:
            chunk=x.iloc[start:stop]; valid=chunk[FEATURE_COLUMNS].notna().all(axis=1)
            if valid.any():
                p=model.predict_proba(chunk.loc[valid,FEATURE_COLUMNS])[:,1]
                idx=chunk.index[valid]
                out.loc[idx,"prob_up"]=p
                out.loc[idx,"ml_signal"]=np.where(p>=long_threshold,1,np.where(p<=short_threshold,-1,0))
        start += step_bars
    return out
