import numpy as np
import pandas as pd
import pytest

from cryptopulse import data
from cryptopulse.backtest import add_funding, run_backtest
from cryptopulse.config import Config
from cryptopulse.position import Position
from cryptopulse.walkforward import walk_forward


class FakeSession:
    """Имитирует Binance: отдаёт свечи постранично (лимит 1500)."""
    def __init__(self, n=4000, interval_ms=3_600_000, t0=1_700_000_000_000):
        self.rows = [[t0 + i * interval_ms, "1", "2", "0.5", "1.5", "10", t0 + i * interval_ms + interval_ms - 1]
                     for i in range(n)]
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        batch = [r for r in self.rows if r[0] >= params["startTime"]][: params["limit"]]
        class R:
            def raise_for_status(self): pass
            def json(self_): return batch
        return R()


def test_fetch_history_paginates_and_caches(tmp_path):
    s = FakeSession(4000)
    df = data.fetch_history("BTCUSDT", "1h", "2023-01-01", cache_dir=str(tmp_path), session=s)
    assert len(df) == 4000 and s.calls == 3 and df.index.is_monotonic_increasing
    calls = s.calls
    df2 = data.fetch_history("BTCUSDT", "1h", "2023-01-01", cache_dir=str(tmp_path), session=s)
    assert len(df2) == 4000 and not df2.index.duplicated().any()
    assert s.calls == calls + 1   # докачка: один запрос, пустой


def test_funding_applied_to_right_bar():
    idx = pd.date_range("2024-01-01", periods=10, freq="1h")
    df = pd.DataFrame({"close": 1.0}, index=idx)
    f = pd.Series([0.0003], index=[pd.Timestamp("2024-01-01 08:00")])
    out = add_funding(df, f, 0.0001)
    assert out.fund.iloc[8] == pytest.approx(0.0003) and out.fund.sum() == pytest.approx(0.0003)
    const = add_funding(df, None, 0.0001)
    assert const.fund.sum() == pytest.approx(0.0001 * 10 / 8)


def test_funding_hurts_long_helps_short():
    class B: high, low, close, fund = 100, 100, 100, 0.001
    for side, sign in (("long", 1), ("short", -1)):
        p = Position(side, 100, 50, 150, 1, 10, 10, None, 5, [], {})
        p.update(B)
        assert p.funding == pytest.approx(sign * 0.1)
        assert p.pnl(100, 0, 0) == pytest.approx(-sign * 0.1)


def test_funding_reduces_backtest_result():
    df = data.synthetic(4000)
    _, base = run_backtest(df, Config(default_funding=0.0))
    _, costly = run_backtest(df, Config(default_funding=0.003))
    assert base["trades"] > 0 and costly["end_equity"] != base["end_equity"]


def test_walk_forward_runs_and_has_no_lookahead_in_windows():
    df = data.synthetic(5000)
    w, trades, summary = walk_forward(df, Config(), None, train_bars=1500, test_bars=500,
                                      grid={"min_score": [5.0, 6.0]})
    assert summary["windows"] == len(w) == 7
    if len(trades):   # все сделки строго внутри тестовых окон
        assert (trades.open >= w["from"].min()).all()
