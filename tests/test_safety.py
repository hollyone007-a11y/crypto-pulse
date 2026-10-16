import pandas as pd
import pytest

from cryptopulse.analysis import Signal
from cryptopulse.bot import TradingBot
from cryptopulse.config import Config
from cryptopulse.data import synthetic
from cryptopulse.position import Position
from cryptopulse.store import Store

DF = synthetic(1500)


class FakeBroker:
    def __init__(self):
        self.pos, self.protected, self.eq = None, True, 1000.0
        self.calls, self.fail_protect = [], False

    def balance(self): return self.eq
    def position(self, s): return self.pos
    def has_protection(self, s): return self.protected
    def open(self, s, sig, qty):
        self.pos, self.protected = (sig.side, qty), True
        return qty
    def protect(self, s, side, qty, sl, tp):
        self.calls.append("protect")
        if self.fail_protect:
            raise RuntimeError("api down")
        self.protected = True
    def close_all(self, s):
        self.calls.append("close_all"); self.pos = None


def mk(msgs=None, store=None, mode="paper", broker=None):
    msgs = [] if msgs is None else msgs
    return TradingBot(Config(), mode, notify=msgs.append, fetch=lambda s, i, n: DF, broker=broker,
                      store=store or Store(":memory:")), msgs


def sig(side="long", entry=100.0):
    sgn = 1 if side == "long" else -1
    return Signal(side, entry, entry - sgn * 2, entry + sgn * 5, 10, 6.0, ["htf_trend"], {"regime": "bull"})


BAR = type("B", (), {"Index": pd.Timestamp("2024-02-01"), "close": 100.0})


def losing_trade(bot, loss):
    bot._open(sig(), BAR)
    p = bot.pos
    bot.equity -= loss
    bot._record_close(p, 98.0, "sl", -loss, BAR.Index)


def test_state_survives_restart(tmp_path):
    db = str(tmp_path / "s.db")
    b1, _ = mk(store=Store(db))
    b1._open(sig(), BAR)
    b1.set_param("risk_per_trade", 0.02)
    b1.pause()
    b2, _ = mk(store=Store(db))
    assert b2.pos is not None and b2.pos.entry == 100.0 and b2.paused
    assert b2.cfg.risk_per_trade == 0.02          # настройки тоже сохранились
    b2._record_close(b2.pos, 105, "tp", 20.0, BAR.Index)
    b3, _ = mk(store=Store(db))
    assert len(b3.trades) == 1 and b3.pos is None


def test_killswitch_halts_and_resume_resets_peak():
    b, msgs = mk()
    losing_trade(b, 160)   # 1000 -> 840 = -16% > 15%
    assert b.halted and b.paused and any("KILL-SWITCH" in m for m in msgs)
    assert "Kill-switch сброшен" in b.resume()
    assert not b.halted and b.peak == b.equity


def test_loss_streak_reduces_risk():
    b, msgs = mk()
    for _ in range(4):
        losing_trade(b, 1)
        b.cooldown = 0
    assert b.streak == 4 and any("риск на сделку снижен" in m for m in msgs)
    full = Config().risk_per_trade
    b._open(sig(), BAR)
    from cryptopulse.risk import position_size
    q_full, _ = position_size(b.equity, sig(), b.cfg, 1.0)
    assert b.pos.qty == pytest.approx(q_full * b.cfg.streak_risk_mult)
    b._record_close(b.pos, 105, "tp", 10, BAR.Index)
    assert b.streak == 0


def test_set_param_validates():
    b, _ = mk()
    assert "диапазоне" in b.set_param("max_leverage", 100)
    assert "Нельзя" in b.set_param("fee", 0.1)
    assert b.set_param("max_leverage", 5).startswith("max_leverage = 5")


def test_manual_close_paper():
    b, _ = mk()
    assert "нет" in b.close_position()
    b._open(sig(), BAR)
    b.last_price = 101.0
    assert "закрыта вручную" in b.close_position() and b.pos is None
    assert b.trades[-1]["exit_reason"] == "manual"


def test_reconcile_foreign_position_blocks_trading():
    br = FakeBroker(); br.pos = ("long", 1.0)
    b, msgs = mk(mode="live", broker=br)
    b.step()
    assert b.foreign and b.pos is None and any("не бот" in m for m in msgs)
    assert len([m for m in msgs if "не бот" in m]) == 1
    b.step()
    assert len([m for m in msgs if "не бот" in m]) == 1   # не спамит


def test_reconcile_restores_missing_protection():
    br = FakeBroker()
    b, msgs = mk(mode="live", broker=br)
    b._open(sig(), BAR)
    br.protected = False
    b.step()
    assert br.calls == ["protect"] and br.protected and any("нет стопа" in m for m in msgs)


def test_reconcile_closes_when_protection_fails():
    br = FakeBroker()
    b, msgs = mk(mode="live", broker=br)
    b._open(sig(), BAR)
    br.protected, br.fail_protect = False, True
    b.step()
    assert "close_all" in br.calls and br.pos is None and any("закрыта по рынку" in m for m in msgs)


def test_live_detects_exchange_side_close():
    br = FakeBroker()
    b, msgs = mk(mode="live", broker=br)
    b._open(sig(), BAR)
    br.pos, br.eq = None, 1020.0      # биржа закрыла по TP
    b.step()
    assert b.pos is None and b.equity == 1020.0 and b.trades[-1]["pnl"] == pytest.approx(20.0)
