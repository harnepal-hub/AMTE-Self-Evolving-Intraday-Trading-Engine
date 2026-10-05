from app.research.evolution_engine import candidate_space, champion_config

def test_candidate_space_is_bounded():
    candidates = candidate_space()
    assert len(candidates) == 32
    assert len({c.name for c in candidates}) == 32

def test_champion_matches_live_filter_stack():
    c = champion_config()
    assert c.hull_length == 8
    assert c.ema_length == 200
    assert c.volume_ratio_min == 1.2
    assert c.atr_expansion_min == 1.1
    assert c.ema_distance_min == 0.003
    assert c.cooldown_bars == 3
