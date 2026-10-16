"""Торговый цикл. По умолчанию paper (виртуальная торговля на реальных свечах)."""
import dataclasses
import threading
import time

import pandas as pd

from .analysis import generate_signal
from .backtest import _HTF, prepare
from .broker import CcxtBroker, Journal
from .config import Config
from .data import fetch_klines
from .position import Position
from .risk import position_size
from .store import Store

SECS = {"15m": 900, "1h": 3600, "4h": 14400}
SETTINGS_LIMITS = {"risk_per_trade": (0.001, 0.03), "max_leverage": (1, 20), "min_score": (3.0, 10.0)}


class TradingBot:
    def __init__(self, cfg: Config, mode: str = "paper", notify=print, fetch=fetch_klines, broker=None, store=None):
        self.cfg, self.mode, self.notify, self.fetch = cfg, mode, notify, fetch
        self.broker = broker or (CcxtBroker() if mode == "live" else None)
        self.journal = Journal()
        self.store = store or Store("cryptopulse.db")
        self.lock = threading.RLock()
        self.equity = self.broker.balance() if self.broker else cfg.equity
        self.start_equity, self.peak = self.equity, self.equity
        self.pos, self.cooldown, self.streak = None, 0, 0
        self.paused = self.halted = self.foreign = False
        self.last_check, self.last_error, self.last_price = None, None, None
        self.trades = []
        self._restore()

    # ---------- состояние ----------
    def _restore(self):
        st = self.store.get_state()
        self.trades = self.store.trades()
        if not st:
            return
        for k in ("equity", "start_equity", "peak", "cooldown", "streak", "paused", "halted"):
            setattr(self, k, st[k])
        for k, v in st.get("settings", {}).items():
            setattr(self.cfg, k, v)
        if st.get("pos"):
            d = st["pos"]
            d["open_time"] = pd.Timestamp(d["open_time"])
            self.pos = Position(**d)

    def save(self):
        st = {k: getattr(self, k) for k in ("equity", "start_equity", "peak", "cooldown", "streak", "paused", "halted")}
        st["pos"] = dataclasses.asdict(self.pos) if self.pos else None
        st["settings"] = {k: getattr(self.cfg, k) for k in SETTINGS_LIMITS}
        self.store.set_state(st)

    # ---------- основной шаг ----------
    def step(self):
        cfg = self.cfg
        df = self.fetch(cfg.symbol, cfg.ltf, 1000).iloc[:-1]   # только закрытые свечи
        m = prepare(df, cfg)
        bar, prev = next(m.iloc[-1:].itertuples()), next(m.iloc[-2:-1].itertuples())
        with self.lock:
            self.last_check, self.last_price = bar.Index, bar.close
            if self.broker and not self._reconcile():
                return self.save()
            if self.pos is not None:
                self._manage(bar)
            self.cooldown = max(0, self.cooldown - 1)
            if self.pos is None and self.cooldown == 0 and not self.paused:
                sig = generate_signal(bar, prev, _HTF(bar._asdict()), cfg)
                if sig:
                    self._open(sig, bar)
            self.save()

    def _reconcile(self) -> bool:
        """Сверка с биржей (live). False — торговать нельзя."""
        sym = self.cfg.symbol
        ex = self.broker.position(sym)
        if ex and not self.pos:
            if not self.foreign:
                self.notify(f"⚠️ На бирже есть позиция {ex[0]} {ex[1]}, которую открыл не бот. Бот её не трогает "
                            f"и не торгует, пока она открыта.")
            self.foreign = True
            return False
        self.foreign = False
        if ex and self.pos and not self.broker.has_protection(sym):
            self.notify("🚨 У позиции нет стопа/тейка на бирже — восстанавливаю.")
            try:
                self.broker.protect(sym, self.pos.side, ex[1], self.pos.sl, self.pos.tp)
            except Exception as e:
                self.broker.close_all(sym)
                self.notify(f"🚨 Не удалось выставить защиту ({e}). Позиция закрыта по рынку.")
        return True

    def _manage(self, bar):
        p, cfg = self.pos, self.cfg
        if self.broker is None:
            res = p.update(bar)
            if not res:
                return
            px, why = res
            pnl = max(p.pnl(px, cfg.fee, cfg.slippage), -p.margin)
            self.equity += pnl
        elif self.broker.position(cfg.symbol) is None:
            new_eq = self.broker.balance()
            pnl, px, why = new_eq - self.equity, bar.close, "closed"
            self.equity = new_eq
        else:
            p.bars += 1
            return
        self._record_close(p, px, why, pnl, bar.Index)

    def _record_close(self, p, px, why, pnl, when):
        cfg = self.cfg
        t = {"open": p.open_time, "close": when, "side": p.side, "entry": p.entry, "exit": px, "pnl": pnl,
             "roe": pnl / p.margin, "leverage": p.leverage, "exit_reason": why, "bars": p.bars, "mae": p.mae,
             "mfe": p.mfe, "score": p.score, "reasons": ",".join(p.reasons), "funding": p.funding, **p.ctx}
        self.trades.append(t)
        self.store.add_trade(t)
        self.journal.write(event="close", **t, equity=self.equity)
        self.pos, self.cooldown = None, cfg.cooldown_bars
        self.streak = self.streak + 1 if pnl <= 0 else 0
        self.peak = max(self.peak, self.equity)
        self.save()   # закрытие фиксируем сразу: после падения не «воскресим» закрытую позицию
        icon = "✅" if pnl > 0 else "🔻"
        msg = (f"{icon} Закрыта {p.side.upper()} {cfg.symbol} по {why}\n"
               f"PnL {pnl:+.2f} USDT (ROE {t['roe']*100:+.1f}%)\nБаланс: {self.equity:.2f}")
        if self.streak >= cfg.max_loss_streak:
            msg += f"\n⚠️ {self.streak} стопов подряд — риск на сделку снижен в {1/cfg.streak_risk_mult:.0f} раза"
        self.notify(msg)
        if self.equity <= self.peak * (1 - cfg.max_drawdown):
            self.halted = self.paused = True
            self.notify(f"🛑 KILL-SWITCH: просадка {100*(1-self.equity/self.peak):.1f}% от пика "
                        f"(лимит {cfg.max_drawdown*100:.0f}%). Торговля остановлена. /resume — продолжить вручную.")

    def _open(self, sig, bar):
        mult = self.cfg.streak_risk_mult if self.streak >= self.cfg.max_loss_streak else 1.0
        qty, margin = position_size(self.equity, sig, self.cfg, mult)
        if self.broker:
            qty = self.broker.open(self.cfg.symbol, sig, qty)
        self.pos = Position(sig.side, sig.entry, sig.sl, sig.tp, qty, margin, sig.leverage, bar.Index,
                            sig.score, sig.reasons, sig.ctx)
        self.save()   # сразу фиксируем позицию: после падения не откроем дубль
        self.journal.write(event="open", side=sig.side, entry=sig.entry, sl=sig.sl, tp=sig.tp, lev=sig.leverage,
                           qty=qty, score=sig.score, reasons=sig.reasons, ctx=sig.ctx, t=bar.Index)
        icon = "🟢" if sig.side == "long" else "🔴"
        self.notify(f"{icon} Открыта {sig.side.upper()} {self.cfg.symbol} x{sig.leverage}\n"
                    f"Вход {sig.entry:.2f} | SL {sig.sl:.2f} | TP {sig.tp:.2f}\n"
                    f"Маржа {margin:.2f} USDT | score {sig.score:.1f}\nПричины: {', '.join(sig.reasons)}")

    # ---------- ручное управление (Telegram) ----------
    def close_position(self) -> str:
        with self.lock:
            p = self.pos
            if not p:
                return "Открытых позиций нет."
            if self.broker:
                self.broker.close_all(self.cfg.symbol)
                new_eq = self.broker.balance()
                pnl, px = new_eq - self.equity, self.last_price
                self.equity = new_eq
            else:
                px = self.last_price
                pnl = max(p.pnl(px, self.cfg.fee, self.cfg.slippage), -p.margin)
                self.equity += pnl
            self._record_close(p, px, "manual", pnl, pd.Timestamp.now("UTC").tz_localize(None))
            self.save()
            return f"Позиция закрыта вручную. PnL {pnl:+.2f} USDT."

    def set_param(self, key: str, value: float) -> str:
        if key not in SETTINGS_LIMITS:
            return f"Нельзя менять '{key}'. Доступно: {', '.join(SETTINGS_LIMITS)}"
        lo, hi = SETTINGS_LIMITS[key]
        if not lo <= value <= hi:
            return f"{key} должен быть в диапазоне {lo}…{hi}"
        with self.lock:
            setattr(self.cfg, key, int(value) if key == "max_leverage" else value)
            self.save()
        return f"{key} = {getattr(self.cfg, key)} (действует на новые сделки)"

    def resume(self) -> str:
        with self.lock:
            was_halted = self.halted
            self.paused = self.halted = False
            if was_halted:
                self.peak = self.equity   # новая точка отсчёта просадки — осознанное решение пользователя
            self.save()
        return "▶️ Поиск входов включён." + (" Kill-switch сброшен, пик просадки обнулён." if was_halted else "")

    def pause(self):
        with self.lock:
            self.paused = True
            self.save()

    def run_forever(self, stop: threading.Event):
        cfg = self.cfg
        while not stop.is_set():
            try:
                self.step()
                self.last_error = None
            except Exception as e:  # сеть/биржа не должны убивать бота
                if str(e) != self.last_error:
                    self.notify(f"⚠️ Ошибка цикла: {e}")
                self.last_error = str(e)
            wait = SECS[cfg.ltf] - time.time() % SECS[cfg.ltf] + 5   # ждём закрытия следующей свечи
            stop.wait(min(wait, 300) if self.last_error else wait)


def run(cfg: Config, mode: str = "paper", once: bool = False):
    bot = TradingBot(cfg, mode)
    print(f"[{mode}] {cfg.symbol} {cfg.ltf}/{cfg.htf} equity={bot.equity:.2f}")
    if once:
        return bot.step()
    bot.run_forever(threading.Event())
