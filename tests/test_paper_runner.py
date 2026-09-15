from scripts.run_paper_trading import Book, CandleBuilder, TWPaperRunner


def test_book_parsing_accepts_dict_levels(tmp_path):
    r = TWPaperRunner("B-BTC_USDT", tmp_path, 1, 16, True, True, 1)
    r.on_depth({"bids": {"100.0": "3.0"}, "asks": {"101.0": "1.0"}})
    assert r.book.bid == 100.0
    assert r.book.ask == 101.0
    assert r.events == 1


def test_invalid_crossed_book_is_ignored(tmp_path):
    r = TWPaperRunner("B-BTC_USDT", tmp_path, 1, 16, True, True, 1)
    r.on_depth({"bids": [[101.0, 1.0]], "asks": [[100.0, 1.0]]})
    assert r.book.bid == 0.0
    assert r.events == 0


def test_candle_builder_closes_previous_bucket():
    c = CandleBuilder()
    assert c.update(100.0, 1.0, 0) is None
    finished = c.update(101.0, 2.0, 300_000)
    assert finished is not None
    assert finished[1:5] == (100.0, 100.0, 100.0, 100.0)
