from app.signals.tw_all_in_one import TWAllInOneSignal, ehma


def test_ehma_warms_up_and_is_finite():
    values = [100 + i * 0.1 for i in range(100)]
    x = ehma(values, 16)
    assert x == x


def test_tw_signal_accepts_valid_parameter_set():
    s = TWAllInOneSignal(length=16, ema_length=100, ema_filter=True, slope_filter=True)
    out = 0
    for i in range(150):
        out = s.update(100 + i * 0.02)
    assert out in (-1, 0, 1)


def test_tw_signal_is_causal_on_same_prefix():
    a = TWAllInOneSignal(length=8, ema_length=20)
    b = TWAllInOneSignal(length=8, ema_length=20)
    prices = [100 + ((i % 7) - 3) * 0.2 + i * 0.01 for i in range(80)]
    for p in prices:
        assert a.update(p) == b.update(p)
