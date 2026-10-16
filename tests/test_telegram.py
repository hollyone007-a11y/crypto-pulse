import pandas as pd

from cryptopulse.bot import TradingBot
from cryptopulse.config import Config
from cryptopulse.data import synthetic
from cryptopulse.telegram_ui import Commands, TelegramUI


def make_bot(msgs):
    df = synthetic(4000)
    return TradingBot(Config(), "paper", notify=msgs.append, fetch=lambda s, i, n: df)


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
    bot = TradingBot(Config(), "paper", notify=msgs.append, fetch=lambda s, i, n: full)
    # прогоняем историю свеча за свечой, как живой цикл
    for end in range(300, 3000, 1):
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
