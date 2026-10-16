"""Telegram-интерфейс (Bot API, long polling, без сторонних библиотек).

Переменные окружения: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (список через запятую — кто может управлять ботом).
"""
import html
import os
import threading
import time

import pandas as pd
import requests

from .backtest import run_backtest
from .bot import TradingBot
from .data import fetch_klines
from .report import analyze

HELP = (
    "<b>crypto-pulse</b>\n"
    "/status — состояние бота и баланс\n"
    "/position — открытая позиция\n"
    "/stats — статистика закрытых сделок\n"
    "/report — глубокий разбор сделок\n"
    "/backtest — бэктест на последних 1500 свечах\n"
    "/pause — не открывать новые сделки\n"
    "/resume — снова искать входы\n"
    "/help — эта справка"
)


class Commands:
    """Логика команд отдельно от сети — легко тестировать."""

    def __init__(self, bot: TradingBot):
        self.bot = bot

    def handle(self, text: str) -> str:
        cmd = text.strip().split()[0].split("@")[0].lower() if text.strip() else ""
        fn = {"/start": self.help, "/help": self.help, "/status": self.status, "/position": self.position,
              "/stats": self.stats, "/report": self.report, "/backtest": self.backtest,
              "/pause": self.pause, "/resume": self.resume}.get(cmd)
        return fn() if fn else "Неизвестная команда. /help"

    def help(self):
        return HELP

    def status(self):
        b = self.bot
        ret = 100 * (b.equity / b.start_equity - 1)
        state = "⏸ на паузе" if b.paused else "▶️ работает"
        err = f"\n⚠️ Последняя ошибка: {html.escape(b.last_error)}" if b.last_error else ""
        return (f"{state} | режим <b>{b.mode}</b>\n{b.cfg.symbol} {b.cfg.ltf}/{b.cfg.htf}\n"
                f"Баланс: {b.equity:.2f} USDT ({ret:+.2f}%)\nСделок: {len(b.trades)}\n"
                f"Позиция: {'есть' if b.pos else 'нет'}\nПоследняя свеча: {b.last_check}{err}")

    def position(self):
        p = self.bot.pos
        if not p:
            return "Открытых позиций нет."
        return (f"{p.side.upper()} {self.bot.cfg.symbol} x{p.leverage}\nВход {p.entry:.2f}\n"
                f"SL {p.sl:.2f} | TP {p.tp:.2f}\nМаржа {p.margin:.2f} USDT\n"
                f"Свечей в позиции: {p.bars}\nПричины: {', '.join(p.reasons)}")

    def stats(self):
        if not self.bot.trades:
            return "Закрытых сделок пока нет."
        t = pd.DataFrame(self.bot.trades)
        w, l = t[t.pnl > 0], t[t.pnl <= 0]
        pf = w.pnl.sum() / abs(l.pnl.sum()) if len(l) and l.pnl.sum() else float("inf")
        return (f"Сделок: {len(t)} | винрейт {100*len(w)/len(t):.1f}%\nPnL: {t.pnl.sum():+.2f} USDT\n"
                f"Profit factor: {pf:.2f}\nСредний ROE: {100*t.roe.mean():+.1f}%\n"
                f"Достигли цели ROE≥{self.bot.cfg.target_roe*100:.0f}%: {100*(t.roe >= self.bot.cfg.target_roe*0.95).mean():.0f}%")

    def report(self):
        if not self.bot.trades:
            return "Закрытых сделок пока нет."
        return "<pre>" + html.escape(analyze(pd.DataFrame(self.bot.trades))) + "</pre>"

    def backtest(self):
        cfg = self.bot.cfg
        trades, st = run_backtest(self.bot.fetch(cfg.symbol, cfg.ltf, 1500), cfg)
        body = "\n".join(f"{k}: {v}" for k, v in st.items())
        return f"<b>Бэктест {cfg.symbol}</b>\n<pre>{html.escape(body)}</pre>\nПрошлые результаты не гарантируют будущих."

    def pause(self):
        self.bot.paused = True
        return "⏸ Новые сделки отключены (открытая позиция сопровождается до SL/TP)."

    def resume(self):
        self.bot.paused = False
        return "▶️ Поиск входов включён."


class TelegramUI:
    def __init__(self, token: str, allowed_chats, bot: TradingBot, session=None):
        self.url = f"https://api.telegram.org/bot{token}/"
        self.allowed = {str(c) for c in allowed_chats}
        self.bot, self.cmds = bot, Commands(bot)
        self.http = session or requests.Session()
        self.offset = None
        bot.notify = self.broadcast

    def api(self, method, **params):
        r = self.http.post(self.url + method, json=params, timeout=65)
        r.raise_for_status()
        return r.json()["result"]

    def send(self, chat_id, text):
        for i in range(0, len(text), 3900):   # лимит Telegram — 4096 символов
            chunk = text[i:i + 3900]
            try:
                self.api("sendMessage", chat_id=chat_id, text=chunk, parse_mode="HTML")
            except requests.HTTPError:        # сломанная HTML-разметка → шлём как есть
                self.api("sendMessage", chat_id=chat_id, text=chunk)

    def broadcast(self, text):
        for c in self.allowed:
            try:
                self.send(c, html.escape(text))
            except Exception as e:
                print("telegram send failed:", e)

    def process(self, update):
        msg = update.get("message") or {}
        chat = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "")
        if not text.startswith("/"):
            return
        if chat not in self.allowed:   # чужим не отвечаем вообще
            return
        try:
            self.send(chat, self.cmds.handle(text))
        except Exception as e:
            self.send(chat, f"Ошибка: {html.escape(str(e))}")

    def poll(self, stop: threading.Event):
        while not stop.is_set():
            try:
                for u in self.api("getUpdates", offset=self.offset, timeout=30):
                    self.offset = u["update_id"] + 1
                    self.process(u)
            except Exception as e:
                print("telegram poll error:", e)
                stop.wait(5)


def serve(cfg, mode="paper"):
    token, chats = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"].split(",")
    bot = TradingBot(cfg, mode)
    ui = TelegramUI(token, [c.strip() for c in chats], bot)
    stop = threading.Event()
    threading.Thread(target=bot.run_forever, args=(stop,), daemon=True).start()
    ui.broadcast(f"🤖 crypto-pulse запущен ({mode}) {cfg.symbol}. /help")
    try:
        ui.poll(stop)
    except KeyboardInterrupt:
        stop.set()
