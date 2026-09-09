from pathlib import Path

import pytest

from app.data.instruments import InstrumentConfig, InstrumentRegistry, SessionConfig


CONFIG = Path(__file__).parents[1] / "config" / "instruments.yaml"


def test_registry_loads_all_configured_instruments():
    registry = InstrumentRegistry.from_yaml(CONFIG)
    assert {"BTCUSD", "NIFTY50", "RELIANCE"} <= set(registry.instruments)
    assert registry.get("NIFTY50").signal_symbol == "NIFTY 50"
    assert registry.get("NIFTY50").execution_symbol is None


def test_derivative_metadata_is_supported():
    registry = InstrumentRegistry.from_yaml(CONFIG)
    btc = registry.get("BTCUSD")
    assert btc.contract_multiplier > 0
    assert btc.lot_size > 0
    assert btc.session.type == "continuous"


def test_explicit_execution_symbol_is_preserved():
    instrument = InstrumentConfig(
        key="INDEX",
        market="test",
        exchange="TEST",
        asset_class="index",
        symbol="INDEX SPOT",
        signal_symbol="INDEX SPOT",
        execution_symbol=None,
        timezone="Asia/Kolkata",
        session=SessionConfig(type="fixed", open="09:15", close="15:30"),
        tick_size=0.05,
        quantity_step=1,
        quote_currency="INR",
    )
    assert instrument.execution_symbol is None


def test_unknown_instrument_is_rejected():
    registry = InstrumentRegistry.from_yaml(CONFIG)
    with pytest.raises(KeyError, match="Unknown instrument"):
        registry.get("DOES_NOT_EXIST")
