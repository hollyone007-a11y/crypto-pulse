import pandas as pd

from cryptopulse.bot import TradingBot
from cryptopulse.config import Config
from cryptopulse.store import Store
from cryptopulse.data import synthetic
from cryptopulse.telegram_ui import Commands, TelegramUI


def make_bot(msgs):
    df = synthetic(4000)
    return TradingBot(Config(), "paper", notify=msgs.append, fetch=lambda s, i, n: df, store=Store(":memory:"))


class FakeHTTP:
    def __init__(self): self.sent = []
    def post(self, url, json=None, timeout=None):
        self.sent.append((url.rsplit("/", 1)[1], json))
        class R:
            def raise_for_status(self): pass
            def json(self): return {"result": []}
        return R()


def test_commands_without_trades():
    c = Commands(make_bot([]))
    assert "Открытых позиций нет" in c.handle("/position")
    assert "пока нет" in c.handle("/stats") and "пока нет" in c.handle("/report")
    assert "paper" in c.handle("/status")
    assert c.handle("/nope").startswith("Неизвестная")


def test_pause_resume_blocks_entries():
    msgs = []
    bot = make_bot(msgs)
    c = Commands(bot)
    c.handle("/pause")
    bot.step()
    assert bot.pos is None and not msgs
    c.handle("/resume")
    assert bot.paused is False


def test_bot_trades_and_commands_reflect_it():
    msgs = []
    full = synthetic(4000)
    bot = TradingBot(Config(), "paper", notify=msgs.append, fetch=lambda s, i, n: full, store=Store(":memory:"))
    # прогоняем историю свеча за свечой, как живой цикл
    for end in range(300, 2700, 4):
        bot.fetch = lambda s, i, n, e=end: full.iloc[max(0, e - 999): e + 1]
        bot.step()
    assert bot.trades and any("Открыта" in m for m in msgs) and any("Закрыта" in m for m in msgs)
    c = Commands(bot)
    assert "Сделок:" in c.handle("/stats")
    assert "<pre>" in c.handle("/report")
    assert "Бэктест" in c.handle("/backtest")


def test_unauthorized_chat_is_ignored_and_authorized_gets_reply():
    http = FakeHTTP()
    ui = TelegramUI("T", ["42"], make_bot([]), session=http)
    ui.process({"update_id": 1, "message": {"chat": {"id": 999}, "text": "/status"}})
    assert http.sent == []
    ui.process({"update_id": 2, "message": {"chat": {"id": 42}, "text": "/status"}})
    assert http.sent and http.sent[0][0] == "sendMessage" and http.sent[0][1]["chat_id"] == "42"


def test_long_message_is_split():
    http = FakeHTTP()
    ui = TelegramUI("T", ["1"], make_bot([]), session=http)
    ui.send("1", "x" * 9000)
    assert len(http.sent) == 3


def test_close_requires_confirm_and_expires(monkeypatch):
    import time as _t
    from cryptopulse.analysis import Signal
    bot = make_bot([])
    c = Commands(bot)
    assert "нет" in c.handle("/close")
    B = type("B", (), {"Index": pd.Timestamp("2024-02-01"), "close": 100.0})
    bot._open(Signal("long", 100.0, 98.0, 105.0, 10, 6.0, [], {}), B); bot.last_price = 100.0
    assert "/confirm" in c.handle("/close") and bot.pos is not None
    assert "закрыта вручную" in c.handle("/confirm") and bot.pos is None
    assert "Нет активного" in c.handle("/confirm")
    c.handle("/close")  # без позиции — запрос не создаётся
    bot._open(Signal("long", 100.0, 98.0, 105.0, 10, 6.0, [], {}), B)
    c.handle("/close")
    c.close_requested -= 120
    assert "истёк" in c.handle("/confirm") and bot.pos is not None


def test_set_and_settings():
    c = Commands(make_bot([]))
    assert "risk_per_trade = 0.005" in c.handle("/set риск 0.005")
    assert "диапазоне" in c.handle("/set плечо 99")
    assert "числом" in c.handle("/set score abc") and "Формат" in c.handle("/set")
    assert "0.50%" in c.handle("/settings")


def test_chart_and_daily_summary():
    import datetime as dt
    msgs = []
    bot = make_bot(msgs)
    c = Commands(bot)
    assert "пока нет" in c.chart_text() and c.chart_png() is None
    bot.trades = [{"pnl": 10.0, "close": pd.Timestamp("2024-03-01 12:00")},
                  {"pnl": -4.0, "close": pd.Timestamp("2024-03-01 18:00")}]
    assert "▁" in c.chart_text() or "█" in c.chart_text()
    s = c.daily_summary(dt.date(2024, 3, 1))
    assert "сделок 2" in s and "+6.00" in s and "не было" in c.daily_summary(dt.date(2024, 3, 2))
    http = FakeHTTP()
    ui = TelegramUI("T", ["1"], bot, session=http)
    ui.last_summary = dt.date(2024, 3, 1)
    ui.maybe_daily_summary(dt.datetime(2024, 3, 2, 0, 5, tzinfo=dt.timezone.utc))
    ui.maybe_daily_summary(dt.datetime(2024, 3, 2, 9, 0, tzinfo=dt.timezone.utc))   # второй раз — тишина
    assert len(http.sent) == 1 and "Итоги 2024-03-01" in http.sent[0][1]["text"]


def test_start_sends_keyboard():
    http = FakeHTTP()
    ui = TelegramUI("T", ["1"], make_bot([]), session=http)
    ui.process({"update_id": 1, "message": {"chat": {"id": 1}, "text": "/start"}})
    assert "reply_markup" in http.sent[0][1]
