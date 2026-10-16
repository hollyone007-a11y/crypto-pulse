"""Живой цикл. По умолчанию paper (виртуальная торговля на реальных свечах)."""
import threading
import time

from .analysis import generate_signal
from .backtest import _HTF, prepare
from .broker import CcxtBroker, Journal
from .config import Config
from .data import fetch_klines
from .position import Position
from .risk import position_size

SECS = {"15m": 900, "1h": 3600, "4h": 14400}


class TradingBot:
    def __init__(self, cfg: Config, mode: str = "paper", notify=print, fetch=fetch_klines):
        self.cfg, self.mode, self.notify, self.fetch = cfg, mode, notify, fetch
        self.broker = CcxtBroker() if mode == "live" else None
        self.journal = Journal()
        self.equity = self.broker.balance() if self.broker else cfg.equity
        self.start_equity = self.equity
        self.pos = None
        self.cooldown = 0
        self.paused = False
        self.trades = []          # закрытые сделки (для /stats и /report)
        self.last_check = None
        self.last_error = None
        self.lock = threading.Lock()

    def step(self):
        """Один шаг по закрытой свече: сопровождение позиции и поиск нового сигнала."""
        cfg = self.cfg
        df = self.fetch(cfg.symbol, cfg.ltf, 1000).iloc[:-1]   # только закрытые свечи
        m = prepare(df, cfg)
        bar, prev = next(m.iloc[-1:].itertuples()), next(m.iloc[-2:-1].itertuples())
        with self.lock:
            self.last_check = bar.Index
            if self.pos is not None:
                self._manage(bar)
            self.cooldown = max(0, self.cooldown - 1)
            if self.pos is None and self.cooldown == 0 and not self.paused:
                sig = generate_signal(bar, prev, _HTF(bar._asdict()), cfg)
                if sig:
                    self._open(sig, bar)

    def _manage(self, bar):
        p, cfg = self.pos, self.cfg
        if self.mode == "paper":
            res = p.update(bar)
            if not res:
                return
            px, why = res
            pnl = max(p.pnl(px, cfg.fee, cfg.slippage), -p.margin)
            self.equity += pnl
        elif self.broker.is_flat(cfg.symbol):
            new_eq = self.broker.balance()
            pnl, px, why = new_eq - self.equity, bar.close, "closed"
            self.equity = new_eq
        else:
            return
        t = {"open": p.open_time, "close": bar.Index, "side": p.side, "entry": p.entry, "exit": px, "pnl": pnl,
             "roe": pnl / p.margin, "leverage": p.leverage, "exit_reason": why, "bars": p.bars, "mae": p.mae,
             "mfe": p.mfe, "score": p.score, "reasons": ",".join(p.reasons), **p.ctx}
        self.trades.append(t)
        self.journal.write(event="close", **t, equity=self.equity)
        self.pos, self.cooldown = None, cfg.cooldown_bars
        icon = "✅" if pnl > 0 else "🔻"
        self.notify(f"{icon} Закрыта {p.side.upper()} {cfg.symbol} по {why}\n"
                    f"PnL {pnl:+.2f} USDT (ROE {t['roe']*100:+.1f}%)\nБаланс: {self.equity:.2f}")

    def _open(self, sig, bar):
        qty, margin = position_size(self.equity, sig, self.cfg)
        if self.broker:
            qty = self.broker.open(self.cfg.symbol, sig, qty)
        self.pos = Position(sig.side, sig.entry, sig.sl, sig.tp, qty, margin, sig.leverage, bar.Index,
                            sig.score, sig.reasons, sig.ctx)
        self.journal.write(event="open", side=sig.side, entry=sig.entry, sl=sig.sl, tp=sig.tp, lev=sig.leverage,
                           qty=qty, score=sig.score, reasons=sig.reasons, ctx=sig.ctx, t=bar.Index)
        icon = "🟢" if sig.side == "long" else "🔴"
        self.notify(f"{icon} Открыта {sig.side.upper()} {self.cfg.symbol} x{sig.leverage}\n"
                    f"Вход {sig.entry:.2f} | SL {sig.sl:.2f} | TP {sig.tp:.2f}\n"
                    f"Маржа {margin:.2f} USDT | score {sig.score:.1f}\nПричины: {', '.join(sig.reasons)}")

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
