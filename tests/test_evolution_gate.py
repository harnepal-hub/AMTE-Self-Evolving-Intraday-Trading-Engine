from app.research.evolution_gate import EvolutionGate, checkpoint, promotion_allowed


def test_checkpoint_stages():
    assert checkpoint(0) == "COLLECT"
    assert checkpoint(49) == "COLLECT"
    assert checkpoint(50) == "CHALLENGER_REVIEW"
    assert checkpoint(99) == "CHALLENGER_REVIEW"
    assert checkpoint(100) == "PROMOTION_REVIEW"


def test_promotion_requires_all_gates():
    assert promotion_allowed(100, 1.25, 0.60, 0.10, True, True, True)
    assert not promotion_allowed(99, 1.25, 0.60, 0.10, True, True, True)
    assert not promotion_allowed(100, 1.19, 0.60, 0.10, True, True, True)
    assert not promotion_allowed(100, 1.25, 0.54, 0.10, True, True, True)
    assert not promotion_allowed(100, 1.25, 0.60, 0.16, True, True, True)
    assert not promotion_allowed(100, 1.25, 0.60, 0.10, False, True, True)
    assert not promotion_allowed(100, 1.25, 0.60, 0.10, True, False, True)
    assert not promotion_allowed(100, 1.25, 0.60, 0.10, True, True, False)


def test_custom_gate():
    gate = EvolutionGate(review_trades=20, promotion_trades=40)
    assert checkpoint(20, gate) == "CHALLENGER_REVIEW"
    assert checkpoint(40, gate) == "PROMOTION_REVIEW"
