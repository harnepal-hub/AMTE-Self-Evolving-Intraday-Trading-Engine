from scripts.run_3strategy_optimisation import consensus


def test_all_three_requires_unanimous_direction():
    assert consensus(1, 1, 1, "ALL3") == 1
    assert consensus(-1, -1, -1, "ALL3") == -1
    assert consensus(1, 1, -1, "ALL3") == 0


def test_pairwise_modes_require_matching_nonzero_votes():
    assert consensus(1, 1, 0, "TURTLE_AF") == 1
    assert consensus(1, -1, 0, "TURTLE_AF") == 0
    assert consensus(0, -1, -1, "AF_DTP") == -1


def test_two_of_three_majority():
    assert consensus(1, 1, -1, "2_OF_3") == 1
    assert consensus(-1, -1, 1, "2_OF_3") == -1
    assert consensus(1, -1, 0, "2_OF_3") == 0
    assert consensus(0, 0, 0, "2_OF_3") == 0
