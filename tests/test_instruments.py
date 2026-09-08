from pathlib import Path

import pytest

from app.data.instruments import InstrumentRegistry


CONFIG = Path(__file__).parents[1] / "config" / "instruments.yaml"


def test_registry_loads_all_configured_instruments():
    registry = InstrumentRegistry.from_yaml(CONFIG)
    assert {"BTCUSD", "NIFTY50", "RELIANCE"} <= set(registry.instruments)
    assert registry.get("NIFTY50").signal_symbol == "NIFTY 50"
    assert registry.get("NIFTY50").execution_symbol == "NIFTY 50"


def test_derivative_metadata_is_supported():
    registry = InstrumentRegistry.from_yaml(CONFIG)
    btc = registry.get("BTCUSD")
    assert btc.contract_multiplier > 0
    assert btc.lot_size > 0
    assert btc.session.type == "continuous"


def test_unknown_instrument_is_rejected():
    registry = InstrumentRegistry.from_yaml(CONFIG)
    with pytest.raises(KeyError, match="Unknown instrument"):
        registry.get("DOES_NOT_EXIST")
