from scripts.run_paper_trading import PaperRunner


def test_book_imbalance_calculation_accepts_dict_levels(tmp_path):
    r = PaperRunner("B-BTC_USDT", tmp_path, 1, 0.35, 30)
    r.on_book({"bids": [[100.0, 3.0]], "asks": [[101.0, 1.0]]})
    assert r.book.bid == 100.0
    assert r.book.ask == 101.0
    assert r.events == 1


def test_invalid_crossed_book_is_ignored(tmp_path):
    r = PaperRunner("B-BTC_USDT", tmp_path, 1, 0.35, 30)
    r.on_book({"bids": [[101.0, 1.0]], "asks": [[100.0, 1.0]]})
    assert r.book.bid == 0.0
    assert r.events == 0


def test_trade_flow_is_recorded(tmp_path):
    r = PaperRunner("B-BTC_USDT", tmp_path, 1, 0.35, 30)
    r.on_trade({"quantity": 2, "is_maker": False})
    r.on_trade({"quantity": 1, "is_maker": True})
    assert list(r.trade_flow) == [2.0, -1.0]
