"""Telegram-интерфейс (Bot API, long polling, без сторонних библиотек).

Переменные окружения: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID (список через запятую — кто может управлять ботом).
"""
import datetime as dt
import html
import io
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
    "/chart — график баланса\n"
    "/close — закрыть позицию (потребует /confirm)\n"
    "/settings — текущие настройки; /set риск|плечо|score значение\n"
    "/pause — не открывать новые сделки\n"
    "/resume — снова искать входы (сбрасывает kill-switch)\n"
    "/help — эта справка"
)


class Commands:
    """Логика команд отдельно от сети — легко тестировать."""

    ALIASES = {"риск": "risk_per_trade", "плечо": "max_leverage", "score": "min_score"}

    def __init__(self, bot: TradingBot):
        self.bot = bot
        self.close_requested = 0.0

    def handle(self, text: str) -> str:
        parts = text.strip().split()
        cmd = parts[0].split("@")[0].lower() if parts else ""
        self.args = parts[1:]
        fn = {"/close": self.close, "/confirm": self.confirm, "/settings": self.settings, "/set": self.set,
              "/start": self.help, "/help": self.help, "/status": self.status, "/position": self.position,
              "/stats": self.stats, "/report": self.report, "/backtest": self.backtest,
              "/pause": self.pause, "/resume": self.resume}.get(cmd)
        return fn() if fn else "Неизвестная команда. /help"

    def help(self):
        return HELP

    def status(self):
        b = self.bot
        ret = 100 * (b.equity / b.start_equity - 1)
        state = "🛑 KILL-SWITCH (остановлен)" if b.halted else "⏸ на паузе" if b.paused else "▶️ работает"
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

    def close(self):
        if not self.bot.pos:
            return "Открытых позиций нет."
        self.close_requested = time.time()
        return "⚠️ Закрыть позицию по рынку? Подтвердите командой /confirm в течение 60 секунд."

    def confirm(self):
        if time.time() - self.close_requested > 60:
            return "Нет активного запроса (или он истёк). Сначала /close."
        self.close_requested = 0.0
        return self.bot.close_position()

    def settings(self):
        c = self.bot.cfg
        return (f"<b>Настройки</b>\nриск на сделку: {c.risk_per_trade*100:.2f}%\nмакс. плечо: {c.max_leverage}x\n"
                f"мин. score: {c.min_score}\nцель ROE: {c.target_roe*100:.0f}%\nkill-switch: {c.max_drawdown*100:.0f}%\n"
                f"Изменить: /set риск 0.005 | /set плечо 8 | /set score 6")

    def set(self):
        if len(self.args) != 2:
            return "Формат: /set риск|плечо|score значение"
        key = self.ALIASES.get(self.args[0].lower(), self.args[0])
        try:
            val = float(self.args[1].replace(",", "."))
        except ValueError:
            return "Значение должно быть числом."
        return self.bot.set_param(key, val)

    def chart_png(self):
        """PNG с кривой баланса или None, если нет сделок/matplotlib."""
        if not self.bot.trades:
            return None
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except ImportError:
            return None
        t = pd.DataFrame(self.bot.trades)
        eq = self.bot.start_equity + t.pnl.cumsum()
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(range(len(eq) + 1), [self.bot.start_equity, *eq], lw=1.8)
        ax.fill_between(range(len(eq) + 1), [self.bot.start_equity, *eq], self.bot.start_equity, alpha=0.15)
        ax.set_title(f"Баланс: {self.bot.equity:.2f} USDT  |  сделок: {len(t)}")
        ax.set_xlabel("№ сделки"); ax.grid(alpha=0.3)
        buf = io.BytesIO(); fig.savefig(buf, format="png", dpi=110, bbox_inches="tight"); plt.close(fig)
        return buf.getvalue()

    def chart_text(self):
        if not self.bot.trades:
            return "Закрытых сделок пока нет."
        eq = self.bot.start_equity + pd.DataFrame(self.bot.trades).pnl.cumsum()
        bars = "▁▂▃▄▅▆▇█"
        lo, hi = eq.min(), eq.max()
        line = "".join(bars[int((v - lo) / (hi - lo + 1e-9) * 7)] for v in eq.tail(40))
        return f"Баланс (последние {min(40, len(eq))} сделок):\n{line}\n{lo:.2f} … {hi:.2f} USDT"

    def daily_summary(self, day: dt.date) -> str:
        t = pd.DataFrame(self.bot.trades)
        if t.empty:
            return f"📅 {day}: сделок не было. Баланс {self.bot.equity:.2f} USDT."
        d = t[pd.to_datetime(t["close"]).dt.date == day]
        if d.empty:
            return f"📅 {day}: сделок не было. Баланс {self.bot.equity:.2f} USDT."
        return (f"📅 Итоги {day}: сделок {len(d)}, прибыльных {int((d.pnl > 0).sum())}\n"
                f"PnL {d.pnl.sum():+.2f} USDT | Баланс {self.bot.equity:.2f} USDT")

    def pause(self):
        self.bot.pause()
        return "⏸ Новые сделки отключены (открытая позиция сопровождается до SL/TP)."

    def resume(self):
        return self.bot.resume()


class TelegramUI:
    def __init__(self, token: str, allowed_chats, bot: TradingBot, session=None):
        self.url = f"https://api.telegram.org/bot{token}/"
        self.allowed = {str(c) for c in allowed_chats}
        self.bot, self.cmds = bot, Commands(bot)
        self.http = session or requests.Session()
        self.offset = None
        self.last_summary = dt.datetime.now(dt.timezone.utc).date()
        bot.notify = self.broadcast

    def api(self, method, **params):
        r = self.http.post(self.url + method, json=params, timeout=65)
        r.raise_for_status()
        return r.json()["result"]

    KEYBOARD = {"keyboard": [["/status", "/position", "/chart"], ["/stats", "/report", "/settings"],
                              ["/pause", "/resume", "/close"]], "resize_keyboard": True}

    def send_photo(self, chat_id, png: bytes):
        r = self.http.post(self.url + "sendPhoto", data={"chat_id": chat_id},
                           files={"photo": ("chart.png", png, "image/png")}, timeout=65)
        r.raise_for_status()

    def maybe_daily_summary(self, now: dt.datetime):
        """После полуночи UTC шлёт итоги прошедшего дня (один раз)."""
        today = now.date()
        if today > self.last_summary:
            y = today - dt.timedelta(days=1)
            self.last_summary = today
            self.broadcast(self.cmds.daily_summary(y))

    def send(self, chat_id, text, keyboard=False):
        for i in range(0, len(text), 3900):   # лимит Telegram — 4096 символов
            chunk = text[i:i + 3900]
            try:
                extra = {"reply_markup": self.KEYBOARD} if keyboard and i == 0 else {}
                self.api("sendMessage", chat_id=chat_id, text=chunk, parse_mode="HTML", **extra)
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
            if text.split()[0].split("@")[0].lower() == "/chart":
                png = self.cmds.chart_png()
                return self.send_photo(chat, png) if png else self.send(chat, self.cmds.chart_text())
            self.send(chat, self.cmds.handle(text), keyboard=text.startswith(("/start", "/help")))
        except Exception as e:
            self.send(chat, f"Ошибка: {html.escape(str(e))}")

    def poll(self, stop: threading.Event):
        while not stop.is_set():
            try:
                self.maybe_daily_summary(dt.datetime.now(dt.timezone.utc))
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
