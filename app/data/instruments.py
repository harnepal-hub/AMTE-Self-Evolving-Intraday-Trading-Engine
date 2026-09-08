"""Typed, venue-neutral instrument definitions for AMTE."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    open: str | None = None
    close: str | None = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        if value not in {"continuous", "fixed"}:
            raise ValueError("session.type must be 'continuous' or 'fixed'")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.type == "fixed" and (self.open is None or self.close is None):
            raise ValueError("fixed sessions require open and close")
        if self.type == "continuous" and (self.open is not None or self.close is not None):
            raise ValueError("continuous sessions cannot define open or close")


class InstrumentConfig(BaseModel):
    """Canonical instrument metadata used by data, risk and execution layers."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1)
    market: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    asset_class: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    signal_symbol: str | None = None
    execution_symbol: str | None = None
    timezone: str = Field(min_length=1)
    session: SessionConfig
    tick_size: float = Field(gt=0)
    quantity_step: float = Field(gt=0)
    quote_currency: str = Field(min_length=1)
    contract_multiplier: float = Field(default=1.0, gt=0)
    lot_size: float = Field(default=1.0, gt=0)
    margin_currency: str | None = None

    def model_post_init(self, __context: object) -> None:
        if self.signal_symbol is None:
            object.__setattr__(self, "signal_symbol", self.symbol)
        if self.execution_symbol is None:
            object.__setattr__(self, "execution_symbol", self.symbol)


class InstrumentRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instruments: dict[str, InstrumentConfig]

    @classmethod
    def from_yaml(cls, path: str | Path) -> "InstrumentRegistry":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        raw = payload.get("instruments")
        if not isinstance(raw, dict) or not raw:
            raise ValueError("Configuration must contain a non-empty instruments mapping")
        configs = []
        for key, value in raw.items():
            if not isinstance(value, dict):
                raise ValueError(f"Instrument {key!r} must be a mapping")
            configs.append(InstrumentConfig(key=key, **value))
        return cls(instruments={item.key: item for item in configs})

    def get(self, key: str) -> InstrumentConfig:
        try:
            return self.instruments[key]
        except KeyError as exc:
            raise KeyError(f"Unknown instrument: {key}") from exc
