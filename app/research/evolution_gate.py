"""Guardrails for paper-trading evolution and champion promotion."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvolutionGate:
    review_trades: int = 50
    promotion_trades: int = 100
    min_profit_factor: float = 1.20
    min_profitable_day_rate: float = 0.55
    max_drawdown_pct: float = 0.15


def checkpoint(trades: int, gate: EvolutionGate = EvolutionGate()) -> str:
    if trades < gate.review_trades:
        return "COLLECT"
    if trades < gate.promotion_trades:
        return "CHALLENGER_REVIEW"
    return "PROMOTION_REVIEW"


def promotion_allowed(
    trades: int,
    profit_factor: float,
    profitable_day_rate: float,
    max_drawdown_pct: float,
    challenger_beats_champion: bool,
    historical_revalidated: bool,
    locked_test_revalidated: bool,
    gate: EvolutionGate = EvolutionGate(),
) -> bool:
    """Require sufficient paper evidence and independent revalidation.

    Paper results alone never promote a challenger. A challenger must beat the
    champion, then pass the historical and locked-test gates again.
    """
    return (
        trades >= gate.promotion_trades
        and profit_factor >= gate.min_profit_factor
        and profitable_day_rate >= gate.min_profitable_day_rate
        and max_drawdown_pct <= gate.max_drawdown_pct
        and challenger_beats_champion
        and historical_revalidated
        and locked_test_revalidated
    )
