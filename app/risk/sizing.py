"""Position-sizing primitives."""


def risk_position_size(capital: float, risk_pct: float, entry: float, stop: float) -> int:
    """Calculate whole-unit position size from fixed monetary risk."""
    if capital <= 0 or risk_pct <= 0:
        return 0
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0:
        return 0
    risk_amount = capital * (risk_pct / 100.0)
    return max(0, int(risk_amount // risk_per_unit))
