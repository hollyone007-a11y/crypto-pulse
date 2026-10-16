import pandas as pd
import pytest

from cryptopulse.backtest import prepare, run_backtest
from cryptopulse.config import Config
from cryptopulse.data import synthetic
from cryptopulse.position import Position
from cryptopulse.risk import build_trade, position_size


def test_tp_gives_target_roe():
    cfg = Config()
    s = build_trade("long", 100.0, 1.5, 6, [], cfg)  # ATR 1.5% -> TP 5.25%
    assert s and s.leverage * (abs(s.tp - s.entry) / s.entry - 2 * (cfg.fee + cfg.slippage)) >= cfg.target_roe
    assert s.sl < s.entry < s.tp


def test_skips_when_target_needs_too_much_leverage():
    assert build_trade("long", 100.0, 0.1, 6, [], Config()) is None  # ATR 0.1% -> нужно плечо >> 10


def test_short_levels_inverted():
    s = build_trade("short", 100.0, 1.5, 6, [], Config())
    assert s.tp < s.entry < s.sl


def test_risk_capped_at_one_percent():
    cfg = Config()
    s = build_trade("long", 100.0, 1.5, 6, [], cfg)
    qty, margin = position_size(1000, s, cfg)
    loss = qty * (100 - s.sl) + qty * 200 * (cfg.fee + cfg.slippage)
    assert loss == pytest.approx(10, rel=0.01) and margin <= 900


def test_sl_wins_when_both_hit_same_bar():
    p = Position("long", 100, 98, 104, 1, 10, 10, None, 5, [], {})
    class B: high, low = 105, 97
    assert p.update(B) == (98, "sl")


def test_no_lookahead_htf():
    cfg = Config()
    df = synthetic(1200)
    m = prepare(df, cfg)
    # на свече 05:00 доступна HTF-свеча, закрывшаяся не позже 04:00 (т.е. стартовавшая в 00:00)
    t = pd.Timestamp("2024-01-05 05:00")
    h = df.resample("4h").agg({"close": "last"}).loc["2024-01-05 00:00", "close"]  # закрыта в 04:00
    assert m.loc[t, "h_close"] == pytest.approx(h)


def test_backtest_runs_and_is_consistent():
    cfg = Config()
    trades, st = run_backtest(synthetic(4000), cfg)
    assert st["trades"] == len(trades) > 0
    assert st["end_equity"] == pytest.approx(cfg.equity + trades.pnl.sum(), abs=0.05)
    assert (trades.roe >= -1.0001).all()
